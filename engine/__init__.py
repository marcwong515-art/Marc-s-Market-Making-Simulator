"""
Engine package — imports the C++ lob_engine extension.

The .so is placed in build/ by CMake. We add that directory to sys.path
so `import lob_engine` works from anywhere in the project.
"""
import sys
import os

_here = os.path.dirname(os.path.abspath(__file__))
_build = os.path.join(os.path.dirname(_here), "build")
if _build not in sys.path:
    sys.path.insert(0, _build)

try:
    from lob_engine import *  # noqa: F401, F403
    from lob_engine import LimitOrderBook, Order, Trade, BookUpdate  # noqa: F401
    from lob_engine import Side, OrderType, TIF, RejectReason, Callbacks, now_us  # noqa: F401
except ImportError as e:
    raise ImportError(
        "Could not import lob_engine. Run:\n"
        "  mkdir -p build && cmake -B build -S . && cmake --build build\n"
        f"Original error: {e}"
    ) from e
