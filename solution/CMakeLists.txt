add_executable(evaluator main.c)
target_link_libraries(evaluator err)
target_link_libraries(evaluator pthread)
target_link_libraries(evaluator rt)

add_compile_definitions(ORDERED_OUTPUT=1)
