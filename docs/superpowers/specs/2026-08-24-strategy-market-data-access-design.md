# 策略指数K线访问能力 设计

日期: 2026-08-24
状态: 待评审
分支: feat/strategy-mind-rebrand

## 1. 背景与目标

策略(Custom/AI 策略文件)目前被沙箱隔离,`filter_history(df, params)` 只能拿到「个股历史窗口 + 用户参数平铺 dict」,且 import 白名单仅 `polars / numpy / app.backtest.matrix / datetime`。因此**任何策略都无法读取指数K线**,无法做「上证指数 MACD 死叉」这类市场级过滤。

本特性目标:**让所有策略都能通过 import 一个小工具模块,任意读取指数(及 ETF)的完整日K(含技术指标),且不影响任何现有策略。**

## 2. 范围

### 2.1 支持的数据
- **A股指数**全部(走 `kline_index_daily`,如 000001.SH / 399001.SZ / 创业板 / 科创等)。
- **ETF**(走 `kline_etf_enriched` 独立通道)。
- 通用入口按 `repo.resolve_asset_type(symbol)` 自动分派,顺带覆盖**股票**(同一 repo 调用,成本为零)。

### 2.2 周期与区间
- 仅**日K(1d)完整历史**。
- 可传 `start` / `end` 裁区间(`date` 或 ISO 字符串)。
- `columns` 支持**列下推**;不传则返回含框架算好的技术指标(`macd_dif/dea/hist`、`ma5/10/20/...`、`rsi` 等,即 `get_index_daily` 的 enriched 输出,由 `_compute_index_enriched_range` 计算)。
- **分钟级不在本期**。

### 2.3 执行路径
全部路径生效(回测 / 盘后选股 / 实时监控)——依赖「白名单可 import」,天然全路径可用,无需在策略调用点注入。

## 3. 设计

### 3.1 新模块 `backend/app/strategy/market_data.py`
纯读函数,全部返回 polars DataFrame,策略侧零状态持有:

```python
from app.strategy.market_data import (
    get_index_daily,
    get_etf_daily,
    get_daily,          # resolve_asset_type 自动分派
    list_index_symbols, # 可选辅助: 列出可用指数 (见 §3.5)
)

# 常用
get_index_daily("000001.SH")                          # 完整历史+指标
get_index_daily("000001.SH", start="2025-01-01", end="2025-06-30", columns=["date","close","macd_dif","macd_dea"])
get_daily("000001.SH")                                # 自动分派到指数
```

函数签名统一:

```python
def get_index_daily(symbol: str, start=None, end=None, columns=None) -> pl.DataFrame
def get_etf_daily(symbol: str, start=None, end=None, columns=None) -> pl.DataFrame
def get_daily(symbol: str, start=None, end=None, columns=None) -> pl.DataFrame
```

### 3.2 repo 定位(模块自足、线程安全、懒加载)
```python
_repo = None
_lock = threading.Lock()

def _get_repo():
    global _repo
    if _repo is None:
        with _lock:
            if _repo is None:
                from app.tickflow.repository import DataStore, KlineRepository
                _repo = KlineRepository(DataStore())  # DataStore() 默认 settings.data_dir, 与 main.py:47 同源
    return _repo
```
- **懒加载**:策略首次调用才构建,启动零开销。
- **同源**:`DataStore()` 默认取 `settings.data_dir`(repository.py:54),与 `main.py:47` 创建方式一致 → 读同一份 parquet。
- **线程安全**:双重检查锁。

### 3.3 白名单放行(改 `backend/app/strategy/ai_generator.py`)
```python
_ALLOWED_IMPORT_MODULES = frozenset({
    "polars", "numpy", "app.backtest.matrix", "datetime", "__future__",
    "app.strategy.market_data",   # 新增
})
```
策略即可 `from app.strategy.market_data import get_index_daily`。

### 3.4 安全与错误处理
- 模块属**框架侧信任代码**,仅暴露只读、纯函数 API;对策略的 `open/eval/exec/getattr/getattr`、dunder 逃逸拦截**照旧生效**(`ai_generator._validate_safety` 不动)。
- **不**向策略暴露任何「选数据目录 / 任意读文件」能力——symbol 仅作为 repo 查询参数,由 repo 内部过滤。
- 指数缺失 / 未知 symbol → **返回空 DataFrame**(不抛),与 `repo.get_index_daily` 行为一致;策略自行判断空表。模块内部仅 log warning。
- symbol 轻校验:必须 `str`,空串视为无数据。

### 3.5 可选辅助
`list_index_symbols()`:从 `kline_index_daily` 枚举可用指数 symbol + name,返回 `list[dict]`。供策略发现/遍历指数用。若实现成本过高,可降级为从 `repo.get_instruments_asset("index")` 读取。

## 4. 测试(TDD)

新增 `backend/tests/test_strategy_market_data.py`:
1. **白名单回归**:`_validate_safety` 接受 `from app.strategy.market_data import get_index_daily`(不抛);同时确认对 `import os`/`getattr` 仍拒绝。
2. **注入 fake repo**:`market_data._set_repo(fake)` → `get_index_daily` 返回预期列与区间;`get_daily` 按 asset_type 分派到指数。
3. **空数据 / 未知 symbol** → 返回空 df、不抛。
4. **懒加载**:首次调用才触发 `_get_repo`(可 mock 计数)。

## 5. 系统代码改动清单
| 文件 | 改动 |
|------|------|
| `backend/app/strategy/market_data.py` | 新增模块 |
| `backend/app/strategy/ai_generator.py` | 白名单 +1 行 |
| `backend/tests/test_strategy_market_data.py` | 新增测试 |

对现有策略**零影响**(纯新增模块 + 白名单放行;不动 `filter_history` 签名、不动 `StrategyDataContext`、不动面板构建)。

## 6. 不在本期范围
- 策略内「上证指数近N日新死叉」**使用方逻辑**(是后续任务,基于本模块实现)。
- 分钟级/多周期指数K线。
- 策略写回/订阅类能力(仍禁止)。
- 把 app 运行时 repo 经 contextvar 注入模块(作为后续优化,reuse 同实例;v1 用 DataStore() 自建,数据同源)。

## 7. 关键接口引用
- `repo.get_index_daily(symbol, start, end, columns)` → `backend/app/tickflow/repository.py:1391`,内部 `_compute_index_enriched_range`(:1606)算指标。
- `repo.resolve_asset_type(symbol)` → `:1284`。
- `repo.get_etf_daily` → `:1419`。
- `KlineRepository(DataStore())` → `repository.py:48 / :301`。
- `_ALLOWED_IMPORT_MODULES` → `ai_generator.py:360`。
