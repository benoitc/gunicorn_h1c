<!--
Write this in plain English, a few lines per section. The reviewer reads the
code; don't walk through the diff file by file. See "Write for the person who
reads it" in CONTRIBUTING.md.

If this makes gunicorn_h1c follow a specification (RFC 9110, RFC 9112, WSGI,
ASGI), it is a bug fix: cite the section, no severity or CVE. See "Security
issues and specification bugs" in CONTRIBUTING.md.
-->

## What was wrong

<!-- What happened, who is affected, why it matters. -->

## The fix

<!-- The change in a few lines. Cite the spec section if there is one. -->

## Breaking changes

<!-- None, or what users must change. -->

Fixes #

- [ ] I read this change and can answer questions about it.
- [ ] A test fails without the fix and passes with it.
- [ ] gunicorn's Python parsers agree, or the difference is explained (`crosscheck.py`).
- [ ] `CHANGELOG.md` is updated.
