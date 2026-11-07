---
name: Bug report
about: The parser accepts, rejects or returns something it should not, or crashes
title: ''
labels: bug
assignees: ''
---

<!--
Write in plain English, see "Write for the person who reads it" in
CONTRIBUTING.md. A crash or memory error goes through SECURITY.md, not here.
-->

**Versions**

- gunicorn_h1c:
- Python (say if free-threaded):
- Platform:
- gunicorn and worker class, if seen through gunicorn:

**Reproduction**

The exact bytes and the calls you made.

```python
from gunicorn_h1c import H1CProtocol

p = H1CProtocol()
p.feed(b"...")
```

**What happened**

**What you expected**

<!-- Cite the spec section if you know it, e.g. RFC 9112 section 7.1. -->
