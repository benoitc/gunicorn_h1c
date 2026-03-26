"""Tests for header validation against request smuggling attacks.

These tests verify that the parsers properly reject requests with
ambiguous or conflicting framing indicators.
"""
import pytest

from gunicorn_h1c import (
    parse_request,
    parse_request_fast,
    H1CProtocol,
    InvalidHeader,
)


class TestDuplicateContentLength:
    """Duplicate Content-Length headers must be rejected."""

    def test_basic_parser_duplicate_cl(self):
        """Basic parser rejects duplicate Content-Length."""
        data = b"POST / HTTP/1.1\r\nContent-Length: 5\r\nContent-Length: 10\r\n\r\n"
        with pytest.raises(InvalidHeader, match="Duplicate Content-Length"):
            parse_request(data)

    def test_fast_parser_duplicate_cl(self):
        """Fast parser rejects duplicate Content-Length."""
        data = b"POST / HTTP/1.1\r\nContent-Length: 5\r\nContent-Length: 10\r\n\r\n"
        with pytest.raises(InvalidHeader, match="Duplicate Content-Length"):
            parse_request_fast(data)

    def test_protocol_duplicate_cl(self):
        """H1CProtocol rejects duplicate Content-Length."""
        data = b"POST / HTTP/1.1\r\nContent-Length: 5\r\nContent-Length: 10\r\n\r\n"
        p = H1CProtocol()
        with pytest.raises(InvalidHeader, match="Duplicate Content-Length"):
            p.feed(data)

    def test_duplicate_cl_same_value(self):
        """Even identical duplicate Content-Length values are rejected."""
        data = b"POST / HTTP/1.1\r\nContent-Length: 5\r\nContent-Length: 5\r\n\r\n"
        with pytest.raises(InvalidHeader, match="Duplicate Content-Length"):
            parse_request(data)

    def test_duplicate_cl_same_value_fast(self):
        """Fast parser also rejects identical duplicate Content-Length."""
        data = b"POST / HTTP/1.1\r\nContent-Length: 5\r\nContent-Length: 5\r\n\r\n"
        with pytest.raises(InvalidHeader, match="Duplicate Content-Length"):
            parse_request_fast(data)


class TestContentLengthWithTransferEncoding:
    """CL+TE conflict must be rejected (request smuggling vector)."""

    def test_basic_parser_cl_te_conflict(self):
        """Basic parser rejects CL with Transfer-Encoding."""
        data = b"POST / HTTP/1.1\r\nContent-Length: 5\r\nTransfer-Encoding: chunked\r\n\r\n"
        with pytest.raises(InvalidHeader, match="Content-Length with Transfer-Encoding"):
            parse_request(data)

    def test_fast_parser_cl_te_conflict(self):
        """Fast parser rejects CL with Transfer-Encoding."""
        data = b"POST / HTTP/1.1\r\nContent-Length: 5\r\nTransfer-Encoding: chunked\r\n\r\n"
        with pytest.raises(InvalidHeader, match="Content-Length with Transfer-Encoding"):
            parse_request_fast(data)

    def test_protocol_cl_te_conflict(self):
        """H1CProtocol rejects CL with Transfer-Encoding."""
        data = b"POST / HTTP/1.1\r\nContent-Length: 5\r\nTransfer-Encoding: chunked\r\n\r\n"
        p = H1CProtocol()
        with pytest.raises(InvalidHeader, match="Content-Length with Transfer-Encoding"):
            p.feed(data)

    def test_te_before_cl(self):
        """Order doesn't matter - TE before CL is also rejected."""
        data = b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\nContent-Length: 5\r\n\r\n"
        with pytest.raises(InvalidHeader, match="Content-Length with Transfer-Encoding"):
            parse_request(data)

    def test_te_before_cl_fast(self):
        """Fast parser: TE before CL also rejected."""
        data = b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\nContent-Length: 5\r\n\r\n"
        with pytest.raises(InvalidHeader, match="Content-Length with Transfer-Encoding"):
            parse_request_fast(data)


class TestChunkedInHTTP10:
    """Chunked Transfer-Encoding in HTTP/1.0 must be rejected."""

    def test_basic_parser_chunked_http10(self):
        """Basic parser rejects chunked in HTTP/1.0."""
        data = b"POST / HTTP/1.0\r\nTransfer-Encoding: chunked\r\n\r\n"
        with pytest.raises(InvalidHeader, match="[Cc]hunked.*HTTP/1.0"):
            parse_request(data)

    def test_fast_parser_chunked_http10(self):
        """Fast parser rejects chunked in HTTP/1.0."""
        data = b"POST / HTTP/1.0\r\nTransfer-Encoding: chunked\r\n\r\n"
        with pytest.raises(InvalidHeader, match="[Cc]hunked.*HTTP/1.0"):
            parse_request_fast(data)

    def test_protocol_chunked_http10(self):
        """H1CProtocol rejects chunked in HTTP/1.0."""
        data = b"POST / HTTP/1.0\r\nTransfer-Encoding: chunked\r\n\r\n"
        p = H1CProtocol()
        with pytest.raises(InvalidHeader, match="[Cc]hunked.*HTTP/1.0"):
            p.feed(data)


class TestStackedChunkedEncoding:
    """Stacked chunked encoding must be rejected."""

    def test_basic_parser_stacked_chunked(self):
        """Basic parser rejects stacked chunked encoding."""
        data = b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked, chunked\r\n\r\n"
        with pytest.raises(InvalidHeader, match="[Ss]tacked"):
            parse_request(data)

    def test_fast_parser_stacked_chunked(self):
        """Fast parser rejects stacked chunked encoding."""
        data = b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked, chunked\r\n\r\n"
        with pytest.raises(InvalidHeader, match="[Ss]tacked"):
            parse_request_fast(data)

    def test_protocol_stacked_chunked(self):
        """H1CProtocol rejects stacked chunked encoding."""
        data = b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked, chunked\r\n\r\n"
        p = H1CProtocol()
        with pytest.raises(InvalidHeader, match="[Ss]tacked"):
            p.feed(data)

    def test_stacked_chunked_multiple_headers(self):
        """Multiple T-E headers with chunked is also stacked."""
        data = b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\nTransfer-Encoding: chunked\r\n\r\n"
        with pytest.raises(InvalidHeader, match="[Ss]tacked"):
            parse_request(data)

    def test_stacked_chunked_multiple_headers_fast(self):
        """Fast parser: multiple T-E headers with chunked is stacked."""
        data = b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\nTransfer-Encoding: chunked\r\n\r\n"
        with pytest.raises(InvalidHeader, match="[Ss]tacked"):
            parse_request_fast(data)


class TestValidRequests:
    """Valid requests should still be accepted."""

    def test_single_content_length(self):
        """Single Content-Length header is valid."""
        data = b"POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 5\r\n\r\nHello"
        result = parse_request(data)
        assert result["method"] == b"POST"

    def test_single_content_length_fast(self):
        """Fast parser: single Content-Length is valid."""
        data = b"POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 5\r\n\r\nHello"
        req = parse_request_fast(data)
        assert req.method == b"POST"
        assert req.content_length == 5

    def test_chunked_http11(self):
        """Chunked encoding in HTTP/1.1 is valid."""
        data = b"POST / HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: chunked\r\n\r\n"
        result = parse_request(data)
        assert result["method"] == b"POST"

    def test_chunked_http11_fast(self):
        """Fast parser: chunked in HTTP/1.1 is valid."""
        data = b"POST / HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: chunked\r\n\r\n"
        req = parse_request_fast(data)
        assert req.method == b"POST"
        assert req.has_chunked

    def test_gzip_chunked(self):
        """gzip, chunked Transfer-Encoding is valid."""
        data = b"POST / HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: gzip, chunked\r\n\r\n"
        result = parse_request(data)
        assert result["method"] == b"POST"

    def test_gzip_chunked_fast(self):
        """Fast parser: gzip, chunked is valid."""
        data = b"POST / HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: gzip, chunked\r\n\r\n"
        req = parse_request_fast(data)
        assert req.method == b"POST"
        assert req.has_chunked

    def test_protocol_valid_chunked(self):
        """H1CProtocol: chunked in HTTP/1.1 is valid."""
        data = b"POST / HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: chunked\r\n\r\n"
        p = H1CProtocol()
        p.feed(data)
        assert p.method == b"POST"
        assert p.is_chunked

    def test_protocol_full_chunked_request(self):
        """H1CProtocol: complete chunked request works."""
        chunks = []
        p = H1CProtocol(on_body=lambda c: chunks.append(c))
        p.feed(b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n")
        p.feed(b"5\r\nHello\r\n")
        p.feed(b"0\r\n\r\n")
        assert chunks == [b"Hello"]
        assert p.is_complete


class TestChunkSizeValidation:
    """Chunk size must be strictly validated."""

    def test_protocol_invalid_chunk_hex(self):
        """Invalid hex in chunk size is rejected."""
        p = H1CProtocol()
        p.feed(b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n")
        with pytest.raises(Exception):  # ParseError
            p.feed(b"GG\r\n")

    def test_protocol_empty_chunk_size(self):
        """Empty chunk size line is rejected."""
        p = H1CProtocol()
        p.feed(b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n")
        with pytest.raises(Exception):  # ParseError
            p.feed(b"\r\n")

    def test_protocol_whitespace_in_chunk_size(self):
        """Leading whitespace in chunk size is rejected."""
        p = H1CProtocol()
        p.feed(b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n")
        with pytest.raises(Exception):  # ParseError
            p.feed(b" 5\r\n")

    def test_protocol_valid_chunk_extension(self):
        """Chunk extension after semicolon is allowed."""
        chunks = []
        p = H1CProtocol(on_body=lambda c: chunks.append(c))
        p.feed(b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n")
        p.feed(b"5;ext=value\r\nHello\r\n")
        p.feed(b"0\r\n\r\n")
        assert chunks == [b"Hello"]
