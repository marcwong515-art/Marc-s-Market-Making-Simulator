#include "lob.hpp"
#include <chrono>
#include <cstdio>
#include <random>

// Sends 1 000 000 orders (alternating limit/cancel) and prints throughput.
int main() {
    using namespace lob;

    size_t trade_count = 0;
    Callbacks cb;
    cb.on_trade = [&](const Trade&) { ++trade_count; };

    LimitOrderBook book(cb);

    std::mt19937_64 rng(42);
    // Prices centred at 10000, spread ±50 ticks
    std::uniform_int_distribution<Price>    price_dist(9975, 10025);
    std::uniform_int_distribution<Quantity> qty_dist(1, 100);
    std::bernoulli_distribution             side_dist(0.5);

    constexpr int N = 1'000'000;

    auto t0 = std::chrono::high_resolution_clock::now();

    for (int i = 1; i <= N; ++i) {
        Order o;
        o.id    = static_cast<OrderId>(i);
        o.type  = OrderType::Limit;
        o.side  = side_dist(rng) ? Side::Buy : Side::Sell;
        o.price = price_dist(rng);
        o.qty   = qty_dist(rng);
        o.tif   = TIF::GTC;
        o.ts_us = now_us();
        book.submit(o);
    }

    auto t1 = std::chrono::high_resolution_clock::now();
    double elapsed_s = std::chrono::duration<double>(t1 - t0).count();

    std::printf("Orders submitted : %d\n",  N);
    std::printf("Trades generated : %zu\n", trade_count);
    std::printf("Elapsed          : %.3f s\n", elapsed_s);
    std::printf("Throughput       : %.0f orders/sec\n", N / elapsed_s);
    std::printf("Resting orders   : %zu\n", book.num_orders());

    return 0;
}
