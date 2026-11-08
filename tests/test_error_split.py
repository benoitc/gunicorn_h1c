"""The same invalid bytes give the same error wherever the reads split them.

gunicorn's WSGI worker calls parse_request(data, last_len=N) each time more
data arrives, and its ASGI worker feeds H1CProtocol one socket read at a time.
Every input the test suite rejects (tests/data/invalid_corpus.py, recorded by
tests/record_invalid_corpus.py) is replayed here at every split point, and the
exception class and message must be the ones a parse of the whole input gives.
"""

import importlib.util
import pathlib

import pytest
from gunicorn_h1c._parser_fast import IncompleteError as FastIncompleteError
from gunicorn_h1c._parser_fast import ParseError as FastParseError
from gunicorn_h1c._protocol import ParseError as ProtocolParseError

import gunicorn_h1c

_spec = importlib.util.spec_from_file_location(
    "invalid_corpus", pathlib.Path(__file__).parent / "data" / "invalid_corpus.py"
)
_corpus = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_corpus)
CASES = _corpus.CASES

ERRORS = (gunicorn_h1c.ParseError, FastParseError, ProtocolParseError)
INCOMPLETE = (gunicorn_h1c.IncompleteError, FastIncompleteError)

ONE_SHOT_KWARGS = {
    "limit_request_line",
    "limit_request_fields",
    "limit_request_field_size",
    "permit_unconventional_http_method",
    "permit_unconventional_http_version",
}


def outcome(call):
    try:
        call()
    except INCOMPLETE:
        return "incomplete"
    except ERRORS as e:
        return (type(e).__name__, str(e))
    return "ok"


def one_shot_kwargs(kwargs):
    return {k: v for k, v in kwargs.items() if k in ONE_SHOT_KWARGS}


def incremental(func, data, split, kwargs):
    """What gunicorn's WSGI worker sees: a parse of the first read, then a
    parse of everything with last_len set to the first read's length."""
    first = outcome(lambda: func(data[:split], last_len=0, **kwargs))
    if first != "incomplete":
        return first
    return outcome(lambda: func(data, last_len=split, **kwargs))


def fed(data, cuts, kwargs):
    p = gunicorn_h1c.H1CProtocol(**kwargs)
    start = 0
    for cut in list(cuts) + [len(data)]:
        if cut > start:
            result = outcome(lambda chunk=data[start:cut]: p.feed(chunk))
            if result != "ok":
                return result
        start = cut
    return "ok"


def check_split(got, whole, data, split):
    """The error at a split must be the whole-input error. One exception:
    with last_len set, picohttpparser only looks for the end of the head
    before parsing, so an input that stops before the head ends may still be
    reported as incomplete; the same error comes once the head is complete.
    """
    if got == "incomplete" and b"\r\n\r\n" not in data:
        return
    assert got == whole, split


def case_id(case):
    entry, data, kwargs = case
    return f"{entry}:{data[:24]!r}:{len(data)}"


@pytest.mark.parametrize("case", CASES, ids=[case_id(c) for c in CASES])
@pytest.mark.parametrize("func_name", ["parse_request", "parse_request_fast"])
def test_one_shot_error_does_not_depend_on_split(case, func_name):
    _, data, kwargs = case
    func = getattr(gunicorn_h1c, func_name)
    kwargs = one_shot_kwargs(kwargs)
    whole = outcome(lambda: func(data, **kwargs))
    if not isinstance(whole, tuple):
        pytest.skip("not an error for this entry point")
    for split in range(1, len(data)):
        check_split(incremental(func, data, split, kwargs), whole, data, split)


@pytest.mark.parametrize("case", CASES, ids=[case_id(c) for c in CASES])
def test_raw_error_does_not_depend_on_split(case):
    _, data, _ = case
    func = gunicorn_h1c.parse_request_raw
    whole = outcome(lambda: func(data))
    if not isinstance(whole, tuple):
        pytest.skip("not an error for this entry point")
    for split in range(1, len(data)):
        check_split(incremental(func, data, split, {}), whole, data, split)


@pytest.mark.parametrize("case", CASES, ids=[case_id(c) for c in CASES])
def test_parse_headers_error_does_not_depend_on_split(case):
    _, data, _ = case
    end = data.find(b"\r\n")
    if end < 0:
        pytest.skip("no header block")
    block = data[end + 2 :]
    func = gunicorn_h1c.parse_headers
    whole = outcome(lambda: func(block))
    if not isinstance(whole, tuple):
        pytest.skip("not an error for this entry point")
    for split in range(1, len(block)):
        check_split(incremental(func, block, split, {}), whole, block, split)


@pytest.mark.parametrize("case", CASES, ids=[case_id(c) for c in CASES])
def test_protocol_error_does_not_depend_on_split(case):
    _, data, kwargs = case
    kwargs = {k: v for k, v in kwargs.items() if not callable(v)}
    whole = fed(data, [], kwargs)
    if not isinstance(whole, tuple):
        pytest.skip("not an error for H1CProtocol")
    for split in range(1, len(data)):
        assert fed(data, [split], kwargs) == whole, split
    if len(data) <= 4096:
        assert fed(data, range(1, len(data)), kwargs) == whole, "bytewise"
