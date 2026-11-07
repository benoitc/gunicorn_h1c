"""Concurrency checks for free-threaded CPython support."""

import sys
import sysconfig
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from gunicorn_h1c import H1CProtocol, parse_request_fast


def test_import_does_not_enable_gil():
    if not sysconfig.get_config_var("Py_GIL_DISABLED"):
        pytest.skip("requires a free-threaded CPython build")

    assert not sys._is_gil_enabled()


def _read_request(request, barrier):
    barrier.wait()
    return request.method, request.path, request.headers, request.asgi_headers


def test_fast_request_lazy_properties_are_thread_safe():
    request = parse_request_fast(
        b"GET /items HTTP/1.1\r\nHost: example.com\r\nX-Test: value\r\n\r\n"
    )
    workers = 16
    barrier = Barrier(workers)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(
            executor.map(lambda _: _read_request(request, barrier), range(workers))
        )

    first = results[0]
    assert all(result[0] is first[0] for result in results)
    assert all(result[1] is first[1] for result in results)
    assert all(result[2] is first[2] for result in results)
    assert all(result[3] is first[3] for result in results)


def _read_protocol(protocol, barrier):
    barrier.wait()
    return (
        protocol.method,
        protocol.path,
        protocol.headers,
        protocol.asgi_headers,
        protocol.get_header(b"host"),
    )


def test_protocol_properties_are_safe_for_concurrent_readers():
    protocol = H1CProtocol()
    protocol.feed(b"GET /items HTTP/1.1\r\nHost: example.com\r\n\r\n")
    workers = 16
    barrier = Barrier(workers)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(
            executor.map(lambda _: _read_protocol(protocol, barrier), range(workers))
        )

    assert all(result[0] == b"GET" for result in results)
    assert all(result[1] == b"/items" for result in results)
    assert all(result[2] == [(b"Host", b"example.com")] for result in results)
    assert all(result[3] == [(b"host", b"example.com")] for result in results)
    assert all(result[4] == b"example.com" for result in results)


def _reset_and_feed(protocol, barrier, iterations):
    barrier.wait()
    for _ in range(iterations):
        protocol.reset()
        protocol.feed(b"GET /items HTTP/1.1\r\nHost: example.com\r\n\r\n")


def _read_while_feeding(protocol, barrier, iterations):
    barrier.wait()
    for _ in range(iterations):
        method = protocol.method
        path = protocol.path
        headers = protocol.headers
        asgi_headers = protocol.asgi_headers
        assert method in (b"", b"GET")
        assert path in (b"", b"/items")
        assert isinstance(headers, list)
        assert isinstance(asgi_headers, list)


def test_protocol_reset_and_properties_are_serialized():
    protocol = H1CProtocol()
    protocol.feed(b"GET /items HTTP/1.1\r\nHost: example.com\r\n\r\n")
    readers = 4
    barrier = Barrier(readers + 1)

    with ThreadPoolExecutor(max_workers=readers + 1) as executor:
        futures = [
            executor.submit(_reset_and_feed, protocol, barrier, 1_000),
            *(
                executor.submit(_read_while_feeding, protocol, barrier, 2_000)
                for _ in range(readers)
            ),
        ]
        for future in futures:
            future.result()
