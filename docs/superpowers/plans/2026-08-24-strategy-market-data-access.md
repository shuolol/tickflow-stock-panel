# 策略指数K线访问能力 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让所有 Custom/AI 策略都能通过白名单 import 一个只读工具模块,读取任意指数(及 ETF)的完整日K(含技术指标)。

**Architecture:** 新增 `app/strategy/market_data.py`,线程安全懒加载一个 `KlineRepository(DataStore())`(数据源与 `main.py` 同源 `settings.data_dir`),暴露 `get_index_daily` / `get_etf_daily` / `get_daily` / `list_index_symbols` 纯读函数。把 `app.strategy.market_data` 加入策略 import 白名单。对现有策略零影响(纯新增 + 白名单放行)。

**Tech Stack:** Python, Polars, FastAPI 后端, pytest。

**Spec:** `docs/superpowers/specs/2026-08-24-strategy-market-data-access-design.md`

## Global Constraints

- import 白名单新增: `app.strategy.market_data`(唯一系统放行项)。
- 策略沙箱对 `open/eval/exec/getattr/__import__`、dunder 逃逸的拦截**不得放宽**(不因本特性削弱)。
- 所有公开函数返回 `pl.DataFrame` 或 `list[dict]`;数据缺失 / 未知 symbol → 返回空 DataFrame(不抛异常)。
- 仅日K(1d);`start`/`end` 接受 `date` 或 ISO 字符串;`columns` 支持列下推。
- 全执行路径生效(回测 / 盘后选股 / 实时监控),不依赖策略调用点注入。
- 测试运行: `cd backend && pytest tests/<file>.py -v`。

---

### Task 1: 白名单放行 `app.strategy.market_data`

**Files:**
- Modify: `backend/app/strategy/ai_generator.py:360-366`(`_ALLOWED_IMPORT_MODULES`)
- Test: `backend/tests/test_strategy_market_data.py`

**Interfaces:**
- Produces: 策略可 `from app.strategy.market_data import get_index_daily`(白名单层面)。

- [ ] **Step 1: Write the failing test**

创建 `backend/tests/test_strategy_market_data.py`(首批只放白名单用例):

```python
"""策略指数K线访问模块 — 测试。"""
import pytest

from app.strategy.ai_generator import AIStrategyGenerator


def test_whitelist_allows_market_data_import():
    AIStrategyGenerator._validate_safety(
        "from app.strategy.market_data import get_index_daily, get_daily"
    )


def test_whitelist_still_blocks_dangerous():
    with pytest.raises(ValueError):
        AIStrategyGenerator._validate_safety("import os")
    with pytest.raises(ValueError):
        AIStrategyGenerator._validate_safety("from os import path")
    with pytest.raises(ValueError):
        AIStrategyGenerator._validate_safety("getattr(obj, '__globals__')")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_strategy_market_data.py -v`
Expected: `test_whitelist_allows_market_data_import` FAIL(白名单无该模块);其余用例 PASS。

- [ ] **Step 3: Add the module to the whitelist**

在 `backend/app/strategy/ai_generator.py` 修改:

```python
_ALLOWED_IMPORT_MODULES = frozenset({
    "polars",
    "numpy",
    "app.backtest.matrix",
    "datetime",
    "__future__",
    "app.strategy.market_data",   # 新增: 策略可读取指数/ETF 日K
})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_strategy_market_data.py -v`
Expected: 3 个用例全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/app/strategy/ai_generator.py backend/tests/test_strategy_market_data.py
git commit -m "feat(strategy): 白名单放行策略指数K线访问模块 market_data"
```

---

### Task 2: 实现 `market_data` 模块

**Files:**
- Create: `backend/app/strategy/market_data.py`
- Test: `backend/tests/test_strategy_market_data.py`(追加委托/分派/异常用例)

**Interfaces:**
- Consumes(from repo): `KlineRepository(DataStore())`;`repo.get_index_daily(symbol, start, end, columns)`;`repo.get_etf_daily(...)`;`repo.get_daily(...)`;`repo.resolve_asset_type(symbol)`;`repo.get_instruments_asset("index")`。
- Produces(供下游策略): `get_index_daily(symbol, start=None, end=None, columns=None) -> pl.DataFrame`;`get_etf_daily(...)`;`get_daily(...)`;`list_index_symbols() -> list[dict]`;测试缝 `_set_repo(repo)` / `_reset_repo()`。

- [ ] **Step 1: Write the failing test(追加到测试文件)**

```python
import datetime

import polars as pl

from app.strategy import market_data


class _FakeRepo:
    """最小 fake: 只实现 market_data 用到的接口。"""
    def __init__(self, index_df=None):
        self.calls: list[tuple] = []
        self._asset = {"000001.SH": "index", "510300.SH": "etf", "600000.SH": "stock"}
        self._index_df = index_df if index_df is not None else pl.DataFrame(
            {"date": ["2026-01-02"], "close": [3000.0], "macd_dif": [1.0], "macd_dea": [2.0]}
        )
        self._empty = pl.DataFrame()

    def resolve_asset_type(self, symbol):
        self.calls.append(("resolve", symbol))
        return self._asset.get(symbol, "stock")

    def get_index_daily(self, symbol, start=None, end=None, columns=None):
        self.calls.append(("index", symbol, start, end, columns))
        return self._index_df if symbol == "000001.SH" else self._empty

    def get_etf_daily(self, symbol, start=None, end=None, columns=None):
        self.calls.append(("etf", symbol, start, end, columns))
        return self._empty

    def get_daily(self, symbol, start=None, end=None, columns=None):
        self.calls.append(("stock", symbol, start, end, columns))
        return self._empty

    def get_instruments_asset(self, asset_type):
        return pl.DataFrame({"symbol": ["000001.SH"], "name": ["上证指数"]})


@pytest.fixture()
def fake_repo():
    fake = _FakeRepo()
    market_data._set_repo(fake)
    yield fake
    market_data._reset_repo()


def test_get_index_daily_delegates_and_normalizes_dates(fake_repo):
    df = market_data.get_index_daily(
        "000001.SH", start="2026-01-01", end="2026-01-31", columns=["date", "close"]
    )
    assert df.height == 1 and df["close"][0] == 3000.0
    _, sym, s, e, cols = fake_repo.calls[-1]
    assert sym == "000001.SH"
    assert s == datetime.date(2026, 1, 1)
    assert e == datetime.date(2026, 1, 31)
    assert cols == ["date", "close"]


@pytest.mark.parametrize("symbol,expected_kind", [
    ("000001.SH", "index"),
    ("510300.SH", "etf"),
    ("600000.SH", "stock"),
])
def test_get_daily_dispatch_by_asset_type(fake_repo, symbol, expected_kind):
    market_data.get_daily(symbol)
    last = fake_repo.calls[-1]
    assert last[0] == expected_kind
    assert last[1] == symbol


def test_bad_symbol_returns_empty_without_calling_repo(fake_repo):
    assert market_data.get_index_daily("").is_empty()
    assert market_data.get_index_daily(None).is_empty()
    assert market_data.get_etf_daily("").is_empty()
    assert market_data.get_daily(None).is_empty()
    assert fake_repo.calls == []


def test_missing_symbol_returns_empty_no_raise(fake_repo):
    assert market_data.get_index_daily("999999.SH").is_empty()


def test_list_index_symbols(fake_repo):
    assert market_data.list_index_symbols() == [{"symbol": "000001.SH", "name": "上证指数"}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_strategy_market_data.py -v`
Expected: 新增用例 FAIL(`ModuleNotFoundError: app.strategy.market_data`)。

- [ ] **Step 3: Implement the module**

创建 `backend/app/strategy/market_data.py`:

```python
"""策略可访问的指数/ETF 日K读取模块 — 白名单放行的只读数据入口。

供 Custom/AI 策略在 filter_history 内读取任意指数(及 ETF)的完整日K。
策略通过白名单 import 本模块, 调用纯读函数; 禁止写操作或任意文件访问。

设计要点:
  - 模块自身是框架侧信任代码, 对策略的沙箱逃逸拦截(ai_generator._validate_safety)照旧生效。
  - repo 线程安全懒加载(首次调用才构建); DataStore() 默认 settings.data_dir, 与 main.py 同源。
  - 未知 symbol / 数据缺失 → 返回空 DataFrame(不抛), 与 repo 语义一致。
"""
from __future__ import annotations

import logging
import threading
from datetime import date
from typing import Any

import polars as pl

logger = logging.getLogger(__name__)

# 完整历史默认区间下界(A股数据远晚于此, 仅作"全量"占位)。
_FULL_START = date(1990, 1, 1)

# ── repo 懒加载(线程安全) ─────────────────────────────
_repo = None
_lock = threading.Lock()


def _get_repo():
    global _repo
    if _repo is None:
        with _lock:
            if _repo is None:
                from app.tickflow.repository import DataStore, KlineRepository
                _repo = KlineRepository(DataStore())
    return _repo


def _set_repo(repo: Any) -> None:
    """测试注入: 用 fake repo 替换单例。"""
    global _repo
    with _lock:
        _repo = repo


def _reset_repo() -> None:
    """测试清理: 重置单例, 下次调用重新懒加载。"""
    global _repo
    with _lock:
        _repo = None


# ── 参数规范化 ─────────────────────────────────────────
def _norm_date(value, default: date) -> date:
    if value is None:
        return default
    if isinstance(value, str):
        return date.fromisoformat(value)
    return value


def _validate_symbol(symbol: Any) -> bool:
    return isinstance(symbol, str) and bool(symbol.strip())


# ── 公开只读 API ───────────────────────────────────────
def get_index_daily(symbol, start=None, end=None, columns=None):
    """读取指数日K(含技术指标)。未知 symbol / 无数据返回空 DataFrame。"""
    if not _validate_symbol(symbol):
        logger.warning("market_data: 非法指数 symbol %r", symbol)
        return pl.DataFrame()
    s = _norm_date(start, _FULL_START)
    e = _norm_date(end, date.today())
    try:
        return _get_repo().get_index_daily(symbol, s, e, columns)
    except Exception as exc:  # noqa: BLE001
        logger.warning("market_data get_index_daily failed %s: %s", symbol, exc)
        return pl.DataFrame()


def get_etf_daily(symbol, start=None, end=None, columns=None):
    """读取 ETF 日K(含技术指标)。同 get_index_daily 语义。"""
    if not _validate_symbol(symbol):
        logger.warning("market_data: 非法 ETF symbol %r", symbol)
        return pl.DataFrame()
    s = _norm_date(start, _FULL_START)
    e = _norm_date(end, date.today())
    try:
        return _get_repo().get_etf_daily(symbol, s, e, columns)
    except Exception as exc:  # noqa: BLE001
        logger.warning("market_data get_etf_daily failed %s: %s", symbol, exc)
        return pl.DataFrame()


def get_daily(symbol, start=None, end=None, columns=None):
    """按资产类型自动分派读取日K: 指数 → get_index_daily; ETF → get_etf_daily; 股票 → get_daily。"""
    if not _validate_symbol(symbol):
        logger.warning("market_data: 非法 symbol %r", symbol)
        return pl.DataFrame()
    s = _norm_date(start, _FULL_START)
    e = _norm_date(end, date.today())
    repo = _get_repo()
    try:
        asset_type = repo.resolve_asset_type(symbol)
        if asset_type == "index":
            return repo.get_index_daily(symbol, s, e, columns)
        if asset_type == "etf":
            return repo.get_etf_daily(symbol, s, e, columns)
        return repo.get_daily(symbol, s, e, columns)
    except Exception as exc:  # noqa: BLE001
        logger.warning("market_data get_daily failed %s: %s", symbol, exc)
        return pl.DataFrame()


def list_index_symbols() -> list[dict]:
    """列出已收录的指数符号(含名称)。无数据返回空列表。"""
    try:
        df = _get_repo().get_instruments_asset("index")
    except Exception as exc:  # noqa: BLE001
        logger.warning("market_data list_index_symbols failed: %s", exc)
        return []
    if df.is_empty() or "symbol" not in df.columns:
        return []
    name_col = "name" if "name" in df.columns else None
    cols = ["symbol"] + ([name_col] if name_col else [])
    return [
        {"symbol": row["symbol"], "name": row.get("name")}
        for row in df.select(cols).iter_rows(named=True)
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_strategy_market_data.py -v`
Expected: 全部用例 PASS(白名单 3 个 + 模块 5 个)。

- [ ] **Step 5: Commit**

```bash
git add backend/app/strategy/market_data.py backend/tests/test_strategy_market_data.py
git commit -m "feat(strategy): 新增 market_data 指数/ETF 日K只读访问模块"
```

---

## 自检
- **Spec 覆盖**: §3.1 模块(达);§3.2 repo 懒加载(达);§3.3 白名单(达);§3.4 安全/空返回(达);§3.5 枚举(Task 2 list_index_symbols);§4 测试(白名单 + 委托 + 分派 + 空 + 枚举)。
- **占位扫描**: 无 TBD/TODO,所有步骤含完整代码。
- **类型一致性**: `get_index_daily`/`get_etf_daily`/`get_daily` 均 `(symbol, start, end, columns)`;`_set_repo`/`_reset_repo` 在 Task 2 测试与实现中同名;`list_index_symbols` 返回 `list[dict]`。
