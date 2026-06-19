cmake_minimum_required(VERSION 3.20)
project(cpp_cmake_fuzz CXX)
set(CMAKE_CXX_STANDARD 17)

# A standalone build of a fuzz target. -O0 -fno-inline keeps the call graph
# faithful so the static reachability set is easy to reason about.
add_executable(fuzz_target src/target.cpp)
target_compile_options(fuzz_target PRIVATE -g -O0 -fno-inline)
