"""Framing and memory-safety fixes from the 0.6.10 review."""

import subprocess
import sys
import textwrap

import pytest
from gunicorn_h1c._parser_fast import ParseError as FastParseError
from gunicorn_h1c._protocol import ParseError as ProtocolParseError

from gunicorn_h1c import (
    H1CProtocol,
    InvalidHeader,
    ParseError,
    parse_request,
    parse_request_fast,
)

REJECTED = (ParseError, FastParseError, ProtocolParseError)

CHUNKED_HEAD = b"POST / HTTP/1.1\r\nHost: a\r\nTransfer-Encoding: chunked\r\n\r\n"


def run_isolated(code):
    """Run code in a child interpreter; return its exit code.

    Used for cases that crashed the interpreter before the fix, so a
    regression shows up as a failed test instead of a dead test run.
    """
    proc = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        timeout=60,
    )
    return proc.returncode, proc.stderr.decode(errors="replace")


def cl_request(value):
    return b"POST / HTTP/1.1\r\nHost: a\r\nContent-Length: " + value + b"\r\n\r\n"


class TestLengthOverflow:
    """A length that does not fit must be rejected, never wrapped around.

    RFC 9110 section 8.6 (Content-Length = 1*DIGIT) and RFC 9112 section 7.1:
    recipients MUST anticipate potentially large numerals and prevent
    parsing errors due to integer conversion overflows.
    """

    TOO_BIG = [
        b"9223372036854775808",  # PY_SSIZE_T_MAX + 1
        b"18446744073709551616",  # 2**64, wrapped to 0 before
        b"99999999999999999999999999999999",
    ]

    @pytest.mark.parametrize("value", TOO_BIG)
    def test_protocol_rejects_content_length_overflow(self, value):
        p = H1CProtocol()
        with pytest.raises(InvalidHeader):
            p.feed(cl_request(value) + b"GET /second HTTP/1.1\r\n\r\n")

    @pytest.mark.parametrize("value", TOO_BIG)
    @pytest.mark.parametrize(
        "func", [parse_request, parse_request_fast], ids=["parse", "fast"]
    )
    def test_parsers_reject_content_length_overflow(self, func, value):
        with pytest.raises(REJECTED):
            func(cl_request(value))

    def test_largest_content_length_is_accepted(self):
        p = H1CProtocol()
        p.feed(cl_request(b"9223372036854775807") + b"abc")
        assert p.content_length == 9223372036854775807
        assert not p.is_complete

    def test_leading_zeros_are_fine(self):
        body = []
        p = H1CProtocol(on_body=body.append)
        p.feed(cl_request(b"0000000000000000000000000003") + b"abc")
        assert p.is_complete
        assert body == [b"abc"]

    @pytest.mark.parametrize(
        "size", [b"10000000000000000", b"fffffffffffffffff", b"1" * 40]
    )
    def test_chunk_size_overflow_is_rejected(self, size):
        p = H1CProtocol()
        with pytest.raises(ProtocolParseError, match="chunk size"):
            p.feed(CHUNKED_HEAD + size + b"\r\n0\r\n\r\nGET /x HTTP/1.1\r\n\r\n")

    def test_largest_chunk_size_is_accepted(self):
        p = H1CProtocol()
        p.feed(CHUNKED_HEAD + b"ffffffffffffffff\r\nabc")
        assert not p.is_complete

    def test_chunk_size_leading_zeros_are_fine(self):
        body = []
        p = H1CProtocol(on_body=body.append)
        p.feed(CHUNKED_HEAD + b"00000000000000000000005\r\nhello\r\n0\r\n\r\n")
        assert p.is_complete
        assert body == [b"hello"]


class TestObsoleteLineFolding:
    """A header line starting with SP or HTAB is obsolete line folding.

    RFC 9112 section 5.2: a server must either reject the message or replace
    each fold with SP. We reject it on every entry point, like gunicorn does
    unless permit_obsolete_folding is set (which already disables the fast
    parser in gunicorn).
    """

    FOLDED = [
        b"POST / HTTP/1.1\r\nHost: a\r\nTransfer-Encoding: gzip\r\n ,chunked\r\n"
        b"Content-Length: 3\r\n\r\nabc",
        b"GET / HTTP/1.1\r\nHost: a\r\nX-A: 1\r\n\t2\r\n\r\n",
        b"GET / HTTP/1.1\r\nHost: a\r\nX-A: 1\r\n    \r\n\r\n",
    ]

    ENTRY_POINTS = [
        "parse_request",
        "parse_request_fast",
        "parse_request_raw",
        "parse_to_wsgi_environ",
        "parse_to_asgi_scope",
    ]

    @pytest.mark.parametrize("data", FOLDED)
    @pytest.mark.parametrize("entry", ENTRY_POINTS)
    def test_request_parsers_reject_folding(self, entry, data):
        import gunicorn_h1c

        func = getattr(gunicorn_h1c, entry)
        with pytest.raises(REJECTED):
            func(data)

    @pytest.mark.parametrize("data", FOLDED)
    @pytest.mark.parametrize("bytewise", [False, True], ids=["whole", "bytewise"])
    def test_protocol_rejects_folding(self, data, bytewise):
        p = H1CProtocol()
        step = 1 if bytewise else len(data)
        with pytest.raises(InvalidHeader, match="folding"):
            for i in range(0, len(data), step):
                p.feed(data[i : i + step])

    def test_parse_headers_rejects_folding(self):
        from gunicorn_h1c import parse_headers

        with pytest.raises(REJECTED):
            parse_headers(b"Host: a\r\nX-A: 1\r\n 2\r\n\r\n")

    def test_whitespace_inside_a_value_is_fine(self):
        p = H1CProtocol()
        p.feed(b"GET / HTTP/1.1\r\nHost: a\r\nX-A: 1 2\t3\r\n\r\n")
        assert p.is_complete
        assert p.headers == [(b"Host", b"a"), (b"X-A", b"1 2\t3")]
