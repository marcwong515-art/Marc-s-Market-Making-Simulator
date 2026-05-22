"""
Avellaneda-Stoikov (2008) Market-Making Strategy.

Reference
---------
Avellaneda, M. & Stoikov, S. (2008). "High-frequency trading in a limit order book."
Quantitative Finance, 8(3), 217-224.

Mathematical derivation
-----------------------
The model assumes a single risky asset with mid-price S(t) following Brownian
motion:  dS = σ dW

The market maker has inventory q (positive = long) and maximises expected
utility of terminal wealth with CARA utility (risk aversion γ > 0).

Reservation price (indifference price):
    r(S, q, t) = S - q · γ · σ² · (T - t)

Interpretation: the maker shifts her mid down when long (q > 0) because she
prices in the cost of unwinding; and up when short.

Optimal half-spread:
    δ* = (γ · σ² · (T - t)) / 2  +  (1/γ) · ln(1 + γ/k)

where k is the order-book depth parameter (controls how quickly demand falls
off with distance from mid; higher k → less elastic — maker quotes wider).

The maker quotes:
    bid = r - δ*
    ask = r + δ*

Parameters
----------
gamma   : Risk-aversion coefficient (γ). Typical range: 0.01 – 1.0.
          Higher γ → reservation price moves more per unit of inventory,
          spreads widen faster as T-t shrinks.
sigma   : Annualised volatility of the mid price (in tick units / sqrt(year)).
          Estimated or set from historical data.
k       : Order-book depth / elasticity. Typical range: 0.5 – 5.0.
          Lower k → demand is less sensitive to spread → maker can quote wider.
T       : Time horizon in seconds. The strategy becomes more aggressive
          (smaller spread, stronger inventory push) as t → T.
order_size      : Fixed order quantity.
max_inventory   : Hard inventory limit; suppress one side beyond this.
min_half_spread : Floor on the quoted half-spread in ticks (never go below 1).
"""

from __future__ import annotations
import math
import time
from sim.backtester import Strategy, StrategyOrder, BookSnapshot, Fill


class AvellanedaStoikov(Strategy):
    """
    Avellaneda-Stoikov optimal market-making strategy.

    See module docstring for full derivation.
    """

    def __init__(
        self,
        gamma: float = 0.1,
        sigma: float = 2.0,
        k: float = 1.5,
        T: float = 60.0,
        order_size: int = 5,
        max_inventory: int = 50,
        min_half_spread: int = 1,
    ) -> None:
        # ── Model parameters ──────────────────────────────────────────────────
        self.gamma   = gamma   # risk aversion
        self.sigma   = sigma   # volatility (ticks / sqrt-second; rescaled below)
        self.k       = k       # order-book depth parameter
        self.T       = T       # time horizon (seconds)

        # ── Execution parameters ──────────────────────────────────────────────
        self.size            = order_size
        self.max_inventory   = max_inventory
        self.min_half_spread = min_half_spread

        # ── Run-time state ────────────────────────────────────────────────────
        self._inventory: int    = 0
        self._start_us: int     = 0    # set on first book update

    def reset(self) -> None:
        self._inventory = 0
        self._start_us  = 0

    def on_book_update(self, book: BookSnapshot) -> list[StrategyOrder]:
        if book.mid is None:
            return []

        # Initialise clock on first call.
        if self._start_us == 0:
            self._start_us = book.ts_us

        # Elapsed time in seconds.
        elapsed_s = max(0.0, (book.ts_us - self._start_us) / 1e6)
        tau = max(1e-6, self.T - elapsed_s)   # remaining time (seconds)

        S   = book.mid           # current mid in ticks
        q   = self._inventory
        g   = self.gamma
        sig = self.sigma          # in ticks / sqrt-second
        kk  = self.k

        # ── Reservation price ─────────────────────────────────────────────────
        # r = S - q · γ · σ² · τ
        reservation = S - q * g * sig**2 * tau

        # ── Optimal half-spread ───────────────────────────────────────────────
        # δ* = (γ · σ² · τ) / 2  +  (1/γ) · ln(1 + γ/k)
        half_spread = (g * sig**2 * tau) / 2.0 + (1.0 / g) * math.log(1.0 + g / kk)
        half_spread = max(self.min_half_spread, round(half_spread))

        bid_price = round(reservation - half_spread)
        ask_price = round(reservation + half_spread)

        # Ensure bid < ask (can collapse if tau → 0).
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
