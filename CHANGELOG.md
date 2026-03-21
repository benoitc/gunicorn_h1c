# Changelog

All notable changes to this project will be documented in this file.

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
