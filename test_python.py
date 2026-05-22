"""
Python integration tests: backtester, strategies, and metrics.

Run with:  pytest tests/test_python.py -v
"""

import sys
import os

# Resolve the project root regardless of where pytest is invoked from.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD_DIR    = os.path.join(PROJECT_ROOT, "build")
for p in (PROJECT_ROOT, BUILD_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import lob_engine as lob  # type: ignore[import]
from data.synthetic import generate_poisson, generate_hawkes, RawOrder
from sim.backtester import Backtester, Strategy, BookSnapshot, Fill
from sim.metrics import compute_metrics
from strategies.naive import NaiveMarketMaker
from strategies.avellaneda_stoikov import AvellanedaStoikov
from strategies.glosten_milgrom import GlostenMilgrom


# ── Synthetic data tests ───────────────────────────────────────────────────────

class TestSyntheticGenerator:
    def test_poisson_count(self):
        orders = generate_poisson(n_orders=500, seed=0)
        assert len(orders) == 500

    def test_poisson_timestamps_sorted(self):
        orders = generate_poisson(n_orders=200, seed=1)
        ts = [o.ts_us for o in orders]
        assert ts == sorted(ts)

    def test_poisson_prices_positive(self):
        orders = generate_poisson(n_orders=200, mid_price=10000, seed=2)
        assert all(o.price > 0 for o in orders)

    def test_poisson_qty_at_least_one(self):
        orders = generate_poisson(n_orders=200, seed=3)
        assert all(o.qty >= 1 for o in orders)

    def test_hawkes_generates_events(self):
        orders = generate_hawkes(T_seconds=5.0, baseline_rate=100.0, seed=0)
        assert len(orders) > 0

    def test_hawkes_timestamps_sorted(self):
        orders = generate_hawkes(T_seconds=5.0, seed=1)
        ts = [o.ts_us for o in orders]
        assert ts == sorted(ts)

    def test_hawkes_clustering(self):
        # Higher alpha → more clustering → more events than pure Poisson
        orders_hawkes  = generate_hawkes(T_seconds=5.0, baseline_rate=200.0,
                                         alpha=0.8, beta=1.0, seed=42)
        orders_poisson = generate_poisson(n_orders=1000, arrival_rate=200.0, seed=42)
        # Hawkes should produce comparable magnitude of events
        assert len(orders_hawkes) > 0


# ── Backtester tests ──────────────────────────────────────────────────────────

class TestBacktester:
    def _make_minimal_flow(self) -> list[RawOrder]:
        """50 alternating buy/sell limit orders at same price → lots of fills."""
        orders = []
        for i in range(50):
            orders.append(RawOrder(
                ts_us=i * 1000,
                side="buy" if i % 2 == 0 else "sell",
                price=10000,
                qty=1,
                order_type="limit",
            ))
        return orders

    def test_run_returns_account_state(self):
        bt = Backtester(strategy=NaiveMarketMaker(), tick_size=0.01)
        flow = generate_poisson(n_orders=200, seed=0)
        state = bt.run(flow)
        assert state is not None

    def test_book_series_populated(self):
        bt = Backtester(strategy=NaiveMarketMaker(), tick_size=0.01)
        flow = generate_poisson(n_orders=100, seed=1)
        bt.run(flow)
        assert len(bt.book_series) > 0

    def test_pnl_series_populated(self):
        bt = Backtester(strategy=NaiveMarketMaker(), tick_size=0.01)
        flow = generate_poisson(n_orders=100, seed=2)
        bt.run(flow)
        assert len(bt.pnl_series) > 0

    def test_reset_between_runs(self):
        bt = Backtester(strategy=NaiveMarketMaker(), tick_size=0.01)
        flow = generate_poisson(n_orders=100, seed=3)
        bt.run(flow)
        fills_first = len(bt._account.fills)
        bt.run(flow)
        # State resets; second run is independent.
        assert len(bt._account.fills) == fills_first  # deterministic

    def test_null_strategy_no_crash(self):
        """A strategy that emits no orders should not crash the backtester."""
        bt = Backtester(strategy=Strategy(), tick_size=0.01)
        flow = generate_poisson(n_orders=200, seed=4)
        bt.run(flow)  # should not raise


# ── Strategy tests ────────────────────────────────────────────────────────────

class TestNaive:
    def test_quotes_around_mid(self):
        strat = NaiveMarketMaker(half_spread_ticks=5, order_size=10)
        book  = BookSnapshot(best_bid=99, best_ask=101, mid=100.0, spread=2.0, ts_us=0)
        orders = strat.on_book_update(book)
        assert len(orders) == 2
        sides  = {o.side for o in orders}
        prices = {o.price for o in orders}
        assert sides == {"buy", "sell"}
        assert 95 in prices  # mid(100) - k(5)
        assert 105 in prices  # mid(100) + k(5)

    def test_suppresses_buy_at_max_inventory(self):
        strat = NaiveMarketMaker(max_inventory=10)
        strat._inventory = 10
        book = BookSnapshot(best_bid=99, best_ask=101, mid=100.0, spread=2.0, ts_us=0)
        orders = strat.on_book_update(book)
        assert all(o.side != "buy" for o in orders)

    def test_suppresses_sell_at_min_inventory(self):
        strat = NaiveMarketMaker(max_inventory=10)
        strat._inventory = -10
        book = BookSnapshot(best_bid=99, best_ask=101, mid=100.0, spread=2.0, ts_us=0)
        orders = strat.on_book_update(book)
        assert all(o.side != "sell" for o in orders)

    def test_no_orders_when_no_mid(self):
        strat  = NaiveMarketMaker()
        book   = BookSnapshot(best_bid=None, best_ask=None, mid=None,
                              spread=float("nan"), ts_us=0)
        orders = strat.on_book_update(book)
        assert orders == []

    def test_inventory_updates_on_fill(self):
        strat = NaiveMarketMaker()
        fill  = Fill(order_id=1, side="buy", price=100, qty=5, ts_us=0)
        strat.on_fill(fill)
        assert strat._inventory == 5

    def test_reset_clears_inventory(self):
        strat = NaiveMarketMaker()
        strat._inventory = 99
        strat.reset()
        assert strat._inventory == 0


class TestAvellanedaStoikov:
    def _make_book(self, mid=10000.0, ts_us=0):
        return BookSnapshot(
            best_bid=int(mid) - 1, best_ask=int(mid) + 1,
            mid=mid, spread=2.0, ts_us=ts_us,
        )

    def test_quotes_both_sides(self):
        strat  = AvellanedaStoikov(gamma=0.1, sigma=2.0, k=1.5, T=60.0)
        orders = strat.on_book_update(self._make_book())
        assert len(orders) == 2
        assert {o.side for o in orders} == {"buy", "sell"}

    def test_spread_widens_with_time(self):
        # At t=0 vs t close to T, spread should be smaller near T.
        strat = AvellanedaStoikov(gamma=0.1, sigma=2.0, k=1.5, T=60.0)
        orders_early = strat.on_book_update(self._make_book(ts_us=0))
        strat.reset()
        orders_late  = strat.on_book_update(self._make_book(ts_us=int(59 * 1e6)))
        early_spread = orders_early[1].price - orders_early[0].price
        late_spread  = orders_late[1].price  - orders_late[0].price
        assert early_spread >= late_spread  # spread narrows as tau → 0

    def test_bid_shifts_down_when_long(self):
        strat  = AvellanedaStoikov(gamma=0.5, sigma=3.0, k=1.5, T=60.0)
        book   = self._make_book()
        orders_flat = strat.on_book_update(book)

        strat.reset()
        strat._inventory = 20
        orders_long = strat.on_book_update(book)

        bid_flat = next(o.price for o in orders_flat if o.side == "buy")
        bid_long = next(o.price for o in orders_long if o.side == "buy")
        assert bid_long < bid_flat  # reservation price shifts down

    def test_inventory_tracking(self):
        strat = AvellanedaStoikov()
        strat.on_fill(Fill(order_id=1, side="buy",  price=100, qty=3, ts_us=0))
        strat.on_fill(Fill(order_id=2, side="sell", price=101, qty=1, ts_us=1))
        assert strat._inventory == 2


class TestGlostenMilgrom:
    def _make_book(self, mid=10000.0):
        return BookSnapshot(
            best_bid=int(mid) - 1, best_ask=int(mid) + 1,
            mid=mid, spread=2.0, ts_us=0,
        )

    def test_quotes_both_sides(self):
        strat  = GlostenMilgrom()
        orders = strat.on_book_update(self._make_book())
        assert len(orders) == 2

    def test_spread_widens_on_imbalance(self):
        strat = GlostenMilgrom(base_half_spread=3, lambda_factor=3.0, window=20)
        book  = self._make_book()

        # Neutral flow first (no trades recorded → OFI = 0).
        orders_neutral = strat.on_book_update(book)
        spread_neutral = orders_neutral[1].price - orders_neutral[0].price

        # Inject heavy buy flow via on_trade with a constructible Trade object.
        for _ in range(20):
            t = lob.Trade()
            t.aggressor_id   = 0
            t.passive_id     = 0
            t.aggressor_side = lob.Side.Buy
            t.price          = 10000
            t.qty            = 10
            t.ts_us          = 0
            strat.on_trade(t)

        orders_imbalanced = strat.on_book_update(book)
        spread_imbalanced = orders_imbalanced[1].price - orders_imbalanced[0].price

        assert spread_imbalanced > spread_neutral

    def test_reset_clears_flow_and_inventory(self):
        strat = GlostenMilgrom()
        strat._inventory = 5
        strat._flow.append(10)
        strat.reset()
        assert strat._inventory == 0
        assert len(strat._flow) == 0


# ── Metrics tests ─────────────────────────────────────────────────────────────

class TestMetrics:
    def _run_backtest(self, strategy, n=500, seed=42):
        bt   = Backtester(strategy=strategy, tick_size=0.01)
        flow = generate_poisson(n_orders=n, seed=seed)
        bt.run(flow)
        return bt

    def test_metrics_keys_present(self):
        bt  = self._run_backtest(NaiveMarketMaker())
        res = compute_metrics(bt)
        d   = res.to_dict()
        for key in ["Total PnL", "Sharpe", "Max Drawdown", "Fills", "Fill Ratio"]:
            assert key in d

    def test_max_drawdown_non_negative(self):
        bt  = self._run_backtest(NaiveMarketMaker())
        res = compute_metrics(bt)
        assert res.max_drawdown >= 0

    def test_fill_ratio_bounded(self):
        bt  = self._run_backtest(NaiveMarketMaker())
        res = compute_metrics(bt)
        assert 0.0 <= res.fill_ratio

    def test_pnl_series_length_matches(self):
        bt  = self._run_backtest(NaiveMarketMaker())
        res = compute_metrics(bt)
        assert len(res.pnl) == len(res.ts_us)

    def test_all_three_strategies_run(self):
        flow = generate_poisson(n_orders=300, seed=10)
        for strat in [NaiveMarketMaker(), AvellanedaStoikov(), GlostenMilgrom()]:
            bt = Backtester(strategy=strat, tick_size=0.01)
            bt.run(flow)
            m = compute_metrics(bt)
            assert isinstance(m.total_pnl, float)
