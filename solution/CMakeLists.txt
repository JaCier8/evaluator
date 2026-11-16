
# Adding cpp
enable_language(CXX)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(evaluator
        main.cpp)

target_link_libraries(evaluator err)
target_link_libraries(evaluator pthread)
target_link_libraries(evaluator rt)

add_compile_definitions(ORDERED_OUTPUT=1)

# Benchmark variant B: sem_pol released at the end of each iteration
# instead of right after the policy answers (see bench/RESULTS.md).
add_executable(evaluator_late main.cpp)
target_link_libraries(evaluator_late err pthread rt)
target_compile_definitions(evaluator_late PRIVATE POL_RELEASE_LATE=1)
