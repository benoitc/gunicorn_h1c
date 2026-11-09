"""Chunk extensions and Transfer-Encoding, checked as gunicorn's Python
parsers check them, on every entry point and however the request is split.

RFC 9112 section 7.1.1 (chunk extension grammar, BWS before ";") and section
6.3 (a request's Transfer-Encoding must end with chunked).
"""

import pytest
from gunicorn_h1c._protocol import ParseError as ProtocolParseError

from gunicorn_h1c import (
    H1CProtocol,
    InvalidChunkExtension,
)

HEAD = b"POST / HTTP/1.1\r\nHost: a\r\nTransfer-Encoding: chunked\r\n\r\n"


def chunked(size_line):
    return HEAD + size_line + b"\r\nx\r\n0\r\n\r\n"


def feed(data, bytewise=False):
    body = []
    p = H1CProtocol(on_body=body.append)
    step = 1 if bytewise else len(data)
    for i in range(0, len(data), step):
        p.feed(data[i : i + step])
    return p, b"".join(body)


BYTEWISE = pytest.mark.parametrize("bytewise", [False, True], ids=["whole", "bytewise"])


# 1. Chunk extension grammar

EXT_INVALID = {
    "space inside a token value": b"1;a=b c",
    "empty name": b"1;=b",
    "no name": b"1;",
    "unclosed quote": b'1;a="b',
    "separator in a name": b"1;a@b",
    "= with no value": b"1;a=",
    "whitespace after the last extension": b"1;a ",
    "control character in name": b"1;a\x01",
    "control character in quoted": b'1;a="b\x01"',
    "DEL in value": b"1;a=b\x7f",
    "quoted-pair with a control character": b'1;a="b\\\x01"',
    "two names": b"1;a b",
}

EXT_VALID = {
    "name": b"1;a",
    "token value": b"1;a=b",
    "quoted value": b'1;a="b c"',
    "quoted pair": b'1;a="b\\"c"',
    "tab in quoted": b'1;a="b\tc"',
    "obs-text in quoted": b'1;a="b\xe9"',
    "several": b'1;a=b;c;d="e"',
    "BWS around =": b"1;a = b",
    "BWS after ;": b"1; a",
    "BWS before a later ;": b"1;a ;b",
    "empty quoted value": b'1;a=""',
}


@BYTEWISE
@pytest.mark.parametrize("size_line", EXT_INVALID.values(), ids=EXT_INVALID.keys())
def test_invalid_extension_rejected(size_line, bytewise):
    with pytest.raises(InvalidChunkExtension):
        feed(chunked(size_line), bytewise)


@BYTEWISE
@pytest.mark.parametrize("size_line", EXT_VALID.values(), ids=EXT_VALID.keys())
def test_valid_extension_accepted(size_line, bytewise):
    p, body = feed(chunked(size_line), bytewise)
    assert p.is_complete
    assert body == b"x"


@pytest.mark.parametrize("size_line", [b"g;a@b", b"1x;a@b", b";a=b"])
def test_size_is_checked_before_the_extension(size_line):
    with pytest.raises(ProtocolParseError) as excinfo:
        feed(chunked(size_line))
    assert not isinstance(excinfo.value, InvalidChunkExtension)
    assert "chunk size" in str(excinfo.value)


def test_long_extension_is_linear():
    """A long, valid extension line parses in one pass."""
    import time

    line = b"1;" + b";".join(b"a%d=b" % i for i in range(1500))
    start = time.perf_counter()
    p = H1CProtocol(on_body=lambda c: None, limit_request_field_size=len(line) + 10)
    p.feed(chunked(line))
    assert p.is_complete
    assert time.perf_counter() - start < 0.5
