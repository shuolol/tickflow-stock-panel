# 超跌金叉反转(ADX) — 过拟合诊断与优化意见

> 针对策略 `custom_mt0uavjm`（超跌金叉反转(ADX)）的样本量与过拟合评估。
> 生成日期：2026-08-24
> 数据来源：`data/backtest_results/custom_mt0uavjm_7fb84379c7.json`（最近一次落盘回测）

---

## 一、结论先行

- **当前形态：高风险过拟合。** 回测指标「好看」是**小样本统计噪声**，不是可验证的 alpha。
- **最大硬伤：交易笔数仅 12 笔**（2 年 482 个交易日）。
- **关键死穴：无法用项目自带的「步进优化(walk-forward)」做样本外验证**——该策略是 `python_history_legacy` 后端，而 walk-forward 仅支持 `matrix_native`（`backend/app/backtest/walkforward.py:156` 直接报错）。
- **优化方向：放宽压制信号量的硬条件、提高交易笔数到 ≥50（理想 100+），并减少自由度。**
- ⚠️ 前提认知：**放宽后回测数字会「变难看」——这是策略从「运气」走向「可信」的表现，不是退步。**

---

## 二、当前策略的完整触发条件

### 基础过滤（basic_filter）
- 价格 3~200 元；市值 ≥ 10 亿；成交额 ≥ 0.5 亿；剔除 ST；剔除上市 ≤ 60 天。

### 入场信号（硬编码于 `data/user_data/custom_signals/oversold_macd_about_to_golden.json`）
| # | 条件 | 含义 |
|---|------|------|
| 1 | `momentum_60d ≤ -0.30` | 60 日累计跌幅 ≥ 30% |
| 2 | `macd_dif < macd_dea` | MACD 未金叉（DIF 在 DEA 下方） |
| 3 | `macd_dif > macd_dif(3日前)` | DIF 在回升，逼近金叉 |
| 4 | `momentum_5d ≥ +0.05` | 5 日涨幅 ≥ 5% |

### filter_history（`data/strategies/custom/custom_mt0uavjm.py`）
- ADX(14) ∈ [25, 40]（近似「ADX≈30」）；
- 近 `limit_up_lookback_days` 日内 `consecutive_limit_ups ≥ 2`（连板基因）。

### 退场
- 趋势死叉 `csg_macd_dead_trend`（7 条件，专门过滤零轴上方的回调死叉），或
- 持有满 `MAX_HOLD_DAYS = 5` 天；止损 `STOP_LOSS = -0.07`。

> 全叠加约 **14~16 个**相互咬合的约束，其中仅 ADX 三参数可在 UI 调整，其余为手工定死的固定阈值。

---

## 三、回测实证数据（12 笔）

区间：2024-08-23 ~ 2026-08-20（约 2 年）· 模式：position

| 指标 | 数值 | 备注 |
|------|------|------|
| **交易数 n_trades** | **12** | 11 胜 / 1 负 |
| 胜率 | **91.67%** | 12 样本下的高胜率 = 噪声 |
| 盈亏比 | **6.9** | 高度依赖单笔大赚 |
| 累计收益 | +13.11% | |
| 夏普 | 1.12 | |
| 最大回撤 | -5.32% | 97% 时间空仓所致，非风控强 |
| 单笔最佳 | +27.14% | 大概率是收益的主要来源 |
| 单笔最差 | -1.76% | |
| 净值曲线点数 | 482 | 但只有 12 笔交易 |

### 为什么这是过拟合的典型长相
- **12 笔 → 极高方差**：91.67% 胜率的标准误约 ±15 个百分点，真实胜率大概率落在 70%~90%+ 的宽区间甚至更低。
- **盈亏比 6.9 由单笔驱动**：剔除 +27.14% 那笔，整条曲线大概率明显塌陷。
- **低回撤是「没仓位」**：12 笔分布在 482 个交易日里，其余时间完全空仓。
- **结论**：十几个手调阈值 + 12 次抽样，共同「拟合」出了当前这张好看的曲线。

---

## 四、提高交易笔数的杠杆（按边际影响排序）

> 信号量由这些条件**串联压制**，放宽任何一个都会增加候选。**影响越大、越好用，但代价也越大。**

| 排序 | 条件 | 现值 | 位置 / 是否UI可调 | 放宽建议 |
|------|------|------|-----------------|---------|
| 🥇 1 | **连板基因 `min_consecutive_limit_ups ≥ 2`** | 近 60 日需 2 连板 | `custom_mt0uavjm.py` `filter_history`，**代码硬编码** | 改成 ≥1；放大最狠 |
| 🥈 2 | **ADX 窄带 [25, 40]** | ADX(14) ∈ [25,40] | `META.params`，**UI 可调** | 放宽到 [15, 50] |
| 🥉 3 | **60日跌幅 ≥ 30%** | `momentum_60d ≤ -0.30` | 信号 JSON，**UI 不可调** | 放宽到 -0.25 / -0.20 |
| 4 | **5日涨幅 ≥ 5%** | `momentum_5d ≥ 0.05` | 信号 JSON | 降到 +3% / +2% |
| 5 | 退场/止损 | MAX_HOLD_DAYS=5, STOP_LOSS=-0.07 | `META` | 只影响资金回收速度，对入场笔数是**二阶效应** |

> ⚠️ **附带发现的一个 bug**：`custom_mt0uavjm.py:185` 连板回看窗口实际为 `limit_up_lookback_days = 60`（60 日），但 RULES 文案与你的初衷是「近 200 日」。60 日窗口更紧，也在偷偷压制信号量。

### 建议的落盘改动（把硬编码抬进 `META.params` 做成 UI 可调）

在 `META["params"]` 追加：

```python
{
    "id": "min_limit_ups",
    "label": "连板基因(需≥N连板, 0=关闭)",
    "type": "int",
    "default": 2,
    "min": 0,
    "max": 3,
    "step": 1,
},
{
    "id": "limit_up_lookback_days",
    "label": "连板回看窗口(天)",
    "type": "int",
    "default": 200,
    "min": 60,
    "max": 250,
    "step": 10,
},
```

并在 `filter_history` 改为从 `params` 读取，而不是写死：

```python
min_limit_ups = int(params.get("min_limit_ups", 2))
limit_up_lookback_days = int(params.get("limit_up_lookback_days", 200))
```

---

## 五、建议行动（低风险 → 高影响）

1. **先动连板基因**：`min_consecutive_limit_ups` 2 → 1（最大杠杆），并把它和回看窗口抬进 UI 参数。
2. **放宽 ADX**：`adx_min 25→15`、`adx_max 40→50`（UI 现成，最快能试）。
3. **再放宽跌幅**：信号 JSON 里 `momentum_60d ≤ -0.30 → -0.25`。
4. **一次只放开一个**，每次回测后观察 `n_trades` 与胜率/盈亏比的变化：
   - 目标：`n_trades ≥ 50`（理想 100+）。
5. **最终目标不是「更好看」，而是「更可信」**：若放开后收益变平庸、胜率回落到 50%~70%，说明之前确实是少数幸运交易撑起来的——这正是走向稳健的信号。

---

## 六、红线提醒

- **放宽参数 = 改变策略身份**：一旦放宽，它就不再是「超跌 + ADX≈30 + 连板基因」，而是另一个策略，**需从头重新验证**。
- **治本要减少自由度**：与其逐个让步放宽，不如砍掉臃肿条件（例如退场为单挑一种失败模式而堆的 7 个条件）。阈值越多、越精确，越像在拟合历史样本。
- **样本外是唯一金标准**：在该策略能跑 `matrix_native` 之前，这个 +13.11% 的收益数字只当参考，**不要据此真金白银下注**。

---

## 七、附：关键文件

| 类型 | 路径 |
|------|------|
| 策略定义 | `data/strategies/custom/custom_mt0uavjm.py` |
| 入场信号 | `data/user_data/custom_signals/oversold_macd_about_to_golden.json` |
| 退场信号 | `data/user_data/custom_signals/csg_macd_dead_trend.json` |
| 回测结果存档 | `data/backtest_results/custom_mt0uavjm_7fb84379c7.json` |
| 落盘逻辑 | `backend/app/api/backtest.py`（`_persist_result_to_disk` / `list_results` / `get_result`） |
