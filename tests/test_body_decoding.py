"""Request bodies decode the same however the bytes are split.

H1CProtocol decodes bodies straight from the bytes given to feed() and keeps
only what it cannot use yet. These tests build valid chunked and
Content-Length requests, feed them whole, at random cuts and one byte at a
time, and compare the body handed to on_body, completion and remaining()
with what was sent.
"""

import random

import pytest

from gunicorn_h1c import H1CProtocol

NEXT_REQUEST = b"GET /next HTTP/1.1\r\nHost: a\r\n\r\n"


def chunked_request(rnd, n_chunks, max_size, trailers, extensions):
    body = bytearray()
    out = bytearray(
        b"POST /up HTTP/1.1\r\nHost: a\r\nTransfer-Encoding: chunked\r\n\r\n"
    )
    for _ in range(n_chunks):
        size = rnd.randint(1, max_size)
        data = bytes(rnd.getrandbits(8) for _ in range(size))
        body += data
        ext = (
            b";x=%d" % rnd.randint(0, 99) if extensions and rnd.random() < 0.3 else b""
        )
        hexsize = (b"%x" if rnd.random() < 0.5 else b"%X") % size
        if rnd.random() < 0.2:
            hexsize = b"0" + hexsize
        out += hexsize + ext + b"\r\n" + data + b"\r\n"
    out += b"0\r\n"
    for i in range(trailers):
        out += b"X-T%d: %d\r\n" % (i, rnd.randint(0, 9999))
    out += b"\r\n"
    return bytes(out), bytes(body)


def feed(data, cuts):
    received = []
    done = []
    p = H1CProtocol(on_body=received.append, on_message_complete=lambda: done.append(1))
    start = 0
    for cut in list(cuts) + [len(data)]:
        if cut > start:
            p.feed(data[start:cut])
        start = cut
    return p, b"".join(received), done


def random_cuts(rnd, n, count):
    if n <= 1:
        return []
    return sorted(rnd.sample(range(1, n), min(count, n - 1)))


@pytest.mark.parametrize("seed", range(40))
def test_chunked_body_any_split(seed):
    rnd = random.Random(seed)
    n_chunks = rnd.choice([1, 2, 5, 40, 400])
    max_size = rnd.choice([1, 16, 1024, 8192])
    data, body = chunked_request(rnd, n_chunks, max_size, rnd.randint(0, 3), seed % 2)
    data += NEXT_REQUEST
    splits = [[]] + [random_cuts(rnd, len(data), k) for k in (1, 2, 7, 50)]
    if len(data) <= 20000:
        splits.append(range(1, len(data)))
    for cuts in splits:
        p, got, done = feed(data, cuts)
        assert p.is_complete
        assert got == body
        assert done == [1]
        assert p.remaining() == NEXT_REQUEST


@pytest.mark.parametrize("seed", range(20))
def test_content_length_body_any_split(seed):
    rnd = random.Random(1000 + seed)
    body = bytes(rnd.getrandbits(8) for _ in range(rnd.choice([1, 100, 4096, 70000])))
    data = (
        b"POST /up HTTP/1.1\r\nHost: a\r\nContent-Length: %d\r\n\r\n" % len(body)
        + body
        + NEXT_REQUEST
    )
    for cuts in ([], random_cuts(rnd, len(data), 1), random_cuts(rnd, len(data), 9)):
        p, got, done = feed(data, cuts)
        assert p.is_complete
        assert got == body
        assert p.remaining() == NEXT_REQUEST


def test_line_and_crlf_split_at_every_byte():
    """Every cut inside a size line, an extension, a CRLF and a trailer."""
    data = (
        b"POST / HTTP/1.1\r\nHost: a\r\nTransfer-Encoding: chunked\r\n\r\n"
        b"5;name=value\r\nhello\r\n0006\r\n world\r\n0\r\nX-T: 1\r\n\r\n" + NEXT_REQUEST
    )
    for cut in range(1, len(data)):
        for second in (cut, min(len(data) - 1, cut + 1), min(len(data) - 1, cut + 3)):
            p, got, _ = feed(data, sorted({cut, second}))
            assert got == b"hello world", (cut, second)
            assert p.remaining() == NEXT_REQUEST


def test_input_is_not_kept():
    """The parser copies what it keeps, so the caller may reuse its buffer."""
    head = b"POST /x HTTP/1.1\r\nHost: a\r\nTransfer-Encoding: chunked\r\n\r\n"
    received = []
    p = H1CProtocol(on_body=received.append)
    for piece in (
        head + b"5\r\nhel",
        b"lo\r\n3",
        b"\r\nabc\r\n0\r\n\r\n" + NEXT_REQUEST,
    ):
        buf = bytearray(piece)
        p.feed(buf)
        buf[:] = b"X" * len(buf)
    assert p.path == b"/x"
    assert p.headers == [(b"Host", b"a"), (b"Transfer-Encoding", b"chunked")]
    assert b"".join(received) == b"helloabc"
    assert p.remaining() == NEXT_REQUEST


HEAD = b"POST / HTTP/1.1\r\nHost: a\r\nTransfer-Encoding: chunked\r\n\r\n"


def test_one_on_body_call_per_feed():
    """Chunk data decoded in one feed() arrives in one call, as a
    Content-Length body does."""
    received = []
    p = H1CProtocol(on_body=received.append)
    p.feed(HEAD + b"5\r\nhello\r\n6\r\n world\r\n1\r\n!\r\n0\r\n\r\n")
    assert received == [b"hello world!"]
    assert p.is_complete


def test_one_call_per_read():
    received = []
    p = H1CProtocol(on_body=received.append)
    p.feed(HEAD + b"5\r\nhello\r\n3\r\nab")
    p.feed(b"c\r\n2\r\nde\r\n0\r\n\r\n")
    assert received == [b"helloab", b"cde"]


def test_data_before_an_error_is_delivered():
    from gunicorn_h1c._protocol import ParseError

    received = []
    p = H1CProtocol(on_body=received.append)
    with pytest.raises(ParseError, match="chunk size"):
        p.feed(HEAD + b"5\r\nhello\r\n3\r\nabc\r\nZZ\r\n")
    assert received == [b"helloabc"]


def test_on_body_error_wins():
    def on_body(chunk):
        raise RuntimeError("app")

    p = H1CProtocol(on_body=on_body)
    with pytest.raises(RuntimeError, match="app"):
        p.feed(HEAD + b"5\r\nhello\r\nZZ\r\n")
