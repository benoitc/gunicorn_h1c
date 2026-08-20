"""Tests for H1CProtocol callback-based parser."""

import pytest
from gunicorn_h1c._protocol import ParseError as ProtocolParseError

from gunicorn_h1c import (
    H1CProtocol,
    InvalidChunkExtension,
    LimitRequestHeaders,
    LimitRequestLine,
    ParseError,  # From _parser module
)


class TestH1CProtocolBasic:
    """Basic tests for callback-based parser."""

    def test_simple_get(self):
        """Test parsing a simple GET request."""
        headers_complete = False
        message_complete = False

        def on_headers():
            nonlocal headers_complete
            headers_complete = True

        def on_complete():
            nonlocal message_complete
            message_complete = True

        p = H1CProtocol(
            on_headers_complete=on_headers,
            on_message_complete=on_complete,
        )
        p.feed(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")

        assert headers_complete
        assert message_complete
        assert p.method == b"GET"
        assert p.path == b"/"
        assert p.http_version == (1, 1)
        assert p.is_complete

    def test_post_with_body(self):
        """Test parsing POST with Content-Length body."""
        body_chunks = []
        complete = False

        def on_body(chunk):
            body_chunks.append(chunk)

        def on_complete():
            nonlocal complete
            complete = True

        p = H1CProtocol(
            on_body=on_body,
            on_message_complete=on_complete,
        )
        p.feed(b"POST /api HTTP/1.1\r\nContent-Length: 5\r\n\r\nHello")

        assert complete
        assert body_chunks == [b"Hello"]
        assert p.method == b"POST"
        assert p.content_length == 5

    def test_chunked_body(self):
        """Test parsing chunked transfer encoding."""
        chunks = []
        complete = False

        def on_body(chunk):
            chunks.append(chunk)

        def on_complete():
            nonlocal complete
            complete = True

        p = H1CProtocol(
            on_body=on_body,
            on_message_complete=on_complete,
        )
        p.feed(b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n")
        p.feed(b"5\r\nHello\r\n")
        p.feed(b"0\r\n\r\n")

        assert complete
        assert chunks == [b"Hello"]
        assert p.is_chunked

    def test_partial_headers(self):
        """Headers split across multiple feeds."""
        complete = False

        def on_headers():
            nonlocal complete
            complete = True

        p = H1CProtocol(on_headers_complete=on_headers)

        p.feed(b"GET / HTTP/1.1\r\n")
        assert not complete

        p.feed(b"Host: local")
        assert not complete

        p.feed(b"host\r\n\r\n")
        assert complete
        assert p.method == b"GET"

    def test_http_10(self):
        """Test HTTP/1.0 parsing."""
        p = H1CProtocol()
        p.feed(b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n")

        assert p.http_version == (1, 0)
        assert not p.should_keep_alive  # HTTP/1.0 defaults to close

    def test_http_11_keep_alive(self):
        """Test HTTP/1.1 default keep-alive."""
        p = H1CProtocol()
        p.feed(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")

        assert p.http_version == (1, 1)
        assert p.should_keep_alive  # HTTP/1.1 defaults to keep-alive

    def test_connection_close(self):
        """Test Connection: close header."""
        p = H1CProtocol()
        p.feed(b"GET / HTTP/1.1\r\nConnection: close\r\n\r\n")

        assert not p.should_keep_alive

    def test_connection_keep_alive_http10(self):
        """Test Connection: keep-alive with HTTP/1.0."""
        p = H1CProtocol()
        p.feed(b"GET / HTTP/1.0\r\nConnection: keep-alive\r\n\r\n")

        assert p.should_keep_alive


class TestH1CProtocolCallbacks:
    """Test callback invocation order and data."""

    def test_callback_order(self):
        """Test callbacks fire in correct order."""
        events = []

        p = H1CProtocol(
            on_message_begin=lambda: events.append("begin"),
            on_url=lambda url: events.append(f"url:{url}"),
            on_header=lambda n, v: events.append(f"header:{n}:{v}"),
            on_headers_complete=lambda: events.append("headers_complete"),
            on_body=lambda c: events.append(f"body:{c}"),
            on_message_complete=lambda: events.append("message_complete"),
        )
        p.feed(b"POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 5\r\n\r\nHello")

        # Callback order: begin, url, headers, headers_complete, body, message_complete
        assert events == [
            "begin",
            "url:b'/'",
            "header:b'Host':b'localhost'",
            "header:b'Content-Length':b'5'",
            "headers_complete",
            "body:b'Hello'",
            "message_complete",
        ]

    def test_skip_body(self):
        """Test returning True from on_headers_complete skips body."""
        body_received = False

        def on_headers():
            return True  # Skip body

        def on_body(chunk):
            nonlocal body_received
            body_received = True

        p = H1CProtocol(
            on_headers_complete=on_headers,
            on_body=on_body,
        )
        p.feed(b"POST / HTTP/1.1\r\nContent-Length: 5\r\n\r\nHello")

        assert not body_received
        assert p.is_complete

    def test_multiple_headers(self):
        """Test multiple headers are captured."""
        headers = []

        def on_header(name, value):
            headers.append((name, value))

        p = H1CProtocol(on_header=on_header)
        p.feed(
            b"GET / HTTP/1.1\r\nHost: localhost\r\nAccept: */*\r\nUser-Agent: test\r\n\r\n"
        )

        assert len(headers) == 3
        assert headers[0] == (b"Host", b"localhost")
        assert headers[1] == (b"Accept", b"*/*")
        assert headers[2] == (b"User-Agent", b"test")


class TestH1CProtocolChunked:
    """Test chunked transfer encoding parsing."""

    def test_multiple_chunks(self):
        """Test parsing multiple chunks."""
        chunks = []

        p = H1CProtocol(on_body=lambda c: chunks.append(c))
        p.feed(b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n")
        p.feed(b"5\r\nHello\r\n")
        p.feed(b"6\r\n World\r\n")
        p.feed(b"0\r\n\r\n")

        assert chunks == [b"Hello", b" World"]

    def test_chunk_split_across_feeds(self):
        """Test chunk data split across multiple feeds."""
        chunks = []

        p = H1CProtocol(on_body=lambda c: chunks.append(c))
        p.feed(b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n")
        p.feed(b"b\r\n")  # 11 bytes = "Hello World"
        p.feed(b"Hello")
        p.feed(b" Worl")
        p.feed(b"d\r\n")  # last byte of chunk + CRLF
        p.feed(b"0\r\n\r\n")

        assert b"".join(chunks) == b"Hello World"

    def test_chunk_size_with_extension(self):
        """Test chunk size with extension (ignored)."""
        chunks = []

        p = H1CProtocol(on_body=lambda c: chunks.append(c))
        p.feed(b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n")
        p.feed(b"5;ext=value\r\nHello\r\n")
        p.feed(b"0\r\n\r\n")

        assert chunks == [b"Hello"]

    def test_chunk_with_trailers(self):
        """Test chunked encoding with trailers (ignored)."""
        complete = False

        def on_complete():
            nonlocal complete
            complete = True

        p = H1CProtocol(on_message_complete=on_complete)
        p.feed(b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n")
        p.feed(b"5\r\nHello\r\n")
        p.feed(b"0\r\nX-Trailer: value\r\n\r\n")

        assert complete


class TestH1CProtocolReset:
    """Test parser reset for keepalive."""

    def test_reset(self):
        """Test reset clears state for next request."""
        p = H1CProtocol()
        p.feed(b"GET /first HTTP/1.1\r\nHost: localhost\r\n\r\n")

        assert p.path == b"/first"
        assert p.is_complete

        p.reset()

        assert not p.is_complete
        assert p.method == b""
        assert p.path == b""

        p.feed(b"POST /second HTTP/1.1\r\nHost: localhost\r\n\r\n")

        assert p.method == b"POST"
        assert p.path == b"/second"
        assert p.is_complete

    def test_keepalive_sequence(self):
        """Test multiple requests on same parser."""
        methods = []

        def on_headers():
            methods.append(p.method)

        p = H1CProtocol(on_headers_complete=on_headers)

        # First request
        p.feed(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        assert p.is_complete
        p.reset()

        # Second request
        p.feed(b"POST /api HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n")
        assert p.is_complete
        p.reset()

        # Third request
        p.feed(b"DELETE /resource HTTP/1.1\r\nHost: localhost\r\n\r\n")
        assert p.is_complete

        assert methods == [b"GET", b"POST", b"DELETE"]


class TestH1CProtocolFinish:
    """Test finish() for EOF handling."""

    def test_finish_chunked_in_trailer_state(self):
        """Test finish() completes request when in chunked trailer state."""
        chunks = []
        complete = False

        def on_body(chunk):
            chunks.append(chunk)

        def on_complete():
            nonlocal complete
            complete = True

        p = H1CProtocol(
            on_body=on_body,
            on_message_complete=on_complete,
        )
        # Send chunked request without final CRLF after zero-chunk
        p.feed(b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n")
        p.feed(b"5\r\nHello\r\n")
        p.feed(b"0\r\n")  # Zero chunk but no final CRLF

        assert not complete  # Not complete yet - waiting for final CRLF
        assert not p.is_complete

        p.finish()  # Signal EOF

        assert complete
        assert p.is_complete
        assert chunks == [b"Hello"]

    def test_finish_no_effect_when_complete(self):
        """Test finish() is no-op when already complete."""
        complete_count = 0

        def on_complete():
            nonlocal complete_count
            complete_count += 1

        p = H1CProtocol(on_message_complete=on_complete)
        p.feed(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")

        assert complete_count == 1
        assert p.is_complete

        p.finish()  # Should be no-op

        assert complete_count == 1  # Not called again

    def test_finish_no_effect_during_headers(self):
        """Test finish() is no-op during header parsing."""
        p = H1CProtocol()
        p.feed(b"GET / HTTP/1.1\r\n")  # Incomplete headers

        assert not p.is_complete

        p.finish()

        assert not p.is_complete  # Still incomplete


class TestH1CProtocolProperties:
    """Test property accessors."""

    def test_headers_list(self):
        """Test headers property returns list of tuples."""
        p = H1CProtocol()
        p.feed(b"GET / HTTP/1.1\r\nHost: localhost\r\nAccept: */*\r\n\r\n")

        headers = p.headers
        assert isinstance(headers, list)
        assert len(headers) == 2
        assert headers[0] == (b"Host", b"localhost")
        assert headers[1] == (b"Accept", b"*/*")

    def test_get_header(self):
        """Test case-insensitive header lookup."""
        p = H1CProtocol()
        p.feed(b"GET / HTTP/1.1\r\nHost: localhost\r\nContent-Type: text/plain\r\n\r\n")

        assert p.get_header(b"Host") == b"localhost"
        assert p.get_header(b"host") == b"localhost"
        assert p.get_header(b"HOST") == b"localhost"
        assert p.get_header(b"Content-Type") == b"text/plain"
        assert p.get_header(b"content-type") == b"text/plain"
        assert p.get_header(b"X-Missing") is None

    def test_asgi_headers_lowercase(self):
        """Test asgi_headers returns headers with lowercase names."""
        p = H1CProtocol()
        p.feed(b"GET / HTTP/1.1\r\nHost: localhost\r\nContent-Type: text/plain\r\n\r\n")

        # Regular headers preserve original case
        assert p.headers[0] == (b"Host", b"localhost")
        assert p.headers[1] == (b"Content-Type", b"text/plain")

        # ASGI headers have lowercase names
        asgi_headers = p.asgi_headers
        assert asgi_headers[0] == (b"host", b"localhost")
        assert asgi_headers[1] == (b"content-type", b"text/plain")
        assert isinstance(asgi_headers, list)

    def test_content_length(self):
        """Test content_length property."""
        p = H1CProtocol()
        p.feed(b"POST / HTTP/1.1\r\nContent-Length: 100\r\n\r\n")

        assert p.content_length == 100

    def test_content_length_none(self):
        """Test content_length is None when not set."""
        p = H1CProtocol()
        p.feed(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")

        assert p.content_length is None

    def test_should_upgrade(self):
        """Test should_upgrade property."""
        p = H1CProtocol()
        p.feed(b"GET / HTTP/1.1\r\nUpgrade: websocket\r\n\r\n")

        assert p.should_upgrade


class TestH1CProtocolErrors:
    """Test error handling."""

    def test_invalid_request(self):
        """Test invalid HTTP request raises ParseError."""
        p = H1CProtocol()

        with pytest.raises(ProtocolParseError):
            p.feed(b"INVALID\r\n\r\n")

    def test_invalid_chunk_size(self):
        """Test invalid chunk size raises ParseError."""
        p = H1CProtocol()
        p.feed(b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n")

        with pytest.raises(ProtocolParseError):
            p.feed(b"ZZZZ\r\n")  # Invalid hex

    def test_parse_error_has_status_code(self):
        """Test ParseError has status_code attribute."""
        p = H1CProtocol()

        with pytest.raises(ProtocolParseError):
            p.feed(b"INVALID\r\n\r\n")


class TestH1CProtocolBodyParsing:
    """Test body parsing edge cases."""

    def test_zero_content_length(self):
        """Test Content-Length: 0 completes immediately."""
        complete = False

        def on_complete():
            nonlocal complete
            complete = True

        p = H1CProtocol(on_message_complete=on_complete)
        p.feed(b"POST / HTTP/1.1\r\nContent-Length: 0\r\n\r\n")

        assert complete

    def test_body_split_across_feeds(self):
        """Test body data split across multiple feeds."""
        chunks = []

        p = H1CProtocol(on_body=lambda c: chunks.append(c))
        p.feed(b"POST / HTTP/1.1\r\nContent-Length: 10\r\n\r\nHello")
        p.feed(b"World")

        assert b"".join(chunks) == b"HelloWorld"

    def test_body_received_after_headers(self):
        """Test body arrives after headers in separate feed."""
        headers_complete = False
        body_chunks = []

        def on_headers():
            nonlocal headers_complete
            headers_complete = True

        p = H1CProtocol(
            on_headers_complete=on_headers,
            on_body=lambda c: body_chunks.append(c),
        )

        p.feed(b"POST / HTTP/1.1\r\nContent-Length: 5\r\n\r\n")
        assert headers_complete
        assert body_chunks == []

        p.feed(b"Hello")
        assert body_chunks == [b"Hello"]


class TestH1CProtocolQueryString:
    """Test path with query string."""

    def test_path_includes_query(self):
        """Test path includes query string."""
        p = H1CProtocol()
        p.feed(b"GET /search?q=hello&page=1 HTTP/1.1\r\nHost: localhost\r\n\r\n")

        assert p.path == b"/search?q=hello&page=1"

    def test_on_url_receives_full_path(self):
        """Test on_url callback receives path with query."""
        url_received = None

        def on_url(url):
            nonlocal url_received
            url_received = url

        p = H1CProtocol(on_url=on_url)
        p.feed(b"GET /api?key=value HTTP/1.1\r\nHost: localhost\r\n\r\n")

        assert url_received == b"/api?key=value"


class TestH1CProtocolChunkExtensionValidation:
    """Test chunk extension validation per RFC 9112."""

    def test_bare_cr_in_chunk_extension_rejected(self):
        """Test chunk extension with bare CR is rejected."""
        p = H1CProtocol()
        p.feed(b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n")

        with pytest.raises(InvalidChunkExtension) as exc_info:
            p.feed(b"1;\r\r\na\r\n0\r\n\r\n")

        assert "bare CR" in str(exc_info.value)

    def test_bare_cr_in_chunk_extension_value_rejected(self):
        """Test chunk extension value with bare CR is rejected."""
        p = H1CProtocol()
        p.feed(b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n")

        with pytest.raises(InvalidChunkExtension) as exc_info:
            p.feed(b"1;ext=val\rue\r\na\r\n0\r\n\r\n")

        assert "bare CR" in str(exc_info.value)

    def test_valid_chunk_extension_accepted(self):
        """Test valid chunk extension is accepted."""
        chunks = []

        p = H1CProtocol(on_body=lambda c: chunks.append(c))
        p.feed(b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n")
        p.feed(b"5;ext=value\r\nHello\r\n0\r\n\r\n")

        assert chunks == [b"Hello"]
        assert p.is_complete

    def test_multiple_chunk_extensions_accepted(self):
        """Test multiple chunk extensions are accepted."""
        chunks = []

        p = H1CProtocol(on_body=lambda c: chunks.append(c))
        p.feed(b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n")
        p.feed(b"5;ext1=a;ext2=b\r\nHello\r\n0\r\n\r\n")

        assert chunks == [b"Hello"]
        assert p.is_complete

    def test_chunk_without_extension_accepted(self):
        """Test chunk without extension works normally."""
        chunks = []

        p = H1CProtocol(on_body=lambda c: chunks.append(c))
        p.feed(b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n")
        p.feed(b"5\r\nHello\r\n0\r\n\r\n")

        assert chunks == [b"Hello"]
        assert p.is_complete

    def test_invalid_chunk_extension_is_parse_error(self):
        """Test InvalidChunkExtension inherits from ParseError."""
        assert issubclass(InvalidChunkExtension, Exception)
        # It should inherit from ParseError (which inherits from ValueError)
        p = H1CProtocol()
        p.feed(b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n")

        try:
            p.feed(b"1;\r\r\na\r\n0\r\n\r\n")
            pytest.fail("Should have raised InvalidChunkExtension")
        except InvalidChunkExtension:
            pass  # Expected
        except Exception as e:
            pytest.fail(f"Wrong exception type: {type(e).__name__}")


class TestH1CProtocolLimitValidation:
    """Test request limit validation."""

    def test_limit_request_line_exceeded(self):
        """Test LimitRequestLine raised when request line is too long."""
        p = H1CProtocol(limit_request_line=20)

        with pytest.raises(LimitRequestLine):
            # Request line "GET /very/long/path HTTP/1.1" exceeds 20 bytes
            p.feed(b"GET /very/long/path HTTP/1.1\r\nHost: localhost\r\n\r\n")

    def test_limit_request_line_at_boundary(self):
        """Test request line at exactly the limit is accepted."""
        # "GET / HTTP/1.1" is 14 bytes
        p = H1CProtocol(limit_request_line=14)
        p.feed(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")

        assert p.is_complete
        assert p.method == b"GET"

    def test_limit_request_fields_exceeded(self):
        """Test LimitRequestHeaders raised when too many headers."""
        p = H1CProtocol(limit_request_fields=2)

        with pytest.raises(LimitRequestHeaders):
            p.feed(
                b"GET / HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Accept: */*\r\n"
                b"User-Agent: test\r\n"  # 3rd header exceeds limit of 2
                b"\r\n"
            )

    def test_limit_request_fields_at_boundary(self):
        """Test number of headers at exactly the limit is accepted."""
        p = H1CProtocol(limit_request_fields=2)
        p.feed(b"GET / HTTP/1.1\r\nHost: localhost\r\nAccept: */*\r\n\r\n")

        assert p.is_complete
        assert len(p.headers) == 2

    def test_limit_request_field_size_exceeded(self):
        """Test LimitRequestHeaders raised when header value is too large."""
        p = H1CProtocol(limit_request_field_size=20)

        with pytest.raises(LimitRequestHeaders):
            p.feed(
                b"GET / HTTP/1.1\r\n"
                b"Host: this-is-a-very-long-hostname.example.com\r\n"
                b"\r\n"
            )

    def test_limit_request_field_size_at_boundary(self):
        """Test header field at exactly the limit is accepted."""
        # "Host" (4) + ": " (2) + "localhost" (9) = 15, plus name "Host" = 4
        # Total header line without name: ": localhost" = 11
        p = H1CProtocol(limit_request_field_size=20)
        p.feed(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")

        assert p.is_complete

    def test_limit_exceptions_inherit_from_parse_error(self):
        """Test limit exceptions inherit from ParseError for proper handling."""
        # This ensures gunicorn can catch these with a single ParseError handler
        assert issubclass(LimitRequestLine, ParseError)
        assert issubclass(LimitRequestHeaders, ParseError)


class TestH1CProtocolRemaining:
    """remaining() exposes bytes fed after a completed message.

    gunicorn's ASGI worker feeds whole data_received() buffers into feed(),
    so anything a client pipelines behind the request lands in the same call:
    the HTTP/2 preface after Upgrade: h2c, a WebSocket frame sent straight
    after the handshake, or a second HTTP/1 request.
    """

    H2C_UPGRADE = (
        b"GET / HTTP/1.1\r\nHost: a\r\nUpgrade: h2c\r\n"
        b"Connection: Upgrade, HTTP2-Settings\r\n"
        b"HTTP2-Settings: AAMAAABkAAQAoAAAAAIAAAAA\r\n\r\n"
    )
    H2C_PREFACE = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"

    def test_h2c_preface_recovered(self):
        """The blocking case: HTTP/2 preface pipelined behind the upgrade."""
        p = H1CProtocol()
        p.feed(self.H2C_UPGRADE + self.H2C_PREFACE)

        assert p.is_complete
        assert p.should_upgrade
        assert p.remaining() == self.H2C_PREFACE

    def test_websocket_frame_after_handshake(self):
        """A client frame sent immediately after the handshake survives."""
        frame = b"\x81\x85\x37\xfa\x21\x3d\x7f\x9f\x4d\x51\x58"
        p = H1CProtocol()
        p.feed(
            b"GET /ws HTTP/1.1\r\nHost: a\r\n"
            b"Upgrade: websocket\r\nConnection: Upgrade\r\n\r\n" + frame
        )

        assert p.is_complete
        assert p.remaining() == frame

    def test_pipelined_request(self):
        """A second HTTP/1 request in the same segment is recoverable."""
        second = b"GET /2 HTTP/1.1\r\nHost: a\r\n\r\n"
        p = H1CProtocol()
        p.feed(b"GET /1 HTTP/1.1\r\nHost: a\r\n\r\n" + second)

        assert p.path == b"/1"
        assert p.remaining() == second

    def test_no_tail_is_empty_bytes(self):
        p = H1CProtocol()
        p.feed(b"GET / HTTP/1.1\r\nHost: a\r\n\r\n")

        assert p.is_complete
        assert p.remaining() == b""

    def test_empty_while_incomplete(self):
        """While a message is still parsing, every byte fed belongs to it."""
        p = H1CProtocol()
        p.feed(b"GET / HTTP/1.1\r\nHost: ")

        assert not p.is_complete
        assert p.remaining() == b""

    def test_empty_mid_body(self):
        p = H1CProtocol()
        p.feed(b"POST / HTTP/1.1\r\nHost: a\r\nContent-Length: 5\r\n\r\nhel")

        assert not p.is_complete
        assert p.remaining() == b""

    def test_content_length_body_tail(self):
        """Tail is measured from the end of the body, not the headers."""
        p = H1CProtocol()
        p.feed(b"POST / HTTP/1.1\r\nHost: a\r\nContent-Length: 5\r\n\r\nhelloTAIL")

        assert p.is_complete
        assert p.remaining() == b"TAIL"

    def test_chunked_body_tail(self):
        """Same for a chunked body: measured past the terminating chunk."""
        p = H1CProtocol()
        p.feed(
            b"POST / HTTP/1.1\r\nHost: a\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"5\r\nhello\r\n0\r\n\r\nAFTER"
        )

        assert p.is_complete
        assert p.remaining() == b"AFTER"

    def test_chunked_with_trailer_tail(self):
        p = H1CProtocol()
        p.feed(
            b"POST / HTTP/1.1\r\nHost: a\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"5\r\nhello\r\n0\r\nX-Trace-Id: 1\r\n\r\nAFTER"
        )

        assert p.is_complete
        assert p.remaining() == b"AFTER"

    def test_tail_split_across_feeds(self):
        """A tail arriving in pieces is accumulated, not truncated."""
        p = H1CProtocol()
        p.feed(b"GET / HTTP/1.1\r\nHost: a\r\n\r\nPRI * HTTP")
        p.feed(b"/2.0\r\n\r\nSM\r\n\r\n")

        assert p.remaining() == self.H2C_PREFACE

    def test_tail_split_byte_by_byte(self):
        p = H1CProtocol()
        p.feed(b"GET / HTTP/1.1\r\nHost: a\r\n\r\n")
        for byte in b"abcdef":
            p.feed(bytes([byte]))

        assert p.remaining() == b"abcdef"

    def test_message_split_before_tail(self):
        """Headers split across feeds, tail arriving with the body."""
        p = H1CProtocol()
        p.feed(b"POST / HTTP/1.1\r\nHost: a\r\nContent-Len")
        p.feed(b"gth: 5\r\n\r\nhelloTAIL")

        assert p.is_complete
        assert p.remaining() == b"TAIL"

    def test_body_split_before_tail(self):
        p = H1CProtocol()
        p.feed(b"POST / HTTP/1.1\r\nHost: a\r\nContent-Length: 5\r\n\r\nhel")
        p.feed(b"loTAIL")

        assert p.is_complete
        assert p.remaining() == b"TAIL"

    def test_chunked_split_before_tail(self):
        p = H1CProtocol()
        p.feed(
            b"POST / HTTP/1.1\r\nHost: a\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhel"
        )
        p.feed(b"lo\r\n0\r\n")
        assert not p.is_complete
        p.feed(b"\r\nAFTER")

        assert p.is_complete
        assert p.remaining() == b"AFTER"

    def test_reset_drops_the_tail(self):
        """reset() must not leak the tail into the next message."""
        p = H1CProtocol()
        p.feed(b"GET /1 HTTP/1.1\r\nHost: a\r\n\r\nGET /2 HTTP/1.1\r\nHost: b\r\n\r\n")
        assert p.remaining() != b""

        p.reset()

        assert p.remaining() == b""

    def test_reset_then_feed_tail_parses_pipeline(self):
        """The keepalive loop: capture, reset, feed back."""
        p = H1CProtocol()
        p.feed(
            b"GET /1 HTTP/1.1\r\nHost: a\r\n\r\n"
            b"GET /2 HTTP/1.1\r\nHost: b\r\n\r\n"
            b"GET /3 HTTP/1.1\r\nHost: c\r\n\r\n"
        )

        paths = [p.path]
        tail = p.remaining()
        while tail:
            p.reset()
            p.feed(tail)
            paths.append(p.path)
            tail = p.remaining()

        assert paths == [b"/1", b"/2", b"/3"]

    def test_pipelined_bodies_survive_reset(self):
        """Bodies too, not just header-only requests."""
        bodies = []
        p = H1CProtocol(on_body=bodies.append)
        p.feed(
            b"POST /1 HTTP/1.1\r\nHost: a\r\nContent-Length: 3\r\n\r\nabc"
            b"POST /2 HTTP/1.1\r\nHost: a\r\nContent-Length: 3\r\n\r\ndef"
        )
        assert bodies == [b"abc"]

        tail = p.remaining()
        p.reset()
        p.feed(tail)

        assert p.path == b"/2"
        assert bodies == [b"abc", b"def"]
        assert p.remaining() == b""

    def test_finish_reports_no_tail(self):
        """At EOF nothing follows, so a half-parsed trailer is not a tail."""
        p = H1CProtocol()
        p.feed(
            b"POST / HTTP/1.1\r\nHost: a\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"5\r\nhello\r\n0\r\n"
        )
        p.finish()

        assert p.is_complete
        assert p.remaining() == b""


class TestH1CProtocolRemainingLimit:
    """The retained tail is bounded so a completed parser cannot grow forever.

    Callers that feed every socket read without checking is_complete would
    otherwise let a client stream unbounded data into the parser between the
    end of a message and reset().
    """

    def complete_with_tail(self, tail, **kwargs):
        p = H1CProtocol(**kwargs)
        p.feed(b"GET / HTTP/1.1\r\nHost: a\r\n\r\n" + tail)
        return p

    def test_tail_capped_at_limit(self):
        p = self.complete_with_tail(b"0123456789EXTRA", limit_remaining=10)

        assert p.remaining() == b"0123456789"
        assert p.remaining_truncated

    def test_not_truncated_within_limit(self):
        p = self.complete_with_tail(b"0123456789", limit_remaining=10)

        assert p.remaining() == b"0123456789"
        assert not p.remaining_truncated

    def test_default_limit_bounds_a_flood(self):
        """Feeding a completed parser must not grow the buffer without bound."""
        p = self.complete_with_tail(b"")
        for _ in range(64):
            p.feed(b"x" * 65536)  # 4 MB total

        assert len(p.remaining()) == 65536
        assert p.remaining_truncated

    def test_overflow_never_raises_from_feed(self):
        """feed() must not start failing on input it used to discard."""
        p = self.complete_with_tail(b"", limit_remaining=4)
        for _ in range(100):
            p.feed(b"flood")  # no exception

        assert p.remaining() == b"floo"

    def test_limit_applies_across_split_feeds(self):
        p = H1CProtocol(limit_remaining=10)
        p.feed(b"GET / HTTP/1.1\r\nHost: a\r\n\r\n01234")
        p.feed(b"56789EXTRA")

        assert p.remaining() == b"0123456789"
        assert p.remaining_truncated

    @pytest.mark.parametrize(
        "request_bytes",
        [
            b"GET / HTTP/1.1\r\nHost: a\r\n\r\n",
            b"GET / HTTP/1.1\r\nHost: a\r\nUpgrade: h2c\r\nConnection: Upgrade\r\n\r\n",
            b"POST / HTTP/1.1\r\nHost: a\r\nContent-Length: 2\r\n\r\nhi",
            b"POST / HTTP/1.1\r\nHost: a\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n",
        ],
        ids=["no-body", "upgrade", "content-length", "chunked"],
    )
    def test_every_completion_path_clamps(self, request_bytes):
        p = H1CProtocol(limit_remaining=10)
        p.feed(request_bytes + b"0123456789EXTRA")

        assert p.remaining() == b"0123456789"
        assert p.remaining_truncated

    def test_zero_means_unlimited(self):
        p = self.complete_with_tail(b"", limit_remaining=0)
        p.feed(b"y" * 200000)

        assert len(p.remaining()) == 200000
        assert not p.remaining_truncated

    def test_reset_clears_truncated_flag(self):
        p = self.complete_with_tail(b"0123456789EXTRA", limit_remaining=10)
        assert p.remaining_truncated

        p.reset()

        assert not p.remaining_truncated

    def test_negative_limit_rejected(self):
        with pytest.raises(ValueError):
            H1CProtocol(limit_remaining=-1)

    def test_h2c_preface_fits_default_limit(self):
        """The case this all exists for stays well inside the default."""
        p = H1CProtocol()
        p.feed(
            b"GET / HTTP/1.1\r\nHost: a\r\nUpgrade: h2c\r\n"
            b"Connection: Upgrade, HTTP2-Settings\r\n"
            b"HTTP2-Settings: AAMAAABkAAQAoAAAAAIAAAAA\r\n\r\n"
            b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"
        )

        assert p.remaining() == b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"
        assert not p.remaining_truncated
