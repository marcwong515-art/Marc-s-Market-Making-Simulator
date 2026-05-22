#include "lob.hpp"
#include <cassert>
#include <cmath>
#include <limits>

namespace lob {

// ── Constructor ───────────────────────────────────────────────────────────────
LimitOrderBook::LimitOrderBook(Callbacks cb) : callbacks(std::move(cb)) {}

// ── Public accessors ──────────────────────────────────────────────────────────

std::optional<Price> LimitOrderBook::best_bid() const {
    if (bids_.empty()) return std::nullopt;
    return bids_.begin()->first;
}

std::optional<Price> LimitOrderBook::best_ask() const {
    if (asks_.empty()) return std::nullopt;
    return asks_.begin()->first;
}

std::optional<Price> LimitOrderBook::mid_price() const {
    auto bb = best_bid();
    auto ba = best_ask();
    if (!bb || !ba) return std::nullopt;
    return (*bb + *ba);  // caller divides by 2 to get true mid
}

double LimitOrderBook::spread() const {
    auto bb = best_bid();
    auto ba = best_ask();
    if (!bb || !ba) return std::numeric_limits<double>::quiet_NaN();
    return static_cast<double>(*ba - *bb);
}

Quantity LimitOrderBook::depth_at(Side side, Price price) const {
    Quantity total = 0;
    if (side == Side::Buy) {
        auto it = bids_.find(price);
        if (it != bids_.end())
            for (const auto& r : it->second) total += r.qty;
    } else {
        auto it = asks_.find(price);
        if (it != asks_.end())
            for (const auto& r : it->second) total += r.qty;
    }
    return total;
}

size_t LimitOrderBook::num_levels(Side side) const {
    return (side == Side::Buy) ? bids_.size() : asks_.size();
}

void LimitOrderBook::clear() {
    bids_.clear();
    asks_.clear();
    index_.clear();
}

// ── submit() dispatcher ───────────────────────────────────────────────────────

bool LimitOrderBook::submit(const Order& order) {
    switch (order.type) {
        case OrderType::Limit:  return handle_limit(order);
        case OrderType::Market: return handle_market(order);
        case OrderType::Cancel: return handle_cancel(order);
        case OrderType::Modify: return handle_modify(order);
    }
    return false;
}

// ── Limit order ───────────────────────────────────────────────────────────────
//
// 1. Post-only check: reject if order would immediately cross the spread.
// 2. Match against opposite side up to the order's limit price.
// 3. Rest any remaining quantity (GTC) or discard (IOC).
// 4. Emit book-update.
//
bool LimitOrderBook::handle_limit(const Order& o) {
    if (o.qty <= 0) {
        if (callbacks.on_reject) callbacks.on_reject(o.id, RejectReason::InvalidQty);
        return false;
    }
    int64_t ts = o.ts_us ? o.ts_us : now_us();

    // Post-only: reject if order would immediately match.
    if (o.post_only) {
        bool crosses = (o.side == Side::Buy  && !asks_.empty() && o.price >= asks_.begin()->first)
                    || (o.side == Side::Sell && !bids_.empty() && o.price <= bids_.begin()->first);
        if (crosses) {
            if (callbacks.on_reject) callbacks.on_reject(o.id, RejectReason::PostOnlyWouldMatch);
            return false;
        }
    }

    Quantity remaining = o.qty;
    if (!o.post_only)
        match(o.id, o.side, o.price, remaining, ts);

    // Rest remaining qty on book if GTC.
    if (remaining > 0 && o.tif == TIF::GTC) {
        Order rest = o;
        rest.ts_us = ts;
        add_resting(rest, remaining);
    }

    emit_book_update(ts);
    return true;
}

// ── Market order ──────────────────────────────────────────────────────────────
//
// Sweeps the opposite side with no price constraint. Unmatched qty is dropped.
//
bool LimitOrderBook::handle_market(const Order& o) {
    if (o.qty <= 0) {
        if (callbacks.on_reject) callbacks.on_reject(o.id, RejectReason::InvalidQty);
        return false;
    }
    int64_t ts = o.ts_us ? o.ts_us : now_us();

    // Use extreme price limits so the crossing check always passes.
    Price limit_price = (o.side == Side::Buy) ? std::numeric_limits<Price>::max()
                                               : std::numeric_limits<Price>::min();
    Quantity remaining = o.qty;
    match(o.id, o.side, limit_price, remaining, ts);
    emit_book_update(ts);
    return true;
}

// ── Cancel order ──────────────────────────────────────────────────────────────

bool LimitOrderBook::handle_cancel(const Order& o) {
    auto idx_it = index_.find(o.id);
    if (idx_it == index_.end()) {
        if (callbacks.on_reject) callbacks.on_reject(o.id, RejectReason::UnknownOrder);
        return false;
    }
    Side  side  = idx_it->second.side;
    Price price = idx_it->second.price;
    index_.erase(idx_it);

    int64_t ts = o.ts_us ? o.ts_us : now_us();

    // Remove from FIFO queue at the given price level.
    auto erase_from = [&](auto& book) {
        auto lev = book.find(price);
        if (lev == book.end()) return;
        auto& q = lev->second;
        for (auto it = q.begin(); it != q.end(); ++it) {
            if (it->id == o.id) { q.erase(it); break; }
        }
        if (q.empty()) book.erase(lev);
    };

    if (side == Side::Buy) erase_from(bids_);
    else                   erase_from(asks_);

    emit_book_update(ts);
    return true;
}

// ── Modify order ──────────────────────────────────────────────────────────────
//
// Price change or qty increase → cancel + re-insert (time priority resets).
// Qty-only decrease               → update in-place (time priority preserved).
//
bool LimitOrderBook::handle_modify(const Order& o) {
    auto idx_it = index_.find(o.id);
    if (idx_it == index_.end()) {
        if (callbacks.on_reject) callbacks.on_reject(o.id, RejectReason::UnknownOrder);
        return false;
    }
    Side  old_side  = idx_it->second.side;
    Price old_price = idx_it->second.price;
    int64_t ts = o.ts_us ? o.ts_us : now_us();

    // Find pointer to the resting entry.
    RestingOrder* r = nullptr;
    auto find_r = [&](auto& book) -> RestingOrder* {
        auto lev = book.find(old_price);
        if (lev == book.end()) return nullptr;
        for (auto& entry : lev->second)
            if (entry.id == o.id) return &entry;
        return nullptr;
    };
    r = (old_side == Side::Buy) ? find_r(bids_) : find_r(asks_);
    if (!r) {
        if (callbacks.on_reject) callbacks.on_reject(o.id, RejectReason::UnknownOrder);
        return false;
    }

    Price    new_price = (o.new_price > 0) ? o.new_price : old_price;
    Quantity new_qty   = (o.new_qty   > 0) ? o.new_qty   : r->qty;
    bool price_changed = (new_price != old_price);

    if (!price_changed && new_qty <= r->qty) {
        // Qty-only decrease: update in-place, keep time priority.
        r->qty = new_qty;
        emit_book_update(ts);
    } else {
        // Cancel and re-insert (loses time priority).
        Order cancel_o;
        cancel_o.id    = o.id;
        cancel_o.type  = OrderType::Cancel;
        cancel_o.ts_us = ts;
        handle_cancel(cancel_o);  // emits book_update internally

        Order new_o;
        new_o.id    = o.id;
        new_o.type  = OrderType::Limit;
        new_o.side  = old_side;
        new_o.price = new_price;
        new_o.qty   = new_qty;
        new_o.tif   = TIF::GTC;
        new_o.ts_us = ts;
        handle_limit(new_o);  // emits book_update internally
    }
    return true;
}

// ── Core matching loop ────────────────────────────────────────────────────────
//
// Walk the passive side level by level (best price first).
// Stop when:
//   (a) aggressor is fully filled, or
//   (b) the next passive price no longer crosses the aggressor's limit.
//
// For a Buy  aggressor: cross if ask_price <= limit_price.
// For a Sell aggressor: cross if bid_price >= limit_price.
//
// Emits one Trade callback per resting order consumed (partial or full).
// Updates index_ when a resting order is fully consumed.
//
void LimitOrderBook::match(OrderId aggressor_id, Side aggressor_side,
                            Price limit_price, Quantity& remaining_qty,
                            int64_t ts_us) {
    if (aggressor_side == Side::Buy) {
        // Sweep asks (ascending): take front level while ask <= limit.
        while (remaining_qty > 0 && !asks_.empty()) {
            auto lev = asks_.begin();
            if (lev->first > limit_price) break;  // no longer crosses

            auto& q = lev->second;
            while (remaining_qty > 0 && !q.empty()) {
                RestingOrder& passive = q.front();
                Quantity fill = std::min(remaining_qty, passive.qty);

                if (callbacks.on_trade) {
                    callbacks.on_trade({ aggressor_id, passive.id,
                                         aggressor_side, passive.price,
                                         fill, ts_us });
                }

                remaining_qty -= fill;
                passive.qty   -= fill;

                if (passive.qty == 0) {
                    index_.erase(passive.id);
                    q.pop_front();
                }
            }
            if (q.empty()) asks_.erase(lev);
        }
    } else {
        // Sweep bids (descending): take front level while bid >= limit.
        while (remaining_qty > 0 && !bids_.empty()) {
            auto lev = bids_.begin();
            if (lev->first < limit_price) break;  // no longer crosses

            auto& q = lev->second;
            while (remaining_qty > 0 && !q.empty()) {
                RestingOrder& passive = q.front();
                Quantity fill = std::min(remaining_qty, passive.qty);

                if (callbacks.on_trade) {
                    callbacks.on_trade({ aggressor_id, passive.id,
                                         aggressor_side, passive.price,
                                         fill, ts_us });
                }

                remaining_qty -= fill;
                passive.qty   -= fill;

                if (passive.qty == 0) {
                    index_.erase(passive.id);
                    q.pop_front();
                }
            }
            if (q.empty()) bids_.erase(lev);
        }
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

void LimitOrderBook::add_resting(const Order& o, Quantity qty) {
    RestingOrder r{ o.id, o.side, o.price, qty, o.ts_us };
    if (o.side == Side::Buy) bids_[o.price].push_back(r);
    else                     asks_[o.price].push_back(r);
    index_[o.id] = { o.side, o.price };
}

void LimitOrderBook::emit_book_update(int64_t ts_us) const {
    if (!callbacks.on_book_update) return;
    callbacks.on_book_update({ best_bid(), best_ask(), ts_us });
}

} // namespace lob
