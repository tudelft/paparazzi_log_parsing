cmake_minimum_required(VERSION 3.10)
project(PaparazziLogParsing CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

add_executable(paparazzi_log_parsing fastreader.cpp)
find_package(Threads REQUIRED)
target_link_libraries(paparazzi_log_parsing Threads::Threads)
