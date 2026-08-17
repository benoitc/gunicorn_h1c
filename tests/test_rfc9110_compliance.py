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

# (request, field named in the error) for fields whose grammar admits a single
# member. Transfer-Encoding is absent on purpose: repeats there are legal.
SINGLETON_REPEAT_IDS = ["Host", "Content-Type", "host-vs-HOST", "content-TYPE"]
SINGLETON_REPEATS = [
    (
        b"GET /p HTTP/1.1\r\nHost: a.example\r\nHost: b.example\r\n\r\n",
        "Duplicate Host header",
    ),
    (
        b"POST /p HTTP/1.1\r\nHost: localhost\r\n"
        b"Content-Type: text/plain\r\nContent-Type: application/json\r\n\r\n",
        "Duplicate Content-Type header",
    ),
    (
        b"GET /p HTTP/1.1\r\nhost: a.example\r\nHOST: b.example\r\n\r\n",
        "Duplicate Host header",
    ),
    (
        b"POST /p HTTP/1.1\r\nHost: localhost\r\n"
        b"content-type: text/plain\r\nContent-TYPE: application/json\r\n\r\n",
        "Duplicate Content-Type header",
    ),
]


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


class TestSingletonFieldRepeats:
    """RFC 9110 section 5.3: singleton fields must not be repeated.

    Host, Content-Length, and Content-Type each have a grammar admitting only
    one member, so a repeat cannot be merged into a list and leaves the message
    ambiguous. Duplicate Host is a routing- and cache-confusion vector.
    """

    @pytest.mark.parametrize(
        ("data", "message"), SINGLETON_REPEATS, ids=SINGLETON_REPEAT_IDS
    )
    def test_basic_parser_rejects(self, data, message):
        with pytest.raises(InvalidHeader, match=message):
            parse_request(data)

    @pytest.mark.parametrize(
        ("data", "message"), SINGLETON_REPEATS, ids=SINGLETON_REPEAT_IDS
    )
    def test_fast_parser_rejects(self, data, message):
        with pytest.raises(InvalidHeader, match=message):
            parse_request_fast(data)

    @pytest.mark.parametrize(
        ("data", "message"), SINGLETON_REPEATS, ids=SINGLETON_REPEAT_IDS
    )
    def test_protocol_rejects(self, data, message):
        p = H1CProtocol()
        with pytest.raises(InvalidHeader, match=message):
            p.feed(data)

    def test_single_host_accepted(self):
        data = b"GET /p HTTP/1.1\r\nHost: a.example\r\n\r\n"
        result = parse_request(data)
        assert (b"Host", b"a.example") in result["headers"]

    def test_single_content_type_accepted(self):
        data = (
            b"POST /p HTTP/1.1\r\nHost: localhost\r\nContent-Type: text/plain\r\n"
            b"Content-Length: 5\r\n\r\nhello"
        )
        req = parse_request_fast(data)
        assert req.get_header("content-type") == b"text/plain"
        assert req.content_length == 5

    def test_protocol_single_occurrence_accepted(self):
        data = (
            b"POST /p HTTP/1.1\r\nHost: a.example\r\nContent-Type: text/plain\r\n"
            b"Content-Length: 5\r\n\r\nhello"
        )
        p = H1CProtocol()
        p.feed(data)
        assert (b"Host", b"a.example") in p.headers
        assert (b"Content-Type", b"text/plain") in p.headers

    def test_repeated_transfer_encoding_accepted(self):
        """Transfer-Encoding is not a singleton; framing logic owns it."""
        data = (
            b"POST /p HTTP/1.1\r\nHost: localhost\r\n"
            b"Transfer-Encoding: gzip\r\nTransfer-Encoding: chunked\r\n\r\n"
        )
        result = parse_request(data)
        assert result["method"] == b"POST"

    def test_repeated_transfer_encoding_accepted_protocol(self):
        data = (
            b"POST /p HTTP/1.1\r\nHost: localhost\r\n"
            b"Transfer-Encoding: gzip\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"0\r\n\r\n"
        )
        p = H1CProtocol()
        p.feed(data)  # no exception

    def test_repeated_list_valued_field_accepted(self):
        """Ordinary list-valued fields repeat legitimately and must pass."""
        data = (
            b"GET /p HTTP/1.1\r\nHost: localhost\r\n"
            b"Accept: text/html\r\nAccept: application/json\r\n"
            b"Via: 1.1 alpha\r\nVia: 1.1 beta\r\n\r\n"
        )
        result = parse_request(data)
        assert (b"Accept", b"text/html") in result["headers"]
        assert (b"Accept", b"application/json") in result["headers"]
        assert (b"Via", b"1.1 alpha") in result["headers"]
        assert (b"Via", b"1.1 beta") in result["headers"]
