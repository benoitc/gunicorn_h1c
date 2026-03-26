# gunicorn_h1c

Fast HTTP/1.1 parser for Gunicorn using [picohttpparser](https://github.com/h2o/picohttpparser).

## Features

- SIMD-optimized parsing (SSE4.2 on x86, NEON on ARM)
- Zero-copy request parsing with lazy Python object creation
- Callback-based parser for asyncio integration (H1CProtocol)
- Common header extraction (Content-Length, Transfer-Encoding, Connection)
- Incremental parsing support
- Chunked transfer encoding support
- WSGI environ and ASGI scope generation
- Limit enforcement matching gunicorn's Python parser
- Specific exception types for validation errors
- Python 3.9+

## Installation

```bash
pip install gunicorn_h1c
```

## Usage

### Basic Parsing

```python
from gunicorn_h1c import parse_request

data = b"GET /path?query=1 HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n"
result = parse_request(data)

print(result['method'])        # b'GET'
print(result['path'])          # b'/path?query=1'
print(result['minor_version']) # 1 (HTTP/1.1)
print(result['headers'])       # [(b'Host', b'localhost'), (b'Content-Length', b'0')]
print(result['consumed'])      # 67 (bytes consumed)
```

### Fast Parsing (Zero-Copy)

```python
from gunicorn_h1c import parse_request_fast

data = b"POST /api HTTP/1.1\r\nContent-Length: 100\r\nTransfer-Encoding: chunked\r\n\r\n"
req = parse_request_fast(data)

# Properties are created lazily - only when accessed
print(req.method)          # b'POST'
print(req.path)            # b'/api'
print(req.consumed)        # bytes consumed

# Common headers extracted during parse (no Python overhead)
print(req.content_length)  # 100
print(req.has_chunked)     # True
print(req.connection_close) # -1 (not set), 0 (keep-alive), 1 (close)

# Header lookup (case-insensitive)
print(req.get_header("content-length"))  # b'100'
```

### Response Parsing

```python
from gunicorn_h1c import parse_response

data = b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 13\r\n\r\n"
result = parse_response(data)

print(result['status'])        # 200
print(result['message'])       # b'OK'
print(result['minor_version']) # 1
print(result['headers'])       # [(b'Content-Type', b'text/html'), ...]
print(result['consumed'])      # bytes consumed
```

### Header-Only Parsing

```python
from gunicorn_h1c import parse_headers

data = b"Content-Type: text/html\r\nContent-Length: 100\r\n\r\n"
headers = parse_headers(data)

print(headers)  # [(b'Content-Type', b'text/html'), (b'Content-Length', b'100')]
```

### WSGI Environ Creation

```python
from gunicorn_h1c import parse_to_wsgi_environ

data = b"GET /path?foo=bar HTTP/1.1\r\nHost: example.com\r\nContent-Type: text/plain\r\n\r\n"
environ = parse_to_wsgi_environ(
    data,
    server=("example.com", 80),
    client=("192.168.1.1", 54321),
    url_scheme="https"
)

print(environ['REQUEST_METHOD'])  # 'GET'
print(environ['PATH_INFO'])       # '/path'
print(environ['QUERY_STRING'])    # 'foo=bar'
print(environ['SERVER_NAME'])     # 'example.com'
print(environ['SERVER_PORT'])     # '80'
print(environ['REMOTE_ADDR'])     # '192.168.1.1'
print(environ['HTTP_HOST'])       # 'example.com'
print(environ['CONTENT_TYPE'])    # 'text/plain'
print(environ['wsgi.url_scheme']) # 'https'
print(environ['_consumed'])       # bytes consumed
```

### ASGI Scope Creation

```python
from gunicorn_h1c import parse_to_asgi_scope

data = b"POST /api HTTP/1.1\r\nHost: example.com\r\nContent-Length: 50\r\n\r\n"
scope = parse_to_asgi_scope(
    data,
    server=("example.com", 443),
    client=("10.0.0.1", 12345),
    scheme="https",
    root_path="/v1"
)

print(scope['type'])         # 'http'
print(scope['asgi'])         # {'version': '3.0', 'spec_version': '2.4'}
print(scope['http_version']) # '1.1'
print(scope['method'])       # 'POST'
print(scope['scheme'])       # 'https'
print(scope['path'])         # '/api'
print(scope['raw_path'])     # b'/api'
print(scope['query_string']) # b''
print(scope['root_path'])    # '/v1'
print(scope['headers'])      # [(b'host', b'example.com'), ...]
print(scope['server'])       # ('example.com', 443)
print(scope['client'])       # ('10.0.0.1', 12345)
print(scope['_consumed'])    # bytes consumed
```

### Callback-Based Protocol Parser (asyncio)

For asyncio servers, `H1CProtocol` provides a callback-based API that enables zero-copy,
synchronous parsing in `data_received()`:

```python
import asyncio
from gunicorn_h1c import H1CProtocol

class MyProtocol(asyncio.Protocol):
    def connection_made(self, transport):
        self.transport = transport
        self.parser = H1CProtocol(
            on_headers_complete=self._on_headers,
            on_body=self._on_body,
            on_message_complete=self._on_complete,
        )

    def data_received(self, data):
        try:
            self.parser.feed(data)
        except ParseError as e:
            self.transport.close()

    def _on_headers(self):
        # Build ASGI scope or process headers
        method = self.parser.method  # b'GET'
        path = self.parser.path      # b'/path'
        headers = self.parser.headers  # [(b'Host', b'localhost'), ...]

        # Return True to skip body parsing (e.g., for HEAD requests)
        return self.parser.method == b"HEAD"

    def _on_body(self, chunk):
        # Process body chunk (zero-copy)
        pass

    def _on_complete(self):
        # Request complete, send response
        self.parser.reset()  # Reuse for next request (keep-alive)
```

### Limit Enforcement

All parsing functions enforce limits matching gunicorn's Python parser:

```python
from gunicorn_h1c import parse_request, LimitRequestLine, LimitRequestHeaders

# Default limits: request_line=8190, fields=100, field_size=8190
try:
    result = parse_request(data)
except LimitRequestLine:
    # Request line too long
    pass
except LimitRequestHeaders:
    # Too many headers or header too large
    pass

# Custom limits
result = parse_request(
    data,
    limit_request_line=4096,      # Max request line length
    limit_request_fields=50,       # Max number of headers
    limit_request_field_size=4096  # Max header size (name + value)
)

# Allow unconventional methods (lowercase, short, etc.)
result = parse_request(
    b"get / HTTP/1.1\r\n\r\n",
    permit_unconventional_http_method=True
)
```

### Incremental Parsing

```python
from gunicorn_h1c import parse_request, IncompleteError

buffer = b"GET / HTTP/1.1\r\n"
last_len = 0

while True:
    try:
        result = parse_request(buffer, last_len=last_len)
        break  # Complete request
    except IncompleteError:
        last_len = len(buffer)
        buffer += read_more_data()  # Get more data
```

### Raw Parsing (Maximum Speed)

For scenarios requiring maximum performance, `parse_request_raw` returns offsets into the original buffer:

```python
from gunicorn_h1c import parse_request_raw

data = b"GET /path HTTP/1.1\r\nHost: localhost\r\n\r\n"
result = parse_request_raw(data)

# Returns: (method_offset, method_len, path_offset, path_len,
#           minor_version, header_count, consumed, header_data)
method_offset, method_len, path_offset, path_len, version, header_count, consumed, header_data = result

method = data[method_offset:method_offset + method_len]  # b'GET'
path = data[path_offset:path_offset + path_len]          # b'/path'
```

## Performance

Benchmarks on Apple M4 Pro (single thread):

| Parser | Requests/sec |
|--------|-------------|
| gunicorn_h1c (fast) | ~2,500,000 |
| gunicorn_h1c (H1CProtocol, reused) | ~4,700,000 |
| httptools | ~2,200,000 |
| Pure Python | ~150,000 |

**H1CProtocol Performance:**
- Simple GET: ~4.7M req/s (209ns/op) when reusing parser
- Incremental parsing: ~3x faster than pull-based API with buffer + retry
- Body parsing: ~3.0M req/s for chunked, ~3.7M req/s for Content-Length

## API Reference

### Request Parsing

#### `parse_request(data, last_len=0, ...) -> dict`

Parse HTTP request, returns dict with:
- `method`: bytes
- `path`: bytes
- `minor_version`: int (0 or 1)
- `headers`: list of (name, value) tuples
- `consumed`: int (bytes consumed)

**Optional parameters:**
- `limit_request_line`: int (default 8190) - Maximum request line length
- `limit_request_fields`: int (default 100) - Maximum number of headers
- `limit_request_field_size`: int (default 8190) - Maximum header size
- `permit_unconventional_http_method`: bool (default False) - Allow lowercase/short methods
- `permit_unconventional_http_version`: bool (default False) - Allow non-1.0/1.1 versions

#### `parse_request_fast(data, last_len=0, ...) -> HttpRequest`

Parse HTTP request with zero-copy optimization, returns `HttpRequest` object with:
- `method`: bytes (lazy)
- `path`: bytes (lazy)
- `minor_version`: int
- `headers`: tuple of (name, value) tuples (lazy)
- `consumed`: int
- `header_count`: int
- `content_length`: int (-1 if not set)
- `has_chunked`: bool
- `connection_close`: int (-1=unset, 0=keep-alive, 1=close)
- `get_header(name)`: bytes or None (case-insensitive lookup)

**Optional parameters:** Same as `parse_request()`.

#### `parse_request_raw(data, last_len=0) -> tuple`

Ultra-fast parsing returning raw offsets:
- `method_offset`: int
- `method_len`: int
- `path_offset`: int
- `path_len`: int
- `minor_version`: int
- `header_count`: int
- `consumed`: int
- `header_data`: bytes (packed header offsets)

### Callback-Based Protocol Parser

#### `H1CProtocol`

Callback-based HTTP/1.1 parser for asyncio integration.

**Constructor:**
```python
H1CProtocol(
    on_message_begin=None,      # () -> None
    on_url=None,                # (url: bytes) -> None
    on_header=None,             # (name: bytes, value: bytes) -> None
    on_headers_complete=None,   # () -> bool (return True to skip body)
    on_body=None,               # (chunk: bytes) -> None
    on_message_complete=None,   # () -> None
    limit_request_line=8190,    # Maximum request line length
    limit_request_fields=100,   # Maximum number of headers
    limit_request_field_size=8190,  # Maximum header size
    permit_unconventional_http_method=False,
    permit_unconventional_http_version=False,
)
```

**Methods:**
- `feed(data: bytes) -> None`: Feed data to parser. Callbacks fire synchronously.
- `reset() -> None`: Reset parser for next request (keepalive).
- `get_header(name: bytes) -> bytes | None`: Case-insensitive header lookup.

**Properties (valid after on_headers_complete):**
- `method`: bytes - HTTP method (GET, POST, etc.)
- `path`: bytes - Request path including query string
- `http_version`: tuple[int, int] - HTTP version as (major, minor)
- `headers`: list[tuple[bytes, bytes]] - List of (name, value) tuples
- `content_length`: int | None - Content-Length value or None
- `is_chunked`: bool - True if Transfer-Encoding: chunked
- `should_keep_alive`: bool - True if connection should be kept alive
- `should_upgrade`: bool - True if Upgrade header present
- `is_complete`: bool - True if message parsing is complete

### Response Parsing

#### `parse_response(data, last_len=0) -> dict`

Parse HTTP response, returns dict with:
- `status`: int (status code)
- `message`: bytes (status message)
- `minor_version`: int (0 or 1)
- `headers`: list of (name, value) tuples
- `consumed`: int (bytes consumed)

### Header Parsing

#### `parse_headers(data, last_len=0) -> list`

Parse HTTP headers only, returns list of (name, value) tuples.

### WSGI/ASGI Support

#### `parse_to_wsgi_environ(data, server=None, client=None, url_scheme="http", ...) -> dict`

Parse HTTP request and build WSGI environ dict. Parameters:
- `data`: Raw HTTP request bytes
- `server`: (host, port) tuple for SERVER_NAME/SERVER_PORT
- `client`: (addr, port) tuple for REMOTE_ADDR/REMOTE_PORT
- `url_scheme`: URL scheme (default "http")

**Optional parameters:** Same limit/flag parameters as `parse_request()`.

Returns dict with `REQUEST_METHOD`, `PATH_INFO`, `QUERY_STRING`, `SERVER_PROTOCOL`, `HTTP_*` headers, and `_consumed`.

#### `parse_to_asgi_scope(data, server=None, client=None, scheme="http", root_path="", ...) -> dict`

Parse HTTP request and build ASGI scope dict. Parameters:
- `data`: Raw HTTP request bytes
- `server`: (host, port) tuple
- `client`: (addr, port) tuple
- `scheme`: URL scheme (default "http")
- `root_path`: ASGI root_path (default "")

**Optional parameters:** Same limit/flag parameters as `parse_request()`.

Returns dict with `type`, `asgi`, `http_version`, `method`, `scheme`, `path`, `raw_path`, `query_string`, `root_path`, `headers`, `server`, `client`, and `_consumed`.

### Exceptions

**Base exceptions:**
- `ParseError`: Base exception for parse errors (inherits from `ValueError`)
- `IncompleteError`: Need more data (incremental parsing)

**Validation exceptions (inherit from `ParseError`):**
- `LimitRequestLine`: Request line exceeds `limit_request_line`
- `LimitRequestHeaders`: Too many headers or header exceeds `limit_request_field_size`
- `InvalidRequestMethod`: Invalid method characters or format (lowercase, too short, contains `#`)
- `InvalidHTTPVersion`: HTTP version not 1.0 or 1.1 (e.g., HTTP/2.0, HTTP/0.9)
- `InvalidHeaderName`: Invalid header name characters (not RFC 9110 token, e.g., space)
- `InvalidHeader`: Invalid header value (contains NUL, CR, or LF)
- `InvalidChunkExtension`: Chunk extension contains bare CR (RFC 9112 violation)

When parsing fails, the parser analyzes the buffer to raise the most specific exception possible, helping identify the exact cause of malformed requests.

## License

MIT License (picohttpparser) + Apache 2.0 (Python bindings)

## Credits

- [picohttpparser](https://github.com/h2o/picohttpparser) by Kazuho Oku et al.
- Python bindings by Benoit Chesneau
