---
name: protocol-change-review
description: Run the three required checks (compliance, latest specification, safety) on a gunicorn_h1c change, commit or pull request that touches request parsing, line terminators, header validation, Content-Length, Transfer-Encoding, chunked coding, trailers, limits, H1CProtocol state, buffer handling or the C/Python boundary. Use before committing, before opening or merging a PR, when reviewing a contributor PR, and when asked whether code follows a spec.
---

# Protocol change review

gunicorn_h1c parses bytes from untrusted clients in C, usually behind a
proxy and always next to gunicorn's two pure-Python parsers. A change is done
when it matches the spec, the spec is the current one, the three parsers
agree, and neither a hostile peer nor a careless caller can use it to read
past a buffer or crash the process. Run the three checks below on each
commit; a PR with several commits gets one verdict per commit. The spec list
is in [AGENTS.md](../../../AGENTS.md#specifications).

## 1. Compliance

Get the spec text. Never quote it from memory.

```bash
mkdir -p "$TMPDIR/rfc" && cd "$TMPDIR/rfc"
for r in 9110 9112 3986; do
  [ -f rfc$r.txt ] || curl -sfL https://www.rfc-editor.org/rfc/rfc$r.txt -o rfc$r.txt
done
awk '/^7.1.  Chunked Transfer Coding/,/^7.2/' rfc9112.txt
```

For each commit:

1. Find every spec sentence the commit, PR or code comment cites. Confirm it
   is verbatim and in the section named.
2. Read the whole section: exceptions, "unless", and the rule for the other
   role (sender/recipient, client/server, proxy) are usually a paragraph
   away.
3. Note the level. MUST / MUST NOT is a fix. SHOULD needs a reason to skip.
   MAY is a choice the change has to justify. Rejecting what the spec allows
   breaks clients.
4. Check the exception matches the response gunicorn should send:
   `LimitRequestLine` (414), `LimitRequestHeaders` (431), the `Invalid*`
   classes and `ParseError` (400), and whether `should_keep_alive` must turn
   false.
5. Check every entry point that reaches the code: `parse_request`,
   `parse_request_fast`, `parse_request_raw`, `parse_headers`,
   `parse_to_wsgi_environ`, `parse_to_asgi_scope` and `H1CProtocol.feed`,
   the last one with the input whole and one byte per `feed()`.
6. Compare with gunicorn's WSGI and ASGI pure-Python parsers on the same
   bytes, from a checkout of gunicorn master:

```bash
git -C ../gunicorn worktree add --detach "$TMPDIR/gunicorn" origin/master
GUNICORN_SRC="$TMPDIR/gunicorn" .venv/bin/python \
  .agents/skills/protocol-change-review/crosscheck.py
```

   Add the commit's inputs to `CASES` in a copy of the script. Each parser
   reports `ok` (complete), `wait` (needs more data) or `rej:<exception>`.
   gunicorn_h1c must never say `ok` where a Python parser rejects.

7. Check the test fails on main and passes with the change. Use a worktree
   at the merge base, not `git stash`, built on its own:

```bash
git worktree add "$TMPDIR/base" origin/main
cp tests/test_x.py "$TMPDIR/base/tests/"
cd "$TMPDIR/base" && ../path/to/.venv/bin/python setup.py build_ext --inplace --force \
  && PYTHONPATH="$PWD/src" ../path/to/.venv/bin/python -m pytest tests/test_x.py
```

Run the full suite on a GIL build and on a free-threaded build (see
AGENTS.md).

## 2. Latest specification

1. Name the governing document and confirm it is current on
   https://datatracker.ietf.org/doc/rfcNNNN/ ("Obsoleted by", "Updated by").
   RFC 7230/7231/2616 are replaced by RFC 9110/9112.
2. Check for errata on https://www.rfc-editor.org/errata/rfcNNNN and for an
   active httpbis draft that changes the rule.
3. For the WSGI environ, PEP 3333; for the ASGI scope,
   https://asgi.readthedocs.io/.
4. Write the version and the date checked in the PR description.
5. If the latest text disagrees with the code, fix it in the change or say
   in the PR that it is out of scope.

## 3. Safety

Walk the classes that apply and state which you checked.

- **Message length disagreement**: Content-Length vs Transfer-Encoding,
  duplicate, list-form or overflowing Content-Length, chunk sizes that
  overflow, compression codings without chunked, bare LF / CR, obsolete line
  folding, whitespace in names or chunk sizes, the leading empty line, body
  bytes left in `remaining()` on a kept-alive connection.
- **Resource exhaustion**: every buffer `H1CProtocol` keeps while waiting
  for a CRLF (request line, header lines, chunk-size lines, trailer lines),
  header and trailer counts, `limit_remaining`, and scanning cost per
  `feed()` (no rescanning from the start). A peer must not grow any of them
  without a bound set by a `limit_*` argument.
- **Memory safety**: lengths and offsets checked before use (`last_len`,
  parsed numbers, counts), `Py_buffer` released only after everything
  pointing into it is copied, state built and validated before it is
  published or a callback runs, re-entry from callbacks refused, objects
  handed to Python treated as mutable, reference counts balanced on every
  error path, `tp_traverse` / `tp_clear` covering every object field, and
  critical sections around mutable state on free-threaded builds. Drive
  crash cases in a child interpreter (`run_isolated` in
  `tests/test_security_review.py`).
- **Trust boundaries**: header names mapped to the same environ key, names
  or values truncated, anything that lets client bytes pass as data a proxy
  set.

Never weaken a limit to let a legitimate client pass; exclude that case
precisely. A regression test feeds the bytes through every entry point
concerned and asserts the bound or the rejection.

A finding that the fix makes follow a spec is a bug even when it is a
smuggling or exhaustion vector: public PR, `CHANGELOG.md` under Fixed with
the spec section, no advisory, no CVE, no attack description. Memory errors
and trust-boundary issues go through SECURITY.md. See "Specification issues
are bugs, not security issues" in AGENTS.md.

## Report

One block per commit, in the PR description or the hand-off:

```
<sha> <subject>
  spec: RFC 9112 §7.1 MUST, quote verified | not found | paraphrased
  latest: RFC 9112 current (checked 2026-11-07)
  verdict: compliant | partial (<missing>) | wrong (<why>) | not a spec item
  parsers: WSGI / ASGI / h1c agree | differ (<how>, h1c stricter?)
  test: fails before, passes after | does not discriminate | missing
  safety: <classes checked>, <bounds>, memory safety checked, no new unbounded state
  perf: <benchmark medians main vs branch> | not on the hot path
```

Then list what needs the maintainer's decision: a SHOULD left out, a
deliberate deviation, a difference with gunicorn's parsers that needs a
gunicorn change.
