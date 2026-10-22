UNAME := $(shell uname)
ifeq ($(UNAME),Darwin)
CXX := /opt/homebrew/opt/llvm/bin/clang++
LIBCXX ?= -lc++
else
CXX := clang++
LIBCXX ?= -lstdc++
endif
FUZZ_WORK ?= $(HOME)/fuzzwork
DUCKDB_DIR ?= $(FUZZ_WORK)/duckdb

CXXFLAGS = -std=c++17 -O3 -Wall \
           -I$(DUCKDB_DIR)/src/include \
           -I$(DUCKDB_DIR)/third_party/yyjson/include

ASAN_BUILD = $(FUZZ_WORK)/build-asan
COV_BUILD  = $(FUZZ_WORK)/build-cov
MAIN_BUILD = $(FUZZ_WORK)/build-main

ifeq ($(UNAME),Darwin)
ASAN_LIBS = $(ASAN_BUILD)/extension/libduckdb_generated_extension_loader.a \
            $(ASAN_BUILD)/extension/core_functions/libcore_functions_extension.a \
            $(ASAN_BUILD)/extension/parquet/libparquet_extension.a \
            $(ASAN_BUILD)/extension/json/libjson_extension.a \
            $(ASAN_BUILD)/third_party/utf8proc/libduckdb_utf8proc.a \
            $(ASAN_BUILD)/src/libduckdb_static.a \
            -fsanitize=address,undefined -fno-omit-frame-pointer $(LIBCXX) -lz

COV_LIBS  = $(COV_BUILD)/extension/libduckdb_generated_extension_loader.a \
            $(COV_BUILD)/extension/core_functions/libcore_functions_extension.a \
            $(COV_BUILD)/extension/parquet/libparquet_extension.a \
            $(COV_BUILD)/extension/json/libjson_extension.a \
            $(COV_BUILD)/third_party/utf8proc/libduckdb_utf8proc.a \
            $(COV_BUILD)/src/libduckdb_static.a \
            -fprofile-instr-generate -fcoverage-mapping $(LIBCXX) -lz
else
# DuckDB 2.0 registers statically linked extensions through generated loader objects, so link the same way
# DuckDB's own shell is linked (loader objects first, extension archives after libduckdb_static.a).
ASAN_LIBS = $(ASAN_BUILD)/tools/shell/CMakeFiles/shell.dir/shell_static_extension_loader.c.o \
            $(ASAN_BUILD)/tools/shell/CMakeFiles/shell.dir/__/__/extension/loader/static_extension_autoregister.cpp.o \
            $(ASAN_BUILD)/src/libduckdb_static.a \
            $(ASAN_BUILD)/src/main/http/libduckdb_httplib.a \
            $(ASAN_BUILD)/src/main/extension/libduckdb_loadable_extensions.a \
            $(ASAN_BUILD)/src/main/extension/local_extension_repository/libduckdb_local_extension_repository.a \
            $(ASAN_BUILD)/extension/core_functions/libcore_functions_extension.a \
            $(ASAN_BUILD)/extension/parquet/libparquet_extension.a \
            $(ASAN_BUILD)/extension/json/libjson_extension.a \
            $(ASAN_BUILD)/extension/icu/libicu_extension.a \
            $(ASAN_BUILD)/third_party/utf8proc/libduckdb_utf8proc.a \
            $(ASAN_BUILD)/src/libduckdb_static.a \
            $(ASAN_BUILD)/third_party/zstd/libduckdb_zstd.a \
            -fsanitize=address,undefined -fno-omit-frame-pointer $(LIBCXX) -ldl -lz

COV_LIBS = $(COV_BUILD)/tools/shell/CMakeFiles/shell.dir/shell_static_extension_loader.c.o \
            $(COV_BUILD)/tools/shell/CMakeFiles/shell.dir/__/__/extension/loader/static_extension_autoregister.cpp.o \
            $(COV_BUILD)/src/libduckdb_static.a \
            $(COV_BUILD)/src/main/http/libduckdb_httplib.a \
            $(COV_BUILD)/src/main/extension/libduckdb_loadable_extensions.a \
            $(COV_BUILD)/src/main/extension/local_extension_repository/libduckdb_local_extension_repository.a \
            $(COV_BUILD)/extension/core_functions/libcore_functions_extension.a \
            $(COV_BUILD)/extension/parquet/libparquet_extension.a \
            $(COV_BUILD)/extension/json/libjson_extension.a \
            $(COV_BUILD)/extension/icu/libicu_extension.a \
            $(COV_BUILD)/third_party/utf8proc/libduckdb_utf8proc.a \
            $(COV_BUILD)/src/libduckdb_static.a \
            $(COV_BUILD)/third_party/zstd/libduckdb_zstd.a \
            -fprofile-instr-generate -fcoverage-mapping $(LIBCXX) -ldl -lz
endif

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
