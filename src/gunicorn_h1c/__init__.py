"""
gunicorn_h1c - Fast HTTP/1.1 parser for Gunicorn

This package provides high-performance HTTP/1.1 parsing using
picohttpparser with SIMD optimizations (SSE4.2 on x86, NEON on ARM).

Two parser implementations are available:

1. _parser - Basic parser returning dicts (simpler API)
2. _parser_fast - Optimized parser with zero-copy HttpRequest objects

Usage:
    from gunicorn_h1c import parse_request, parse_request_fast, HttpRequest

    # Basic parsing (returns dict)
    result = parse_request(b"GET / HTTP/1.1\\r\\nHost: localhost\\r\\n\\r\\n")
    print(result['method'])  # b'GET'
    print(result['path'])    # b'/'
    print(result['headers']) # [(b'Host', b'localhost')]

    # Fast parsing (returns HttpRequest object with lazy evaluation)
    req = parse_request_fast(b"GET / HTTP/1.1\\r\\nHost: localhost\\r\\n\\r\\n")
    print(req.method)         # b'GET' (created lazily)
    print(req.content_length) # -1 (extracted during parse)
    print(req.has_chunked)    # False
"""

__version__ = "0.2.0"
__all__ = [
    # Basic parser
    "parse_request",
    "parse_response",
    "parse_headers",
    "parse_to_wsgi_environ",
    "parse_to_asgi_scope",
    # Fast parser
    "parse_request_fast",
    "parse_request_raw",
    "HttpRequest",
    # Callback-based protocol parser
    "H1CProtocol",
    # Exceptions
    "ParseError",
    "IncompleteError",
]

# Import from C extensions
try:
    from gunicorn_h1c._parser import (
        parse_request,
        parse_response,
        parse_headers,
        parse_to_wsgi_environ,
        parse_to_asgi_scope,
        ParseError,
        IncompleteError,
    )
except ImportError as e:
    raise ImportError(
        "Failed to import gunicorn_h1c._parser C extension. "
        "Please ensure the package is properly installed with: "
        "pip install gunicorn_h1c"
    ) from e

try:
    from gunicorn_h1c._parser_fast import (
        parse_request as parse_request_fast,
        parse_request_raw,
        HttpRequest,
    )
except ImportError as e:
    raise ImportError(
        "Failed to import gunicorn_h1c._parser_fast C extension. "
        "Please ensure the package is properly installed with: "
        "pip install gunicorn_h1c"
    ) from e

try:
    from gunicorn_h1c._protocol import (
        H1CProtocol,
        ParseError as ProtocolParseError,
    )
except ImportError as e:
    raise ImportError(
        "Failed to import gunicorn_h1c._protocol C extension. "
        "Please ensure the package is properly installed with: "
        "pip install gunicorn_h1c"
    ) from e
