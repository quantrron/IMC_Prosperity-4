"""
Minimal datamodel stub for local testing.
The real datamodel is injected by the Prosperity platform at runtime.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
import json


Symbol = str
Product = str


@dataclass
class Order:
    symbol: Symbol
    price: int
    quantity: int

    def __repr__(self):
        side = "BUY" if self.quantity > 0 else "SELL"
        return f"Order({self.symbol} {side} {abs(self.quantity)}@{self.price})"


@dataclass
class OrderDepth:
    buy_orders: dict[int, int] = field(default_factory=dict)   # price -> volume (positive)
    sell_orders: dict[int, int] = field(default_factory=dict)  # price -> volume (negative)


@dataclass
class Trade:
    symbol: Symbol
    price: int
    quantity: int
    buyer: str = ""
    seller: str = ""
    timestamp: int = 0


@dataclass
class Listing:
    symbol: Symbol
    product: Product
    denomination: str


class Observation:
    def __init__(self, plain_value_observations=None, conversion_observations=None):
        self.plainValueObservations = plain_value_observations or {}
        self.conversionObservations = conversion_observations or {}


class TradingState:
    def __init__(
        self,
        traderData: str = "",
        timestamp: int = 0,
        listings: dict[Symbol, Listing] | None = None,
        order_depths: dict[Symbol, OrderDepth] | None = None,
        own_trades: dict[Symbol, list[Trade]] | None = None,
        market_trades: dict[Symbol, list[Trade]] | None = None,
        position: dict[Product, int] | None = None,
        observations: Observation | None = None,
    ):
        self.traderData = traderData
        self.timestamp = timestamp
        self.listings = listings or {}
        self.order_depths = order_depths or {}
        self.own_trades = own_trades or {}
        self.market_trades = market_trades or {}
        self.position = position or {}
        self.observations = observations or Observation()


class ProsperityEncoder(json.JSONEncoder):
    def default(self, obj):
        if hasattr(obj, "__dict__"):
            return obj.__dict__
        return super().default(obj)
