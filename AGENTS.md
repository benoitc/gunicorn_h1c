# Agents

Instructions for AI coding agents working on gunicorn_h1c. Read this file
before you change anything; it applies to every agent and every change.

## Project

gunicorn_h1c is the C HTTP/1.1 request parser behind gunicorn's
`--http-parser fast`. It wraps a vendored copy of
[picohttpparser](https://github.com/h2o/picohttpparser) and exposes three
extension modules:

- `_parser`: one-shot parsers returning dicts (`parse_request`,
  `parse_response`, `parse_headers`, `parse_to_wsgi_environ`,
  `parse_to_asgi_scope`). gunicorn's WSGI workers use `parse_request`.
- `_parser_fast`: zero-copy `parse_request_fast` / `parse_request_raw`.
- `_protocol`: `H1CProtocol`, the callback parser gunicorn's ASGI worker
  feeds with every socket read.

Python 3.9+, including free-threaded builds (3.14t and later).

## First principle: compliance, latest spec, safety

gunicorn_h1c parses bytes from untrusted clients, in C. Every change that
touches how bytes are read, framed or validated, or how `H1CProtocol` changes
state, must pass three checks before it is called done, committed or opened
as a pull request:

1. **Compliance**: the behaviour matches the governing spec section, a test
   shows the accept and the reject path, and the test fails on main without
   the change.
2. **Latest specification**: you checked that the spec you cite is the
   current one, and you read the section itself instead of quoting from
   memory.
3. **Safety**: the change opens no known abuse class (message length
   disagreement, resource exhaustion), every per-connection buffer stays
   bounded, and the C code is memory safe (see
   [Memory safety](#memory-safety)).

Use the [protocol-change-review](.agents/skills/protocol-change-review/SKILL.md)
skill to run them. When in doubt whether a change is protocol code, it is.

## Specification issues are bugs, not security issues

A report is a specification issue when the fix is to make gunicorn_h1c do
what a spec it implements says (see [Specifications](#specifications)):

- input accepted, rejected or framed differently from the spec (request
  line, line terminators, fields, obsolete folding, Content-Length,
  Transfer-Encoding, chunked coding, trailers);
- gunicorn_h1c disagreeing with gunicorn's pure-Python parsers on the same
  bytes;
- a missing or wrong bound that the spec or a limit argument defines
  (`limit_request_line`, `limit_request_fields`, `limit_request_field_size`,
  `limit_remaining`).

This holds even when the report calls it request smuggling, desync or
denial of service. Treat it as a bug:

- fix it in a public pull request, with a public issue if one is needed;
- list it under **Fixed** in `CHANGELOG.md`, describing the fix and citing
  the spec section;
- do not open a GitHub Security Advisory, request a CVE, embargo the fix,
  give it a severity, or describe how to attack it;
- when it arrived as an advisory, close the advisory as not a vulnerability
  and link the pull request that fixes it.

Only a problem that is not a spec deviation goes through
[SECURITY.md](SECURITY.md):

- memory errors in the extension: a crash, an out-of-bounds read or write,
  a use after free, a double free or reference counting that corrupts
  objects, whether reached through request bytes or through the Python API;
- data from one source passing as data from another, such as two header
  names mapped to the same WSGI environ key so a client can add to a header
  set by a proxy;
- one request's data reaching another request outside any spec rule.

If unsure, ask the maintainer before treating anything as a security issue.

## Setup

```bash
python -m venv .venv
.venv/bin/pip install -e ".[test]" ruff
.venv/bin/python setup.py build_ext --inplace --force
```

setuptools does not track header dependencies: after editing
`pico_utils.h` or `picohttpparser.h`, rebuild with `--force` or the modules
that include them keep the old code.

Free-threaded build, with uv:

```bash
uv python install 3.14t
uv venv -p 3.14t .venv-ft
uv pip install -p .venv-ft/bin/python setuptools wheel pytest
.venv-ft/bin/python setup.py build_ext --inplace --force
```

## Required checks

These match what CI gates on.

```bash
.venv/bin/pytest tests/                            # full suite
.venv/bin/pytest tests/test_protocol.py -k chunk   # one file / one test
.venv/bin/ruff format --check . && .venv/bin/ruff check .
```

Also run before a release or a change to the hot path:

```bash
.venv-ft/bin/pytest tests/                       # free-threaded build
.venv/bin/python benchmarks/bench_protocol.py    # compare against main
```

Benchmark main and the branch alternately, several rounds, on a quiet
machine, and report medians with the spread.

## Layout

- `src/gunicorn_h1c/picohttpparser/`: vendored picohttpparser. It differs
  from upstream (CRLF-only requests); keep the note at the top of the file
  current.
- `src/gunicorn_h1c/pico_utils.h`: validation shared by all modules: limits,
  header names and values, Content-Length, Transfer-Encoding, framing checks,
  error classification.
- `src/gunicorn_h1c/pico_parser_module.c`: `_parser`.
- `src/gunicorn_h1c/pico_parser_fast.c`: `_parser_fast`.
- `src/gunicorn_h1c/pico_protocol.c`: `H1CProtocol`: incremental head
  scanning, body framing, chunked decoding, trailers, `remaining()`.
- `tests/`: pytest suite; `tests/test_security_review.py` runs crash cases
  in a child interpreter so a regression fails the test instead of killing
  the run.
- `tests/test_error_split.py`: replays every input the suite rejects
  (`tests/data/invalid_corpus.py`) at every split point and checks the error
  matches a parse of the whole input. Regenerate the corpus after adding
  invalid cases:
  `H1C_CORPUS=tests/data/invalid_corpus.py PYTHONPATH=tests:src pytest -p record_invalid_corpus tests/ --ignore=tests/test_error_split.py`.
- `benchmarks/bench_protocol.py`: throughput of the callback and pull APIs.

## Memory safety

- Check every length and offset that comes from the caller or the wire
  before using it: `last_len`, sizes parsed from digits (guard the multiply
  against overflow), line and header counts.
- Keep a `Py_buffer` until everything that points into it is copied.
  Release it after the result is built (`PICO_RELEASE_AND_RETURN`).
- Build and validate state before publishing it to the object or calling
  any Python code. A callback can run arbitrary code, including calls back
  into the parser; `H1CProtocol` refuses re-entry while `in_feed` is set.
- Treat objects returned to Python as mutable by the caller: check types
  before using `PyTuple_GET_ITEM` / `PyBytes_AS_STRING` on them.
- Every `PyObject *` field is visited in `tp_traverse` and cleared in
  `tp_clear`.
- Mutable per-object state is accessed inside `Py_BEGIN_CRITICAL_SECTION`
  on free-threaded builds (the wrappers at the end of `pico_protocol.c`).
  No mutable static state.
- Keep source compatibility with Python 3.9: no `Py_NewRef`, no
  `Py_XSETREF`.

## Conventions

- The three HTTP/1 parsers (gunicorn's WSGI `http/message.py` and
  `http/body.py`, ASGI `asgi/parser.py`, and gunicorn_h1c) must accept and
  reject the same input. Probe them side by side with
  `.agents/skills/protocol-change-review/crosscheck.py`. Where gunicorn_h1c
  has to differ, it is stricter, and the difference is listed as a gunicorn
  follow-up.
- Never relax a limit or a rejection to make a client pass; exclude the
  legitimate case precisely.
- Raise the existing exception types so gunicorn maps them to the right
  status: `LimitRequestLine` (414), `LimitRequestHeaders` (431),
  `InvalidHeader`, `InvalidHeaderName`, `InvalidRequestMethod`,
  `InvalidHTTPVersion`, `InvalidChunkExtension` and `ParseError` (400).
- Every bug fix and feature has a test. Show it failing on main.
- Changelog: `CHANGELOG.md`, plain English, what was wrong and what the fix
  does for the user, citing the spec section when there is one. No
  implementation details, no attack narratives. Release notes reuse the
  section as is.

## Pull requests and commits

- One topic per pull request; related fixes are grouped, each in its own
  commit with its tests.
- Descriptions state what was wrong, the fix in a few lines, and breaking
  changes. Reference issues with `Fixes #N`. No test plan, no file-by-file
  walkthrough.
- No `Co-Authored-By`, "Generated with" or similar trailers.
- Follow "Write for the person who reads it" in
  [CONTRIBUTING.md](CONTRIBUTING.md#write-for-the-person-who-reads-it).

## Releases

1. Bump `version` in `pyproject.toml` and `__version__` in
   `src/gunicorn_h1c/__init__.py`; add the `## [x.y.z]` section to
   `CHANGELOG.md`.
2. After the pull request is merged and CI is green on main, tag `vX.Y.Z`
   on the merge commit and create the GitHub release with the changelog
   section as notes.
3. Build and upload (the maintainer runs the upload):

```bash
python -m build -o dist .
twine check dist/*
twine upload dist/*
```

## Specifications

The specs gunicorn_h1c implements. Cite the current one in code and
changelog. Last checked: 2026-11-07.

| Spec | Scope in gunicorn_h1c | Code |
|---|---|---|
| RFC 9110 HTTP Semantics | field names and values (§5.5), singleton fields (§5.3), Content-Length (§8.6), fields forbidden in trailers (§6.5.1) | `pico_utils.h` |
| RFC 9112 HTTP/1.1 | line terminators and bare CR (§2.2), request line and target forms (§3), field lines (§5), obsolete line folding (§5.2), Transfer-Encoding (§6.1), message body length (§6.3), chunked coding, extensions and trailers (§7.1) | `picohttpparser.c`, `pico_utils.h`, `pico_protocol.c` |
| RFC 3986 URI | request-target forms checked against the method | `pico_utils.h` |
| PEP 3333 WSGI (RFC 3875 CGI for variable names) | `parse_to_wsgi_environ` | `pico_parser_module.c` |
| ASGI HTTP connection scope | `parse_to_asgi_scope`, `H1CProtocol.asgi_headers` | `pico_parser_module.c`, `pico_protocol.c` |

RFC 7230 and RFC 7231 are obsoleted by RFC 9110 and RFC 9112; use the new
numbers in new code.

## Skills

Task guides live in `.agents/skills/<name>/SKILL.md`; read the one that
matches the task before starting. `.claude/skills` links to the same
directory, so there is one copy.

- [protocol-change-review](.agents/skills/protocol-change-review/SKILL.md):
  the three checks above (compliance, latest spec, safety) for a change, a
  commit or a pull request.
