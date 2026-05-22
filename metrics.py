"""
Performance metrics for market-making strategies.

Computes and returns a dict of scalar metrics, and a dict of time-series
arrays suitable for plotting.
"""

from __future__ import annotations
import numpy as np
from dataclasses import dataclass


@dataclass
class MetricsResult:
    # ── Scalars ───────────────────────────────────────────────────────────────
    total_pnl: float
    sharpe: float
    max_drawdown: float
    fill_count: int
    fill_ratio: float          # fills / total book updates (proxy for activity)
    inv_mean: float
    inv_std: float
    inv_max_abs: int
    adverse_selection_cost: float  # mean PnL change N ticks after each fill
    mean_latency_us: float
    p99_latency_us: float

    # ── Time series (for plotting) ────────────────────────────────────────────
    ts_us: np.ndarray
    pnl: np.ndarray
    inventory: np.ndarray

    def to_dict(self) -> dict:
        return {
            "Total PnL":              round(self.total_pnl, 4),
            "Sharpe":                 round(self.sharpe, 3),
            "Max Drawdown":           round(self.max_drawdown, 4),
            "Fills":                  self.fill_count,
            "Fill Ratio":             round(self.fill_ratio, 4),
            "Inventory Mean":         round(self.inv_mean, 2),
            "Inventory Std":          round(self.inv_std, 2),
            "Inventory Max |q|":      self.inv_max_abs,
            "Adverse Selection Cost": round(self.adverse_selection_cost, 6),
            "Mean Latency (µs)":      round(self.mean_latency_us, 1),
            "P99 Latency (µs)":       round(self.p99_latency_us, 1),
        }


def compute_metrics(
    backtester,
    adverse_selection_window: int = 10,
) -> MetricsResult:
    """
    Compute all metrics from a completed Backtester run.

    Parameters
    ----------
    backtester               : Backtester instance after .run() has been called.
    adverse_selection_window : N ticks post-fill to measure PnL change.
    """
    # ── PnL series ────────────────────────────────────────────────────────────
    if not backtester.pnl_series:
        ts_arr  = np.array([0])
        pnl_arr = np.array([0.0])
    else:
        ts_arr, pnl_arr = map(np.array, zip(*backtester.pnl_series))

    total_pnl = float(pnl_arr[-1]) if len(pnl_arr) else 0.0

    # ── Sharpe (annualised, assuming microsecond timestamps) ──────────────────
    returns = np.diff(pnl_arr)
    sharpe = 0.0
    if len(returns) > 1 and returns.std() > 0:
        # Sharpe = mean / std * sqrt(periods_per_year)
        # We annualise assuming 1 µs ticks and ~8 trading hours/day, 252 days.
        mean_dt_us = float(np.diff(ts_arr).mean()) if len(ts_arr) > 1 else 1e3
        periods_per_year = (252 * 8 * 3600 * 1e6) / mean_dt_us
        sharpe = float(returns.mean() / returns.std() * np.sqrt(periods_per_year))

    # ── Max drawdown ──────────────────────────────────────────────────────────
    running_max   = np.maximum.accumulate(pnl_arr)
    drawdowns     = running_max - pnl_arr
    max_drawdown  = float(drawdowns.max()) if len(drawdowns) else 0.0

    # ── Inventory ─────────────────────────────────────────────────────────────
    if backtester.inv_series:
        _, inv_vals = map(np.array, zip(*backtester.inv_series))
    else:
        inv_vals = np.array([0])

    inv_mean    = float(inv_vals.mean())
    inv_std     = float(inv_vals.std())
    inv_max_abs = int(np.abs(inv_vals).max())

    # ── Fill count & fill ratio ───────────────────────────────────────────────
    fills       = backtester._account.fills
    fill_count  = len(fills)
    n_updates   = max(len(backtester.book_series), 1)
    fill_ratio  = fill_count / n_updates

    # ── Adverse selection cost ────────────────────────────────────────────────
    # After each fill, look N book-updates ahead and measure PnL change.
    # Negative means PnL fell after the fill → adverse selection.
    adv_costs: list[float] = []
    for fill in fills:
        # Find index in pnl_series closest to fill time.
        if len(ts_arr) == 0:
            continue
        idx = int(np.searchsorted(ts_arr, fill.ts_us))
        ahead = idx + adverse_selection_window
        if ahead < len(pnl_arr):
            adv_costs.append(pnl_arr[ahead] - pnl_arr[idx])

    adverse_selection_cost = float(np.mean(adv_costs)) if adv_costs else 0.0

    # ── Latency ───────────────────────────────────────────────────────────────
    lats = backtester.latency_us
    mean_lat = float(np.mean(lats)) if lats else 0.0
    p99_lat  = float(np.percentile(lats, 99)) if lats else 0.0

    return MetricsResult(
        total_pnl=total_pnl,
        sharpe=sharpe,
        max_drawdown=max_drawdown,
        fill_count=fill_count,
        fill_ratio=fill_ratio,
        inv_mean=inv_mean,
        inv_std=inv_std,
        inv_max_abs=inv_max_abs,
        adverse_selection_cost=adverse_selection_cost,
        mean_latency_us=mean_lat,
        p99_latency_us=p99_lat,
        ts_us=ts_arr,
        pnl=pnl_arr,
        inventory=inv_vals,
    )
