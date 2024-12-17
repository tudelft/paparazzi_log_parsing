cmake_minimum_required(VERSION 3.10)

# Set the project name
project(PaparazziLogParsing)


set(CMAKE_CXX_STANDARD 17)


set(Boost_USE_STATIC_LIBS ON) 
set(Boost_USE_MULTITHREADED ON)  
set(Boost_USE_STATIC_RUNTIME OFF) 

# Add a define flag
add_definitions(-fconcepts)

set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# Find Boost
find_package(Boost REQUIRED COMPONENTS filesystem iostreams system)

# Include Boost headers
include_directories(${Boost_INCLUDE_DIRS})

# Find pprzlink
find_package(pprzlink++ REQUIRED)

# Include pprzlink headers
include_directories(${pprzlink++_INCLUDE_DIRS})

# Add the executable
add_executable(paparazzi_log_parsing fastreader.cpp)

# Link Boost libraries
target_link_libraries(paparazzi_log_parsing Boost::filesystem Boost::iostreams Boost::system pprzlink++)
