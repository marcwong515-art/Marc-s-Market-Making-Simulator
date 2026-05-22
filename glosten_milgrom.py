"""
Glosten-Milgrom Inspired Market-Making Strategy.

Inspiration
-----------
Glosten, L.R. & Milgrom, P.R. (1985). "Bid, Ask and Transaction Prices in a
Specialist Market with Heterogeneously Informed Traders." Journal of Financial
Economics, 14(1), 71-100.

Core insight: the bid-ask spread compensates the market maker for adverse
selection from informed traders.  When recent order flow is imbalanced
(mostly buys or mostly sells), the probability of facing an informed trader
rises, so the maker should widen her spread and shift her quotes in the
direction of the flow.

Implementation
--------------
We maintain a rolling signed order-flow imbalance (OFI):

    OFI = (buy_volume - sell_volume) / (buy_volume + sell_volume + ε)
         ∈ [-1, 1]

The half-spread is then widened proportionally:

    half_spread = base_spread * (1 + λ · |OFI|)

where λ (lambda_factor) controls the sensitivity to informed flow.

Additionally, the mid is skewed in the direction of the imbalance signal:

    skewed_mid = mid + θ · OFI · base_spread

where θ (skew_factor) shifts quotes to reduce adverse selection.

Parameters
----------
base_half_spread : Baseline half-spread in ticks (when OFI = 0).
lambda_factor    : Spread widening multiplier on OFI magnitude.
skew_factor      : Quote-skew strength on OFI direction.
window           : Number of recent trades to compute OFI over.
order_size       : Fixed order quantity.
max_inventory    : Hard inventory limit.
"""

from __future__ import annotations
from collections import deque
from sim.backtester import Strategy, StrategyOrder, BookSnapshot, Fill
import lob_engine as lob


class GlostenMilgrom(Strategy):
    """
    Flow-imbalance-aware market maker inspired by Glosten-Milgrom (1985).

    Widens the spread and skews quotes when recent order flow suggests
    informed trading activity.
    """

    def __init__(
        self,
        base_half_spread: int = 3,
        lambda_factor: float = 2.0,
        skew_factor: float = 0.5,
        window: int = 50,
        order_size: int = 5,
        max_inventory: int = 50,
    ) -> None:
        self.base_half_spread = base_half_spread
        self.lambda_factor    = lambda_factor
        self.skew_factor      = skew_factor
        self.window           = window
        self.size             = order_size
        self.max_inventory    = max_inventory

        # Rolling window of signed trade volumes (+qty for buy, -qty for sell).
        self._flow: deque[int]  = deque(maxlen=window)
        self._inventory: int    = 0

    def reset(self) -> None:
        self._flow.clear()
        self._inventory = 0

    def on_trade(self, trade: lob.Trade) -> list[StrategyOrder]:
        """Record every market trade (not just our own) to update OFI."""
        signed = trade.qty if trade.aggressor_side == lob.Side.Buy else -trade.qty
        self._flow.append(signed)
        return []

    def on_book_update(self, book: BookSnapshot) -> list[StrategyOrder]:
        if book.mid is None:
            return []

        ofi = self._compute_ofi()

        # Widen spread proportionally to |OFI|.
        half_spread = self.base_half_spread * (1.0 + self.lambda_factor * abs(ofi))
        half_spread = max(1, round(half_spread))

        # Skew mid toward the direction of flow (to reduce adverse selection).
        skewed_mid = book.mid + self.skew_factor * ofi * self.base_half_spread

        bid_price = round(skewed_mid - half_spread)
        ask_price = round(skewed_mid + half_spread)

        if bid_price >= ask_price:
            bid_price = ask_price - 1

        orders: list[StrategyOrder] = []

        if self._inventory <= self.max_inventory and bid_price > 0:
            orders.append(StrategyOrder(side="buy",  price=bid_price, qty=self.size))
        if self._inventory >= -self.max_inventory:
            orders.append(StrategyOrder(side="sell", price=ask_price, qty=self.size))

        return orders

    def on_fill(self, fill: Fill) -> list[StrategyOrder]:
        delta = fill.qty if fill.side == "buy" else -fill.qty
        self._inventory += delta
        return []

    # ── Internal ──────────────────────────────────────────────────────────────

    def _compute_ofi(self) -> float:
        """
        Order Flow Imbalance ∈ [-1, 1].

        Positive → net buy pressure (possible informed buying).
        Negative → net sell pressure (possible informed selling).
        """
        if not self._flow:
            return 0.0
        total_buy  = sum(v for v in self._flow if v > 0)
        total_sell = sum(-v for v in self._flow if v < 0)
        denom = total_buy + total_sell
        if denom == 0:
            return 0.0
        return (total_buy - total_sell) / denom
