# Changelog

All notable changes to this project will be documented in this file.

## [0.6.9] - 2026-08-23

### Fixed

- Parse the request body on messages that carry an `Upgrade` header. Any
  `Upgrade` value used to suppress body parsing: the message completed at the
  end of the headers, `on_body` never fired, and the payload was left in
  `remaining()` as raw bytes, still chunk-encoded when the request was chunked.
  An upgrade request is an ordinary HTTP/1.1 request, since the protocol switch
  happens only after the server answers 101 (RFC 9110 section 7.8) and an h2c
  client must send any payload in full before its first HTTP/2 frame (RFC 7540
  section 3.2). On gunicorn's ASGI worker this lost the body silently, and made
  this parser disagree with the pure-Python one about the same request.

### Changed

- `remaining()` no longer includes the body on those messages. A caller that
  worked around the old behaviour by taking the declared body off the front of
  the tail should drop that.
- An upgrade request with an unterminated chunked body now stays incomplete
  instead of completing at the headers, exactly as a normal chunked request
  does.

Messages carrying `Upgrade` that declare no body are unaffected, which covers
the WebSocket handshake and a bare h2c upgrade. `CONNECT` is unaffected: it has
no body framing, so everything after its headers stays in `remaining()`.

## [0.6.8] - 2026-08-20

### Fixed

- Bound the tail retained after a completed message. 0.6.7 kept everything fed
  to an already-complete parser, so a caller that feeds every socket read
  without checking `is_complete` let a client grow the parser buffer without
  limit between the end of a message and `reset()`. The tail now stops at
  `limit_remaining` (64 KB by default, `0` disables the cap).

### Added

- `limit_remaining` constructor argument and the `remaining_truncated`
  property, true once the tail has hit the cap and bytes have been dropped.

`feed()` never raises on overflow: it discards the excess, exactly as every
version before 0.6.7 discarded the whole tail. Callers that ignore
`remaining()` are unaffected apart from the memory bound.

## [0.6.7] - 2026-08-20

### Added

- `H1CProtocol.remaining()` returns the bytes fed after a completed message.
  Callers that feed whole socket reads can now recover what a client pipelined
  behind the request: the HTTP/2 preface after an `Upgrade: h2c` (which
  unblocks cleartext HTTP/2 on gunicorn's ASGI worker), a WebSocket frame sent
  straight after the handshake, or a second HTTP/1 request.

  - Returns `b""` until `is_complete` is true, since every byte fed to an
    in-progress message belongs to it.
  - Measured from the end of the message, so past the body or the terminating
    chunk and its trailers, not the end of the headers.
  - A tail split over several `feed()` calls is accumulated.
  - `reset()` drops the tail; read it first and feed it back to parse a
    pipelined request.

### Changed

- Bytes fed to an already-complete parser are retained for `remaining()`
  instead of being discarded. Stop feeding a completed parser, or drain it with
  `remaining()` and `reset()`, rather than pumping a whole connection through
  `feed()`.

## [0.6.6] - 2026-08-17

### Security

- Reject repeated singleton header fields per RFC 9110 section 5.3. `Host` and
  `Content-Type` now raise `InvalidHeader` on a repeat, as `Content-Length`
  already did. A repeat cannot be merged into a list, so the message is
  ambiguous and gunicorn, a cache, and an upstream proxy may each resolve it
  differently. Duplicate `Host` is a routing- and cache-confusion vector.

### Changed

- The singleton field set is now stated in one place in `pico_utils.h`, shared
  by `parse_request`, `parse_request_fast`, `parse_to_wsgi_environ`,
  `parse_to_asgi_scope`, and `H1CProtocol.feed()`. Response parsing is
  unaffected.
- Repeated `Transfer-Encoding` remains accepted; it is not a singleton field and
  its repeats are handled by the framing checks.

### Compatibility

- This turns requests that previously parsed into 400 Bad Request. That is
  intended, and it matches what the pure-Python parser does, but it is a
  behavior change for anyone sitting behind a proxy that duplicates `Host`. Such
  a proxy is itself non-conformant, and that ambiguity is the reason for the
  change. Duplicate list-valued fields (`Accept`, `Via`, `X-Forwarded-For`,
  `Set-Cookie`, and so on) are unaffected.

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
