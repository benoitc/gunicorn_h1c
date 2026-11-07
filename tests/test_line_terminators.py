"""Line terminators: requests must end every line with CRLF.

RFC 9112 section 2.2 lets a recipient accept a single LF as a line
terminator. gunicorn_h1c does not, so that it accepts and rejects the same
bytes as gunicorn's pure-Python parsers. A bare CR is never valid there either
(RFC 9112 section 2.2: a recipient must treat it as invalid). The chunk-size
line (section 7.1) and the trailer section (section 7.1.2) follow the same rule.
"""

import pytest
from gunicorn_h1c._parser_fast import IncompleteError as FastIncompleteError
from gunicorn_h1c._parser_fast import ParseError as FastParseError
from gunicorn_h1c._protocol import ParseError as ProtocolParseError

from gunicorn_h1c import (
    H1CProtocol,
    IncompleteError,
    InvalidChunkExtension,
    ParseError,
    parse_headers,
    parse_request,
    parse_request_fast,
    parse_request_raw,
    parse_response,
    parse_to_asgi_scope,
    parse_to_wsgi_environ,
)

REJECTED = (ParseError, FastParseError, ProtocolParseError, InvalidChunkExtension)
INCOMPLETE = (IncompleteError, FastIncompleteError)


def feed_protocol(data):
    p = H1CProtocol()
    p.feed(data)
    return p


def feed_protocol_bytewise(data):
    p = H1CProtocol()
    for i in range(len(data)):
        p.feed(data[i : i + 1])
    return p


REQUEST_ENTRY_POINTS = {
    "parse_request": parse_request,
    "parse_request_fast": parse_request_fast,
    "parse_request_raw": parse_request_raw,
    "parse_to_wsgi_environ": parse_to_wsgi_environ,
    "parse_to_asgi_scope": parse_to_asgi_scope,
    "H1CProtocol.feed": feed_protocol,
    "H1CProtocol.feed bytewise": feed_protocol_bytewise,
}

BAD_REQUESTS = {
    "all bare LF": b"GET / HTTP/1.1\nHost: a\nX: 1\n\n",
    "LF after request line": b"GET / HTTP/1.1\nHost: a\r\n\r\n",
    "LF inside header block": b"GET / HTTP/1.1\r\nHost: a\nX: 1\r\n\r\n",
    "LF ends last header": b"GET / HTTP/1.1\r\nHost: a\n\r\n",
    "LF ends header section": b"GET / HTTP/1.1\r\nHost: a\r\n\n",
    "leading bare LF": b"\nGET / HTTP/1.1\r\nHost: a\r\n\r\n",
    "CR after request line": b"GET / HTTP/1.1\rHost: a\r\n\r\n",
    "CR inside header block": b"GET / HTTP/1.1\r\nHost: a\rX: 1\r\n\r\n",
    "CR before header end": b"GET / HTTP/1.1\r\nHost: a\r\n\r\r\n",
    "leading bare CR": b"\rGET / HTTP/1.1\r\nHost: a\r\n\r\n",
}

GOOD_REQUESTS = {
    "simple": b"GET / HTTP/1.1\r\nHost: a\r\nX: 1\r\n\r\n",
    "leading CRLF": b"\r\nGET / HTTP/1.1\r\nHost: a\r\n\r\n",
    "no headers": b"GET / HTTP/1.0\r\n\r\n",
    "tab in value": b"GET / HTTP/1.1\r\nHost: a\r\nX: a\tb\r\n\r\n",
}


@pytest.mark.parametrize("entry", REQUEST_ENTRY_POINTS)
@pytest.mark.parametrize("case", BAD_REQUESTS)
def test_request_rejects_bare_lf_and_cr(entry, case):
    with pytest.raises(REJECTED):
        REQUEST_ENTRY_POINTS[entry](BAD_REQUESTS[case])


@pytest.mark.parametrize("entry", REQUEST_ENTRY_POINTS)
@pytest.mark.parametrize("case", GOOD_REQUESTS)
def test_request_accepts_crlf(entry, case):
    result = REQUEST_ENTRY_POINTS[entry](GOOD_REQUESTS[case])
    if isinstance(result, H1CProtocol):
        assert result.is_complete


@pytest.mark.parametrize(
    "func",
    [parse_request, parse_request_fast, parse_request_raw],
    ids=["parse_request", "parse_request_fast", "parse_request_raw"],
)
def test_incremental_parse_rejects_bare_lf(func):
    """The last_len fast path must not let a bare LF through."""
    first = b"GET / HTTP/1.1\r\nHost: a\r\n"
    with pytest.raises(INCOMPLETE):
        func(first)
    with pytest.raises(REJECTED):
        func(first + b"X: 1\n\n", last_len=len(first))


def test_partial_request_with_bare_lf_fails_early():
    """No need to wait for more data once a bare LF has been seen."""
    with pytest.raises(REJECTED):
        parse_request(b"GET / HTTP/1.1\nHost: a")


def test_bare_lf_error_message_names_the_problem():
    with pytest.raises(ParseError, match="LF"):
        parse_request(b"GET / HTTP/1.1\nHost: a\n\n")


@pytest.mark.parametrize(
    "data",
    [
        b"Host: a\nX: 1\n\n",
        b"Host: a\r\nX: 1\n\r\n",
        b"Host: a\r\n\n",
        b"Host: a\rX: 1\r\n\r\n",
    ],
)
def test_parse_headers_rejects_bare_lf_and_cr(data):
    with pytest.raises(REJECTED):
        parse_headers(data)


def test_parse_headers_accepts_crlf():
    assert parse_headers(b"Host: a\r\nX: 1\r\n\r\n") == [(b"Host", b"a"), (b"X", b"1")]


def test_parse_response_still_accepts_bare_lf():
    """Only requests are strict; the response parser keeps the RFC 9112
    section 2.2 leniency."""
    result = parse_response(b"HTTP/1.1 200 OK\nContent-Length: 0\n\n")
    assert result["status"] == 200


CHUNKED_HEAD = b"POST / HTTP/1.1\r\nHost: a\r\nTransfer-Encoding: chunked\r\n\r\n"

BAD_CHUNKED = {
    "LF ends chunk-size line": b"5\nhello\r\n0\r\n\r\n",
    "LF inside chunk-ext": b"5;a\nb\r\nhello\r\n0\r\n\r\n",
    "LF ends chunk-ext": b"5;a=b\nhello\r\n0\r\n\r\n",
    "LF ends last-chunk": b"0\n\r\n",
    "LF inside trailer line": b"0\r\nX: 1\nY: 2\r\n\r\n",
    "LF ends trailer line": b"0\r\nX: 1\n\r\n",
    "LF ends trailer section": b"0\r\nX: 1\r\n\n",
    "LF only after last-chunk": b"0\r\n\n",
    "CR inside chunk-ext": b"5;a\rb\r\nhello\r\n0\r\n\r\n",
    "CR inside trailer line": b"0\r\nX: 1\rY: 2\r\n\r\n",
}

GOOD_CHUNKED = {
    "plain": b"5\r\nhello\r\n0\r\n\r\n",
    "extension": b"5;name=value\r\nhello\r\n0\r\n\r\n",
    "trailer": b"5\r\nhello\r\n0\r\nX-Checksum: abc\r\n\r\n",
}


@pytest.mark.parametrize("bytewise", [False, True], ids=["whole", "bytewise"])
@pytest.mark.parametrize("case", BAD_CHUNKED)
def test_chunked_rejects_bare_lf_and_cr(case, bytewise):
    feed = feed_protocol_bytewise if bytewise else feed_protocol
    with pytest.raises(REJECTED):
        feed(CHUNKED_HEAD + BAD_CHUNKED[case])


@pytest.mark.parametrize("bytewise", [False, True], ids=["whole", "bytewise"])
@pytest.mark.parametrize("case", GOOD_CHUNKED)
def test_chunked_accepts_crlf(case, bytewise):
    body = []
    p = H1CProtocol(on_body=body.append)
    data = CHUNKED_HEAD + GOOD_CHUNKED[case]
    step = 1 if bytewise else len(data)
    for i in range(0, len(data), step):
        p.feed(data[i : i + step])
    assert p.is_complete
    assert b"".join(body) == b"hello"
