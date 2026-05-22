#pragma once
#include <chrono>
#include <cstdint>
#include <functional>
#include <map>
#include <deque>
#include <unordered_map>
#include <optional>
#include <string>

namespace lob {

// ── Timestamps ────────────────────────────────────────────────────────────────
using Clock     = std::chrono::steady_clock;
using TimePoint = std::chrono::time_point<Clock>;
using Micros    = std::chrono::microseconds;

inline int64_t now_us() {
    return std::chrono::duration_cast<Micros>(
        Clock::now().time_since_epoch()).count();
}

// ── Primitive types ───────────────────────────────────────────────────────────
using OrderId  = uint64_t;
using Price    = int64_t;   // integer ticks (e.g. cents, or price * 1e4)
using Quantity = int64_t;

// ── Order side ────────────────────────────────────────────────────────────────
enum class Side : uint8_t { Buy = 0, Sell = 1 };

// ── Order type ────────────────────────────────────────────────────────────────
enum class OrderType : uint8_t {
    Limit   = 0,
    Market  = 1,
    Cancel  = 2,
    Modify  = 3,
};

// ── Time-in-force ─────────────────────────────────────────────────────────────
enum class TIF : uint8_t {
    GTC  = 0,  // Good Till Cancel (default)
    IOC  = 1,  // Immediate Or Cancel
};

// ── Incoming order instruction ────────────────────────────────────────────────
struct Order {
    OrderId   id        = 0;
    OrderType type      = OrderType::Limit;
    Side      side      = Side::Buy;
    Price     price     = 0;     // ignored for Market orders
    Quantity  qty       = 0;
    TIF       tif       = TIF::GTC;
    bool      post_only = false; // reject if would immediately match
    int64_t   ts_us     = 0;     // submission timestamp (microseconds)

    // For Modify: new_price / new_qty replace the resting order's fields.
    // Set to 0 to leave unchanged.
    Price    new_price  = 0;
    Quantity new_qty    = 0;
};

// ── A resting limit on the book ───────────────────────────────────────────────
struct RestingOrder {
    OrderId  id;
    Side     side;
    Price    price;
    Quantity qty;       // remaining quantity
    int64_t  ts_us;     // time of arrival (for time-priority)
};

// ── Trade (fill) event ────────────────────────────────────────────────────────
struct Trade {
    OrderId  aggressor_id;  // the incoming order that triggered the fill
    OrderId  passive_id;    // the resting order that got hit
    Side     aggressor_side;
    Price    price;
    Quantity qty;
    int64_t  ts_us;
};

// ── Book-update event (emitted after each order is fully processed) ───────────
struct BookUpdate {
    std::optional<Price> best_bid;
    std::optional<Price> best_ask;
    int64_t ts_us;
};

// ── Rejection reasons ─────────────────────────────────────────────────────────
enum class RejectReason : uint8_t {
    None            = 0,
    UnknownOrder    = 1,
    PostOnlyWouldMatch = 2,
    InvalidQty      = 3,
};

// ── Callbacks (set before submitting orders) ──────────────────────────────────
struct Callbacks {
    std::function<void(const Trade&)>      on_trade;
    std::function<void(const BookUpdate&)> on_book_update;
    std::function<void(OrderId, RejectReason)> on_reject;
};

// ── The limit order book ──────────────────────────────────────────────────────
//
// Bids: std::map<Price, deque<RestingOrder>> sorted descending (highest first).
// Asks: std::map<Price, deque<RestingOrder>> sorted ascending  (lowest first).
//
// O(log n) insert/delete; best bid/ask tracked in O(1) via map iterators.
// A lookup map (id -> price+side) allows O(log n) cancel/modify.
//
class LimitOrderBook {
public:
    explicit LimitOrderBook(Callbacks cb = {});

    // Submit any order instruction. Returns false if rejected.
    bool submit(const Order& order);

    // Convenience accessors
    std::optional<Price> best_bid() const;
    std::optional<Price> best_ask() const;
    std::optional<Price> mid_price() const;   // (best_bid + best_ask) / 2
    double spread() const;                    // ask - bid in ticks (NaN if one-sided)

    // Total resting quantity at a given price level
    Quantity depth_at(Side side, Price price) const;

    // Number of distinct price levels
    size_t num_levels(Side side) const;

    // Total number of resting orders
    size_t num_orders() const { return index_.size(); }

    // Reset everything
    void clear();

    Callbacks callbacks;

private:
    // ── Book storage ──────────────────────────────────────────────────────────
    // Bids: reverse order (highest price = best)
    std::map<Price, std::deque<RestingOrder>, std::greater<Price>> bids_;
    // Asks: normal order  (lowest price  = best)
    std::map<Price, std::deque<RestingOrder>>                       asks_;

    // Lookup: order id -> (side, price) for O(log n) cancel/modify
    struct IndexEntry { Side side; Price price; };
    std::unordered_map<OrderId, IndexEntry> index_;

    uint64_t next_id_ = 1;

    // ── Internal helpers ──────────────────────────────────────────────────────
    bool handle_limit (const Order& o);
    bool handle_market(const Order& o);
    bool handle_cancel(const Order& o);
    bool handle_modify(const Order& o);

    // Sweep the passive side; stop when price no longer crosses limit_price.
    // Use INT64_MAX (buy) / INT64_MIN (sell) for unconditional market sweeps.
    void match(OrderId aggressor_id, Side aggressor_side,
               Price limit_price, Quantity& remaining_qty, int64_t ts_us);

    void add_resting(const Order& o, Quantity qty);
    void emit_book_update(int64_t ts_us) const;
    void prune_empty_levels();
};

} // namespace lob
