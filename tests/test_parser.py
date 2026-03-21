"""Tests for gunicorn_h1c HTTP parser."""
import pytest


class TestBasicParser:
    """Tests for the basic parser (_parser module)."""

    def test_parse_simple_get(self):
        from gunicorn_h1c import parse_request

        data = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
        result = parse_request(data)

        assert result["method"] == b"GET"
        assert result["path"] == b"/"
        assert result["minor_version"] == 1
        assert len(result["headers"]) == 1
        assert result["headers"][0] == (b"Host", b"localhost")
        assert result["consumed"] == len(data)

    def test_parse_post_with_headers(self):
        from gunicorn_h1c import parse_request

        data = b"POST /api/users HTTP/1.1\r\nHost: api.example.com\r\nContent-Type: application/json\r\nContent-Length: 42\r\n\r\n"
        result = parse_request(data)

        assert result["method"] == b"POST"
        assert result["path"] == b"/api/users"
        assert len(result["headers"]) == 3

    def test_parse_with_query_string(self):
        from gunicorn_h1c import parse_request

        data = b"GET /search?q=hello&page=1 HTTP/1.1\r\nHost: localhost\r\n\r\n"
        result = parse_request(data)

        assert result["path"] == b"/search?q=hello&page=1"

    def test_http_10(self):
        from gunicorn_h1c import parse_request

        data = b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n"
        result = parse_request(data)

        assert result["minor_version"] == 0

    def test_incomplete_request(self):
        from gunicorn_h1c import parse_request, IncompleteError

        data = b"GET / HTTP/1.1\r\nHost: local"
        with pytest.raises(IncompleteError):
            parse_request(data)

    def test_invalid_request(self):
        from gunicorn_h1c import parse_request, ParseError

        data = b"INVALID REQUEST\r\n\r\n"
        with pytest.raises(ParseError):
            parse_request(data)

    def test_incremental_parsing(self):
        from gunicorn_h1c import parse_request, IncompleteError

        # Start with partial data
        buffer = b"GET / HTTP/1.1\r\n"

        with pytest.raises(IncompleteError):
            parse_request(buffer, last_len=0)

        # Add more data
        buffer += b"Host: localhost\r\n\r\n"
        result = parse_request(buffer, last_len=16)

        assert result["method"] == b"GET"


class TestFastParser:
    """Tests for the fast parser (_parser_fast module)."""

    def test_parse_simple_get(self):
        from gunicorn_h1c import parse_request_fast

        data = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
        req = parse_request_fast(data)

        assert req.method == b"GET"
        assert req.path == b"/"
        assert req.minor_version == 1
        assert len(req.headers) == 1
        assert req.consumed == len(data)

    def test_content_length_extraction(self):
        from gunicorn_h1c import parse_request_fast

        data = b"POST /api HTTP/1.1\r\nContent-Length: 100\r\n\r\n"
        req = parse_request_fast(data)

        assert req.content_length == 100
        assert not req.has_chunked

    def test_chunked_detection(self):
        from gunicorn_h1c import parse_request_fast

        data = b"POST /api HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n"
        req = parse_request_fast(data)

        assert req.has_chunked
        assert req.content_length == -1

    def test_connection_close(self):
        from gunicorn_h1c import parse_request_fast

        data = b"GET / HTTP/1.1\r\nConnection: close\r\n\r\n"
        req = parse_request_fast(data)

        assert req.connection_close == 1

    def test_connection_keepalive(self):
        from gunicorn_h1c import parse_request_fast

        data = b"GET / HTTP/1.1\r\nConnection: keep-alive\r\n\r\n"
        req = parse_request_fast(data)

        assert req.connection_close == 0

    def test_get_header(self):
        from gunicorn_h1c import parse_request_fast

        data = b"GET / HTTP/1.1\r\nHost: localhost\r\nX-Custom: value\r\n\r\n"
        req = parse_request_fast(data)

        assert req.get_header("Host") == b"localhost"
        assert req.get_header("host") == b"localhost"  # case-insensitive
        assert req.get_header("X-Custom") == b"value"
        assert req.get_header("X-Missing") is None

    def test_header_count(self):
        from gunicorn_h1c import parse_request_fast

        data = b"GET / HTTP/1.1\r\nA: 1\r\nB: 2\r\nC: 3\r\n\r\n"
        req = parse_request_fast(data)

        assert req.header_count == 3

    def test_incremental_parsing_fast(self):
        from gunicorn_h1c import parse_request_fast
        from gunicorn_h1c._parser_fast import IncompleteError

        # Partial request
        buffer = b"GET / HTTP/1.1\r\n"
        with pytest.raises(IncompleteError):
            parse_request_fast(buffer, last_len=0)

        # Complete request with last_len optimization
        buffer += b"Host: localhost\r\n\r\n"
        req = parse_request_fast(buffer, last_len=16)
        assert req.method == b"GET"

    def test_many_headers(self):
        from gunicorn_h1c import parse_request_fast

        # Build request with 200 headers
        headers = b"".join(f"X-Header-{i}: value{i}\r\n".encode() for i in range(200))
        data = b"GET / HTTP/1.1\r\n" + headers + b"\r\n"

        req = parse_request_fast(data)
        assert req.header_count == 200

    def test_incremental_parsing_raw(self):
        from gunicorn_h1c._parser_fast import parse_request_raw, IncompleteError

        buffer = b"GET / HTTP/1.1\r\n"
        with pytest.raises(IncompleteError):
            parse_request_raw(buffer, last_len=0)

        buffer += b"Host: localhost\r\n\r\n"
        result = parse_request_raw(buffer, last_len=16)
        assert result[6] == len(buffer)  # consumed == full length


class TestParseResponse:
    """Tests for response parsing."""

    def test_parse_simple_response(self):
        from gunicorn_h1c import parse_response

        data = b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\n"
        result = parse_response(data)

        assert result["status"] == 200
        assert result["message"] == b"OK"
        assert result["minor_version"] == 1
        assert result["consumed"] == len(data)

    def test_parse_404_response(self):
        from gunicorn_h1c import parse_response

        data = b"HTTP/1.1 404 Not Found\r\n\r\n"
        result = parse_response(data)

        assert result["status"] == 404
        assert result["message"] == b"Not Found"


class TestParseHeaders:
    """Tests for header-only parsing."""

    def test_parse_headers(self):
        from gunicorn_h1c import parse_headers

        data = b"Host: localhost\r\nContent-Type: text/plain\r\n\r\n"
        headers = parse_headers(data)

        assert len(headers) == 2
        assert headers[0] == (b"Host", b"localhost")
        assert headers[1] == (b"Content-Type", b"text/plain")


class TestParseToWsgiEnviron:
    """Tests for WSGI environ generation."""

    def test_simple_get(self):
        from gunicorn_h1c import parse_to_wsgi_environ

        data = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
        environ = parse_to_wsgi_environ(data)

        assert environ["REQUEST_METHOD"] == "GET"
        assert environ["PATH_INFO"] == "/"
        assert environ["QUERY_STRING"] == ""
        assert environ["SERVER_PROTOCOL"] == "HTTP/1.1"
        assert environ["HTTP_HOST"] == "localhost"

    def test_with_query_string(self):
        from gunicorn_h1c import parse_to_wsgi_environ

        data = b"GET /search?q=hello&page=1 HTTP/1.1\r\nHost: localhost\r\n\r\n"
        environ = parse_to_wsgi_environ(data)

        assert environ["PATH_INFO"] == "/search"
        assert environ["QUERY_STRING"] == "q=hello&page=1"

    def test_server_and_client(self):
        from gunicorn_h1c import parse_to_wsgi_environ

        data = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
        environ = parse_to_wsgi_environ(
            data,
            server=("127.0.0.1", 8000),
            client=("10.0.0.1", 54321)
        )

        assert environ["SERVER_NAME"] == "127.0.0.1"
        assert environ["SERVER_PORT"] == "8000"
        assert environ["REMOTE_ADDR"] == "10.0.0.1"
        assert environ["REMOTE_PORT"] == "54321"

    def test_url_scheme(self):
        from gunicorn_h1c import parse_to_wsgi_environ

        data = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
        environ = parse_to_wsgi_environ(data, url_scheme="https")

        assert environ["wsgi.url_scheme"] == "https"

    def test_content_type_header(self):
        from gunicorn_h1c import parse_to_wsgi_environ

        data = b"POST /api HTTP/1.1\r\nContent-Type: application/json\r\n\r\n"
        environ = parse_to_wsgi_environ(data)

        assert environ["CONTENT_TYPE"] == "application/json"
        assert "HTTP_CONTENT_TYPE" not in environ

    def test_content_length_header(self):
        from gunicorn_h1c import parse_to_wsgi_environ

        data = b"POST /api HTTP/1.1\r\nContent-Length: 42\r\n\r\n"
        environ = parse_to_wsgi_environ(data)

        assert environ["CONTENT_LENGTH"] == "42"
        assert "HTTP_CONTENT_LENGTH" not in environ

    def test_header_transformation(self):
        from gunicorn_h1c import parse_to_wsgi_environ

        data = b"GET / HTTP/1.1\r\nAccept-Language: en-US\r\nX-Custom-Header: value\r\n\r\n"
        environ = parse_to_wsgi_environ(data)

        assert environ["HTTP_ACCEPT_LANGUAGE"] == "en-US"
        assert environ["HTTP_X_CUSTOM_HEADER"] == "value"

    def test_duplicate_headers(self):
        from gunicorn_h1c import parse_to_wsgi_environ

        data = b"GET / HTTP/1.1\r\nAccept: text/html\r\nAccept: application/json\r\n\r\n"
        environ = parse_to_wsgi_environ(data)

        assert environ["HTTP_ACCEPT"] == "text/html,application/json"

    def test_http_10(self):
        from gunicorn_h1c import parse_to_wsgi_environ

        data = b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n"
        environ = parse_to_wsgi_environ(data)

        assert environ["SERVER_PROTOCOL"] == "HTTP/1.0"


class TestParseToAsgiScope:
    """Tests for ASGI scope generation."""

    def test_simple_get(self):
        from gunicorn_h1c import parse_to_asgi_scope

        data = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
        scope = parse_to_asgi_scope(data)

        assert scope["type"] == "http"
        assert scope["asgi"] == {"version": "3.0", "spec_version": "2.4"}
        assert scope["http_version"] == "1.1"
        assert scope["method"] == "GET"
        assert scope["path"] == "/"
        assert scope["raw_path"] == b"/"
        assert scope["query_string"] == b""
        assert scope["root_path"] == ""

    def test_with_query_string(self):
        from gunicorn_h1c import parse_to_asgi_scope

        data = b"GET /search?q=hello&page=1 HTTP/1.1\r\nHost: localhost\r\n\r\n"
        scope = parse_to_asgi_scope(data)

        assert scope["path"] == "/search"
        assert scope["raw_path"] == b"/search"
        assert scope["query_string"] == b"q=hello&page=1"

    def test_server_and_client(self):
        from gunicorn_h1c import parse_to_asgi_scope

        data = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
        scope = parse_to_asgi_scope(
            data,
            server=("127.0.0.1", 8000),
            client=("10.0.0.1", 54321)
        )

        assert scope["server"] == ("127.0.0.1", 8000)
        assert scope["client"] == ("10.0.0.1", 54321)

    def test_scheme_and_root_path(self):
        from gunicorn_h1c import parse_to_asgi_scope

        data = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
        scope = parse_to_asgi_scope(data, scheme="https", root_path="/app")

        assert scope["scheme"] == "https"
        assert scope["root_path"] == "/app"

    def test_headers_lowercase(self):
        from gunicorn_h1c import parse_to_asgi_scope

        data = b"GET / HTTP/1.1\r\nHost: localhost\r\nContent-Type: text/plain\r\n\r\n"
        scope = parse_to_asgi_scope(data)

        headers = scope["headers"]
        assert len(headers) == 2
        assert headers[0] == (b"host", b"localhost")
        assert headers[1] == (b"content-type", b"text/plain")

    def test_http_10(self):
        from gunicorn_h1c import parse_to_asgi_scope

        data = b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n"
        scope = parse_to_asgi_scope(data)

        assert scope["http_version"] == "1.0"

    def test_server_none_default(self):
        from gunicorn_h1c import parse_to_asgi_scope

        data = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
        scope = parse_to_asgi_scope(data)

        assert scope["server"] is None
        assert scope["client"] is None
