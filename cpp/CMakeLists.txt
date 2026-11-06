cmake_minimum_required(VERSION 3.10)
project(PaparazziLogParsing C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

find_package(Threads REQUIRED)

add_executable(paparazzi_log_parsing fastreader.cpp export_columns.cpp)
target_link_libraries(paparazzi_log_parsing Threads::Threads)
if(MSVC)
  target_compile_options(paparazzi_log_parsing PRIVATE /utf-8 /EHsc)
endif()

# The Blackbox decoder is only needed by the Web UI for Betaflight/INDIFlight logs, not for MATLAB conversions
option(BUILD_BLACKBOX "Build blackbox_decode for .bfl/.bbl logs" ON)
if(NOT BUILD_BLACKBOX)
  return()
endif()

set(BLACKBOX_SRC
  blackbox-tools/src/blackbox_decode.c
  blackbox-tools/src/blackbox_fielddefs.c
  blackbox-tools/src/decoders.c
  blackbox-tools/src/parser.c
  blackbox-tools/src/platform.c
  blackbox-tools/src/stats.c
  blackbox-tools/src/stream.c
  blackbox-tools/src/tools.c
  blackbox-tools/src/units.c)
add_executable(blackbox_decode ${BLACKBOX_SRC})
target_include_directories(blackbox_decode PRIVATE blackbox-tools/src)
target_compile_definitions(blackbox_decode PRIVATE _DEFAULT_SOURCE)
target_link_libraries(blackbox_decode Threads::Threads)
if(NOT WIN32)
  target_link_libraries(blackbox_decode m)
endif()
# Vendored upstream code is kept unchanged; silence its two known harmless warnings
target_compile_options(blackbox_decode PRIVATE
  $<$<C_COMPILER_ID:GNU>:-Wno-discarded-qualifiers -Wno-unused-result>
  $<$<C_COMPILER_ID:Clang,AppleClang>:-Wno-incompatible-pointer-types-discards-qualifiers -Wno-unused-result>)
