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


def te_request(value, extra=b""):
    return (
        b"POST / HTTP/1.1\r\nHost: a\r\nTransfer-Encoding: "
        + value
        + b"\r\n"
        + extra
        + b"\r\n"
    )


class TestTransferEncoding:
    """Transfer-Encoding is handled exactly as gunicorn's Python parsers do
    (RFC 9112 section 6.1): an empty coding is unsupported, and a compression
    coding without chunked closes the connection so that its unread body is
    never parsed as another request."""

    EMPTY = [b"", b" ", b"gzip, ", b",chunked", b"chunked,,", b"gzip,,chunked"]

    @pytest.mark.parametrize("value", EMPTY)
    def test_empty_coding_is_rejected(self, value):
        p = H1CProtocol()
        with pytest.raises(InvalidHeader, match="Transfer-Encoding"):
            p.feed(te_request(value))

    @pytest.mark.parametrize("value", EMPTY)
    @pytest.mark.parametrize(
        "func", [parse_request, parse_request_fast], ids=["parse", "fast"]
    )
    def test_parsers_reject_empty_coding(self, func, value):
        with pytest.raises(REJECTED):
            func(te_request(value))

    @pytest.mark.parametrize("coding", [b"gzip", b"deflate", b"compress", b"GZIP"])
    def test_compression_without_chunked_closes(self, coding):
        p = H1CProtocol()
        p.feed(te_request(coding) + b"GET /hidden HTTP/1.1\r\nHost: a\r\n\r\n")
        assert p.is_complete
        assert not p.should_keep_alive

    @pytest.mark.parametrize("where", ["before", "after"])
    def test_keep_alive_does_not_override_compression(self, where):
        keep = b"Connection: keep-alive\r\n"
        if where == "before":
            data = (
                b"POST / HTTP/1.1\r\nHost: a\r\n"
                + keep
                + b"Transfer-Encoding: gzip\r\n\r\n"
            )
        else:
            data = te_request(b"gzip", keep)
        p = H1CProtocol()
        p.feed(data)
        assert not p.should_keep_alive

    def test_compression_then_chunked_parses_and_closes(self):
        body = []
        p = H1CProtocol(on_body=body.append)
        p.feed(te_request(b"gzip, chunked") + b"3\r\nabc\r\n0\r\n\r\n")
        assert p.is_complete
        assert body == [b"abc"]
        assert not p.should_keep_alive

    def test_chunked_keeps_alive(self):
        p = H1CProtocol()
        p.feed(te_request(b"chunked") + b"0\r\n\r\n")
        assert p.is_complete
        assert p.should_keep_alive

    def test_identity_with_content_length_uses_the_length(self):
        """Same as gunicorn: identity is accepted and Content-Length frames."""
        body = []
        p = H1CProtocol(on_body=body.append)
        p.feed(te_request(b"identity", b"Content-Length: 3\r\n") + b"abc")
        assert p.is_complete
        assert body == [b"abc"]


class TestWsgiEnvironNames:
    """parse_to_wsgi_environ must not merge two different header names.

    "X-Auth-User" and "X_Auth_User" both map to HTTP_X_AUTH_USER, so a client
    could append its own value to one a proxy set. Names with "_" are
    dropped, like gunicorn's default header_map = "drop".
    """

    def environ(self, headers):
        from gunicorn_h1c import parse_to_wsgi_environ

        return parse_to_wsgi_environ(
            b"GET / HTTP/1.1\r\nHost: a\r\n" + headers + b"\r\n"
        )

    def test_underscore_name_does_not_merge(self):
        env = self.environ(b"X-Auth-User: trusted\r\nX_Auth_User: attacker\r\n")
        assert env["HTTP_X_AUTH_USER"] == "trusted"

    def test_underscore_name_alone_is_dropped(self):
        env = self.environ(b"X_Auth_User: attacker\r\n")
        assert "HTTP_X_AUTH_USER" not in env

    def test_underscore_cannot_reach_content_length(self):
        env = self.environ(b"Content_Length: 5\r\n")
        assert "CONTENT_LENGTH" not in env
        assert "HTTP_CONTENT_LENGTH" not in env

    def test_long_names_are_not_truncated(self):
        a = b"X-" + b"a" * 300 + b"-One"
        b = b"X-" + b"a" * 300 + b"-Two"
        env = self.environ(a + b": 1\r\n" + b + b": 2\r\n")
        assert env["HTTP_X_" + "A" * 300 + "_ONE"] == "1"
        assert env["HTTP_X_" + "A" * 300 + "_TWO"] == "2"

    def test_repeated_header_is_joined(self):
        env = self.environ(b"Accept: a\r\nAccept: b\r\n")
        assert env["HTTP_ACCEPT"] == "a,b"

    def test_content_type_and_host(self):
        env = self.environ(b"Content-Type: text/plain\r\n")
        assert env["CONTENT_TYPE"] == "text/plain"
        assert env["HTTP_HOST"] == "a"


class TestProtocolCallbackSafety:
    """H1CProtocol must not crash whatever a callback or caller does with it.

    Each case runs in a child interpreter: before the fix they killed it with
    SIGSEGV or SIGBUS.
    """

    PRELUDE = """
        import gunicorn_h1c as g
        CHUNKED = (b"POST / HTTP/1.1\\r\\nHost: a\\r\\n"
                   b"Transfer-Encoding: chunked\\r\\n\\r\\n"
                   b"5\\r\\nhello\\r\\n5\\r\\nworld\\r\\n0\\r\\n\\r\\n")
        GET = b"GET / HTTP/1.1\\r\\nHost: a\\r\\nX-A: 1\\r\\n\\r\\n"
        def expect(exc, func):
            try:
                func()
            except exc:
                return
            raise SystemExit("no %s raised" % exc.__name__)
    """

    CASES = {
        "headers read in on_header": """
            seen = []
            def on_header(name, value):
                seen.append((p.headers, p.asgi_headers, p.get_header(b"host")))
            p = g.H1CProtocol(on_header=on_header)
            p.feed(GET)
            assert p.is_complete and len(seen) == 2
            assert seen[0][0] == [(b"Host", b"a"), (b"X-A", b"1")]
            assert seen[0][2] == b"a"
        """,
        "reset in on_header": """
            p = None
            def on_header(name, value):
                p.reset()
            p = g.H1CProtocol(on_header=on_header)
            expect(RuntimeError, lambda: p.feed(GET))
        """,
        "reset in on_body": """
            p = None
            p = g.H1CProtocol(on_body=lambda chunk: p.reset())
            expect(RuntimeError, lambda: p.feed(CHUNKED))
        """,
        "feed in on_body": """
            p = None
            p = g.H1CProtocol(on_body=lambda chunk: p.feed(b"x" * 100000))
            expect(RuntimeError, lambda: p.feed(CHUNKED))
        """,
        "finish in on_message_complete": """
            p = None
            p = g.H1CProtocol(on_message_complete=lambda: p.finish())
            expect(RuntimeError, lambda: p.feed(GET))
        """,
        "reset after callback error still works": """
            p = None
            p = g.H1CProtocol(on_header=lambda n, v: p.reset())
            expect(RuntimeError, lambda: p.feed(GET))
            p.reset()
            p2 = g.H1CProtocol()
            p2.feed(GET)
            assert p2.is_complete
        """,
        "headers after a framing error": """
            p = g.H1CProtocol()
            expect(g.InvalidHeader, lambda: p.feed(
                b"POST / HTTP/1.1\\r\\nHost: a\\r\\n"
                b"Content-Length: 1\\r\\nContent-Length: 2\\r\\n\\r\\n"))
            assert p.headers == []
            assert p.asgi_headers == []
            assert p.get_header(b"host") is None
        """,
        "mutated headers list": """
            p = g.H1CProtocol()
            p.feed(GET)
            h = p.headers
            h.clear()
            h.append(1)
            if h is p.headers:  # GIL build: the list is the parser's own
                expect(TypeError, lambda: p.asgi_headers)
                expect(TypeError, lambda: p.get_header(b"host"))
        """,
        "headers list shrunk": """
            p = g.H1CProtocol()
            p.feed(GET)
            h = p.headers
            del h[:]
            if h is p.headers:  # GIL build: the list is the parser's own
                assert p.get_header(b"host") is None
                assert p.asgi_headers == []
            else:  # free-threaded build: headers is a copy
                assert p.get_header(b"host") == b"a"
        """,
    }

    @pytest.mark.parametrize("case", CASES)
    def test_no_crash(self, case):
        code = textwrap.dedent(self.PRELUDE) + textwrap.dedent(self.CASES[case])
        rc, err = run_isolated(code)
        assert rc == 0, err[-2000:]


class TestLastLenBounds:
    """last_len is an offset into the data, so it must lie inside it."""

    FUNCS = [
        ("parse_request", b"GET / HTTP/1.1\r\nHost: a\r\n"),
        ("parse_request_fast", b"GET / HTTP/1.1\r\nHost: a\r\n"),
        ("parse_request_raw", b"GET / HTTP/1.1\r\nHost: a\r\n"),
        ("parse_response", b"HTTP/1.1 200 OK\r\nX: 1\r\n"),
        ("parse_headers", b"Host: a\r\nX: 1\r\n"),
    ]

    @pytest.mark.parametrize("last_len", [-1, -5, 10**9, 2**62])
    @pytest.mark.parametrize("name,data", FUNCS, ids=[f[0] for f in FUNCS])
    def test_out_of_range_last_len_is_refused(self, name, data, last_len):
        code = f"""
            import gunicorn_h1c as g
            try:
                g.{name}({data!r}, last_len={last_len})
            except ValueError:
                raise SystemExit(0)
            raise SystemExit("no ValueError")
        """
        rc, err = run_isolated(code)
        assert rc == 0, err[-2000:]

    @pytest.mark.parametrize("name,data", FUNCS, ids=[f[0] for f in FUNCS])
    def test_last_len_up_to_the_length_is_fine(self, name, data):
        from gunicorn_h1c._parser_fast import IncompleteError as FastIncomplete

        import gunicorn_h1c

        func = getattr(gunicorn_h1c, name)
        for last_len in (0, 1, len(data) - 1, len(data)):
            with pytest.raises((gunicorn_h1c.IncompleteError, FastIncomplete)):
                func(data, last_len=last_len)
