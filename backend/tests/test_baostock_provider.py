"""baostock 除权因子 provider 测试 — 两阶段增量与全量逻辑。

不依赖真实网络: 注入 fake baostock 模块 + monkeypatch _query_factors, 只验证
符号转换、ex_factor 推导、窗口探测→完整链→窗口过滤的调用序列、北交所跳过、
以及归一化输出契约。真实 baostock 数据的语义一致性由 _verify_baostock_adj.py
(完整链 vs qfq / akshare) 验证过。
"""
from __future__ import annotations

import datetime as dt
import sys
import types

import polars as pl
import pytest

from app.plugins.baostock import provider as bp


@pytest.fixture
def fake_baostock(monkeypatch):
    """get_adj_factors 内部 `import baostock as bs` 会命中 sys.modules → 用假模块挡掉登录。"""
    bs = types.ModuleType("baostock")
    bs.login = lambda: types.SimpleNamespace(error_code="0", error_msg="")
    bs.logout = lambda: None
    monkeypatch.setitem(sys.modules, "baostock", bs)
    return bs


def make_fac(events: list[tuple[str, float]]) -> pl.DataFrame:
    """events: [(dividOperateDate, foreAdjustFactor)] 升序 → 模拟 _query_factors 返回值。"""
    return pl.DataFrame(
        {
            "dividOperateDate": pl.Series([e[0] for e in events]).str.strptime(pl.Date, "%Y-%m-%d"),
            "foreAdjustFactor": pl.Series([e[1] for e in events]),
        }
    )


# ---- 单元: 符号转换 ----

def test_to_bs_code():
    assert bp._to_bs_code("600000.SH") == "sh.600000"
    assert bp._to_bs_code("000001.SZ") == "sz.000001"
    assert bp._to_bs_code("600519.SH") == "sh.600519"
    assert bp._to_bs_code("830000.BJ") is None  # 北交所不覆盖
    assert bp._to_bs_code("600000") is None
    assert bp._to_bs_code("") is None


def test_derive_ex_factors_fore_ratio():
    fac = make_fac([("2015-07-01", 1.6), ("2020-06-18", 1.2), ("2025-06-14", 1.0)])
    out = bp._derive_ex_factors(fac, "000001.SZ")
    assert out.columns == ["trade_date", "symbol", "ex_factor"]
    assert out.schema["trade_date"] == pl.Date
    assert float(out["ex_factor"][0]) == pytest.approx(1.6)          # fore_0 / 1.0
    assert float(out["ex_factor"][1]) == pytest.approx(1.2 / 1.6)    # 配股事件会 < 1
    assert float(out["ex_factor"][2]) == pytest.approx(1.0 / 1.2)


# ---- 调用序列: 增量探测 vs 全量 ----

def test_full_mode_skips_probe(monkeypatch, fake_baostock):
    """全量(start_time=None): 每标的只做一次完整链查询, 不做窗口探测。"""
    calls: list[tuple[str, str]] = []

    def fake_query(bs_code, start_date, end_date):
        calls.append((start_date, end_date))
        return make_fac([("2020-06-18", 1.2), ("2025-06-14", 1.0)])

    monkeypatch.setattr(bp, "_query_factors", fake_query)
    df = bp.BaostockProvider().get_adj_factors(["000001.SZ"], None, None)
    assert calls == [(bp._EPOCH, dt.date.today().strftime("%Y-%m-%d"))]
    assert df.height == 2  # 全量不过滤
    assert df.columns == ["symbol", "trade_date", "ex_factor"]


def test_incremental_probe_empty_skips_full(monkeypatch, fake_baostock):
    """增量: 窗口探测无新事件 → 只花探测一次查询, 不拉完整链。"""
    calls: list[tuple[str, str]] = []

    def fake_query(bs_code, start_date, end_date):
        calls.append((start_date, end_date))
        if start_date == bp._EPOCH:
            return make_fac([("2020-06-18", 1.2), ("2025-06-14", 1.0)])
        return pl.DataFrame()  # 窗口内无新事件

    monkeypatch.setattr(bp, "_query_factors", fake_query)
    df = bp.BaostockProvider().get_adj_factors(
        ["000001.SZ"],
        start_time=dt.datetime(2026, 1, 1),
        end_time=dt.datetime(2026, 8, 19),
    )
    assert calls == [("2026-01-01", "2026-08-19")]  # 只有探测
    assert df.is_empty()


def test_incremental_probe_hit_full_chain_and_window_filter(monkeypatch, fake_baostock):
    """增量: 探测命中新事件 → 完整链推导 ex_factor → 窗口过滤。"""
    calls: list[tuple[str, str]] = []

    def fake_query(bs_code, start_date, end_date):
        calls.append((start_date, end_date))
        if start_date == bp._EPOCH:
            return make_fac([("2015-07-01", 1.6), ("2020-06-18", 1.2), ("2025-06-14", 1.0)])
        return make_fac([("2025-06-14", 1.0)])  # 窗口内有一新事件

    monkeypatch.setattr(bp, "_query_factors", fake_query)
    df = bp.BaostockProvider().get_adj_factors(
        ["000001.SZ"],
        start_time=dt.datetime(2025, 1, 1),
        end_time=dt.datetime(2025, 12, 31),
    )
    # 完整链推导 ex = [1.6, 0.75, 0.8333…], 窗口过滤后仅剩 2025-06-14
    assert calls == [("2025-01-01", "2025-12-31"), (bp._EPOCH, "2025-12-31")]
    assert df.height == 1
    row = df.row(0)
    assert row[0] == "000001.SZ"
    assert str(row[1]) == "2025-06-14"
    assert float(row[2]) == pytest.approx(1.0 / 1.2)


def test_bj_symbols_skipped(monkeypatch, fake_baostock):
    """北交所 (.BJ) 不查询, 也不进结果; 沪深正常查询。"""
    queried: list[str] = []

    def fake_query(bs_code, start_date, end_date):
        queried.append(bs_code)
        return make_fac([("2020-06-18", 1.2), ("2025-06-14", 1.0)])

    monkeypatch.setattr(bp, "_query_factors", fake_query)
    df = bp.BaostockProvider().get_adj_factors(["830000.BJ", "000001.SZ"], None, None)
    assert queried == ["sz.000001"]
    assert df["symbol"].unique().to_list() == ["000001.SZ"]


def test_multi_chunk_calls_on_chunk_done(monkeypatch, fake_baostock):
    """分块回调: chunked 按 100 切片, 200 只 → 2 次 on_chunk_done。"""
    progress: list[tuple[int, int]] = []

    def fake_query(bs_code, start_date, end_date):
        return make_fac([("2020-06-18", 1.2)])

    monkeypatch.setattr(bp, "_query_factors", fake_query)
    symbols = [f"{i:06d}.SZ" for i in range(200)]
    bp.BaostockProvider().get_adj_factors(symbols, None, None, on_chunk_done=lambda n, t: progress.append((n, t)))
    assert progress == [(1, 2), (2, 2)]
