
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
