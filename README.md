# Marc's Market Maker

A limit order book matching engine and market-making simulator, built for a quant trading club portfolio submission.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Python Layer                          │
│                                                         │
│  data/synthetic.py          sim/backtester.py           │
│  ┌─────────────────┐        ┌────────────────────────┐  │
│  │ Poisson / Hawkes│──flow──▶  Event loop            │  │
│  │ order generator │        │  - cancel old quotes   │  │
│  └─────────────────┘        │  - call strategy       │  │
│                             │  - submit new quotes   │  │
│  strategies/                └────────┬───────────────┘  │
│  ┌──────────────────────────┐        │ pybind11          │
│  │ NaiveMarketMaker         │        ▼                   │
│  │ AvellanedaStoikov        │  ┌─────────────────────┐  │
│  │ GlostenMilgrom           │  │  C++ LimitOrderBook │  │
│  └──────────────────────────┘  │  (engine/lob.hpp)   │  │
│                                │                     │  │
│  sim/metrics.py                │  Price-time FIFO    │  │
│  ┌──────────────────────────┐  │  O(log n) insert    │  │
│  │ PnL, Sharpe, drawdown    │  │  O(1) best bid/ask  │  │
│  │ inventory, fill ratio    │  │  Callbacks: on_trade│  │
│  │ adverse selection, lat.  │  │           on_update │  │
│  └──────────────────────────┘  └─────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

---

## Order Book Engine

The C++ engine (`engine/lob.hpp` / `engine/lob.cpp`) implements a price-time priority limit order book:

| Operation | Complexity |
|-----------|-----------|
| Insert limit order | O(log n) via `std::map` |
| Best bid / ask | O(1) via map iterators |
| Cancel | O(log n) via order-id index |
| Modify (qty decrease) | O(log n), preserves time priority |
| Modify (price change) | O(log n), resets time priority |

**Order types:** Limit, Market, Cancel, Modify  
**TIF flags:** GTC (rests), IOC (immediate or cancel)  
**Flags:** `post_only` (rejects if would cross)  
**Events:** `on_trade(Trade)`, `on_book_update(BookUpdate)`, `on_reject(id, reason)`

### Benchmark (Apple M-series, -O3)

```
Orders submitted : 1,000,000
Trades generated : 782,686
Elapsed          : 0.228 s
Throughput       : 4,377,210 orders/sec
```

---

## Avellaneda-Stoikov Strategy

> Avellaneda, M. & Stoikov, S. (2008). *High-frequency trading in a limit order book.* Quantitative Finance, 8(3), 217-224.

The model assumes mid-price $S(t)$ follows a Brownian motion and the market maker maximises expected CARA utility of terminal wealth with risk-aversion $\gamma$.

### Reservation price

The indifference price shifts away from mid proportional to inventory $q$ and remaining time $\tau = T - t$:

$$r(S, q, t) = S - q \cdot \gamma \cdot \sigma^2 \cdot \tau$$

- **Long ($q > 0$):** reservation price drops → maker prices in the cost of unwinding
- **Short ($q < 0$):** reservation price rises

### Optimal half-spread

$$\delta^* = \frac{\gamma \sigma^2 \tau}{2} + \frac{1}{\gamma} \ln\!\left(1 + \frac{\gamma}{k}\right)$$

where $k$ is the order-book depth parameter (controls how quickly the probability of execution falls off with distance from mid). Lower $k$ → less elastic demand → maker can quote wider.

**Quote levels:**

$$\text{bid} = r - \delta^*, \qquad \text{ask} = r + \delta^*$$

**Key behaviour:** spread narrows as $\tau \to 0$ (end of session), and quotes shift aggressively to flatten inventory — both effects emerge naturally from the optimisation, not from hard-coded rules.

---

## Strategies

### 1. Naive Symmetric Quoter (`strategies/naive.py`)

Quotes ±$k$ ticks around mid with fixed size. Suppresses one side when inventory hits a hard limit. Baseline only — no model.

### 2. Avellaneda-Stoikov (`strategies/avellaneda_stoikov.py`)

Full A-S implementation. Parameters:

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `gamma` | 0.15 | Risk aversion (higher → wider spreads, faster inventory squeeze) |
| `sigma` | 2.5 | Mid-price volatility (ticks/√s) |
| `k` | 1.5 | Order-book depth / demand elasticity |
| `T` | 60.0 s | Session horizon |

### 3. Glosten-Milgrom Inspired (`strategies/glosten_milgrom.py`)

Widens the spread when recent order-flow imbalance (OFI) suggests informed trading:

$$\text{half-spread} = \text{base} \times \left(1 + \lambda \cdot |\text{OFI}|\right)$$

$$\text{OFI} = \frac{V_{\text{buy}} - V_{\text{sell}}}{V_{\text{buy}} + V_{\text{sell}}} \in [-1, 1]$$

Quotes are also skewed by $\theta \cdot \text{OFI} \cdot \text{base\_spread}$ in the direction of flow to reduce adverse selection.

---

## Results (60 s Hawkes flow, 300 orders/s baseline, α=0.6, β=1.0)

| Strategy | Total PnL | Sharpe | Max Drawdown | Fills | Fill Ratio |
|----------|-----------|--------|--------------|-------|------------|
| Naive | −1500.15 | −13.57 | 8806.16 | 8,428 | 0.0627 |
| Avellaneda-Stoikov | −1900.48 | −14.63 | 3699.75 | 17,123 | 0.1527 |
| Glosten-Milgrom | −1199.46 | −12.10 | 9013.05 | 5,984 | 0.0434 |

**Interpretation:** All three strategies lose money against adversarially random synthetic flow — this is expected and educational. Adverse selection dominates: each fill is immediately followed by a random price walk that on average moves against inventory. Key observations:

- **A-S quotes tighter spreads early** (large τ → small δ*), producing more fills (17K) and more adverse selection exposure.
- **A-S has the smallest max drawdown** because the reservation-price mechanism continuously skews quotes to flatten inventory.
- **GM achieves best PnL** by widening spreads during high-OFI episodes, trading fill rate for fill quality (5,984 fills vs 17,123 for A-S).
- In a real market with mean-reverting price dynamics, A-S and GM would outperform the naive quoter significantly.

---

## Build

### Prerequisites

```bash
brew install cmake
pip3 install pybind11 numpy pandas matplotlib pytest scipy nbconvert ipykernel
```

### Compile

```bash
mkdir build
cmake -B build -S . -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build build --parallel 4
```

### Run C++ tests + benchmark

```bash
./build/test_engine          # 18 GoogleTest cases
./build/lob_benchmark        # 1M order throughput
```

### Run Python tests

```bash
pytest tests/test_python.py -v   # 30 pytest cases
```

### Run analysis notebook

```bash
cd notebooks
python3 -m nbconvert --to notebook --execute analysis.ipynb \
    --output analysis_executed.ipynb
```

---

## Repo structure

```
engine/
  lob.hpp           — LOB header (all public API)
  lob.cpp           — matching engine implementation
  bindings.cpp      — pybind11 Python bindings
  benchmark.cpp     — 1M order throughput test
  __init__.py       — adds build/ to sys.path for Python imports

sim/
  backtester.py     — event-driven backtester with re-entrancy guard
  metrics.py        — PnL, Sharpe, drawdown, fill ratio, adverse selection
  __init__.py

strategies/
  naive.py                — symmetric ±k quoter
  avellaneda_stoikov.py   — A-S optimal spread (heavily documented)
  glosten_milgrom.py      — flow-imbalance-aware spread widening
  __init__.py

data/
  synthetic.py      — Poisson + Hawkes order flow generators, LOBSTER stub

notebooks/
  analysis.ipynb    — full comparison: metrics table + 9 plots

tests/
  test_engine.cpp   — 18 GoogleTest cases (matching, IOC, post-only, cancel, modify)
  test_python.py    — 30 pytest cases (generator, backtester, strategies, metrics)
```

---

## License

MIT © 2026 Marc Wong
