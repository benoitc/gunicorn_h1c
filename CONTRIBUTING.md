# Contributing to gunicorn_h1c

This guide is for you if you want to report a problem in gunicorn_h1c or send
a change. It covers how to write a report, what counts as a bug and what goes
through the security policy, how to build and test the extension, and what a
pull request needs before it is merged.

gunicorn_h1c is the C HTTP/1.1 parser behind gunicorn's `--http-parser fast`.
It has to accept and reject the same requests as gunicorn's pure-Python
parsers, so most changes are checked against gunicorn too.

## Write for the person who reads it

Every issue, pull request and comment is read by a person. Make it easy for
them.

- Read what you send before you send it. You are responsible for every line,
  whether you wrote it or a tool did.
- Use plain English and short sentences. Say what happens, what you
  expected, and how to reproduce it. Explain any term a reader may not know.
- Don't inflate it. No buzzwords, no severity scores, CVSS or CWE labels, no
  "critical" for a bug, no long generated write-ups. If it takes a page to
  explain, cut it down until it doesn't.
- A pull request says what was wrong and why it matters, the fix in a few
  lines, and any breaking change. Link the issue. Don't walk through the diff
  file by file; the reviewer reads the code.
- A fix comes with a test that fails without it.

AI tools are fine to use. Before you open anything, you must have read the
change, run the tests, and be able to answer questions about it yourself.

We close without further review:

- text we cannot understand after one request to clarify;
- descriptions that don't match the code or the reproduction;
- reports that cannot be reproduced;
- submissions opened by an automated agent that no person has read.

## Report a bug

Check the [open issues](https://github.com/benoitc/gunicorn_h1c/issues)
first; if yours is there, add what you know to it. Otherwise open an issue
with:

- the gunicorn_h1c version, the Python version (say if it is a
  free-threaded build) and the platform;
- if you saw it through gunicorn: the gunicorn version, the worker class and
  the settings you changed;
- the exact bytes that show the problem, as a Python `bytes` literal, and the
  call you made (`parse_request(...)`, or the `H1CProtocol(...)` arguments and
  each `feed()`);
- what you got and what you expected, with the spec section if you know it.

For example:

```python
from gunicorn_h1c import H1CProtocol

p = H1CProtocol()
p.feed(b"GET / HTTP/1.1\nHost: a\n\n")
# 0.6.9 printed True; expected a parse error (RFC 9112 section 2.2)
print(p.is_complete)
```

## Security issues and specification bugs

gunicorn_h1c implements HTTP/1.1 request parsing (RFC 9110, RFC 9112), the
WSGI environ (PEP 3333) and the ASGI HTTP scope. The list is in
[AGENTS.md](AGENTS.md#specifications).

When the fix is to make gunicorn_h1c do what one of these specifications
says, it is a bug, not a security issue. That includes reports described as
request smuggling, desync or denial of service, when they come from:

- input accepted, rejected or framed differently from the specification;
- gunicorn_h1c and gunicorn's Python parsers disagreeing on the same bytes;
- a missing bound that the specification or a limit argument defines.

Report these as a normal bug, in public, as above. Cite the specification
section. The fix goes in a public pull request and in the changelog under
Fixed. We don't publish advisories or request CVEs for them, and advisories
filed for them are closed with a link to the fix.

Problems that are not a specification deviation go through
[SECURITY.md](SECURITY.md): a crash, an out-of-bounds read or write, a use
after free or other memory error in the extension, and data from one source
passing as data from another (for example two header names mapped to the
same WSGI environ key).

## Build and test

You need a C compiler and Python 3.9 or later.

```bash
python -m venv .venv
.venv/bin/pip install -e ".[test]" ruff
.venv/bin/python setup.py build_ext --inplace --force
```

Rebuild with `--force` after editing a header (`pico_utils.h`,
`picohttpparser.h`): setuptools does not see header changes and keeps the old
code in the modules that include them.

Run the tests and the linters:

```bash
.venv/bin/pytest tests/
.venv/bin/ruff format --check . && .venv/bin/ruff check .
```

If your change touches threads or object state, also run the suite on a
free-threaded build:

```bash
uv python install 3.14t
uv venv -p 3.14t .venv-ft
uv pip install -p .venv-ft/bin/python setuptools wheel pytest
.venv-ft/bin/python setup.py build_ext --inplace --force
.venv-ft/bin/pytest tests/
```

If your change touches the parsing path, run the benchmark on main and on
your branch, alternately and on a quiet machine, and put both numbers in the
pull request:

```bash
.venv/bin/python benchmarks/bench_protocol.py
```

## Check against gunicorn

The three HTTP/1 parsers (gunicorn's WSGI parser, its ASGI `PythonProtocol`
and gunicorn_h1c) must agree. To compare them on the same bytes, point the
cross-check script at a gunicorn checkout:

```bash
GUNICORN_SRC=../gunicorn .venv/bin/python \
  .agents/skills/protocol-change-review/crosscheck.py
```

Add your inputs to `CASES` in the script. It exits with an error if
gunicorn_h1c completes a request that a Python parser rejects. When
gunicorn_h1c has to differ, it is the stricter one, and the pull request says
which gunicorn change would make them agree.

## Send a pull request

1. Fork the repository and branch from `main`. Name a bugfix branch
   `fix/<short-name>` or `<issue>-<short-name>`.
2. Write the test first and check that it fails on `main`.
3. Make the change. Cite the specification section in a code comment when
   the code enforces a rule from it.
4. Add a line to `CHANGELOG.md` under the next version: what was wrong and
   what the fix does for the user, in plain English, with the spec section.
   No implementation details.
5. Run the tests and the linters. Every commit should pass them.
6. Open the pull request with what was wrong, the fix in a few lines, and
   any breaking change. Reference the issue with `Fixes #N`. No
   `Co-Authored-By` or "Generated with" lines.

Group related fixes in one pull request, one commit per fix with its tests.
When review asks for changes, push new commits and leave a comment so the
reviewer is notified; squash into logical commits before the merge.

## Decisions

Decisions are pull requests. Benoit Chesneau (benoitc) maintains
gunicorn_h1c and accepts or refuses them. We keep gunicorn_h1c small: it
parses HTTP/1.1 for gunicorn. A feature that gunicorn does not need may be
refused, or may fit better on top of the library.
