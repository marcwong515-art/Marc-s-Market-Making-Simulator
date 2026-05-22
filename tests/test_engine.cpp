#include <gtest/gtest.h>
#include "lob.hpp"

using namespace lob;

// ── Helpers ───────────────────────────────────────────────────────────────────

static Order make_limit(OrderId id, Side side, Price price, Quantity qty,
                        TIF tif = TIF::GTC, bool post_only = false) {
    Order o;
    o.id        = id;
    o.type      = OrderType::Limit;
    o.side      = side;
    o.price     = price;
    o.qty       = qty;
    o.tif       = tif;
    o.post_only = post_only;
    o.ts_us     = 0;
    return o;
}

static Order make_market(OrderId id, Side side, Quantity qty) {
    Order o;
    o.id   = id;
    o.type = OrderType::Market;
    o.side = side;
    o.qty  = qty;
    return o;
}

static Order make_cancel(OrderId id) {
    Order o;
    o.id   = id;
    o.type = OrderType::Cancel;
    return o;
}

// Capture callbacks into vectors for easy inspection.
struct Capture {
    std::vector<Trade>      trades;
    std::vector<BookUpdate> updates;
    std::vector<std::pair<OrderId, RejectReason>> rejects;

    Callbacks make() {
        Callbacks cb;
        cb.on_trade       = [this](const Trade& t)      { trades.push_back(t); };
        cb.on_book_update = [this](const BookUpdate& u)  { updates.push_back(u); };
        cb.on_reject      = [this](OrderId id, RejectReason r) {
            rejects.emplace_back(id, r);
        };
        return cb;
    }
};

// ── Tests ─────────────────────────────────────────────────────────────────────

TEST(LOB, EmptyBook) {
    LimitOrderBook book;
    EXPECT_FALSE(book.best_bid().has_value());
    EXPECT_FALSE(book.best_ask().has_value());
    EXPECT_EQ(book.num_orders(), 0u);
}

TEST(LOB, SingleBidRests) {
    LimitOrderBook book;
    book.submit(make_limit(1, Side::Buy, 100, 10));
    ASSERT_TRUE(book.best_bid().has_value());
    EXPECT_EQ(*book.best_bid(), 100);
    EXPECT_FALSE(book.best_ask().has_value());
    EXPECT_EQ(book.num_orders(), 1u);
}

TEST(LOB, SingleAskRests) {
    LimitOrderBook book;
    book.submit(make_limit(1, Side::Sell, 101, 5));
    ASSERT_TRUE(book.best_ask().has_value());
    EXPECT_EQ(*book.best_ask(), 101);
    EXPECT_FALSE(book.best_bid().has_value());
}

TEST(LOB, FullMatch) {
    Capture cap;
    LimitOrderBook book(cap.make());

    book.submit(make_limit(1, Side::Buy,  100, 10));
    book.submit(make_limit(2, Side::Sell, 100, 10));

    ASSERT_EQ(cap.trades.size(), 1u);
    EXPECT_EQ(cap.trades[0].qty, 10);
    EXPECT_EQ(cap.trades[0].price, 100);
    EXPECT_EQ(book.num_orders(), 0u);
    EXPECT_FALSE(book.best_bid().has_value());
    EXPECT_FALSE(book.best_ask().has_value());
}

TEST(LOB, PartialFill) {
    Capture cap;
    LimitOrderBook book(cap.make());

    book.submit(make_limit(1, Side::Buy,  100, 10));
    book.submit(make_limit(2, Side::Sell, 100, 4));

    ASSERT_EQ(cap.trades.size(), 1u);
    EXPECT_EQ(cap.trades[0].qty, 4);
    EXPECT_EQ(book.depth_at(Side::Buy, 100), 6);  // 10 - 4 remaining
}

TEST(LOB, PriceTimePriority) {
    Capture cap;
    LimitOrderBook book(cap.make());

    // Two bids at same price; first in time should fill first.
    book.submit(make_limit(1, Side::Buy, 100, 5));
    book.submit(make_limit(2, Side::Buy, 100, 5));

    // Sell 5 — should match order 1 first.
    book.submit(make_limit(3, Side::Sell, 100, 5));

    ASSERT_EQ(cap.trades.size(), 1u);
    EXPECT_EQ(cap.trades[0].passive_id, 1u);
    EXPECT_EQ(book.depth_at(Side::Buy, 100), 5);  // order 2 still resting
}

TEST(LOB, BestPriceFirst) {
    Capture cap;
    LimitOrderBook book(cap.make());

    // Two bids: better price should fill first.
    book.submit(make_limit(1, Side::Buy, 99,  5));
    book.submit(make_limit(2, Side::Buy, 101, 5));

    // Sell sweeps: order at 101 fills first.
    book.submit(make_limit(3, Side::Sell, 99, 10));

    ASSERT_EQ(cap.trades.size(), 2u);
    EXPECT_EQ(cap.trades[0].passive_id, 2u);  // 101 first
    EXPECT_EQ(cap.trades[1].passive_id, 1u);  // 99 second
}

TEST(LOB, MarketOrder) {
    Capture cap;
    LimitOrderBook book(cap.make());

    book.submit(make_limit(1, Side::Buy, 100, 20));
    book.submit(make_market(2, Side::Sell, 15));

    ASSERT_EQ(cap.trades.size(), 1u);
    EXPECT_EQ(cap.trades[0].qty, 15);
    EXPECT_EQ(book.depth_at(Side::Buy, 100), 5);
}

TEST(LOB, IOCUnmatched) {
    Capture cap;
    LimitOrderBook book(cap.make());

    // No asks exist — IOC buy should be entirely discarded.
    auto ioc = make_limit(1, Side::Buy, 100, 10, TIF::IOC);
    book.submit(ioc);

    EXPECT_EQ(cap.trades.size(), 0u);
    EXPECT_EQ(book.num_orders(), 0u);  // not rested
}

TEST(LOB, IOCPartialFill) {
    Capture cap;
    LimitOrderBook book(cap.make());

    book.submit(make_limit(1, Side::Sell, 100, 3));
    auto ioc = make_limit(2, Side::Buy, 100, 10, TIF::IOC);
    book.submit(ioc);

    ASSERT_EQ(cap.trades.size(), 1u);
    EXPECT_EQ(cap.trades[0].qty, 3);
    // Remaining 7 discarded, not resting.
    EXPECT_EQ(book.num_orders(), 0u);
}

TEST(LOB, PostOnlyReject) {
    Capture cap;
    LimitOrderBook book(cap.make());

    book.submit(make_limit(1, Side::Sell, 100, 5));
    auto po = make_limit(2, Side::Buy, 100, 5, TIF::GTC, /*post_only=*/true);
    book.submit(po);

    ASSERT_EQ(cap.rejects.size(), 1u);
    EXPECT_EQ(cap.rejects[0].first, 2u);
    EXPECT_EQ(cap.rejects[0].second, RejectReason::PostOnlyWouldMatch);
    EXPECT_EQ(cap.trades.size(), 0u);
}

TEST(LOB, PostOnlyAccepted) {
    Capture cap;
    LimitOrderBook book(cap.make());

    // Post-only on empty book: should rest normally.
    auto po = make_limit(1, Side::Buy, 99, 5, TIF::GTC, /*post_only=*/true);
    book.submit(po);

    EXPECT_EQ(cap.rejects.size(), 0u);
    EXPECT_EQ(book.depth_at(Side::Buy, 99), 5);
}

TEST(LOB, Cancel) {
    Capture cap;
    LimitOrderBook book(cap.make());

    book.submit(make_limit(1, Side::Buy, 100, 10));
    EXPECT_EQ(book.num_orders(), 1u);

    book.submit(make_cancel(1));
    EXPECT_EQ(book.num_orders(), 0u);
    EXPECT_FALSE(book.best_bid().has_value());
}

TEST(LOB, CancelUnknown) {
    Capture cap;
    LimitOrderBook book(cap.make());
    book.submit(make_cancel(999));
    ASSERT_EQ(cap.rejects.size(), 1u);
    EXPECT_EQ(cap.rejects[0].second, RejectReason::UnknownOrder);
}

TEST(LOB, ModifyQtyDecrease) {
    LimitOrderBook book;
    book.submit(make_limit(1, Side::Buy, 100, 10));

    Order mod;
    mod.id      = 1;
    mod.type    = OrderType::Modify;
    mod.new_qty = 6;
    book.submit(mod);

    EXPECT_EQ(book.depth_at(Side::Buy, 100), 6);
    EXPECT_EQ(book.num_orders(), 1u);
}

TEST(LOB, ModifyPriceChange) {
    Capture cap;
    LimitOrderBook book(cap.make());
    book.submit(make_limit(1, Side::Buy, 100, 10));
    book.submit(make_limit(2, Side::Buy, 100, 5));

    // Move order 1 to price 101 — it should lose time priority vs order 2.
    Order mod;
    mod.id        = 1;
    mod.type      = OrderType::Modify;
    mod.new_price = 101;
    book.submit(mod);

    EXPECT_EQ(*book.best_bid(), 101);
    EXPECT_EQ(book.depth_at(Side::Buy, 100), 5);
    EXPECT_EQ(book.depth_at(Side::Buy, 101), 10);
}

TEST(LOB, SpreadAndMid) {
    LimitOrderBook book;
    book.submit(make_limit(1, Side::Buy,  100, 5));
    book.submit(make_limit(2, Side::Sell, 102, 5));

    EXPECT_DOUBLE_EQ(book.spread(), 2.0);
    ASSERT_TRUE(book.mid_price().has_value());
    EXPECT_EQ(*book.mid_price(), 202);  // 100+102, caller divides by 2
}

TEST(LOB, MultiLevelSweep) {
    Capture cap;
    LimitOrderBook book(cap.make());

    book.submit(make_limit(1, Side::Sell, 100, 3));
    book.submit(make_limit(2, Side::Sell, 101, 3));
    book.submit(make_limit(3, Side::Sell, 102, 3));

    // Market buy for 8 should sweep 100 (3) and 101 (3) fully, and 102 (2).
    book.submit(make_market(4, Side::Buy, 8));

    ASSERT_EQ(cap.trades.size(), 3u);
    EXPECT_EQ(cap.trades[0].price, 100);
    EXPECT_EQ(cap.trades[1].price, 101);
    EXPECT_EQ(cap.trades[2].qty,   2);   // partial fill at 102
    EXPECT_EQ(book.depth_at(Side::Sell, 102), 1);
}
