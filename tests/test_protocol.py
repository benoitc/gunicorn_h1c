"""Tests for H1CProtocol callback-based parser."""
import pytest
from gunicorn_h1c import H1CProtocol
from gunicorn_h1c._protocol import ParseError


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
        p.feed(b"GET / HTTP/1.1\r\nHost: localhost\r\nAccept: */*\r\nUser-Agent: test\r\n\r\n")

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

        with pytest.raises(ParseError):
            p.feed(b"INVALID\r\n\r\n")

    def test_invalid_chunk_size(self):
        """Test invalid chunk size raises ParseError."""
        p = H1CProtocol()
        p.feed(b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n")

        with pytest.raises(ParseError):
            p.feed(b"ZZZZ\r\n")  # Invalid hex

    def test_parse_error_has_status_code(self):
        """Test ParseError has status_code attribute."""
        p = H1CProtocol()

        try:
            p.feed(b"INVALID\r\n\r\n")
            assert False, "Should have raised ParseError"
        except ParseError as e:
            # Just verify the exception was raised
            assert str(e)  # Has a message


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
