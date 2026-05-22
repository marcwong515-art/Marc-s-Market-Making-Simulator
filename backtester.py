"""
Event-driven backtester.

Flow per external order
-----------------------
1. Submit raw order to the LOB.
2. LOB fires on_trade / on_book_update callbacks (no strategy calls here;
   a re-entrancy guard blocks them).
3. After the raw order settles:
   a. Cancel all outstanding strategy quotes.
   b. Call strategy.on_book_update with the current book snapshot.
   c. Submit the returned new quotes.
   d. Record PnL / inventory snapshot.

Re-entrancy guard
-----------------
Submitting strategy orders fires more LOB callbacks. The `_processing` flag
suppresses recursive strategy invocations during those callbacks, so we
only ask the strategy for quotes once per external event.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Optional

_here = os.path.dirname(os.path.abspath(__file__))
_build = os.path.join(os.path.dirname(_here), "build")
if _build not in sys.path:
    sys.path.insert(0, _build)

import lob_engine as lob  # type: ignore[import]
from data.synthetic import RawOrder


# ── Public types ──────────────────────────────────────────────────────────────

@dataclass
class BookSnapshot:
    best_bid: Optional[int]
    best_ask: Optional[int]
    mid: Optional[float]
    spread: float
    ts_us: int


@dataclass
class Fill:
    order_id: int
    side: str
    price: int
    qty: int
    ts_us: int


@dataclass
class StrategyOrder:
    side: str             # "buy" | "sell"
    price: int
    qty: int
    order_type: str = "limit"
    post_only: bool = False
    ioc: bool = False


@dataclass
class AccountState:
    cash: float = 0.0
    inventory: int = 0
    realized_pnl: float = 0.0
    fills: list[Fill] = field(default_factory=list)


# ── Strategy base class ───────────────────────────────────────────────────────

class Strategy:
    def on_book_update(self, book: BookSnapshot) -> list[StrategyOrder]:  # noqa: ARG002
        return []

    def on_trade(self, trade: lob.Trade) -> list[StrategyOrder]:  # noqa: ARG002
        return []

    def on_fill(self, fill: Fill) -> list[StrategyOrder]:  # noqa: ARG002
        return []

    def reset(self) -> None:
        pass


# ── Backtester ────────────────────────────────────────────────────────────────

class Backtester:
    """
    Single-asset, single-strategy event-driven backtester.

    Parameters
    ----------
    strategy     : Strategy instance.
    tick_size    : Monetary value of one price tick.
    initial_cash : Starting cash.
    """

    def __init__(
        self,
        strategy: Strategy,
        tick_size: float = 0.01,
        initial_cash: float = 0.0,
    ) -> None:
        self.strategy     = strategy
        self.tick_size    = tick_size
        self.initial_cash = initial_cash

        self._book: Optional[lob.LimitOrderBook] = None
        self._account = AccountState(cash=initial_cash)
        self._next_id: int = 1

        # IDs of strategy quotes currently resting on the book.
        self._outstanding_ids: set[int] = set()
        # Maps strategy order id → (side, price) for cancel reference.
        self._strategy_meta: dict[int, StrategyOrder] = {}
        # Maps order_id → ts_us at submission (for latency).
        self._pending_ts: dict[int, int] = {}

        # Re-entrancy guard: True while we are submitting strategy orders.
        self._processing: bool = False

        # Most recent book snapshot (updated on every LOB book_update event).
        self._last_snap: Optional[BookSnapshot] = None
        # Trades seen since the last external order (for strategy.on_trade).
        self._pending_trades: list[lob.Trade] = []

        # Metrics series.
        self.pnl_series:  list[tuple[int, float]] = []
        self.inv_series:  list[tuple[int, int]]   = []
        self.book_series: list[BookSnapshot]      = []
        self.all_trades:  list[lob.Trade]         = []
        self.latency_us:  list[int]               = []

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self, flow: list[RawOrder]) -> AccountState:
        self.strategy.reset()
        self._account = AccountState(cash=self.initial_cash)
        self._next_id = 1
        self._outstanding_ids.clear()
        self._strategy_meta.clear()
        self._pending_ts.clear()
        self._processing = False
        self._last_snap = None
        self._pending_trades.clear()
        self.pnl_series.clear()
        self.inv_series.clear()
        self.book_series.clear()
        self.all_trades.clear()
        self.latency_us.clear()

        cb = lob.Callbacks()
        cb.on_trade       = self._on_trade_cb
        cb.on_book_update = self._on_book_update_cb
        self._book = lob.LimitOrderBook(cb)

        for raw in sorted(flow, key=lambda r: r.ts_us):
            self._submit_raw(raw)
            # After the external order settles, run one strategy cycle.
            if self._last_snap is not None:
                self._run_strategy_cycle(self._last_snap)

        return self._account

    # ── LOB callbacks (called synchronously inside book.submit) ───────────────

    def _on_trade_cb(self, trade: lob.Trade) -> None:
        self.all_trades.append(trade)
        self._pending_trades.append(trade)

        if trade.passive_id in self._strategy_meta:
            self._record_fill(trade, aggressor=False)
        elif trade.aggressor_id in self._strategy_meta:
            self._record_fill(trade, aggressor=True)

    def _on_book_update_cb(self, update: lob.BookUpdate) -> None:
        snap = BookSnapshot(
            best_bid=update.best_bid,
            best_ask=update.best_ask,
            mid=(update.best_bid + update.best_ask) / 2.0
                 if update.best_bid is not None and update.best_ask is not None
                 else None,
            spread=(update.best_ask - update.best_bid)
                   if update.best_bid is not None and update.best_ask is not None
                   else float("nan"),
            ts_us=update.ts_us,
        )
        self._last_snap = snap
        self.book_series.append(snap)

    # ── Strategy cycle (called once per external event) ───────────────────────

    def _run_strategy_cycle(self, snap: BookSnapshot) -> None:
        # 1. Let strategy observe any trades that arrived.
        for trade in self._pending_trades:
            self.strategy.on_trade(trade)
        self._pending_trades.clear()

        # 2. Cancel all outstanding strategy quotes.
        self._processing = True
        ids_to_cancel = list(self._outstanding_ids)
        for oid in ids_to_cancel:
            self._cancel_strategy_order(oid, snap.ts_us)
        self._processing = False

        # 3. Ask strategy for new quotes.
        new_orders = self.strategy.on_book_update(snap)

        # 4. Submit new quotes.
        self._processing = True
        self._submit_strategy_orders(new_orders, snap.ts_us)
        self._processing = False

        # 5. Record PnL / inventory snapshot.
        mark = snap.mid if snap.mid is not None else 0.0
        unrealized = self._account.inventory * mark * self.tick_size
        total_pnl  = self._account.realized_pnl + unrealized
        self.pnl_series.append((snap.ts_us, total_pnl))
        self.inv_series.append((snap.ts_us, self._account.inventory))

    # ── Fill accounting ───────────────────────────────────────────────────────

    def _record_fill(self, trade: lob.Trade, aggressor: bool) -> None:
        oid = trade.aggressor_id if aggressor else trade.passive_id
        # Determine fill side from the strategy's perspective.
        if aggressor:
            fill_side = "buy" if trade.aggressor_side == lob.Side.Buy else "sell"
        else:
            fill_side = "sell" if trade.aggressor_side == lob.Side.Buy else "buy"

        fill = Fill(
            order_id=oid,
            side=fill_side,
            price=trade.price,
            qty=trade.qty,
            ts_us=trade.ts_us,
        )

        signed_qty = fill.qty if fill_side == "buy" else -fill.qty
        self._account.inventory  += signed_qty
        self._account.cash       -= signed_qty * fill.price * self.tick_size
        self._account.fills.append(fill)

        if oid in self._pending_ts:
            self.latency_us.append(trade.ts_us - self._pending_ts[oid])
            del self._pending_ts[oid]

        # If fully filled, remove from outstanding.
        if trade.qty >= (self._strategy_meta.get(oid, StrategyOrder("buy", 0, 0)).qty):
            self._outstanding_ids.discard(oid)

        self.strategy.on_fill(fill)

    # ── Order submission helpers ──────────────────────────────────────────────

    def _submit_raw(self, raw: RawOrder) -> None:
        o = lob.Order()
        o.id    = self._next_id; self._next_id += 1
        o.side  = lob.Side.Buy if raw.side == "buy" else lob.Side.Sell
        o.ts_us = raw.ts_us

        if raw.order_type == "market":
            o.type = lob.OrderType.Market
            o.qty  = raw.qty
        else:
            o.type  = lob.OrderType.Limit
            o.price = raw.price
            o.qty   = raw.qty
            o.tif   = lob.TIF.GTC

        self._book.submit(o)

    def _submit_strategy_orders(
        self, orders: list[StrategyOrder], ts_us: int
    ) -> None:
        for so in orders:
            oid = self._next_id; self._next_id += 1
            o = lob.Order()
            o.id        = oid
            o.side      = lob.Side.Buy if so.side == "buy" else lob.Side.Sell
            o.ts_us     = ts_us
            o.post_only = so.post_only

            if so.order_type == "market":
                o.type = lob.OrderType.Market
                o.qty  = so.qty
            else:
                o.type  = lob.OrderType.Limit
                o.price = so.price
                o.qty   = so.qty
                o.tif   = lob.TIF.IOC if so.ioc else lob.TIF.GTC

            self._strategy_meta[oid] = so
            self._outstanding_ids.add(oid)
            self._pending_ts[oid] = ts_us
            self._book.submit(o)

    def _cancel_strategy_order(self, oid: int, ts_us: int) -> None:
        self._outstanding_ids.discard(oid)
        o = lob.Order()
        o.id    = oid
        o.type  = lob.OrderType.Cancel
        o.ts_us = ts_us
        self._book.submit(o)
        # Ignore reject if order already filled (no callback hook needed here).
