#!/usr/bin/env python3
"""
Benchmark comparing callback-based H1CProtocol vs pull-based parse_request_fast.

This benchmark measures:
1. Simple GET request parsing (headers only)
2. POST request with small body
3. POST request with chunked body
4. Incremental parsing (headers split across multiple feeds)
"""

import time

from gunicorn_h1c import H1CProtocol, parse_request_fast

# Test data
SIMPLE_GET = (
    b"GET / HTTP/1.1\r\nHost: localhost\r\nAccept: */*\r\nUser-Agent: bench/1.0\r\n\r\n"
)

POST_WITH_BODY = (
    b"POST /api/data HTTP/1.1\r\n"
    b"Host: localhost\r\n"
    b"Content-Type: application/json\r\n"
    b"Content-Length: 27\r\n"
    b"\r\n"
    b'{"key": "value", "num": 42}'
)

CHUNKED_REQUEST = (
    b"POST /api/upload HTTP/1.1\r\n"
    b"Host: localhost\r\n"
    b"Transfer-Encoding: chunked\r\n"
    b"\r\n"
    b"10\r\n"  # 16 bytes
    b"0123456789abcdef\r\n"
    b"10\r\n"  # 16 bytes
    b"fedcba9876543210\r\n"
    b"0\r\n\r\n"
)

# Split headers for incremental test
PARTIAL_HEADERS = [
    b"GET /search?q=hello HTTP/1.1\r\n",
    b"Host: localhost\r\n",
    b"Accept: text/html\r\n",
    b"\r\n",
]


def bench_pull_simple_get(iterations: int) -> float:
    """Benchmark pull-based API with simple GET."""
    start = time.perf_counter()
    for _ in range(iterations):
        req = parse_request_fast(SIMPLE_GET)
        _ = req.method
        _ = req.path
        _ = req.headers
    end = time.perf_counter()
    return end - start


def bench_callback_simple_get(iterations: int) -> float:
    """Benchmark callback-based API with simple GET."""
    start = time.perf_counter()
    for _ in range(iterations):
        p = H1CProtocol()
        p.feed(SIMPLE_GET)
        _ = p.method
        _ = p.path
        _ = p.headers
        p.reset()
    end = time.perf_counter()
    return end - start


def bench_callback_simple_get_reuse(iterations: int) -> float:
    """Benchmark callback-based API with parser reuse."""
    p = H1CProtocol()
    start = time.perf_counter()
    for _ in range(iterations):
        p.feed(SIMPLE_GET)
        _ = p.method
        _ = p.path
        _ = p.headers
        p.reset()
    end = time.perf_counter()
    return end - start


def bench_pull_post_body(iterations: int) -> float:
    """Benchmark pull-based API with POST body."""
    start = time.perf_counter()
    for _ in range(iterations):
        req = parse_request_fast(POST_WITH_BODY)
        _ = req.method
        _ = req.content_length
        # Pull-based doesn't parse body
    end = time.perf_counter()
    return end - start


def bench_callback_post_body(iterations: int) -> float:
    """Benchmark callback-based API with POST body."""
    body_parts = []

    def on_body(chunk):
        body_parts.append(chunk)

    p = H1CProtocol(on_body=on_body)
    start = time.perf_counter()
    for _ in range(iterations):
        body_parts.clear()
        p.feed(POST_WITH_BODY)
        _ = p.method
        _ = p.content_length
        p.reset()
    end = time.perf_counter()
    return end - start


def bench_callback_chunked(iterations: int) -> float:
    """Benchmark callback-based API with chunked body."""
    body_parts = []

    def on_body(chunk):
        body_parts.append(chunk)

    p = H1CProtocol(on_body=on_body)
    start = time.perf_counter()
    for _ in range(iterations):
        body_parts.clear()
        p.feed(CHUNKED_REQUEST)
        _ = p.method
        _ = p.is_chunked
        p.reset()
    end = time.perf_counter()
    return end - start


def bench_pull_incremental(iterations: int) -> float:
    """Benchmark pull-based API with incremental parsing."""
    from gunicorn_h1c._parser_fast import IncompleteError

    start = time.perf_counter()
    for _ in range(iterations):
        buffer = bytearray()
        last_len = 0
        for part in PARTIAL_HEADERS:
            buffer.extend(part)
            try:
                req = parse_request_fast(bytes(buffer), last_len)
                _ = req.method
                _ = req.path
                break
            except IncompleteError:
                last_len = len(buffer)
    end = time.perf_counter()
    return end - start


def bench_callback_incremental(iterations: int) -> float:
    """Benchmark callback-based API with incremental parsing."""
    p = H1CProtocol()
    start = time.perf_counter()
    for _ in range(iterations):
        for part in PARTIAL_HEADERS:
            p.feed(part)
        _ = p.method
        _ = p.path
        p.reset()
    end = time.perf_counter()
    return end - start


def format_rate(elapsed: float, iterations: int) -> str:
    """Format rate as requests/sec."""
    rate = iterations / elapsed
    if rate >= 1_000_000:
        return f"{rate / 1_000_000:.2f}M req/s"
    elif rate >= 1_000:
        return f"{rate / 1_000:.2f}K req/s"
    else:
        return f"{rate:.2f} req/s"


def format_time(elapsed: float, iterations: int) -> str:
    """Format time per operation."""
    ns_per_op = (elapsed / iterations) * 1_000_000_000
    if ns_per_op >= 1_000_000:
        return f"{ns_per_op / 1_000_000:.2f}ms"
    elif ns_per_op >= 1_000:
        return f"{ns_per_op / 1_000:.2f}us"
    else:
        return f"{ns_per_op:.2f}ns"


def run_benchmark(name: str, func, iterations: int) -> float:
    """Run a benchmark and print results."""
    elapsed = func(iterations)
    rate = format_rate(elapsed, iterations)
    time_per_op = format_time(elapsed, iterations)
    print(f"  {name:40} {rate:>15}  ({time_per_op}/op)")
    return elapsed


def main():
    """Run all benchmarks."""
    iterations = 500_000

    print("=" * 75)
    print("H1CProtocol Callback vs Pull-based API Benchmark")
    print("=" * 75)
    print(f"Iterations: {iterations:,}")
    print()

    print("Simple GET (headers only):")
    print("-" * 75)
    pull_time = run_benchmark(
        "pull-based (parse_request_fast)", bench_pull_simple_get, iterations
    )
    callback_time = run_benchmark(
        "callback (H1CProtocol, new each time)", bench_callback_simple_get, iterations
    )
    reuse_time = run_benchmark(
        "callback (H1CProtocol, reused)", bench_callback_simple_get_reuse, iterations
    )
    print()

    print("POST with body (Content-Length):")
    print("-" * 75)
    run_benchmark("pull-based (headers only)", bench_pull_post_body, iterations)
    run_benchmark("callback (headers + body)", bench_callback_post_body, iterations)
    print()

    print("Chunked transfer encoding:")
    print("-" * 75)
    run_benchmark(
        "callback (headers + chunked body)", bench_callback_chunked, iterations
    )
    print()

    print("Incremental parsing (headers split across feeds):")
    print("-" * 75)
    run_benchmark("pull-based (buffer + retry)", bench_pull_incremental, iterations)
    run_benchmark("callback (multiple feeds)", bench_callback_incremental, iterations)
    print()

    print("=" * 75)
    print("Summary:")
    print("-" * 75)
    overhead = ((callback_time / pull_time) - 1) * 100
    print(f"  Callback overhead vs pull-based: {overhead:+.1f}%")
    reuse_overhead = ((reuse_time / pull_time) - 1) * 100
    print(f"  Callback overhead (reused parser): {reuse_overhead:+.1f}%")
    print()


if __name__ == "__main__":
    main()
