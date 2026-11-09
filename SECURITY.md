# Security Policy

This page tells you how to report a security problem in gunicorn_h1c, what
we treat as a security problem and what we treat as a bug, and which
versions get fixes.

## Reporting a vulnerability

**Public GitHub issues are open for everyone to see.** Don't report a
security problem there.

Report it privately, either:

- on GitHub, with the *Report a vulnerability* button in the
  [Security](https://github.com/benoitc/gunicorn_h1c/security) tab, or
- by [email](mailto:security@gunicorn.org?subject=Security%20issue%20in%20gunicorn_h1c)
  to security@gunicorn.org.

Include the gunicorn_h1c version, the Python version (and whether it is a
free-threaded build), the platform, and a minimal reproduction: the bytes or
the Python calls, and what happens (a traceback, a crash, the wrong result).
Write it in plain English, as described in
[CONTRIBUTING.md](CONTRIBUTING.md#write-for-the-person-who-reads-it).

We confirm we received the report, fix it in a private fork of the
repository, release the fix, then publish a GitHub Security Advisory and
request a CVE through GitHub. We credit you in the advisory unless you ask us
not to.

## What is a security issue

Problems that are not a deviation from a specification gunicorn_h1c
implements:

- memory errors in the extension: a crash, an out-of-bounds read or write, a
  use after free, a double free, or reference counting that corrupts
  objects, whether reached through request bytes or through the Python API;
- data from one source passing as data from another, such as two header
  names mapped to the same WSGI environ key so that a client can add to a
  header set by a proxy;
- one request's data reaching another request outside any specification
  rule.

## What is a bug

When the fix is to make gunicorn_h1c follow a specification it implements
(HTTP/1.1 per RFC 9110 and RFC 9112, the WSGI environ, the ASGI scope), it is
a bug, even when described as request smuggling, desync or denial of service.
That covers input accepted or rejected differently from the specification,
gunicorn_h1c disagreeing with gunicorn's Python parsers on the same bytes,
and a missing bound that a specification or a limit argument defines.

Report it as a public issue and cite the specification section; see
[Security issues and specification bugs](CONTRIBUTING.md#security-issues-and-specification-bugs).
We don't publish advisories or request CVEs for these, and advisories filed
for them are closed as bugs with a link to the fix.

## Supported releases

Fixes go into the latest release only. Upgrade to it before you report.

| Version  | Status             |
| -------- | ------------------ |
| 0.6.14   | :white_check_mark: |
| < 0.6.14 | :x:                |

## Python versions

gunicorn_h1c installs on Python 3.9 and later. CI runs on 3.13, 3.14 and
3.15, including the free-threaded builds 3.14t and 3.15t. We *highly
recommend* the latest release of a
[supported series](https://devguide.python.org/versions/) and will not
prioritize issues that only affect end-of-life versions.
