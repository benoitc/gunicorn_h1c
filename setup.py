"""
Build script for gunicorn_h1c C extensions.
"""
import os
import platform
from setuptools import setup, Extension

# Compiler flags for optimization
extra_compile_args = ["-O3"]

# Platform-specific SIMD flags
if platform.machine() in ("x86_64", "AMD64", "i686", "i386"):
    # x86: Enable SSE4.2 for fast string operations
    extra_compile_args.append("-msse4.2")
elif platform.machine() in ("arm64", "aarch64"):
    # ARM64: NEON is enabled by default on aarch64
    pass

# Base path for sources
src_path = os.path.join("src", "gunicorn_h1c")
pico_path = os.path.join(src_path, "picohttpparser")

# Basic parser module
pico_parser = Extension(
    "gunicorn_h1c._parser",
    sources=[
        os.path.join(src_path, "pico_parser_module.c"),
        os.path.join(pico_path, "picohttpparser.c"),
    ],
    include_dirs=[pico_path],
    extra_compile_args=extra_compile_args,
)

# Fast/optimized parser module
pico_parser_fast = Extension(
    "gunicorn_h1c._parser_fast",
    sources=[
        os.path.join(src_path, "pico_parser_fast.c"),
        os.path.join(pico_path, "picohttpparser.c"),
    ],
    include_dirs=[pico_path],
    extra_compile_args=extra_compile_args,
)

# Callback-based protocol parser module
pico_protocol = Extension(
    "gunicorn_h1c._protocol",
    sources=[
        os.path.join(src_path, "pico_protocol.c"),
        os.path.join(pico_path, "picohttpparser.c"),
    ],
    include_dirs=[pico_path],
    extra_compile_args=extra_compile_args,
)

setup(
    ext_modules=[pico_parser, pico_parser_fast, pico_protocol],
)
