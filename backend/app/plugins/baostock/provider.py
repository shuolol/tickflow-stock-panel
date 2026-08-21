"""baostock 内置数据源 provider — 免费 A 股除权因子。

在无 TickFlow Starter+(除权因子)权限时, 用 baostock 的 query_adjust_factor
拉取沪深 A 股复权因子, 换算为项目 ex_factor 语义后写本地 adj_factor/all.parquet。

推导关系 (已在 scripts/verify_baostock_adj.py 与 baostock 自身 qfq K线 / akshare 交叉验证):
  baostock 返回 foreAdjustFactor(前复权因子, 最新事件=1.0, 事件按时间升序递增)。
  项目 ex_factor_k = fore_k / fore_{k-1}  (fore_0 = 1.0)
  → _apply_adj_factor 的 cum_prod/total = fore[t <= D] = 前复权乘子, 精确复现 qfq。
  配股事件注意: backAdjustFactor 会骤降而 foreAdjustFactor 不变 (ex_factor=1.0),
  故必须用 fore 而非 back。

方法签名对齐 custom.GenericHTTPProvider(除权同步按这套签名调用), 注入 custom loader
注册表后, kline_sync.sync_adj_factor 无需改动即可路由到本 provider。

baostock 只覆盖沪深 (sh./sz.), 北交所 (.BJ) 静默跳过。ETF 代码如 510300.SH 同样
可转换查询, 但当前 ETF 除权走独立 Cap 门控路径, 不会路由到本插件。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime

import polars as pl

from app.data_providers.normalizer import normalize_adj_factors
from app.tickflow.rate_limits import chunked

logger = logging.getLogger(__name__)

# baostock 支持的数据集 — 仅除权因子 (日K/实时等仍走原数据源)
_DATASETS = ("adj_factor",)

# 每次【完整链】查询间隔秒数。baostock 免费服务有 ~3000 req/hr 限流, 全市场逐标的
# 一路猛打会触发断连/限流。增量模式(每日管道)先做便宜【窗口探测】只对确有新事件的
# 标的多拉完整链 → 日常开销小。
_QUERY_INTERVAL_S = 0.15
# 窗口探测查询间隔: 轻查询, 每标的一次、每日一次突发, 用小间隔防断连即可。
_PROBE_INTERVAL_S = 0.03

# baostock 底层 socket 无超时(send_msg 的 recv 会无限阻塞)。服务端限流停止响应时
# 单次查询可卡死整个同步 → 登录后给 socket 设 recv 超时, 单查询最久等这么久。
# (真超时会被 baostock 吞成 error_code, _query_factors 抛 RuntimeError → 逐标的
# except 捕获后走重连分支, 不会冻住。)
_SOCKET_TIMEOUT_S = 30

# 项目 symbol (600000.SH / 000001.SZ / 830000.BJ) → baostock code (sh.600000 / sz.000001)
_EXCHANGE_PREFIX = {"SH": "sh", "SZ": "sz"}

# baostock 覆盖最早 A 股 IPO, 取 1990 起即「上市至今」全量。
_EPOCH = "1990-01-01"


@dataclass
class _BaostockConfig:
    """轻量 config shim, 让 custom loader 的 list_sources/provider_has_dataset 能识别本 provider。"""

    name: str = "baostock"
    display_name: str = "baostock（免费除权因子）"
    datasets: dict = field(default_factory=lambda: dict.fromkeys(_DATASETS))
    path: None = None
    builtin: bool = True


def _to_bs_code(symbol: str) -> str | None:
    """项目 symbol → baostock code。北交所 (.BJ) 及异常格式返回 None。"""
    sym = (symbol or "").strip().upper()
    if "." not in sym:
        return None
    code, exchange = sym.split(".", 1)
    prefix = _EXCHANGE_PREFIX.get(exchange)
    if not prefix or not code.isdigit():
        return None
    return f"{prefix}.{code}"


def _to_date(x: datetime | date | str | None) -> date | None:
    if x is None:
        return None
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    try:
        return datetime.strptime(str(x)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _query_factors(bs_code: str, start_date: str, end_date: str) -> pl.DataFrame:
    """拉取单标的复权因子历史, 返回 dividOperateDate/foreAdjustFactor (升序)。"""
    import baostock as bs

    rs = bs.query_adjust_factor(code=bs_code, start_date=start_date, end_date=end_date)
    if rs.error_code != "0":
        raise RuntimeError(f"baostock query_adjust_factor: {rs.error_msg}")
    rows: list[list[str]] = []
    while rs.next():
        rows.append(rs.get_row_data())
    if not rows:
        # 无除权事件 (从未分红/退市/窗口内无新事件) → 该标的不需要因子
        return pl.DataFrame()
    df = pl.DataFrame(
        rows,
        schema={
            "code": pl.Utf8,
            "dividOperateDate": pl.Utf8,
            "foreAdjustFactor": pl.Utf8,
            "backAdjustFactor": pl.Utf8,
            "adjustFactor": pl.Utf8,
        },
        orient="row",
    )
    return (
        df.with_columns(
            pl.col("dividOperateDate").str.strptime(pl.Date, "%Y-%m-%d", strict=False),
            pl.col("foreAdjustFactor").cast(pl.Float64, strict=False),
        )
        .drop_nulls(["dividOperateDate", "foreAdjustFactor"])
        .sort("dividOperateDate")
    )


def _derive_ex_factors(fac: pl.DataFrame, symbol: str) -> pl.DataFrame:
    """在【完整链】上推导 ex_factor 并补 symbol。

    关键: 项目 ex_factor_k = fore_k / fore_{k-1} (fore_0 = 1.0), 每事件是 pre/post 比值。
    若在截断的窗口链上推导, 窗口首个事件会以 1.0 为基准 → 复权比例错误;
    必须在完整链上推导, 再按窗口过滤, 增量同步才安全。
    """
    fores = fac["foreAdjustFactor"].to_list()
    exs = [fores[0] / 1.0] + [fores[k] / fores[k - 1] for k in range(1, len(fores))]
    return fac.with_columns(
        pl.Series("ex_factor", exs),
        pl.lit(symbol).alias("symbol"),
    ).select(
        pl.col("dividOperateDate").alias("trade_date"),
        "symbol",
        "ex_factor",
    )


def _set_socket_timeout(timeout_s: float) -> None:
    """给 baostock 全局 socket 设 recv 超时, 防止单查询无限阻塞。

    baostock 底层 socket 默认无超时, 服务端限流停止响应时 recv 会永久阻塞。
    这里对每个新 socket 设 _SOCKET_TIMEOUT_S。测试注入的 fake 模块没有 baostock.common,
    静默跳过即可。
    """
    try:
        from baostock.common import context as bs_ctx
    except Exception:  # noqa: BLE001
        return
    sock = getattr(bs_ctx, "default_socket", None)
    if sock is None:
        return
    try:
        sock.settimeout(timeout_s)
    except Exception:  # noqa: BLE001
        pass


def _reconnect_bs() -> bool:
    """限流断连/查询失败后重建 baostock 连接(重置 socket 状态), 成功返回 True。

    send_msg 内部把 socket.timeout/断连吞成 error_code, _query_factors 只能看到
    RuntimeError。失败即重连是最稳妥的: 连接是轻量 round-trip, 且能清掉半读状态。
    """
    import baostock as bs

    try:
        bs.logout()
    except Exception:  # noqa: BLE001
        pass
    try:
        lg = bs.login()
        if lg.error_code != "0":
            logger.warning("baostock 重连失败: %s", lg.error_msg)
            return False
    except Exception as e:  # noqa: BLE001
        logger.warning("baostock 重连异常: %s", e)
        return False
    _set_socket_timeout(_SOCKET_TIMEOUT_S)
    return True


class BaostockProvider:
    """内置 baostock 除权因子数据源。"""

    name = "baostock"
    builtin = True

    def __init__(self) -> None:
        self.config = _BaostockConfig()

    def close(self) -> None:  # loader.load_all 会对每个 provider 调 close
        pass

    # ---- adj_factor ----
    def get_adj_factors(
        self,
        symbols: list[str],
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        asset_type: str = "stock",  # noqa: ARG002
        on_chunk_done=None,
        time_budget_s: float | None = None,
    ) -> pl.DataFrame:
        if not symbols:
            return pl.DataFrame()

        # baostock 登录是全局 socket, 并发调用会串扰 → 进程内单飞由 pipeline_jobs
        # 的重任务锁保证 (run_now / sync_adj_factor 同一时刻只有一条)。这里 login 一次。
        import baostock as bs

        lg = bs.login()
        if lg.error_code != "0":
            logger.warning("baostock login failed: %s", lg.error_msg)
            raise RuntimeError(f"baostock 登录失败: {lg.error_msg}")
        _set_socket_timeout(_SOCKET_TIMEOUT_S)

        end = _to_date(end_time) or date.today()
        end_str = end.strftime("%Y-%m-%d")

        frames: list[pl.DataFrame] = []
        skipped_bj: int = 0
        failed: list[str] = []
        start_d = _to_date(start_time)
        end_d = _to_date(end_time)
        # 增量模式(每日管道): 先窗口探测, 只对确有新事件的标的做完整链查询。
        # 全量模式(手动按钮, start_time=None): 每标的直接完整链查询。
        incremental = start_d is not None
        start_str = start_d.strftime("%Y-%m-%d") if incremental else _EPOCH

        # 时间预算: 超过后提前返回部分结果, 由上层把除权因子当软失败处理(不拖垮管道)。
        # baostock 逐标的查询 + 限速, 全市场 5550 只可能远慢于任务超时 → 预算兜底。
        t0 = time.monotonic()
        bailed = False
        processed = 0
        total = len(symbols)

        try:
            chunks = chunked(symbols, 100)
            for i, chunk in enumerate(chunks):
                for sym in chunk:
                    if time_budget_s is not None and time.monotonic() - t0 >= time_budget_s:
                        bailed = True
                        break
                    processed += 1
                    bs_code = _to_bs_code(sym)
                    if bs_code is None:
                        if sym.upper().endswith(".BJ"):
                            skipped_bj += 1
                        continue
                    did_full = False
                    try:
                        if incremental:
                            # Phase 1: 窗口探测 (便宜, 每标的一次/日)
                            probe = _query_factors(bs_code, start_str, end_str)
                            if probe.is_empty():
                                # 窗口内无新除权事件 → 因子链无变化, 跳过
                                if _PROBE_INTERVAL_S:
                                    time.sleep(_PROBE_INTERVAL_S)
                                continue
                        # Phase 2: 完整链查询 (推导正确性必须, 见 _derive_ex_factors)
                        fac = _query_factors(bs_code, _EPOCH, end_str)
                        did_full = True
                        if not fac.is_empty():
                            out = _derive_ex_factors(fac, sym)
                            if start_d is not None:
                                out = out.filter(pl.col("trade_date") >= start_d)
                            if end_d is not None:
                                out = out.filter(pl.col("trade_date") <= end_d)
                            if not out.is_empty():
                                frames.append(out)
                    except Exception as e:  # noqa: BLE001
                        logger.warning("baostock adj 拉取失败 %s: %s", sym, e)
                        failed.append(sym)
                        # 限流/断连后 socket 可能处于半读状态 → 重连重置, 否则后续
                        # 查询会连环失败。连接是轻量 round-trip, 失败时重连是安全兜底。
                        _reconnect_bs()
                        continue
                    # 完整链查询是限流持续成本主因 → 节流; 窗口探测是小突发, 用更小间隔
                    if did_full and _QUERY_INTERVAL_S:
                        time.sleep(_QUERY_INTERVAL_S)
                    elif _PROBE_INTERVAL_S:
                        time.sleep(_PROBE_INTERVAL_S)
                # 无论本 chunk 是否因预算中断, 都上报当前进度, 让上层能据 cur < tot
                # 判定除权因子部分完成。
                if on_chunk_done:
                    on_chunk_done(i + 1, len(chunks))
                if bailed:
                    break
        finally:
            try:
                bs.logout()
            except Exception:  # noqa: BLE001
                pass

        if skipped_bj:
            logger.info("baostock adj: 跳过 %d 只北交所标的 (baostock 不覆盖)", skipped_bj)
        if bailed:
            logger.warning("baostock adj 超时: 预算 %ss 内仅处理 %d/%d 只标的, "
                           "部分标的因子未更新 (可稍后重试或点除权因子全量同步)",
                           time_budget_s, processed, total)
        elif failed:
            logger.warning("baostock adj 部分失败: %d 只标的未获取 (样例: %s)",
                           len(failed), failed[:10])

        if not frames:
            return pl.DataFrame()
        out = pl.concat(frames, how="diagonal_relaxed")
        return normalize_adj_factors(out, source=self.name)

    # ---- 测试(设置页试拉) ----
    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        symbols = symbols or ["600519.SH"]
        if dataset == "adj_factor":
            df = self.get_adj_factors(symbols, None, None)
            return {
                "provider": self.name,
                "dataset": "adj_factor",
                "rows": df.height,
                "columns": df.columns,
                "preview": df.head(5).to_dicts() if not df.is_empty() else [],
            }
        raise ValueError(f"baostock 不支持数据集: {dataset}")


def availability() -> tuple[bool, str]:
    """可用性检测 — 只查依赖是否可导入(避免在启动时阻塞网络)。

    baostock 登录延迟到实际拉取时, 网络问题会在同步阶段以明确错误呈现。
    """
    try:
        import baostock  # noqa: F401

        return True, "ok"
    except ImportError:
        return False, "未安装 baostock, 请点击「安装」安装依赖"
