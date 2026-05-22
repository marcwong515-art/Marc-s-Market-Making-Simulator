"""
Synthetic order flow generator.

Supports:
  - Simple Poisson arrivals with exponential order sizes
  - Hawkes-process clustering (self-exciting arrivals)

Output: a list of raw dicts suitable for the backtester's event loop.
"""

from __future__ import annotations
import numpy as np
from dataclasses import dataclass, field
from typing import Literal


# ── Order record (plain Python, engine-independent) ───────────────────────────

@dataclass
class RawOrder:
    ts_us: int                          # arrival time (microseconds from start)
    side: Literal["buy", "sell"]
    price: int                          # integer ticks
    qty: int
    order_type: Literal["limit", "market"] = "limit"


# ── Simple Poisson generator ──────────────────────────────────────────────────

def generate_poisson(
    n_orders: int = 10_000,
    arrival_rate: float = 1_000.0,      # orders per second
    mid_price: float = 10_000.0,        # centre price in ticks
    tick_std: float = 10.0,             # price std in ticks
    mean_qty: float = 10.0,             # mean order size (exponential dist)
    market_fraction: float = 0.15,      # fraction of orders that are market
    seed: int = 42,
) -> list[RawOrder]:
    """Generate orders via a homogeneous Poisson process."""
    rng = np.random.default_rng(seed)

    # Inter-arrival times ~ Exp(rate)
    inter_arrivals_us = rng.exponential(1e6 / arrival_rate, size=n_orders)
    ts = np.cumsum(inter_arrivals_us).astype(int)

    sides = rng.choice(["buy", "sell"], size=n_orders)

    # Prices: normal around mid; round to nearest tick
    raw_prices = rng.normal(mid_price, tick_std, size=n_orders)
    prices = np.round(raw_prices).clip(1).astype(int)

    # Sizes: exponential, at least 1
    qtys = np.maximum(1, rng.exponential(mean_qty, size=n_orders).astype(int))

    order_types = rng.choice(
        ["market", "limit"],
        p=[market_fraction, 1 - market_fraction],
        size=n_orders,
    )

    return [
        RawOrder(
            ts_us=int(ts[i]),
            side=sides[i],
            price=int(prices[i]),
            qty=int(qtys[i]),
            order_type=order_types[i],
        )
        for i in range(n_orders)
    ]


# ── Hawkes-process generator ──────────────────────────────────────────────────

def generate_hawkes(
    T_seconds: float = 10.0,
    baseline_rate: float = 500.0,       # λ₀ (events/sec)
    alpha: float = 0.6,                 # excitement magnitude (α < β for stability)
    beta: float = 1.0,                  # decay rate (β)
    mid_price: float = 10_000.0,
    tick_std: float = 10.0,
    mean_qty: float = 10.0,
    market_fraction: float = 0.15,
    seed: int = 42,
) -> list[RawOrder]:
    """
    Generate orders via a univariate Hawkes process.

    Intensity: λ(t) = λ₀ + α * Σ_{tᵢ < t} exp(-β(t - tᵢ))

    Simulated via Ogata's modified thinning algorithm.

    Parameters
    ----------
    T_seconds   : total simulation duration
    baseline_rate : background intensity λ₀ (orders/sec)
    alpha       : jump size on each event (α)
    beta        : exponential decay rate   (β)
                  Stability: α < β
    """
    assert alpha < beta, "Hawkes process unstable: need alpha < beta"

    rng = np.random.default_rng(seed)
    T_us = int(T_seconds * 1e6)

    events_us: list[int] = []
    intensity = baseline_rate

    t = 0
    while t < T_us:
        # Upper bound on intensity from current t
        lam_bar = baseline_rate + alpha * len(events_us) / (beta + 1e-9)  # rough upper
        # Tighter: recompute from last event
        if events_us:
            last = events_us[-1]
            lam_bar = baseline_rate + alpha * np.exp(-beta * (t - last) / 1e6)
            # Add contribution from earlier events (bounded)
            lam_bar = min(lam_bar + baseline_rate * alpha / beta, baseline_rate * 10)

        # Draw candidate next event
        dt_us = rng.exponential(1e6 / max(lam_bar, 1.0))
        t_candidate = t + dt_us

        if t_candidate > T_us:
            break

        # Compute true intensity at t_candidate
        if events_us:
            decays = np.exp(-beta * (t_candidate - np.array(events_us)) / 1e6)
            lam_true = baseline_rate + alpha * float(decays.sum())
        else:
            lam_true = baseline_rate

        # Accept/reject
        if rng.uniform() <= lam_true / lam_bar:
            events_us.append(int(t_candidate))

        t = t_candidate

    n = len(events_us)
    if n == 0:
        return []

    sides = rng.choice(["buy", "sell"], size=n)
    prices = np.round(rng.normal(mid_price, tick_std, size=n)).clip(1).astype(int)
    qtys = np.maximum(1, rng.exponential(mean_qty, size=n).astype(int))
    order_types = rng.choice(
        ["market", "limit"],
        p=[market_fraction, 1 - market_fraction],
        size=n,
    )

    return [
        RawOrder(
            ts_us=events_us[i],
            side=sides[i],
            price=int(prices[i]),
            qty=int(qtys[i]),
            order_type=order_types[i],
        )
        for i in range(n)
    ]


# ── LOBSTER loader stub ───────────────────────────────────────────────────────

def load_lobster(message_file: str, orderbook_file: str) -> list[RawOrder]:
    """
    Stub loader for LOBSTER (Limit Order Book System, The Efficient Reconstructor)
    data format.

    LOBSTER files come in pairs:
      *_message_*.csv  — one row per event (type, id, size, price, dir, ts)
      *_orderbook_*.csv — snapshot of top N levels after each event

    Returns RawOrder list from the message file.  Only types 1 (new limit),
    4 (cancel), 5 (delete), and 6 (market/hidden) are used here.

    Download sample data from: https://lobsterdata.com/
    """
    import pandas as pd  # local import so this stub doesn't hard-require pandas

    msg = pd.read_csv(
        message_file,
        header=None,
        names=["ts", "type", "order_id", "size", "price", "direction"],
    )

    orders = []
    for _, row in msg.iterrows():
        if row["type"] not in (1, 4, 5, 6):
            continue
        side = "buy" if row["direction"] == 1 else "sell"
        otype = "market" if row["type"] == 6 else "limit"
        orders.append(
            RawOrder(
                ts_us=int(row["ts"] * 1e6),
                side=side,
                price=int(row["price"]),
                qty=int(row["size"]),
                order_type=otype,
            )
        )
    return orders
