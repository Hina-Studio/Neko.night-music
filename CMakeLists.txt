cmake_minimum_required(VERSION 3.20)
project(ncm_core LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreadedDLL")

option(NCM_PUBLIC "Build without the online/aggregate API classes" OFF)

find_package(pybind11 CONFIG REQUIRED)

set(NCM_SOURCES
    core/src/crypto.cpp
    core/src/http.cpp
    core/src/api.cpp
    core/src/bindings.cpp
)

if(NOT NCM_PUBLIC)
    list(APPEND NCM_SOURCES
        core/src/nextmusic.cpp
        core/src/musicjx.cpp
    )
endif()

pybind11_add_module(ncm_core ${NCM_SOURCES})

target_include_directories(ncm_core PRIVATE
    core/include
    core/third_party
)

target_link_libraries(ncm_core PRIVATE winhttp bcrypt)

target_compile_definitions(ncm_core PRIVATE _WIN32_WINNT=0x0A00 NOMINMAX WIN32_LEAN_AND_MEAN)

if(NCM_PUBLIC)
    target_compile_definitions(ncm_core PRIVATE NCM_PUBLIC)
endif()

if(MSVC)
    target_compile_options(ncm_core PRIVATE /utf-8 /W3 /EHsc)
endif()

set(NCM_OUTPUT_DIR "${CMAKE_SOURCE_DIR}/app" CACHE PATH "Output directory for the compiled extension")

set_target_properties(ncm_core PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${NCM_OUTPUT_DIR}"
    RUNTIME_OUTPUT_DIRECTORY "${NCM_OUTPUT_DIR}"
)
foreach(cfg RELEASE DEBUG RELWITHDEBINFO MINSIZEREL)
    set_target_properties(ncm_core PROPERTIES
        LIBRARY_OUTPUT_DIRECTORY_${cfg} "${NCM_OUTPUT_DIR}"
        RUNTIME_OUTPUT_DIRECTORY_${cfg} "${NCM_OUTPUT_DIR}"
    )
endforeach()
