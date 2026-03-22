# Changelog

All notable changes to this project will be documented in this file.

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
