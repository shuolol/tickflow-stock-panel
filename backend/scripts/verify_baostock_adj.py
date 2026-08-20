"""验证 baostock 除权因子能否喂进项目的 _apply_adj_factor。

关键发现: baostock 返回:
  - foreAdjustFactor: 前复权因子 (事件累计, 最新事件=1.0)
  - backAdjustFactor == adjustFactor: 后复权因子 (从 IPO 累计, 递增)

推导: 前复权乘子 g(t) = fore[latest event <= t]  (baostock 自身 qfq = raw × g(t))
      → 项目 ex_factor_k = fore_k / fore_{k-1}  (fore_0 = 1.0, 事件按日期升序)
      → _apply_adj_factor 的 ratio = cum_prod/total = fore_k/fore_n = fore_k = g(t)

配股事件注意: back 会骤降但 fore 不变 (ex=1.0), 故必须用 fore, 不能用 back。

对照基准:
  1) baostock 自身 qfq K 线 (adjustflag=2)
  2) akshare 东财 qfq K 线 (stock_zh_a_hist adjust=qfq, 独立来源)
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

import polars as pl

from app.indicators.pipeline import _apply_adj_factor

STOCKS = [
    ("sh.600000", "600000.SH"),  # 浦发银行
    ("sz.000001", "000001.SZ"),  # 平安银行
    ("sh.601318", "601318.SH"),  # 中国平安
    ("sz.000333", "000333.SZ"),  # 美的集团 (多年分红)
]


def fetch_baostock_kline(bs_code: str, adjustflag: str, start="2020-01-01", end="2026-08-19"):
    import baostock as bs

    rs = bs.query_history_k_data_plus(
        bs_code, "date,open,high,low,close",
        start_date=start, end_date=end, frequency="d", adjustflag=adjustflag,
    )
    rows = []
    while (rs.error_code == "0") & rs.next():
        rows.append(rs.get_row_data())
    return pl.DataFrame(rows, schema={
        "date": pl.Utf8, "open": pl.Utf8, "high": pl.Utf8, "low": pl.Utf8, "close": pl.Utf8,
    }).with_columns(
        pl.col("date").str.strptime(pl.Date, "%Y-%m-%d"),
        pl.col("open").cast(pl.Float64), pl.col("high").cast(pl.Float64),
        pl.col("low").cast(pl.Float64), pl.col("close").cast(pl.Float64),
    ).drop_nulls(["date", "close"])


def fetch_baostock_factors(bs_code: str, start="1990-01-01", end="2026-08-19"):
    import baostock as bs

    rs = bs.query_adjust_factor(code=bs_code, start_date=start, end_date=end)
    rows = []
    while (rs.error_code == "0") & rs.next():
        rows.append(rs.get_row_data())
    return pl.DataFrame(rows, schema={
        "code": pl.Utf8, "dividOperateDate": pl.Utf8,
        "foreAdjustFactor": pl.Utf8, "backAdjustFactor": pl.Utf8, "adjustFactor": pl.Utf8,
    }).with_columns(
        pl.col("dividOperateDate").str.strptime(pl.Date, "%Y-%m-%d"),
        pl.col("foreAdjustFactor").cast(pl.Float64),
        pl.col("backAdjustFactor").cast(pl.Float64),
        pl.col("adjustFactor").cast(pl.Float64),
    ).drop_nulls(["dividOperateDate", "backAdjustFactor"]).sort("dividOperateDate")


def akshare_qfq(symbol_code: str):
    import akshare as ak

    df = ak.stock_zh_a_hist(symbol=symbol_code, period="daily",
                            start_date="20200101", end_date="20260819", adjust="qfq")
    return pl.from_pandas(df).with_columns(pl.col("日期").str.strptime(pl.Date, "%Y-%m-%d")).select(
        pl.col("日期").alias("date"), pl.col("收盘").alias("close")
    )


def main() -> None:
    import baostock as bs

    lg = bs.login()
    assert lg.error_code == "0", lg.error_msg
    print("baostock login OK\n")

    for bs_code, project_sym in STOCKS:
        factors = fetch_baostock_factors(bs_code)
        raw = fetch_baostock_kline(bs_code, "3").with_columns(pl.lit(project_sym).alias("symbol"))
        qfq = fetch_baostock_kline(bs_code, "2").with_columns(pl.lit(project_sym).alias("symbol"))

        print(f"===== {project_sym} ({bs_code}) =====")
        print(f"除权事件数: {factors.height}, 最早 {factors['dividOperateDate'][0]} → 最晚 {factors['dividOperateDate'][-1]}")

        # 构建 ex_factor: fore_k / fore_{k-1}, fore_0 = 1.0
        fores = factors["foreAdjustFactor"].to_list()
        exs = [fores[0] / 1.0] + [fores[i] / fores[i - 1] for i in range(1, len(fores))]
        fac = factors.with_columns(
            pl.Series("ex_factor", exs),
            pl.lit(project_sym).alias("symbol"),
        ).select(pl.col("dividOperateDate").alias("trade_date"), "symbol", "ex_factor")

        print("最近 4 个事件的 ex_factor (pre/post):")
        for r in fac.tail(4).to_dicts():
            print(f"  {r['trade_date']}  ex={r['ex_factor']:.6f}")

        adjusted = _apply_adj_factor(raw, fac).select(["date", "close"]).rename({"close": "adj_close"})
        merged = qfq.select(["date", "close"]).rename({"close": "bs_qfq"}).join(adjusted, on="date")
        merged = merged.with_columns((pl.col("adj_close") - pl.col("bs_qfq")).abs().alias("diff"))
        rel = (merged["diff"] / merged["bs_qfq"]).mean()
        print(f"  vs baostock qfq: {merged.height} 天, 平均|价差|/价 = {rel:.7f}, max = {merged['diff'].max():.5f}")
        ok = merged["diff"].max() < 0.01
        print(f"  {'✓ 一致' if ok else '✗ 不一致'} (max差 {'< 0.01' if ok else merged['diff'].max()})")
        if not ok:
            for w in merged.sort("diff", descending=True).head(3).to_dicts():
                print(f"      {w['date']}: adj={w['adj_close']:.3f} vs qfq={w['bs_qfq']:.3f} diff={w['diff']:.4f}")

        # akshare 独立交叉验证 (所有股票都验, 但要节流)
        try:
            ak = akshare_qfq(project_sym.split(".")[0])
            m2 = ak.join(adjusted, on="date").with_columns((pl.col("adj_close") - pl.col("close")).abs().alias("diff"))
            rel2 = (m2["diff"] / m2["close"]).mean()
            print(f"  vs akshare qfq: {m2.height} 天, 平均|价差|/价 = {rel2:.7f}, max = {m2['diff'].max():.5f}")
        except Exception as e:  # noqa: BLE001
            print(f"  akshare 交叉验证失败: {e}")
        print()

    bs.logout()


if __name__ == "__main__":
    main()
