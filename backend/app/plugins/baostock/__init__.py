"""baostock 内置数据源插件(免费 A 股除权因子, 无 TickFlow Starter+ 时的平替)。"""
from app.plugins.baostock.provider import BaostockProvider, availability

PROVIDER_NAME = "baostock"

__all__ = [
    "PROVIDER_NAME",
    "BaostockProvider",
    "availability",
]
