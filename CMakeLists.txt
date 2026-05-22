cmake_minimum_required(VERSION 3.18)
project(lob_engine LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# ── Build type ────────────────────────────────────────────────────────────────
# Use RelWithDebInfo so pybind11 skips its post-build strip command.
# The strip call uses /bin/sh -c '...' and the apostrophe in "Marc's Market
# Maker" breaks the shell quoting. We compensate with -O3 flags below.
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE RelWithDebInfo)
endif()
set(CMAKE_CXX_FLAGS_RELWITHDEBINFO "-O3 -march=native -DNDEBUG")

# ── pybind11 ──────────────────────────────────────────────────────────────────
find_package(Python3 COMPONENTS Interpreter Development REQUIRED)
execute_process(
  COMMAND "${Python3_EXECUTABLE}" -m pybind11 --cmakedir
  OUTPUT_VARIABLE PYBIND11_CMAKE_DIR
  OUTPUT_STRIP_TRAILING_WHITESPACE
)
list(APPEND CMAKE_PREFIX_PATH "${PYBIND11_CMAKE_DIR}")
find_package(pybind11 REQUIRED)

# ── Python extension module ───────────────────────────────────────────────────
pybind11_add_module(lob_engine
    engine/lob.cpp
    engine/bindings.cpp
)
target_include_directories(lob_engine PRIVATE engine)

# Output the .so into build/ (apostrophe in project path breaks shell quoting
# if we try to set LIBRARY_OUTPUT_DIRECTORY to the source tree directly).
# Python code adds build/ to sys.path via engine/__init__.py.

# ── Benchmark executable ──────────────────────────────────────────────────────
add_executable(lob_benchmark engine/benchmark.cpp engine/lob.cpp)
target_include_directories(lob_benchmark PRIVATE engine)
set_target_properties(lob_benchmark PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_SOURCE_DIR}/build"
)

# ── GoogleTest ────────────────────────────────────────────────────────────────
include(FetchContent)
FetchContent_Declare(
  googletest
  URL https://github.com/google/googletest/archive/refs/tags/v1.14.0.tar.gz
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE
)
set(gtest_force_shared_crt ON CACHE BOOL "" FORCE)
FetchContent_MakeAvailable(googletest)

enable_testing()
add_executable(test_engine tests/test_engine.cpp engine/lob.cpp)
target_include_directories(test_engine PRIVATE engine)
target_link_libraries(test_engine GTest::gtest_main)
include(GoogleTest)
gtest_discover_tests(test_engine)
set_target_properties(test_engine PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_SOURCE_DIR}/build"
)
