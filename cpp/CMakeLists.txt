cmake_minimum_required(VERSION 3.10)

# Set the project name
project(PaparazziLogParsing)


set(Boost_USE_STATIC_LIBS ON) 
set(Boost_USE_MULTITHREADED ON)  
set(Boost_USE_STATIC_RUNTIME OFF) 


# Find Boost
find_package(Boost REQUIRED COMPONENTS filesystem iostreams)

# Include Boost headers
include_directories(${Boost_INCLUDE_DIRS})

# Add the executable
add_executable(paparazzi_log_parsing fastreader.cpp)

# Link Boost libraries
target_link_libraries(paparazzi_log_parsing Boost::filesystem Boost::iostreams)