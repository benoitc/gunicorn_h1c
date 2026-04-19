"""RFC 9110 / RFC 9112 compliance tests.

Each test pins a specific RFC rule so parser regressions surface as a
named failure. New rules added here must land with the C parser change
that enforces them (TDD workflow).
"""

import pytest

from gunicorn_h1c import (
    H1CProtocol,
    InvalidHeader,
    parse_request,
    parse_request_fast,
)

CONTROL_BYTE_IDS = ["BEL-0x07", "FF-0x0c", "VT-0x0b", "DEL-0x7f"]
CONTROL_BYTES = [b"\x07", b"\x0c", b"\x0b", b"\x7f"]


class TestFieldValueControlChars:
    """RFC 9110 section 5.5: only VCHAR, SP, HTAB, obs-text allowed in value."""

    @pytest.mark.parametrize("ctl", CONTROL_BYTES, ids=CONTROL_BYTE_IDS)
    def test_basic_parser_rejects(self, ctl):
        data = (
            b"GET / HTTP/1.1\r\nHost: localhost\r\nX-Value: bad" + ctl + b"char\r\n\r\n"
        )
        with pytest.raises(InvalidHeader):
            parse_request(data)

    @pytest.mark.parametrize("ctl", CONTROL_BYTES, ids=CONTROL_BYTE_IDS)
    def test_fast_parser_rejects(self, ctl):
        data = (
            b"GET / HTTP/1.1\r\nHost: localhost\r\nX-Value: bad" + ctl + b"char\r\n\r\n"
        )
        with pytest.raises(InvalidHeader):
            parse_request_fast(data)

    @pytest.mark.parametrize("ctl", CONTROL_BYTES, ids=CONTROL_BYTE_IDS)
    def test_protocol_rejects(self, ctl):
        data = (
            b"GET / HTTP/1.1\r\nHost: localhost\r\nX-Value: bad" + ctl + b"char\r\n\r\n"
        )
        p = H1CProtocol()
        with pytest.raises(InvalidHeader):
            p.feed(data)
