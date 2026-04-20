# Changelog

All notable changes to this project will be documented in this file.

## [0.6.5] - 2026-04-20

### Fixed

- Reject Content-Length list form per RFC 9112 section 6.3 (e.g. `Content-Length: 42, 42`)

## [0.6.4] - 2026-04-19

### Fixed

- Reject forbidden trailer field-names per RFC 9110 section 6.5.1
- Enforce request-target form and method pairing per RFC 9112
- Reject control characters in header field-value per RFC 9110 section 5.5

### Changed

- Add H1CProtocol limit validation tests
- Replace `assert False` with `pytest.fail()` to fix B011 lint error
- Ruff format `tests/test_protocol.py`

## [0.6.3] - 2026-03-26

### Added

- `InvalidChunkExtension` exception for chunk extension validation errors
- Validation to reject chunk extensions containing bare CR bytes (RFC 9112 compliance)

### Security

- Prevents potential request smuggling via malformed chunk extensions

## [0.6.2] - 2026-03-26

### Added

- `asgi_headers` property on HttpRequest and H1CProtocol for ASGI-compliant lowercase header names
- `pico_create_header_tuple_lowercase()` helper function in pico_utils.h

### Fixed

- Starlette/FastAPI compatibility when using fast parser with ASGI worker (fixes #2)

## [0.6.1] - 2026-03-26

### Added

- GitHub Actions CI workflow (Python 3.13/3.14 on Linux and macOS)
- Ruff configuration for linting and formatting

## [0.6.0] - 2026-03-26

### Added

- `finish()` method on H1CProtocol for EOF handling in chunked encoding
- Header framing validation to prevent ambiguous requests
  - Reject duplicate Content-Length headers
  - Reject Content-Length with Transfer-Encoding (CL+TE conflict)
  - Reject chunked encoding in HTTP/1.0
  - Reject stacked chunked encoding
  - Reject unknown Transfer-Encoding values
- Strict chunk size parsing (reject whitespace, empty sizes)
- `pico_validate_headers_full()` function for complete header validation
- 30 new tests for header validation

### Changed

- All parsers (parse_request, parse_request_fast, H1CProtocol) now perform framing validation

## [0.5.1] - 2026-03-22

### Fixed

- Header size calculation now includes CRLF to match Python parser behavior

## [0.5.0] - 2026-03-22

### Fixed

- Non-HTTP protocols (FTP/1.1, RTSP/1.0) now raise `InvalidHTTPVersion` instead of generic `ParseError`
- Bare CR/LF in header values now detected and raise `InvalidHeader` in error analysis
- Header size limit calculation now includes ": " separator (name + 2 + value)

## [0.4.1] - 2026-03-22

### Improved

- Specific exception analysis when `phr_parse_request()` returns -1
  - Now analyzes the buffer to identify the exact cause of parse failure
  - Raises `InvalidRequestMethod` for invalid/lowercase method characters
  - Raises `InvalidHTTPVersion` for unsupported HTTP versions (e.g., HTTP/2.0, HTTP/0.9)
  - Raises `InvalidHeaderName` for invalid header name characters (e.g., space)
  - Raises `InvalidHeader` for NUL characters in header values
  - Falls back to generic `ParseError` only when specific cause cannot be determined

## [0.4.0] - 2026-03-22

### Added

- Limit enforcement matching gunicorn's Python parser behavior
  - `limit_request_line`: Maximum request line length (default 8190)
  - `limit_request_fields`: Maximum number of headers (default 100)
  - `limit_request_field_size`: Maximum header size (default 8190)
- Gunicorn compatibility flags
  - `permit_unconventional_http_method`: Allow lowercase methods, short methods, and `#` character
  - `permit_unconventional_http_version`: Allow HTTP versions other than 1.0/1.1
- Specific exception types for validation errors (all inherit from `ParseError`)
  - `LimitRequestLine`: Request line exceeds limit
  - `LimitRequestHeaders`: Too many headers or header too large
  - `InvalidRequestMethod`: Invalid method characters or format
  - `InvalidHTTPVersion`: Invalid HTTP version
  - `InvalidHeaderName`: Invalid header name characters
  - `InvalidHeader`: Invalid header value (NUL, CR, LF)
- Validation functions in `pico_utils.h` for RFC 9110 token character validation

### Changed

- All parsing functions now validate requests by default
- `parse_request`, `parse_request_fast`, `parse_to_wsgi_environ`, `parse_to_asgi_scope`, and `H1CProtocol` accept new limit/flag parameters

## [0.3.0] - 2026-03-21

### Changed

- Refactored shared utilities into `pico_utils.h` to reduce code duplication
- Extracted common header parsing functions used across all three parser modules
- Improved code maintainability with ~200 lines of duplicate code removed

## [0.2.0] - 2026-03-21

### Added

- `H1CProtocol` - Callback-based HTTP/1.1 parser for asyncio integration
  - Zero-copy, synchronous parsing in `data_received()` callbacks
  - Support for Content-Length and chunked transfer encoding body parsing
  - Callbacks: `on_message_begin`, `on_url`, `on_header`, `on_headers_complete`, `on_body`, `on_message_complete`
  - Properties: `method`, `path`, `http_version`, `headers`, `content_length`, `is_chunked`, `should_keep_alive`, `should_upgrade`, `is_complete`
  - `reset()` method for keepalive connection reuse
  - ~4.7M req/s when reusing parser (7% faster than pull-based API)
  - ~3x faster than pull-based API for incremental parsing

## [0.1.0] - 2026-03-21

### Added

- Initial release
- `parse_request` - Basic HTTP request parsing returning dict
- `parse_response` - HTTP response parsing returning dict
- `parse_headers` - Header-only parsing
- `parse_request_fast` - Zero-copy request parsing with lazy `HttpRequest` object
- `parse_request_raw` - Ultra-fast parsing returning raw buffer offsets
- `parse_to_wsgi_environ` - WSGI environ dict generation
- `parse_to_asgi_scope` - ASGI scope dict generation
- SIMD-optimized parsing via picohttpparser (SSE4.2 on x86, NEON on ARM)
- Incremental parsing support with `last_len` parameter
- Common header extraction (Content-Length, Transfer-Encoding, Connection)
- Support for up to 256 headers per request
- Python 3.9+ support
