"""Feed the same bytes to gunicorn_h1c and to gunicorn's pure-Python parsers.

Usage:

    GUNICORN_SRC=/path/to/gunicorn python crosscheck.py

GUNICORN_SRC is a checkout of gunicorn master; gunicorn_h1c is imported from
the current environment (an editable install or PYTHONPATH=src).

Every input is fed whole and one byte at a time to H1CProtocol, to gunicorn's
ASGI PythonProtocol and to its WSGI RequestParser (http_parser = "python").
The one-shot gunicorn_h1c functions get the input whole. Each result is "ok"
(message complete), "wait" (needs more data) or "rej:<exception>".

The script exits 1 if gunicorn_h1c completes a message that a Python parser
rejects. Other differences are printed for review: "wait" against "rej" means
neither side accepts the input.
"""

import os
import sys

sys.path.insert(0, os.environ.get("GUNICORN_SRC", "../gunicorn"))

from gunicorn.asgi.parser import PythonProtocol  # noqa: E402
from gunicorn.config import Config  # noqa: E402
from gunicorn.http.errors import NoMoreData  # noqa: E402
from gunicorn.http.parser import RequestParser  # noqa: E402

import gunicorn_h1c as h1c  # noqa: E402

CFG = Config()
CFG.set("http_parser", "python")
LIMITS = dict(
    limit_request_line=CFG.limit_request_line,
    limit_request_fields=CFG.limit_request_fields,
    limit_request_field_size=CFG.limit_request_field_size,
)

H1C_ERRORS = (h1c.ParseError, h1c._parser_fast.ParseError, h1c._protocol.ParseError)
H1C_INCOMPLETE = (h1c.IncompleteError, h1c._parser_fast.IncompleteError)

HEAD = b"POST / HTTP/1.1\r\nHost: a\r\nTransfer-Encoding: chunked\r\n\r\n"


def post(headers, body=b""):
    return b"POST / HTTP/1.1\r\nHost: a\r\n" + headers + b"\r\n" + body


CASES = {
    # valid requests that must keep parsing
    "GET": b"GET / HTTP/1.1\r\nHost: a\r\nX: 1\r\n\r\n",
    "HTTP/1.0, no headers": b"GET / HTTP/1.0\r\n\r\n",
    "leading CRLF": b"\r\nGET / HTTP/1.1\r\nHost: a\r\n\r\n",
    "Content-Length": post(b"Content-Length: 5\r\n", b"hello"),
    "chunked": HEAD + b"5\r\nhello\r\n0\r\n\r\n",
    "chunked + extension": HEAD + b"5;name=value\r\nhello\r\n0\r\n\r\n",
    "chunked + trailer": HEAD + b"5\r\nhello\r\n0\r\nX-Sum: 1\r\n\r\n",
    "gzip, chunked": post(
        b"Transfer-Encoding: gzip, chunked\r\n", b"3\r\nabc\r\n0\r\n\r\n"
    ),
    "identity + Content-Length": post(
        b"Transfer-Encoding: identity\r\nContent-Length: 3\r\n", b"abc"
    ),
    "100 headers": b"GET / HTTP/1.1\r\n" + b"X-H: 1\r\n" * 100 + b"\r\n",
    # line terminators (RFC 9112 section 2.2)
    "bare LF everywhere": b"GET / HTTP/1.1\nHost: a\nX: 1\n\n",
    "bare LF after request line": b"GET / HTTP/1.1\nHost: a\r\n\r\n",
    "bare LF in header block": b"GET / HTTP/1.1\r\nHost: a\nX: 1\r\n\r\n",
    "bare CR in header block": b"GET / HTTP/1.1\r\nHost: a\rX: 1\r\n\r\n",
    "bare LF ends chunk size": HEAD + b"5\nhello\r\n0\r\n\r\n",
    "bare LF in trailer": HEAD + b"0\r\nX: 1\nY: 2\r\n\r\n",
    # framing
    "obsolete folding": b"GET / HTTP/1.1\r\nHost: a\r\nX: 1\r\n 2\r\n\r\n",
    "Content-Length 2**64": post(b"Content-Length: 18446744073709551616\r\n"),
    "chunk size 2**64": HEAD + b"10000000000000000\r\n0\r\n\r\n",
    "duplicate Content-Length": post(
        b"Content-Length: 1\r\nContent-Length: 2\r\n", b"ab"
    ),
    "Content-Length + chunked": post(
        b"Content-Length: 3\r\nTransfer-Encoding: chunked\r\n", b"0\r\n\r\n"
    ),
    "empty Transfer-Encoding": post(b"Transfer-Encoding: \r\n"),
    "Transfer-Encoding gzip,": post(b"Transfer-Encoding: gzip, \r\n"),
    "unknown Transfer-Encoding": post(b"Transfer-Encoding: foo\r\n"),
    "bad extension": HEAD + b"1;a=b c\r\nx\r\n0\r\n\r\n",
    # limits
    "endless request line": b"GET /" + b"a" * 20000,
    "endless header line": b"GET / HTTP/1.1\r\nX: " + b"a" * 20000,
    "101 headers": b"GET / HTTP/1.1\r\n" + b"X-H: 1\r\n" * 101 + b"\r\n",
    "endless chunk-size line": HEAD + b"1;" + b"a" * 20000,
    "endless trailer line": HEAD + b"0\r\nX: " + b"a" * 20000,
    "101 trailers": HEAD + b"0\r\n" + b"X-T: 1\r\n" * 101 + b"\r\n",
}

# Differences already reviewed, with the reason. They are reported but do not
# fail the run. Remove an entry once gunicorn's parsers are changed.
KNOWN_DIFFERENCES = {}

# Byte-by-byte runs of these only repeat the whole run, slowly.
SKIP_BYTEWISE = {
    "endless request line",
    "endless header line",
    "endless chunk-size line",
    "endless trailer line",
}

ONE_SHOT = (
    "parse_request",
    "parse_request_fast",
    "parse_to_wsgi_environ",
    "parse_to_asgi_scope",
)


def pieces(data, bytewise):
    if bytewise:
        return [data[i : i + 1] for i in range(len(data))]
    return [data]


def run_protocol(cls, data, bytewise):
    p = cls(**LIMITS)
    try:
        for part in pieces(data, bytewise):
            p.feed(part)
            if p.is_complete:
                return "ok"
    except Exception as e:
        return "rej:" + type(e).__name__
    return "wait"


def run_wsgi(data, bytewise):
    parser = RequestParser(CFG, iter(pieces(data, bytewise)), ("127.0.0.1", 1))
    try:
        req = next(parser)
        while req.body.read(8192):
            pass
    except (NoMoreData, StopIteration):
        return "wait"
    except Exception as e:
        return "rej:" + type(e).__name__
    return "ok"


def run_one_shot(name, data):
    func = getattr(h1c, name)
    kwargs = LIMITS if name in ("parse_request", "parse_request_fast") else {}
    try:
        func(data, **kwargs)
    except H1C_INCOMPLETE:
        return "wait"
    except H1C_ERRORS as e:
        return "rej:" + type(e).__name__
    return "ok"


def kind(result):
    return result.split(":")[0]


def main():
    rows = []
    for name, data in CASES.items():
        for bytewise in (False, True):
            if bytewise and name in SKIP_BYTEWISE:
                continue
            res = {
                "H1CProtocol": run_protocol(h1c.H1CProtocol, data, bytewise),
                "PythonProtocol": run_protocol(PythonProtocol, data, bytewise),
                "WSGI python": run_wsgi(data, bytewise),
            }
            if not bytewise:
                for fname in ONE_SHOT:
                    res[fname] = run_one_shot(fname, data)
            rows.append((name + (" [bytewise]" if bytewise else ""), res))

    cols = list(rows[0][1])
    width = max(len(label) for label, _ in rows) + 2
    print("".ljust(width) + "".join(c[:14].ljust(16) for c in cols))
    for label, res in rows:
        print(
            label.ljust(width) + "".join(res.get(c, "-")[:14].ljust(16) for c in cols)
        )

    differ, unsafe = [], []
    for label, res in rows:
        ref = {kind(res["PythonProtocol"]), kind(res["WSGI python"])}
        mine = kind(res["H1CProtocol"])
        if len(ref) > 1 or mine not in ref:
            differ.append(label)
        if mine == "ok" and "rej" in ref:
            if label.replace(" [bytewise]", "") not in KNOWN_DIFFERENCES:
                unsafe.append(label)

    print()
    print(f"{len(rows)} runs, {len(differ)} where the parsers do not all agree")
    for label in differ:
        why = KNOWN_DIFFERENCES.get(label.replace(" [bytewise]", ""))
        print("  ", label + (f"  (known: {why})" if why else ""))
    if unsafe:
        print("gunicorn_h1c completes messages a Python parser rejects:")
        for label in unsafe:
            print("  ", label)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
