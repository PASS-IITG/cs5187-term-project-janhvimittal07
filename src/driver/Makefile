CXX := /opt/homebrew/opt/llvm/bin/clang++
FUZZ_WORK ?= $(HOME)/fuzzwork
DUCKDB_DIR ?= $(FUZZ_WORK)/duckdb

CXXFLAGS = -std=c++17 -O3 -Wall \
           -I$(DUCKDB_DIR)/src/include \
           -I$(DUCKDB_DIR)/third_party/yyjson/include

ASAN_BUILD = $(FUZZ_WORK)/build-asan
COV_BUILD  = $(FUZZ_WORK)/build-cov
MAIN_BUILD = $(FUZZ_WORK)/build-main

ASAN_LIBS = $(ASAN_BUILD)/extension/libduckdb_generated_extension_loader.a \
            $(ASAN_BUILD)/extension/core_functions/libcore_functions_extension.a \
            $(ASAN_BUILD)/extension/parquet/libparquet_extension.a \
            $(ASAN_BUILD)/extension/json/libjson_extension.a \
            $(ASAN_BUILD)/third_party/utf8proc/libduckdb_utf8proc.a \
            $(ASAN_BUILD)/src/libduckdb_static.a \
            -fsanitize=address,undefined -fno-omit-frame-pointer -lc++ -lz

COV_LIBS  = $(COV_BUILD)/extension/libduckdb_generated_extension_loader.a \
            $(COV_BUILD)/extension/core_functions/libcore_functions_extension.a \
            $(COV_BUILD)/extension/parquet/libparquet_extension.a \
            $(COV_BUILD)/extension/json/libjson_extension.a \
            $(COV_BUILD)/third_party/utf8proc/libduckdb_utf8proc.a \
            $(COV_BUILD)/src/libduckdb_static.a \
            -fprofile-instr-generate -fcoverage-mapping -lc++ -lz

.PHONY: all asan cov main clean test

all: asan

asan: driver-asan
	ln -sf driver-asan driver

driver-asan: driver.cpp
	$(CXX) $(CXXFLAGS) driver.cpp $(ASAN_LIBS) -o $@

cov: driver-cov

driver-cov: driver.cpp
	$(CXX) $(CXXFLAGS) driver.cpp $(COV_LIBS) -o $@

test: asan
	python3 test_driver.py

clean:
	rm -f driver-asan driver-cov driver-main driver
