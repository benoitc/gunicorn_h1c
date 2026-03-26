"""Tests for gunicorn_h1c HTTP parser."""

import pytest

from gunicorn_h1c import (
    InvalidHeader,
    InvalidHeaderName,
    InvalidHTTPVersion,
    InvalidRequestMethod,
    LimitRequestHeaders,
    LimitRequestLine,
    ParseError,
    parse_request,
    parse_request_fast,
)


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
        from gunicorn_h1c import IncompleteError, parse_request

        data = b"GET / HTTP/1.1\r\nHost: local"
        with pytest.raises(IncompleteError):
            parse_request(data)

    def test_invalid_request(self):
        from gunicorn_h1c import ParseError, parse_request

        data = b"INVALID REQUEST\r\n\r\n"
        with pytest.raises(ParseError):
            parse_request(data)

    def test_incremental_parsing(self):
        from gunicorn_h1c import IncompleteError, parse_request

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

    def test_asgi_headers_lowercase(self):
        """asgi_headers should return headers with lowercase names."""
        from gunicorn_h1c import parse_request_fast

        data = b"GET / HTTP/1.1\r\nHost: localhost\r\nContent-Type: text/plain\r\n\r\n"
        req = parse_request_fast(data)

        # Regular headers preserve original case
        assert req.headers[0] == (b"Host", b"localhost")
        assert req.headers[1] == (b"Content-Type", b"text/plain")

        # ASGI headers have lowercase names
        asgi_headers = req.asgi_headers
        assert asgi_headers[0] == (b"host", b"localhost")
        assert asgi_headers[1] == (b"content-type", b"text/plain")

    def test_asgi_headers_is_list(self):
        """asgi_headers should return a list (not tuple) per ASGI spec."""
        from gunicorn_h1c import parse_request_fast

        data = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
        req = parse_request_fast(data)

        assert isinstance(req.asgi_headers, list)

    def test_incremental_parsing_fast(self):
        from gunicorn_h1c._parser_fast import IncompleteError

        from gunicorn_h1c import parse_request_fast

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

        # Build request with 200 headers (must increase limit from default 100)
        headers = b"".join(f"X-Header-{i}: value{i}\r\n".encode() for i in range(200))
        data = b"GET / HTTP/1.1\r\n" + headers + b"\r\n"

        req = parse_request_fast(data, limit_request_fields=256)
        assert req.header_count == 200

    def test_incremental_parsing_raw(self):
        from gunicorn_h1c._parser_fast import IncompleteError, parse_request_raw

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
            data, server=("127.0.0.1", 8000), client=("10.0.0.1", 54321)
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

        data = (
            b"GET / HTTP/1.1\r\nAccept: text/html\r\nAccept: application/json\r\n\r\n"
        )
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
            data, server=("127.0.0.1", 8000), client=("10.0.0.1", 54321)
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


class TestValidationExceptions:
    """Tests for validation exception hierarchy."""

    def test_exception_hierarchy(self):
        """All validation exceptions should inherit from ParseError."""
        assert issubclass(LimitRequestLine, ParseError)
        assert issubclass(LimitRequestHeaders, ParseError)
        assert issubclass(InvalidRequestMethod, ParseError)
        assert issubclass(InvalidHTTPVersion, ParseError)
        assert issubclass(InvalidHeaderName, ParseError)
        assert issubclass(InvalidHeader, ParseError)

    def test_parse_error_inherits_value_error(self):
        """ParseError should inherit from ValueError."""
        assert issubclass(ParseError, ValueError)


class TestLimitRequestLine:
    """Tests for request line limit enforcement."""

    def test_request_line_exceeds_limit(self):
        """Should raise LimitRequestLine when request line is too long."""
        long_path = b"/" + b"x" * 200
        data = b"GET " + long_path + b" HTTP/1.1\r\nHost: localhost\r\n\r\n"
        with pytest.raises(LimitRequestLine):
            parse_request(data, limit_request_line=100)

    def test_request_line_within_limit(self):
        """Should succeed when request line is within limit."""
        data = b"GET /short HTTP/1.1\r\nHost: localhost\r\n\r\n"
        result = parse_request(data, limit_request_line=100)
        assert result["method"] == b"GET"

    def test_request_line_at_limit(self):
        """Should succeed when request line is exactly at limit."""
        # "GET / HTTP/1.1" is 14 chars
        data = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
        result = parse_request(data, limit_request_line=14)
        assert result["method"] == b"GET"

    def test_fast_parser_limit_request_line(self):
        """Fast parser should also enforce request line limits."""
        long_path = b"/" + b"x" * 200
        data = b"GET " + long_path + b" HTTP/1.1\r\nHost: localhost\r\n\r\n"
        with pytest.raises(LimitRequestLine):
            parse_request_fast(data, limit_request_line=100)


class TestLimitRequestHeaders:
    """Tests for header limit enforcement."""

    def test_too_many_headers(self):
        """Should raise LimitRequestHeaders when too many headers."""
        headers = b"".join(f"X-Header-{i}: value\r\n".encode() for i in range(50))
        data = b"GET / HTTP/1.1\r\n" + headers + b"\r\n"
        with pytest.raises(LimitRequestHeaders):
            parse_request(data, limit_request_fields=10)

    def test_header_count_within_limit(self):
        """Should succeed when header count is within limit."""
        headers = b"".join(f"X-Header-{i}: value\r\n".encode() for i in range(5))
        data = b"GET / HTTP/1.1\r\n" + headers + b"\r\n"
        result = parse_request(data, limit_request_fields=10)
        assert result["method"] == b"GET"

    def test_header_size_exceeds_limit(self):
        """Should raise LimitRequestHeaders when header is too large."""
        long_value = b"x" * 1000
        data = b"GET / HTTP/1.1\r\nX-Long: " + long_value + b"\r\n\r\n"
        with pytest.raises(LimitRequestHeaders):
            parse_request(data, limit_request_field_size=100)

    def test_header_size_within_limit(self):
        """Should succeed when header size is within limit."""
        data = b"GET / HTTP/1.1\r\nX-Short: value\r\n\r\n"
        result = parse_request(data, limit_request_field_size=100)
        assert result["method"] == b"GET"

    def test_header_size_includes_crlf(self):
        """Header size calculation must include CRLF to match Python parser.

        Header line: "X-Test: value\\r\\n"
        Size = name(6) + ": "(2) + value(5) + CRLF(2) = 15 bytes
        """
        # Exactly at limit (15 bytes) - should pass
        data = b"GET / HTTP/1.1\r\nX-Test: value\r\n\r\n"
        result = parse_request(data, limit_request_field_size=15)
        assert result["method"] == b"GET"

        # One byte over limit - should fail
        with pytest.raises(LimitRequestHeaders):
            parse_request(data, limit_request_field_size=14)

    def test_fast_parser_header_limits(self):
        """Fast parser should also enforce header limits."""
        headers = b"".join(f"X-Header-{i}: value\r\n".encode() for i in range(50))
        data = b"GET / HTTP/1.1\r\n" + headers + b"\r\n"
        with pytest.raises(LimitRequestHeaders):
            parse_request_fast(data, limit_request_fields=10)


class TestInvalidRequestMethod:
    """Tests for method validation."""

    def test_lowercase_method_rejected(self):
        """Lowercase methods should be rejected by default."""
        data = b"get / HTTP/1.1\r\nHost: localhost\r\n\r\n"
        with pytest.raises(InvalidRequestMethod):
            parse_request(data)

    def test_lowercase_method_with_permit(self):
        """Lowercase methods should be allowed with permit flag."""
        data = b"get / HTTP/1.1\r\nHost: localhost\r\n\r\n"
        result = parse_request(data, permit_unconventional_http_method=True)
        assert result["method"] == b"get"

    def test_mixed_case_method_rejected(self):
        """Mixed case methods should be rejected by default."""
        data = b"GeT / HTTP/1.1\r\nHost: localhost\r\n\r\n"
        with pytest.raises(InvalidRequestMethod):
            parse_request(data)

    def test_short_method_rejected(self):
        """Methods shorter than 3 chars should be rejected."""
        data = b"GO / HTTP/1.1\r\nHost: localhost\r\n\r\n"
        with pytest.raises(InvalidRequestMethod):
            parse_request(data)

    def test_short_method_with_permit(self):
        """Short methods should be allowed with permit flag."""
        data = b"GO / HTTP/1.1\r\nHost: localhost\r\n\r\n"
        result = parse_request(data, permit_unconventional_http_method=True)
        assert result["method"] == b"GO"

    def test_hash_in_method_rejected(self):
        """Methods with # should be rejected by default."""
        data = b"GET# / HTTP/1.1\r\nHost: localhost\r\n\r\n"
        with pytest.raises(InvalidRequestMethod):
            parse_request(data)

    def test_standard_methods_accepted(self):
        """Standard HTTP methods should be accepted."""
        for method in [
            b"GET",
            b"POST",
            b"PUT",
            b"DELETE",
            b"PATCH",
            b"HEAD",
            b"OPTIONS",
        ]:
            data = method + b" / HTTP/1.1\r\nHost: localhost\r\n\r\n"
            result = parse_request(data)
            assert result["method"] == method

    def test_fast_parser_method_validation(self):
        """Fast parser should also validate methods."""
        data = b"get / HTTP/1.1\r\nHost: localhost\r\n\r\n"
        with pytest.raises(InvalidRequestMethod):
            parse_request_fast(data)


class TestInvalidHTTPVersion:
    """Tests for HTTP version validation."""

    def test_http_10_accepted(self):
        """HTTP/1.0 should be accepted."""
        data = b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n"
        result = parse_request(data)
        assert result["minor_version"] == 0

    def test_http_11_accepted(self):
        """HTTP/1.1 should be accepted."""
        data = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
        result = parse_request(data)
        assert result["minor_version"] == 1

    def test_fast_parser_version_validation(self):
        """Fast parser should also validate HTTP version."""
        data = b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n"
        req = parse_request_fast(data)
        assert req.minor_version == 0


class TestHeaderValidation:
    """Tests for header name and value validation."""

    def test_valid_header_names(self):
        """Valid header names should be accepted."""
        data = b"GET / HTTP/1.1\r\nX-Custom-Header: value\r\nContent-Type: text/plain\r\n\r\n"
        result = parse_request(data)
        assert len(result["headers"]) == 2

    def test_fast_parser_header_validation(self):
        """Fast parser should also validate headers."""
        data = b"GET / HTTP/1.1\r\nX-Valid: value\r\n\r\n"
        req = parse_request_fast(data)
        assert req.header_count == 1


class TestDefaultLimits:
    """Tests for default limit values."""

    def test_default_limits_allow_reasonable_requests(self):
        """Default limits should allow reasonable sized requests."""
        # Request line under 8190
        path = b"/" + b"x" * 1000
        headers = b"".join(f"X-Header-{i}: value\r\n".encode() for i in range(50))
        data = b"GET " + path + b" HTTP/1.1\r\n" + headers + b"\r\n"
        result = parse_request(data)
        assert result["method"] == b"GET"

    def test_default_limit_request_line(self):
        """Default request line limit should be 8190."""
        # Create a request line that exceeds 8190
        long_path = b"/" + b"x" * 9000
        data = b"GET " + long_path + b" HTTP/1.1\r\nHost: localhost\r\n\r\n"
        with pytest.raises(LimitRequestLine):
            parse_request(data)

    def test_default_limit_request_fields(self):
        """Default header count limit should be 100."""
        headers = b"".join(f"X-Header-{i}: value\r\n".encode() for i in range(150))
        data = b"GET / HTTP/1.1\r\n" + headers + b"\r\n"
        with pytest.raises(LimitRequestHeaders):
            parse_request(data)


class TestParseErrorSpecificExceptions:
    """Tests for specific exceptions when phr_parse_request returns -1."""

    def test_parse_error_lowercase_method(self):
        """Lowercase method should raise InvalidRequestMethod."""
        with pytest.raises(InvalidRequestMethod):
            parse_request(b"get / HTTP/1.1\r\n\r\n")

    def test_parse_error_non_token_char_in_method(self):
        """Non-token char in method should raise InvalidRequestMethod."""
        # Using a control character (0x01) inside the method
        with pytest.raises(InvalidRequestMethod):
            parse_request(b"GE\x01T / HTTP/1.1\r\n\r\n")

    def test_parse_error_invalid_http_version(self):
        """HTTP/2.0 should raise InvalidHTTPVersion."""
        with pytest.raises(InvalidHTTPVersion):
            parse_request(b"GET / HTTP/2.0\r\n\r\n")

    def test_parse_error_http_09(self):
        """HTTP/0.9 should raise InvalidHTTPVersion."""
        with pytest.raises(InvalidHTTPVersion):
            parse_request(b"GET / HTTP/0.9\r\n\r\n")

    def test_parse_error_space_in_header_name(self):
        """Space in header name should raise InvalidHeaderName."""
        with pytest.raises(InvalidHeaderName):
            parse_request(b"GET / HTTP/1.1\r\nBad Header: value\r\n\r\n")

    def test_parse_error_nul_in_header_value(self):
        """NUL in header value should raise InvalidHeader."""
        with pytest.raises(InvalidHeader):
            parse_request(b"GET / HTTP/1.1\r\nX-Test: val\x00ue\r\n\r\n")

    def test_parse_error_bare_lf_in_header_value(self):
        """Bare LF in header value should raise InvalidHeader."""
        with pytest.raises(InvalidHeader):
            parse_request(b"GET / HTTP/1.1\r\nX-Test: value\nmore\r\n\r\n")

    def test_parse_error_bare_cr_in_header_value(self):
        """Bare CR in header value should raise InvalidHeader."""
        with pytest.raises(InvalidHeader):
            parse_request(b"GET / HTTP/1.1\r\nX-Test: value\rmore\r\n\r\n")

    def test_parse_error_ftp_protocol(self):
        """FTP/1.1 should raise InvalidHTTPVersion."""
        with pytest.raises(InvalidHTTPVersion):
            parse_request(b"GET / FTP/1.1\r\n\r\n")

    def test_parse_error_rtsp_protocol(self):
        """RTSP/1.0 should raise InvalidHTTPVersion."""
        with pytest.raises(InvalidHTTPVersion):
            parse_request(b"GET / RTSP/1.0\r\n\r\n")

    def test_fast_parser_parse_error_lowercase_method(self):
        """Fast parser: lowercase method should raise InvalidRequestMethod."""
        with pytest.raises(InvalidRequestMethod):
            parse_request_fast(b"get / HTTP/1.1\r\n\r\n")

    def test_fast_parser_parse_error_invalid_http_version(self):
        """Fast parser: HTTP/2.0 should raise InvalidHTTPVersion."""
        with pytest.raises(InvalidHTTPVersion):
            parse_request_fast(b"GET / HTTP/2.0\r\n\r\n")

    def test_fast_parser_parse_error_space_in_header_name(self):
        """Fast parser: space in header name should raise InvalidHeaderName."""
        with pytest.raises(InvalidHeaderName):
            parse_request_fast(b"GET / HTTP/1.1\r\nBad Header: value\r\n\r\n")

    def test_fast_parser_parse_error_nul_in_header_value(self):
        """Fast parser: NUL in header value should raise InvalidHeader."""
        with pytest.raises(InvalidHeader):
            parse_request_fast(b"GET / HTTP/1.1\r\nX-Test: val\x00ue\r\n\r\n")

    def test_fast_parser_parse_error_bare_lf_in_header_value(self):
        """Fast parser: bare LF in header value should raise InvalidHeader."""
        with pytest.raises(InvalidHeader):
            parse_request_fast(b"GET / HTTP/1.1\r\nX-Test: value\nmore\r\n\r\n")

    def test_fast_parser_parse_error_ftp_protocol(self):
        """Fast parser: FTP/1.1 should raise InvalidHTTPVersion."""
        with pytest.raises(InvalidHTTPVersion):
            parse_request_fast(b"GET / FTP/1.1\r\n\r\n")


class TestProtocolParseErrorSpecificExceptions:
    """Tests for specific exceptions in H1CProtocol when phr_parse_request returns -1."""

    def test_protocol_parse_error_lowercase_method(self):
        """H1CProtocol: lowercase method should raise InvalidRequestMethod."""
        import gunicorn_h1c

        protocol = gunicorn_h1c.H1CProtocol(on_headers_complete=lambda: None)
        with pytest.raises(InvalidRequestMethod):
            protocol.feed(b"get / HTTP/1.1\r\n\r\n")

    def test_protocol_parse_error_invalid_http_version(self):
        """H1CProtocol: HTTP/2.0 should raise InvalidHTTPVersion."""
        import gunicorn_h1c

        protocol = gunicorn_h1c.H1CProtocol(on_headers_complete=lambda: None)
        with pytest.raises(InvalidHTTPVersion):
            protocol.feed(b"GET / HTTP/2.0\r\n\r\n")

    def test_protocol_parse_error_space_in_header_name(self):
        """H1CProtocol: space in header name should raise InvalidHeaderName."""
        import gunicorn_h1c

        protocol = gunicorn_h1c.H1CProtocol(on_headers_complete=lambda: None)
        with pytest.raises(InvalidHeaderName):
            protocol.feed(b"GET / HTTP/1.1\r\nBad Header: value\r\n\r\n")

    def test_protocol_parse_error_nul_in_header_value(self):
        """H1CProtocol: NUL in header value should raise InvalidHeader."""
        import gunicorn_h1c

        protocol = gunicorn_h1c.H1CProtocol(on_headers_complete=lambda: None)
        with pytest.raises(InvalidHeader):
            protocol.feed(b"GET / HTTP/1.1\r\nX-Test: val\x00ue\r\n\r\n")

    def test_protocol_parse_error_bare_lf_in_header_value(self):
        """H1CProtocol: bare LF in header value should raise InvalidHeader."""
        import gunicorn_h1c

        protocol = gunicorn_h1c.H1CProtocol(on_headers_complete=lambda: None)
        with pytest.raises(InvalidHeader):
            protocol.feed(b"GET / HTTP/1.1\r\nX-Test: value\nmore\r\n\r\n")

    def test_protocol_parse_error_ftp_protocol(self):
        """H1CProtocol: FTP/1.1 should raise InvalidHTTPVersion."""
        import gunicorn_h1c

        protocol = gunicorn_h1c.H1CProtocol(on_headers_complete=lambda: None)
        with pytest.raises(InvalidHTTPVersion):
            protocol.feed(b"GET / FTP/1.1\r\n\r\n")
