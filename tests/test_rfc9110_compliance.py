"""RFC 9110 / RFC 9112 compliance tests.

Each test pins a specific RFC rule so parser regressions surface as a
named failure. New rules added here must land with the C parser change
that enforces them (TDD workflow).
"""

import pytest
from gunicorn_h1c._parser_fast import ParseError as FastParseError
from gunicorn_h1c._protocol import ParseError as ProtocolParseError

from gunicorn_h1c import (
    H1CProtocol,
    InvalidHeader,
    InvalidHeaderName,
    ParseError,
    parse_request,
    parse_request_fast,
)

FORBIDDEN_TRAILERS = [
    b"Host",
    b"Content-Length",
    b"Transfer-Encoding",
    b"Trailer",
    b"Authorization",
    b"TE",
]

CONTROL_BYTE_IDS = ["BEL-0x07", "FF-0x0c", "VT-0x0b", "DEL-0x7f"]
CONTROL_BYTES = [b"\x07", b"\x0c", b"\x0b", b"\x7f"]


class TestFieldValueControlChars:
    """RFC 9110 section 5.5: only VCHAR, SP, HTAB, obs-text allowed in value."""

    @pytest.mark.parametrize("ctl", CONTROL_BYTES, ids=CONTROL_BYTE_IDS)
    def test_basic_parser_rejects(self, ctl):
        data = (
            b"GET / HTTP/1.1\r\nHost: localhost\r\nX-Value: bad" + ctl + b"char\r\n\r\n"
        )
        with pytest.raises(InvalidHeader):
            parse_request(data)

    @pytest.mark.parametrize("ctl", CONTROL_BYTES, ids=CONTROL_BYTE_IDS)
    def test_fast_parser_rejects(self, ctl):
        data = (
            b"GET / HTTP/1.1\r\nHost: localhost\r\nX-Value: bad" + ctl + b"char\r\n\r\n"
        )
        with pytest.raises(InvalidHeader):
            parse_request_fast(data)

    @pytest.mark.parametrize("ctl", CONTROL_BYTES, ids=CONTROL_BYTE_IDS)
    def test_protocol_rejects(self, ctl):
        data = (
            b"GET / HTTP/1.1\r\nHost: localhost\r\nX-Value: bad" + ctl + b"char\r\n\r\n"
        )
        p = H1CProtocol()
        with pytest.raises(InvalidHeader):
            p.feed(data)


class TestRequestTargetFormMethodPairing:
    """RFC 9112 sections 3.2.3 & 3.2.4: form must match the method."""

    def test_basic_parser_rejects_asterisk_with_get(self):
        data = b"GET * HTTP/1.1\r\nHost: localhost\r\n\r\n"
        with pytest.raises(ParseError):
            parse_request(data)

    def test_fast_parser_rejects_asterisk_with_get(self):
        data = b"GET * HTTP/1.1\r\nHost: localhost\r\n\r\n"
        with pytest.raises(FastParseError):
            parse_request_fast(data)

    def test_protocol_rejects_asterisk_with_get(self):
        data = b"GET * HTTP/1.1\r\nHost: localhost\r\n\r\n"
        p = H1CProtocol()
        with pytest.raises(ProtocolParseError):
            p.feed(data)

    def test_basic_parser_rejects_authority_with_get(self):
        data = b"GET example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n"
        with pytest.raises(ParseError):
            parse_request(data)

    def test_fast_parser_rejects_authority_with_get(self):
        data = b"GET example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n"
        with pytest.raises(FastParseError):
            parse_request_fast(data)

    def test_protocol_rejects_authority_with_get(self):
        data = b"GET example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n"
        p = H1CProtocol()
        with pytest.raises(ProtocolParseError):
            p.feed(data)

    def test_basic_parser_rejects_relative_target(self):
        data = b"GET foo/bar HTTP/1.1\r\nHost: localhost\r\n\r\n"
        with pytest.raises(ParseError):
            parse_request(data)

    def test_fast_parser_rejects_relative_target(self):
        data = b"GET foo/bar HTTP/1.1\r\nHost: localhost\r\n\r\n"
        with pytest.raises(FastParseError):
            parse_request_fast(data)

    def test_protocol_rejects_relative_target(self):
        data = b"GET foo/bar HTTP/1.1\r\nHost: localhost\r\n\r\n"
        p = H1CProtocol()
        with pytest.raises(ProtocolParseError):
            p.feed(data)

    def test_options_asterisk_still_valid(self):
        data = b"OPTIONS * HTTP/1.1\r\nHost: localhost\r\n\r\n"
        result = parse_request(data)
        assert result["method"] == b"OPTIONS"
        assert result["path"] == b"*"

    def test_connect_authority_still_valid(self):
        data = b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n"
        result = parse_request(data)
        assert result["method"] == b"CONNECT"
        assert result["path"] == b"example.com:443"


class TestForbiddenTrailerFields:
    """RFC 9110 section 6.5.1: certain headers must not appear in trailers."""

    @pytest.mark.parametrize("name", FORBIDDEN_TRAILERS)
    def test_protocol_rejects(self, name):
        data = (
            b"POST /p HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"5\r\nhello\r\n"
            b"0\r\n" + name + b": evil\r\n\r\n"
        )
        p = H1CProtocol()
        with pytest.raises(InvalidHeaderName):
            p.feed(data)

    def test_protocol_accepts_benign_trailer(self):
        data = (
            b"POST /p HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"5\r\nhello\r\n"
            b"0\r\nX-Trace-Id: 123\r\n\r\n"
        )
        p = H1CProtocol()
        p.feed(data)  # no exception


class TestContentLengthListForm:
    """RFC 9112 section 6.3: reject list-form Content-Length (smuggling risk)."""

    def test_basic_parser_rejects(self):
        data = (
            b"POST /p HTTP/1.1\r\nHost: localhost\r\nContent-Length: 5, 5\r\n\r\nhello"
        )
        with pytest.raises(InvalidHeader):
            parse_request(data)

    def test_fast_parser_rejects(self):
        data = (
            b"POST /p HTTP/1.1\r\nHost: localhost\r\nContent-Length: 5, 5\r\n\r\nhello"
        )
        with pytest.raises(InvalidHeader):
            parse_request_fast(data)

    def test_protocol_rejects(self):
        data = (
            b"POST /p HTTP/1.1\r\nHost: localhost\r\nContent-Length: 5, 5\r\n\r\nhello"
        )
        p = H1CProtocol()
        with pytest.raises(InvalidHeader):
            p.feed(data)

    def test_basic_parser_rejects_mismatch(self):
        data = (
            b"POST /p HTTP/1.1\r\nHost: localhost\r\nContent-Length: 5, 6\r\n\r\nhello"
        )
        with pytest.raises(InvalidHeader):
            parse_request(data)
