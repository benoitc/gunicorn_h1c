# gunicorn_h1c

Fast HTTP/1.1 parser for Gunicorn using [picohttpparser](https://github.com/h2o/picohttpparser).

## Features

- SIMD-optimized parsing (SSE4.2 on x86, NEON on ARM)
- Zero-copy request parsing with lazy Python object creation
- Common header extraction (Content-Length, Transfer-Encoding, Connection)
- Incremental parsing support
- WSGI environ and ASGI scope generation
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
| httptools | ~2,200,000 |
| Pure Python | ~150,000 |

## API Reference

### Request Parsing

#### `parse_request(data, last_len=0) -> dict`

Parse HTTP request, returns dict with:
- `method`: bytes
- `path`: bytes
- `minor_version`: int (0 or 1)
- `headers`: list of (name, value) tuples
- `consumed`: int (bytes consumed)

#### `parse_request_fast(data, last_len=0) -> HttpRequest`

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

#### `parse_to_wsgi_environ(data, server=None, client=None, url_scheme="http") -> dict`

Parse HTTP request and build WSGI environ dict. Parameters:
- `data`: Raw HTTP request bytes
- `server`: (host, port) tuple for SERVER_NAME/SERVER_PORT
- `client`: (addr, port) tuple for REMOTE_ADDR/REMOTE_PORT
- `url_scheme`: URL scheme (default "http")

Returns dict with `REQUEST_METHOD`, `PATH_INFO`, `QUERY_STRING`, `SERVER_PROTOCOL`, `HTTP_*` headers, and `_consumed`.

#### `parse_to_asgi_scope(data, server=None, client=None, scheme="http", root_path="") -> dict`

Parse HTTP request and build ASGI scope dict. Parameters:
- `data`: Raw HTTP request bytes
- `server`: (host, port) tuple
- `client`: (addr, port) tuple
- `scheme`: URL scheme (default "http")
- `root_path`: ASGI root_path (default "")

Returns dict with `type`, `asgi`, `http_version`, `method`, `scheme`, `path`, `raw_path`, `query_string`, `root_path`, `headers`, `server`, `client`, and `_consumed`.

### Exceptions

- `ParseError`: Invalid HTTP request/response
- `IncompleteError`: Need more data (incremental parsing)

## License

MIT License (picohttpparser) + Apache 2.0 (Python bindings)

## Credits

- [picohttpparser](https://github.com/h2o/picohttpparser) by Kazuho Oku et al.
- Python bindings by Benoit Chesneau
