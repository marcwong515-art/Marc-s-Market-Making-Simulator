"""
Naive Symmetric Market Maker.

Quotes a bid and ask symmetrically around the current mid price at a fixed
half-spread of k ticks, with a fixed order size.

On each book update:
  - Cancel all outstanding quotes.
  - If a valid mid exists, post bid at (mid - k) and ask at (mid + k).

This is intentionally the simplest possible market-making strategy — a baseline
to compare the more sophisticated approaches against.
"""

from __future__ import annotations
from sim.backtester import Strategy, StrategyOrder, BookSnapshot, Fill


class NaiveMarketMaker(Strategy):
    """
    Parameters
    ----------
    half_spread_ticks : Half the quoted spread in integer ticks (k).
    order_size        : Fixed quantity for each side.
    max_inventory     : If |inventory| exceeds this, suppress the quote that
                        would further worsen it.
    """

    def __init__(
        self,
        half_spread_ticks: int = 3,
        order_size: int = 5,
        max_inventory: int = 50,
    ) -> None:
        self.k              = half_spread_ticks
        self.size           = order_size
        self.max_inventory  = max_inventory
        self._inventory: int = 0

    def reset(self) -> None:
        self._inventory = 0

    def on_book_update(self, book: BookSnapshot) -> list[StrategyOrder]:
        if book.mid is None:
            return []

        mid = round(book.mid)
        orders: list[StrategyOrder] = []

        # Suppress the side that would worsen inventory beyond the limit.
        if self._inventory < self.max_inventory:
            orders.append(StrategyOrder(side="buy",  price=mid - self.k, qty=self.size))
        if self._inventory > -self.max_inventory:
            orders.append(StrategyOrder(side="sell", price=mid + self.k, qty=self.size))

        return orders

    def on_fill(self, fill: Fill) -> list[StrategyOrder]:
        delta = fill.qty if fill.side == "buy" else -fill.qty
        self._inventory += delta
        return []
