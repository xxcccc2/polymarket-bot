# libpmkernel — vendored from https://github.com/lubluniky/bs-p (polymarket-kernel)
# Builds the native math engine used by src.native.pmkernel
CC ?= cc
CFLAGS = -shared -fPIC -O3 -Ic_src
SRCS = c_src/kernel.c c_src/analytics.c
UNAME := $(shell uname)

ifeq ($(UNAME), Darwin)
  TARGET = libpmkernel.dylib
else
  TARGET = libpmkernel.so
  ifneq ($(wildcard /proc/cpuinfo),)
    ifneq ($(shell grep -c avx512f /proc/cpuinfo),0)
      CFLAGS += -mavx512f
    endif
  endif
endif

all: lib/$(TARGET)

lib/$(TARGET): $(SRCS) c_src/kernel.h c_src/analytics.h
	@mkdir -p lib
	$(CC) $(CFLAGS) -o $@ $(SRCS) -lm

clean:
	rm -f lib/libpmkernel.dylib lib/libpmkernel.so

.PHONY: all clean
