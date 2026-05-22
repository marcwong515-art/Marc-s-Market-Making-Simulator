#include <pybind11/pybind11.h>
#include <pybind11/functional.h>
#include <pybind11/stl.h>
#include "lob.hpp"

namespace py = pybind11;
using namespace lob;

PYBIND11_MODULE(lob_engine, m) {
    m.doc() = "Limit Order Book matching engine  -  C++ core via pybind11";

    // ── Enums ─────────────────────────────────────────────────────────────────
    py::enum_<Side>(m, "Side")
        .value("Buy",  Side::Buy)
        .value("Sell", Side::Sell);

    py::enum_<OrderType>(m, "OrderType")
        .value("Limit",  OrderType::Limit)
        .value("Market", OrderType::Market)
        .value("Cancel", OrderType::Cancel)
        .value("Modify", OrderType::Modify);

    py::enum_<TIF>(m, "TIF")
        .value("GTC", TIF::GTC)
        .value("IOC", TIF::IOC);

    py::enum_<RejectReason>(m, "RejectReason")
        .value("None",               RejectReason::None)
        .value("UnknownOrder",       RejectReason::UnknownOrder)
        .value("PostOnlyWouldMatch", RejectReason::PostOnlyWouldMatch)
        .value("InvalidQty",         RejectReason::InvalidQty);

    // ── Structs ───────────────────────────────────────────────────────────────
    py::class_<Order>(m, "Order")
        .def(py::init<>())
        .def_readwrite("id",        &Order::id)
        .def_readwrite("type",      &Order::type)
        .def_readwrite("side",      &Order::side)
        .def_readwrite("price",     &Order::price)
        .def_readwrite("qty",       &Order::qty)
        .def_readwrite("tif",       &Order::tif)
        .def_readwrite("post_only", &Order::post_only)
        .def_readwrite("ts_us",     &Order::ts_us)
        .def_readwrite("new_price", &Order::new_price)
        .def_readwrite("new_qty",   &Order::new_qty);

    py::class_<RestingOrder>(m, "RestingOrder")
        .def_readonly("id",    &RestingOrder::id)
        .def_readonly("side",  &RestingOrder::side)
        .def_readonly("price", &RestingOrder::price)
        .def_readonly("qty",   &RestingOrder::qty)
        .def_readonly("ts_us", &RestingOrder::ts_us);

    py::class_<Trade>(m, "Trade")
        .def(py::init<>())
        .def_readwrite("aggressor_id",   &Trade::aggressor_id)
        .def_readwrite("passive_id",     &Trade::passive_id)
        .def_readwrite("aggressor_side", &Trade::aggressor_side)
        .def_readwrite("price",          &Trade::price)
        .def_readwrite("qty",            &Trade::qty)
        .def_readwrite("ts_us",          &Trade::ts_us);

    py::class_<BookUpdate>(m, "BookUpdate")
        .def_readonly("best_bid", &BookUpdate::best_bid)
        .def_readonly("best_ask", &BookUpdate::best_ask)
        .def_readonly("ts_us",    &BookUpdate::ts_us);

    // ── Callbacks ─────────────────────────────────────────────────────────────
    py::class_<Callbacks>(m, "Callbacks")
        .def(py::init<>())
        .def_readwrite("on_trade",       &Callbacks::on_trade)
        .def_readwrite("on_book_update", &Callbacks::on_book_update)
        .def_readwrite("on_reject",      &Callbacks::on_reject);

    // ── LimitOrderBook ────────────────────────────────────────────────────────
    py::class_<LimitOrderBook>(m, "LimitOrderBook")
        .def(py::init<Callbacks>(), py::arg("callbacks") = Callbacks())
        .def("submit",     &LimitOrderBook::submit)
        .def("best_bid",   &LimitOrderBook::best_bid)
        .def("best_ask",   &LimitOrderBook::best_ask)
        .def("mid_price",  &LimitOrderBook::mid_price)
        .def("spread",     &LimitOrderBook::spread)
        .def("depth_at",   &LimitOrderBook::depth_at)
        .def("num_levels", &LimitOrderBook::num_levels)
        .def("num_orders", &LimitOrderBook::num_orders)
        .def("clear",      &LimitOrderBook::clear)
        .def_readwrite("callbacks", &LimitOrderBook::callbacks);

    // ── Utility ───────────────────────────────────────────────────────────────
    m.def("now_us", &now_us, "Current time in microseconds (steady clock)");
}
