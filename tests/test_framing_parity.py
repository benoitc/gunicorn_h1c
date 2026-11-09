"""Chunk extensions and Transfer-Encoding, checked as gunicorn's Python
parsers check them, on every entry point and however the request is split.

RFC 9112 section 7.1.1 (chunk extension grammar, BWS before ";") and section
6.3 (a request's Transfer-Encoding must end with chunked).
"""

import functools

import pytest
from gunicorn_h1c._parser_fast import ParseError as FastParseError
from gunicorn_h1c._protocol import ParseError as ProtocolParseError

import gunicorn_h1c
from gunicorn_h1c import (
    H1CProtocol,
    InvalidChunkExtension,
    InvalidHeader,
    ParseError,
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


# 2. BWS before the first ";"

BWS_ACCEPTED = {
    "SP before ;": b"1 ;a",
    "HTAB before ;": b"1\t;a",
    "BWS everywhere": b"1 ; a = b",
}
WHITESPACE_REJECTED = {
    "trailing SP": b"1 ",
    "leading SP": b" 1",
    "SP inside size": b"1 2;a",
}


@BYTEWISE
@pytest.mark.parametrize("size_line", BWS_ACCEPTED.values(), ids=BWS_ACCEPTED.keys())
def test_bws_before_first_semicolon_accepted(size_line, bytewise):
    p, body = feed(chunked(size_line), bytewise)
    assert p.is_complete
    assert body == b"x"


@pytest.mark.parametrize(
    "size_line", WHITESPACE_REJECTED.values(), ids=WHITESPACE_REJECTED.keys()
)
def test_whitespace_without_extension_rejected(size_line):
    with pytest.raises(ProtocolParseError, match="chunk size"):
        feed(chunked(size_line))


# 3 and 4. Transfer-Encoding


def post(*te_lines, extra=b"", body=b"", version=b"1.1"):
    head = b"POST / HTTP/" + version + b"\r\nHost: a\r\n"
    for te in te_lines:
        head += b"Transfer-Encoding: " + te + b"\r\n"
    return head + extra + b"\r\n" + body


BODY = b"1\r\nx\r\n0\r\n\r\n"

ONE_SHOT = [
    "parse_request",
    "parse_request_fast",
    "parse_to_wsgi_environ",
    "parse_to_asgi_scope",
]
REJECTED = (ParseError, FastParseError, ProtocolParseError)


def all_entry_points(data):
    """Yield (name, call) for every entry point that parses a request head."""
    for name in ONE_SHOT:
        yield name, functools.partial(getattr(gunicorn_h1c, name), data)
    for bytewise in (False, True):
        yield (
            f"H1CProtocol bytewise={bytewise}",
            functools.partial(feed, data, bytewise),
        )


NOT_ENDING_WITH_CHUNKED = {
    "gzip": post(b"gzip"),
    "deflate": post(b"deflate"),
    "compress": post(b"compress"),
    "identity": post(b"identity"),
    "gzip, identity": post(b"gzip, identity"),
    "gzip with Content-Length": post(
        b"gzip", extra=b"Content-Length: 3\r\n", body=b"abc"
    ),
    "chunked then gzip on two lines": post(b"chunked", b"gzip", body=BODY),
}

ENDING_WITH_CHUNKED = {
    "chunked": post(b"chunked", body=BODY),
    "gzip, chunked": post(b"gzip, chunked", body=BODY),
    "deflate, chunked": post(b"deflate, chunked", body=BODY),
    "identity, chunked": post(b"identity, chunked", body=BODY),
    "gzip then chunked on two lines": post(b"gzip", b"chunked", body=BODY),
}

STAYS_400 = {
    "stacked chunked": post(b"chunked, chunked", body=BODY),
    "coding after chunked": post(b"chunked, gzip", body=BODY),
    "chunked on HTTP/1.0": post(b"chunked", body=BODY, version=b"1.0"),
    "Content-Length with chunked": post(
        b"chunked", extra=b"Content-Length: 3\r\n", body=BODY
    ),
}


@pytest.mark.parametrize(
    "data", NOT_ENDING_WITH_CHUNKED.values(), ids=NOT_ENDING_WITH_CHUNKED.keys()
)
def test_transfer_encoding_not_ending_with_chunked_rejected(data):
    for name, call in all_entry_points(data):
        with pytest.raises(InvalidHeader):
            call()
            pytest.fail(name)


@pytest.mark.parametrize(
    "data", ENDING_WITH_CHUNKED.values(), ids=ENDING_WITH_CHUNKED.keys()
)
def test_transfer_encoding_ending_with_chunked_accepted(data):
    for _name, call in all_entry_points(data):
        call()


def test_not_ending_with_chunked_message():
    with pytest.raises(InvalidHeader, match="does not end with chunked"):
        feed(post(b"gzip"))


@pytest.mark.parametrize("data", STAYS_400.values(), ids=STAYS_400.keys())
def test_other_transfer_encoding_errors_stay_invalid_header(data):
    for name, call in all_entry_points(data):
        with pytest.raises(InvalidHeader):
            call()
            pytest.fail(name)
