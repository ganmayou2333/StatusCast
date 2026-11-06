cmake_minimum_required(VERSION 3.16)
project(statuscast_esp32_host_test C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

# The core sources are compiled twice in this repo: once into the firmware and
# once here. They deliberately have no ESP-IDF dependency.
set(CORE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/../main/core)

add_executable(sc_host_test
    test_main.c
    ${CORE_DIR}/sc_core.c
    ${CORE_DIR}/sc_config.c
    ${CORE_DIR}/sc_json.c
    ${CORE_DIR}/sc_snapshot.c
    ${CORE_DIR}/vendor/cjson/cJSON.c
)

target_include_directories(sc_host_test PRIVATE
    ${CORE_DIR}
    ${CORE_DIR}/vendor/cjson
)

target_compile_definitions(sc_host_test PRIVATE
    SC_CONTRACT_PATH="${CMAKE_CURRENT_SOURCE_DIR}/../../../schema/status-contract.json"
    SC_FIXTURE_DIR="${CMAKE_CURRENT_SOURCE_DIR}/fixtures"
)

if(MSVC)
    target_compile_options(sc_host_test PRIVATE /W3)
else()
    target_compile_options(sc_host_test PRIVATE -Wall -Wextra -Wno-unused-parameter)
endif()

enable_testing()
add_test(NAME sc_host_test COMMAND sc_host_test)
