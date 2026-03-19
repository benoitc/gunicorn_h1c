# gunicorn_h1c

Fast HTTP/1.1 parser for Gunicorn using [picohttpparser](https://github.com/h2o/picohttpparser).

## Features

- SIMD-optimized parsing (SSE4.2 on x86, NEON on ARM)
- Zero-copy request parsing with lazy Python object creation
- Common header extraction (Content-Length, Transfer-Encoding, Connection)
- Incremental parsing support
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

## Performance

Benchmarks on Apple M4 Pro (single thread):

| Parser | Requests/sec |
|--------|-------------|
| gunicorn_h1c (fast) | ~2,500,000 |
| httptools | ~2,200,000 |
| Pure Python | ~150,000 |

## API Reference

### `parse_request(data, last_len=0) -> dict`

Parse HTTP request, returns dict with:
- `method`: bytes
- `path`: bytes
- `minor_version`: int (0 or 1)
- `headers`: list of (name, value) tuples
- `consumed`: int (bytes consumed)

### `parse_request_fast(data) -> HttpRequest`

Parse HTTP request with zero-copy optimization, returns `HttpRequest` object with:
- `method`: bytes (lazy)
- `path`: bytes (lazy)
- `minor_version`: int
- `headers`: tuple of (name, value) tuples (lazy)
- `consumed`: int
- `content_length`: int (-1 if not set)
- `has_chunked`: bool
- `connection_close`: int (-1=unset, 0=keep-alive, 1=close)
- `get_header(name)`: bytes or None

### Exceptions

- `ParseError`: Invalid HTTP request
- `IncompleteError`: Need more data (incremental parsing)

## License

MIT License (picohttpparser) + Apache 2.0 (Python bindings)

## Credits

- [picohttpparser](https://github.com/h2o/picohttpparser) by Kazuho Oku et al.
- Python bindings by Benoit Chesneau
