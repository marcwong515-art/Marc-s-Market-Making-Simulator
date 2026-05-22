# Limit Order Book and Market-Making Simulator

A from-scratch limit order book matching engine and event-driven backtester for studying market-making strategies. Implements three quoting models including the Avellaneda-Stoikov inventory-aware market maker.

## What this is

A research environment for the question every market maker has to answer: given uncertain order flow and inventory risk, where do you place your bid and ask, and how wide do you quote?

The repo contains:

- A C++ matching engine with price-time priority, exposed to Python via pybind11
- An event-driven backtester written in Python
- A synthetic order flow generator (Poisson arrivals with optional Hawkes clustering)
- Three market-making strategies benchmarked against each other on identical flow

## Strategies

**Naive symmetric quoter.** Quotes a fixed spread around the mid. Baseline.

**Avellaneda-Stoikov (2008).** Solves for optimal bid and ask using a reservation price that adjusts away from mid as inventory grows. The reservation price is:

```
r(s, q, t) = s - q * gamma * sigma^2 * (T - t)
```

and the optimal half-spread is:

```
delta = gamma * sigma^2 * (T - t) + (2 / gamma) * ln(1 + gamma / k)
```

where `s` is mid price, `q` is inventory, `gamma` is risk aversion, `sigma` is volatility, `T - t` is time to horizon, and `k` is the order arrival intensity decay. Full derivation in the strategy docstring.

**Glosten-Milgrom-inspired adverse selection.** Widens the spread when recent order flow imbalance suggests informed traders are on one side. Closes the gap when flow looks balanced.

## Metrics

For each strategy:

- Cumulative PnL, Sharpe ratio, max drawdown
- Inventory distribution and variance
- Fill ratio
- Adverse selection cost (PnL change in N ticks after each fill)
- Quote-to-trade latency in microseconds

## Architecture

```
order flow generator -> C++ matching engine -> event stream
                                                    |
                                              event loop (Python)
                                                    |
                                            strategy.on_book_update()
                                            strategy.on_fill()
                                                    |
                                           new orders -> engine
```

The engine emits trade and book-update events through a callback. The Python event loop consumes them, invokes the active strategy, and submits returned orders back to the engine.

## Build

```
git clone <repo>
cd <repo>
cmake -B build && cmake --build build
pip install -r requirements.txt
pip install -e .
```

## Run

```
python -m sim.run --strategy avellaneda_stoikov --duration 3600 --seed 42
```

The end-to-end comparison notebook lives at `notebooks/strategy_comparison.ipynb` and runs all three strategies on the same synthetic flow.

## Results

See the notebook for the full comparison. Avellaneda-Stoikov consistently produces lower inventory variance than the naive quoter at the cost of slightly tighter fill ratios. The adverse-selection-aware variant performs best when the order flow generator includes Hawkes clustering.

## What this is not

This is a research simulator, not a production trading system. The matching engine is single-threaded and the latency numbers measure code path cost, not real exchange round-trips. There is no FIX gateway, no risk system, no real exchange data unless you wire in LOBSTER samples yourself.

## References

- Avellaneda, M. and Stoikov, S. (2008). High-frequency trading in a limit order book. Quantitative Finance.
- Glosten, L. and Milgrom, P. (1985). Bid, ask and transaction prices in a specialist market.

## License

MIT
