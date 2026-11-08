# Changelog

All notable changes to this project will be documented in this file.

## [0.6.12] - 2026-11-09

### Fixed

- Chunked request bodies no longer slow down with their size. The cost grew
  with the square of the body: a 64 KiB chunked body read in one go took
  about 23 times longer than the same body with `Content-Length`, and 4096
  small chunks took 5 ms. A chunked body now takes at most about twice as
  long as the same body with `Content-Length`.
- `parse_request`, `parse_request_fast`, `parse_to_wsgi_environ` and
  `parse_to_asgi_scope` measure the request line after the one empty line
  allowed before it, as `H1CProtocol` and gunicorn do. A request line over
  `limit_request_line` used to be accepted when a CRLF came before it.

### Changed

- Faster parsing. `H1CProtocol` reads a request straight from the bytes
  given to `feed()` and copies only what it has to keep, so a body that
  arrives with the head is no longer copied. Header names and values are not
  checked a second time after parsing, and `asgi_headers` shares its values
  with `headers` instead of copying them. In gunicorn this cuts the parser's
  work per request by about 20% on the ASGI worker and 15% on the WSGI
  worker, and the work for a 64 KiB body by about 60%.
- `H1CProtocol` calls `on_body` once per `feed()` with all the chunk data
  that call decoded, instead of once per chunk, the same way a
  `Content-Length` body arrives. Code that relied on one call per chunk sees
  fewer, larger calls. Data decoded before an invalid chunk is still
  delivered before the error.

## [0.6.11] - 2026-11-08

### Fixed

- Report the same error for the same invalid request however its bytes
  arrive. When `parse_request` or `parse_request_fast` was called with only
  part of the request (as gunicorn's WSGI worker does while it reads, with
  `last_len`), or `H1CProtocol` was fed it in pieces, the error was often a
  generic `ParseError` where parsing the whole request gave `InvalidHeader`,
  `InvalidHeaderName` or `InvalidRequestMethod`. In gunicorn that turned some
  bad header lines into `InvalidRequestLine` depending on where the socket
  read happened to stop. The error now depends only on the first invalid byte
  and where it sits in the request.
- `H1CProtocol` gives the same message for a request line, a header line or a
  header count over its limit whether the request arrived whole or in pieces.
- Count a header line the way gunicorn does: the line as received, spaces
  and tabs around the value included, plus CRLF. gunicorn_h1c used to leave
  that whitespace out, so a line padded past `limit_request_field_size` was
  accepted where gunicorn's own parsers answer 431, including on gunicorn's
  WSGI workers with `--http-parser fast`. Such a line is now rejected on
  every entry point, and a line with no space after the colon gets the byte
  it was charged for that space.
- `H1CProtocol` checks each line of the request head as it arrives, in the
  order gunicorn's parser does (size, count, then content), so when a
  request has more than one problem the first bad line decides, whole or in
  pieces. A head that never ends is rejected at its first bad line instead
  of waiting for more data.

### Changed

- `H1CProtocol` limit messages no longer include the measured length or
  count, since it is not known yet when a line is cut short: for example
  "Number of headers exceeds limit (100)" instead of "Number of headers (150)
  exceeds limit (100)". The exception classes are unchanged.
- A few malformed requests get a different class than a full parse gave
  before, now that the first invalid byte decides. A bare LF at the start of
  a header line or in the method, or a second empty line before the request
  line, is a `ParseError`; a bare LF or CR inside a header value is an
  `InvalidHeader`.
- A header line whose spaces or tabs take it past `limit_request_field_size`
  is now rejected with `LimitRequestHeaders`. Apart from that, nothing that
  was accepted is rejected, or the reverse.
- Code that caught `gunicorn_h1c._protocol.ParseError` or
  `gunicorn_h1c._parser_fast.ParseError` for these requests now gets the
  specific class. Every specific class derives from `gunicorn_h1c.ParseError`
  and from `ValueError`; catch one of those to handle every rejection.

## [0.6.10] - 2026-11-07

### Security

These are covered by GHSA-78cg-ph9m-rjjx.

- `H1CProtocol` validates a request in full before it publishes the parsed
  method, path and headers or runs any callback, and refuses to be re-entered:
  `feed()`, `reset()` and `finish()` called from a callback raise
  `RuntimeError`. `get_header()` and `asgi_headers` raise `TypeError` on a
  header list that no longer holds `(bytes, bytes)` pairs. Before, reading
  `headers` from a callback or after a rejected request, calling `reset()`
  from a callback, or changing the list returned by `headers` crashed the
  interpreter.
- `parse_request`, `parse_request_fast`, `parse_request_raw`,
  `parse_response` and `parse_headers` raise `ValueError` unless
  `0 <= last_len <= len(data)`. An out-of-range `last_len` used to read
  outside the buffer and could crash the interpreter.
- `parse_request`, `parse_response`, `parse_headers`, `parse_to_wsgi_environ`
  and `parse_to_asgi_scope` keep the input buffer until the result is built.
  They used to release it first and then copy the fields out of it, which is
  a use after free when the exporter frees that memory on release.
- `parse_to_wsgi_environ` drops header names containing `_`, like gunicorn's
  default `header_map = "drop"`, and builds environ keys at full length.
  `X-Auth-User` and `X_Auth_User` used to share `HTTP_X_AUTH_USER`, with the
  values joined, so a client could add to a header set by a proxy, and names
  were cut at 256 bytes.

### Fixed

- Reject a `Content-Length` or a chunk size too large to represent instead of
  wrapping it around to a small value (RFC 9110 section 8.6, RFC 9112 section
  7.1).
- Reject obsolete line folding, a header line that starts with a space or a
  tab, on every request entry point (RFC 9112 section 5.2). It used to be
  kept as a separate field with an empty name.
- Handle `Transfer-Encoding` as gunicorn's pure-Python parsers do (RFC 9112
  section 6.1): an empty coding or empty list member is rejected, and `gzip`,
  `deflate` or `compress` closes the connection after the message instead of
  reading the body that follows as the next request. `deflate` is now
  recognised; it used to be rejected as unsupported.
- Apply `limit_request_field_size` to a complete chunk-size or trailer line,
  as already done while the line is still arriving (RFC 9112 sections 7.1.1
  and 7.1.2). The same line is now rejected however its bytes are split.
- Require CRLF at the end of every line of a request. The parser also
  accepted a lone LF to end the request line and header lines, and let one
  through inside a chunk-size line or a trailer section, so a request that
  gunicorn's pure-Python parsers reject could be accepted here. RFC 9112
  section 2.2 lets a server accept a lone LF; gunicorn_h1c no longer does,
  so all three parsers agree. A lone CR stays invalid. Chunk-size lines and
  trailers follow the same rule (RFC 9112 sections 7.1 and 7.1.2).
- Bound the chunk-size line and the trailer section in `H1CProtocol`. A
  chunk-size line or a trailer line that never ended was buffered without
  limit, and so was any number of trailer lines. A chunk-size line is now
  limited by `limit_request_field_size`, each trailer line by
  `limit_request_field_size` and the number of trailer lines by
  `limit_request_fields`, as in gunicorn's pure-Python parsers (RFC 9112
  sections 7.1.1 and 7.1.2). gunicorn answers 400 and 431.
- Bound the request line and header lines in `H1CProtocol` while they are
  still arriving. A request line or header line that never ended was
  buffered without limit, and searching it cost more on each read. It now
  stops at `limit_request_line` or `limit_request_field_size`, and too many
  header lines are refused as soon as they arrive instead of at the end of
  the headers.
- Free `H1CProtocol` objects caught in a reference cycle. The garbage
  collector could not see the callbacks a parser holds, so a parser whose
  callbacks point back to its owner was never freed. gunicorn's ASGI worker
  builds that cycle on every connection, so with `--http-parser fast` each
  connection leaked about 10 KB until the worker was restarted (#20).

### Added

- Support for free-threaded CPython (3.14t and later). Importing
  gunicorn_h1c no longer turns the GIL back on, and the parsers can be used
  from several threads at once. Each `H1CProtocol` is still meant to be used
  by one thread at a time. On a free-threaded build, `H1CProtocol.headers`
  returns a copy of the header list (#16).

### Changed

- A request that ends a line with a lone LF now raises a parse error (a 400
  in gunicorn) on every entry point: `parse_request`, `parse_request_fast`,
  `parse_request_raw`, `parse_headers`, `parse_to_wsgi_environ`,
  `parse_to_asgi_scope` and `H1CProtocol`. `parse_response` still accepts a
  lone LF.

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
