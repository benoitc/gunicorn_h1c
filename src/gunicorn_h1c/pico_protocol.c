/*
 * SPDX-License-Identifier: MIT
 *
 * Copyright 2026 Benoit Chesneau
 * Licensed under the MIT License (see LICENSE file).
 *
 * Callback-based HTTP/1.1 parser for asyncio integration.
 *
 * This module provides H1CProtocol, a zero-copy callback parser
 * optimized for asyncio's data_received() pattern.
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "picohttpparser.h"
#include "pico_utils.h"

#define MAX_HEADERS 256
#define INITIAL_BUFFER_SIZE 4096

/* Cap on bytes retained after a completed message (0 = unlimited).
 * Bounds memory when a caller keeps feeding a parser that is already
 * complete, which would otherwise grow without limit. */
#define PICO_DEFAULT_LIMIT_REMAINING 65536

/* Parser states */
#define STATE_IDLE              0
#define STATE_HEADERS           1
#define STATE_BODY              2
#define STATE_BODY_CHUNKED_SIZE 3
#define STATE_BODY_CHUNKED_DATA 4
#define STATE_BODY_CHUNKED_CRLF 5
#define STATE_BODY_CHUNKED_TRAILER 6
#define STATE_COMPLETE          7

/* ParseError exception */
static PyObject *ParseError;

/* Imported validation exceptions from _parser module */
static PyObject *LimitRequestLine;
static PyObject *LimitRequestHeaders;
static PyObject *InvalidRequestMethod;
static PyObject *InvalidHTTPVersion;
static PyObject *InvalidHeaderName;
static PyObject *InvalidHeader;
static PyObject *InvalidChunkExtension;

/*
 * H1CProtocol type
 */
typedef struct {
    PyObject_HEAD

    /* Callbacks */
    PyObject *on_message_begin;
    PyObject *on_url;
    PyObject *on_header;
    PyObject *on_headers_complete;
    PyObject *on_body;
    PyObject *on_message_complete;

    /* Validation configuration */
    Py_ssize_t limit_request_line;
    Py_ssize_t limit_request_fields;
    Py_ssize_t limit_request_field_size;
    Py_ssize_t limit_remaining;
    int permit_unconventional_http_method;
    int permit_unconventional_http_version;
    int remaining_truncated;

    /* Parser state */
    int state;
    char *buffer;
    size_t buffer_size;
    size_t buffer_len;
    size_t last_len;

    /* Request data (valid after headers complete) */
    PyObject *py_method;
    PyObject *py_path;
    PyObject *py_headers;       /* list of (name, value) tuples */
    PyObject *py_asgi_headers;  /* list with lowercase names for ASGI */
    int minor_version;
    Py_ssize_t content_length;  /* -1 if not set */
    int is_chunked;
    int connection_close;  /* -1=unset, 0=keep-alive, 1=close */
    int should_upgrade;

    /* Body parsing state */
    Py_ssize_t body_remaining;
    size_t chunk_size;
    size_t chunk_remaining;
    int skip_body;

} H1CProtocol;

/* Forward declarations */
static int H1CProtocol_feed_headers(H1CProtocol *self);
static int H1CProtocol_feed_body_content_length(H1CProtocol *self, const char *data,
                                                size_t len, int data_is_buffer);
static int H1CProtocol_feed_body_chunked(H1CProtocol *self);
static int H1CProtocol_keep_tail(H1CProtocol *self, const char *data, size_t len,
                                 size_t consumed, int data_is_buffer);
static void H1CProtocol_clamp_tail(H1CProtocol *self);

/* header_name_eq moved to pico_utils.h as pico_header_name_eq */

/* Ensure buffer can hold additional bytes */
static int
ensure_buffer_capacity(H1CProtocol *self, size_t additional)
{
    size_t needed = self->buffer_len + additional;
    if (needed <= self->buffer_size) {
        return 0;
    }

    /* Grow buffer */
    size_t new_size = self->buffer_size * 2;
    while (new_size < needed) {
        new_size *= 2;
    }

    char *new_buffer = PyMem_Realloc(self->buffer, new_size);
    if (!new_buffer) {
        PyErr_NoMemory();
        return -1;
    }

    self->buffer = new_buffer;
    self->buffer_size = new_size;
    return 0;
}

/*
 * Keep the bytes that follow a finished message so remaining() can hand them
 * back: a pipelined request, a WebSocket frame sent straight after the
 * handshake, or the HTTP/2 preface after an Upgrade: h2c request.
 *
 * `data` is the block just parsed and `consumed` how much of it the message
 * used. data_is_buffer says whether `data` points into self->buffer (body fed
 * on from the header buffer) or at caller-owned memory, since the first case
 * must shift in place rather than copy over itself.
 */
/*
 * Bound the retained tail. Overflow is never an error here: feed() must not
 * start raising on input it used to discard, or a caller that keeps feeding a
 * completed parser would see its connections fail. The excess is dropped and
 * the loss is reported through remaining_truncated, which only a caller that
 * asks for the tail will look at.
 */
static void
H1CProtocol_clamp_tail(H1CProtocol *self)
{
    if (self->limit_remaining == 0) {
        return;  /* unlimited */
    }

    if (self->buffer_len > (size_t)self->limit_remaining) {
        self->buffer_len = (size_t)self->limit_remaining;
        self->remaining_truncated = 1;
    }
}

static int
H1CProtocol_keep_tail(H1CProtocol *self, const char *data, size_t len,
                      size_t consumed, int data_is_buffer)
{
    size_t tail_len = len - consumed;

    if (tail_len == 0) {
        self->buffer_len = 0;
        return 0;
    }

    if (data_is_buffer) {
        memmove(self->buffer, data + consumed, tail_len);
    } else {
        self->buffer_len = 0;
        if (ensure_buffer_capacity(self, tail_len) < 0) {
            return -1;
        }
        memcpy(self->buffer, data + consumed, tail_len);
    }

    self->buffer_len = tail_len;
    H1CProtocol_clamp_tail(self);
    return 0;
}

static void
H1CProtocol_dealloc(H1CProtocol *self)
{
    Py_XDECREF(self->on_message_begin);
    Py_XDECREF(self->on_url);
    Py_XDECREF(self->on_header);
    Py_XDECREF(self->on_headers_complete);
    Py_XDECREF(self->on_body);
    Py_XDECREF(self->on_message_complete);

    Py_XDECREF(self->py_method);
    Py_XDECREF(self->py_path);
    Py_XDECREF(self->py_headers);
    Py_XDECREF(self->py_asgi_headers);

    if (self->buffer) {
        PyMem_Free(self->buffer);
    }

    Py_TYPE(self)->tp_free((PyObject *)self);
}

static int
H1CProtocol_init(H1CProtocol *self, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {
        "on_message_begin", "on_url", "on_header",
        "on_headers_complete", "on_body", "on_message_complete",
        "limit_request_line", "limit_request_fields", "limit_request_field_size",
        "permit_unconventional_http_method", "permit_unconventional_http_version",
        "limit_remaining",
        NULL
    };

    PyObject *on_message_begin = NULL;
    PyObject *on_url = NULL;
    PyObject *on_header = NULL;
    PyObject *on_headers_complete = NULL;
    PyObject *on_body = NULL;
    PyObject *on_message_complete = NULL;
    Py_ssize_t limit_request_line = PICO_DEFAULT_LIMIT_REQUEST_LINE;
    Py_ssize_t limit_request_fields = PICO_DEFAULT_LIMIT_REQUEST_FIELDS;
    Py_ssize_t limit_request_field_size = PICO_DEFAULT_LIMIT_REQUEST_FIELD_SIZE;
    int permit_unconventional_http_method = 0;
    int permit_unconventional_http_version = 0;
    Py_ssize_t limit_remaining = PICO_DEFAULT_LIMIT_REMAINING;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|OOOOOOnnnppn", kwlist,
                                     &on_message_begin, &on_url, &on_header,
                                     &on_headers_complete, &on_body,
                                     &on_message_complete,
                                     &limit_request_line,
                                     &limit_request_fields,
                                     &limit_request_field_size,
                                     &permit_unconventional_http_method,
                                     &permit_unconventional_http_version,
                                     &limit_remaining)) {
        return -1;
    }

    if (limit_remaining < 0) {
        PyErr_SetString(PyExc_ValueError, "limit_remaining must not be negative");
        return -1;
    }

    /* Store callbacks (allow None to mean no callback) */
    if (on_message_begin && on_message_begin != Py_None) {
        Py_INCREF(on_message_begin);
        self->on_message_begin = on_message_begin;
    }
    if (on_url && on_url != Py_None) {
        Py_INCREF(on_url);
        self->on_url = on_url;
    }
    if (on_header && on_header != Py_None) {
        Py_INCREF(on_header);
        self->on_header = on_header;
    }
    if (on_headers_complete && on_headers_complete != Py_None) {
        Py_INCREF(on_headers_complete);
        self->on_headers_complete = on_headers_complete;
    }
    if (on_body && on_body != Py_None) {
        Py_INCREF(on_body);
        self->on_body = on_body;
    }
    if (on_message_complete && on_message_complete != Py_None) {
        Py_INCREF(on_message_complete);
        self->on_message_complete = on_message_complete;
    }

    /* Store validation config */
    self->limit_request_line = limit_request_line;
    self->limit_request_fields = limit_request_fields;
    self->limit_request_field_size = limit_request_field_size;
    self->limit_remaining = limit_remaining;
    self->remaining_truncated = 0;
    self->permit_unconventional_http_method = permit_unconventional_http_method;
    self->permit_unconventional_http_version = permit_unconventional_http_version;

    /* Initialize state */
    self->state = STATE_IDLE;
    self->buffer = PyMem_Malloc(INITIAL_BUFFER_SIZE);
    if (!self->buffer) {
        PyErr_NoMemory();
        return -1;
    }
    self->buffer_size = INITIAL_BUFFER_SIZE;
    self->buffer_len = 0;
    self->last_len = 0;

    /* Request data */
    self->py_method = NULL;
    self->py_path = NULL;
    self->py_headers = NULL;
    self->py_asgi_headers = NULL;
    self->minor_version = 1;
    self->content_length = -1;
    self->is_chunked = 0;
    self->connection_close = -1;
    self->should_upgrade = 0;

    /* Body state */
    self->body_remaining = 0;
    self->chunk_size = 0;
    self->chunk_remaining = 0;
    self->skip_body = 0;

    return 0;
}

/*
 * Feed data to the parser. Callbacks fire synchronously.
 */
static PyObject *
H1CProtocol_feed(H1CProtocol *self, PyObject *args)
{
    Py_buffer buf;

    if (!PyArg_ParseTuple(args, "y*", &buf)) {
        return NULL;
    }

    if (buf.len == 0) {
        PyBuffer_Release(&buf);
        Py_RETURN_NONE;
    }

    /* Handle state transitions */
    if (self->state == STATE_IDLE) {
        self->state = STATE_HEADERS;

        /* Fire on_message_begin */
        if (self->on_message_begin) {
            PyObject *result = PyObject_CallNoArgs(self->on_message_begin);
            if (!result) {
                PyBuffer_Release(&buf);
                return NULL;
            }
            Py_DECREF(result);
        }
    }

    if (self->state == STATE_HEADERS) {
        /* Buffer data for header parsing */
        if (ensure_buffer_capacity(self, buf.len) < 0) {
            PyBuffer_Release(&buf);
            return NULL;
        }
        memcpy(self->buffer + self->buffer_len, buf.buf, buf.len);
        self->buffer_len += buf.len;
        PyBuffer_Release(&buf);

        if (H1CProtocol_feed_headers(self) < 0) {
            return NULL;
        }
    }
    else if (self->state == STATE_BODY) {
        int ret = H1CProtocol_feed_body_content_length(self, buf.buf, buf.len, 0);
        PyBuffer_Release(&buf);
        if (ret < 0) {
            return NULL;
        }
    }
    else if (self->state >= STATE_BODY_CHUNKED_SIZE &&
             self->state <= STATE_BODY_CHUNKED_TRAILER) {
        /* Buffer data for chunked parsing */
        if (ensure_buffer_capacity(self, buf.len) < 0) {
            PyBuffer_Release(&buf);
            return NULL;
        }
        memcpy(self->buffer + self->buffer_len, buf.buf, buf.len);
        self->buffer_len += buf.len;
        PyBuffer_Release(&buf);

        if (H1CProtocol_feed_body_chunked(self) < 0) {
            return NULL;
        }
    }
    else if (self->state == STATE_COMPLETE) {
        /* The message is already finished, so every byte here belongs to
         * whatever follows it. Accumulate so a tail split across several
         * feed() calls still arrives whole, but never past limit_remaining:
         * a caller that keeps feeding a completed parser must not be able to
         * grow this buffer without bound. */
        size_t take = (size_t)buf.len;

        if (self->limit_remaining > 0) {
            size_t cap = (size_t)self->limit_remaining;
            size_t room = (self->buffer_len >= cap) ? 0 : cap - self->buffer_len;
            if (take > room) {
                take = room;
                self->remaining_truncated = 1;
            }
        }

        if (take > 0) {
            if (ensure_buffer_capacity(self, take) < 0) {
                PyBuffer_Release(&buf);
                return NULL;
            }
            memcpy(self->buffer + self->buffer_len, buf.buf, take);
            self->buffer_len += take;
        }
        PyBuffer_Release(&buf);
    }
    else {
        PyBuffer_Release(&buf);
    }

    Py_RETURN_NONE;
}

/*
 * Parse headers from buffer.
 * Returns 0 on success (or need more data), -1 on error.
 */
static int
H1CProtocol_feed_headers(H1CProtocol *self)
{
    /* Check request line length before parsing */
    Py_ssize_t req_line_len = pico_find_request_line_length(self->buffer, self->buffer_len);
    if (req_line_len >= 0 && req_line_len > self->limit_request_line) {
        PyErr_Format(LimitRequestLine,
            "Request line length (%zd) exceeds limit (%zd)",
            req_line_len, self->limit_request_line);
        return -1;
    }

    const char *method, *path;
    size_t method_len, path_len;
    int minor_version;
    struct phr_header headers[MAX_HEADERS];
    size_t num_headers = MAX_HEADERS;

    int ret = phr_parse_request(
        self->buffer, self->buffer_len,
        &method, &method_len,
        &path, &path_len,
        &minor_version,
        headers, &num_headers,
        self->last_len
    );

    if (ret == -2) {
        /* Need more data */
        self->last_len = self->buffer_len;
        return 0;
    }

    if (ret == -1) {
        pico_analyze_parse_error(self->buffer, self->buffer_len,
                                 self->permit_unconventional_http_method,
                                 self->permit_unconventional_http_version,
                                 ParseError,
                                 InvalidRequestMethod,
                                 InvalidHTTPVersion,
                                 InvalidHeaderName,
                                 InvalidHeader);
        return -1;
    }

    /* Validate method */
    if (pico_validate_method(method, method_len,
                              self->permit_unconventional_http_method,
                              InvalidRequestMethod) < 0) {
        return -1;
    }

    /* Validate request-target form against method (RFC 9112) */
    if (pico_validate_request_target(method, method_len, path, path_len,
                                      ParseError) < 0) {
        return -1;
    }

    /* Validate HTTP version */
    if (pico_validate_version(minor_version,
                               self->permit_unconventional_http_version,
                               InvalidHTTPVersion) < 0) {
        return -1;
    }

    /* Validate headers */
    PicoValidationConfig config = {
        .limit_request_line = self->limit_request_line,
        .limit_request_fields = self->limit_request_fields,
        .limit_request_field_size = self->limit_request_field_size,
        .permit_unconventional_http_method = self->permit_unconventional_http_method,
        .permit_unconventional_http_version = self->permit_unconventional_http_version
    };

    if (pico_validate_headers(headers, num_headers, &config,
                               LimitRequestHeaders,
                               InvalidHeaderName,
                               InvalidHeader) < 0) {
        return -1;
    }

    /* Headers complete - store data */
    self->minor_version = minor_version;

    /* Create method bytes */
    Py_XDECREF(self->py_method);
    self->py_method = PyBytes_FromStringAndSize(method, method_len);
    if (!self->py_method) return -1;

    /* Create path bytes */
    Py_XDECREF(self->py_path);
    self->py_path = PyBytes_FromStringAndSize(path, path_len);
    if (!self->py_path) return -1;

    /* Fire on_url callback */
    if (self->on_url) {
        PyObject *result = PyObject_CallOneArg(self->on_url, self->py_path);
        if (!result) return -1;
        Py_DECREF(result);
    }

    /* Process headers */
    Py_XDECREF(self->py_headers);
    self->py_headers = PyList_New(num_headers);
    if (!self->py_headers) return -1;

    /* Clear cached ASGI headers */
    Py_XDECREF(self->py_asgi_headers);
    self->py_asgi_headers = NULL;

    /* Extract special headers using shared utility */
    PicoHeaderInfo hdr_info;
    pico_extract_headers(headers, num_headers, &hdr_info);

    /* Validate header framing for request smuggling prevention */
    if (pico_validate_header_framing(&hdr_info, minor_version, InvalidHeader) < 0) {
        return -1;
    }

    self->content_length = hdr_info.content_length;
    self->is_chunked = hdr_info.has_chunked;
    self->connection_close = hdr_info.connection_close;
    self->should_upgrade = hdr_info.should_upgrade;

    for (size_t i = 0; i < num_headers; i++) {
        const char *name = headers[i].name;
        size_t name_len = headers[i].name_len;
        const char *value = headers[i].value;
        size_t value_len = headers[i].value_len;

        /* Create header tuple using shared utility */
        PyObject *tuple = pico_create_header_tuple(name, name_len, value, value_len);
        if (!tuple) return -1;

        PyList_SET_ITEM(self->py_headers, i, tuple);

        /* Fire on_header callback */
        if (self->on_header) {
            PyObject *name_obj = PyBytes_FromStringAndSize(name, name_len);
            PyObject *value_obj = PyBytes_FromStringAndSize(value, value_len);
            if (!name_obj || !value_obj) {
                Py_XDECREF(name_obj);
                Py_XDECREF(value_obj);
                return -1;
            }
            PyObject *result = PyObject_CallFunctionObjArgs(
                self->on_header, name_obj, value_obj, NULL);
            Py_DECREF(name_obj);
            Py_DECREF(value_obj);
            if (!result) return -1;
            Py_DECREF(result);
        }
    }

    /* Fire on_headers_complete callback */
    int skip_body = 0;
    if (self->on_headers_complete) {
        PyObject *result = PyObject_CallNoArgs(self->on_headers_complete);
        if (!result) return -1;
        if (result == Py_True) {
            skip_body = 1;
        }
        Py_DECREF(result);
    }
    self->skip_body = skip_body;

    /* Keep unconsumed data */
    size_t consumed = ret;
    size_t remaining = self->buffer_len - consumed;

    if (remaining > 0) {
        memmove(self->buffer, self->buffer + consumed, remaining);
    }
    self->buffer_len = remaining;
    self->last_len = 0;

    /* Determine body parsing mode.
     *
     * An Upgrade header does not suppress the body. The protocol switch only
     * happens once the server answers 101 (RFC 9110 section 7.8), which is
     * after the request has been read in full, and RFC 7540 section 3.2
     * requires an h2c client to send any payload in its entirety before its
     * first HTTP/2 frame. So the bytes after the headers are the request
     * body, framed by Content-Length or Transfer-Encoding as usual, and
     * remaining() starts at the end of it.
     *
     * CONNECT needs no case of its own: it carries no framing, so it lands in
     * the no-body branch below and its tunnel bytes stay in remaining(). */
    if (skip_body) {
        self->state = STATE_COMPLETE;
        H1CProtocol_clamp_tail(self);
        if (self->on_message_complete) {
            PyObject *result = PyObject_CallNoArgs(self->on_message_complete);
            if (!result) return -1;
            Py_DECREF(result);
        }
    }
    else if (self->is_chunked) {
        self->state = STATE_BODY_CHUNKED_SIZE;
        self->chunk_size = 0;
        self->chunk_remaining = 0;
        if (remaining > 0) {
            return H1CProtocol_feed_body_chunked(self);
        }
    }
    else if (self->content_length > 0) {
        self->body_remaining = self->content_length;
        self->state = STATE_BODY;
        if (remaining > 0) {
            return H1CProtocol_feed_body_content_length(
                self, self->buffer, remaining, 1);
        }
    }
    else {
        /* No body */
        self->state = STATE_COMPLETE;
        H1CProtocol_clamp_tail(self);
        if (self->on_message_complete) {
            PyObject *result = PyObject_CallNoArgs(self->on_message_complete);
            if (!result) return -1;
            Py_DECREF(result);
        }
    }

    return 0;
}

/*
 * Parse body with Content-Length.
 */
static int
H1CProtocol_feed_body_content_length(H1CProtocol *self, const char *data,
                                     size_t len, int data_is_buffer)
{
    if (self->skip_body) {
        if ((Py_ssize_t)len >= self->body_remaining) {
            size_t consumed = (size_t)self->body_remaining;
            self->body_remaining = 0;
            self->state = STATE_COMPLETE;
            if (H1CProtocol_keep_tail(self, data, len, consumed,
                                      data_is_buffer) < 0) {
                return -1;
            }
            if (self->on_message_complete) {
                PyObject *result = PyObject_CallNoArgs(self->on_message_complete);
                if (!result) return -1;
                Py_DECREF(result);
            }
        } else {
            self->body_remaining -= len;
            /* Clear buffer since we're not using buffered data for body */
            self->buffer_len = 0;
        }
        return 0;
    }

    size_t to_consume;
    if ((Py_ssize_t)len <= self->body_remaining) {
        to_consume = len;
    } else {
        to_consume = (size_t)self->body_remaining;
    }

    /* Fire on_body callback */
    if (self->on_body && to_consume > 0) {
        PyObject *chunk = PyBytes_FromStringAndSize(data, to_consume);
        if (!chunk) return -1;
        PyObject *result = PyObject_CallOneArg(self->on_body, chunk);
        Py_DECREF(chunk);
        if (!result) return -1;
        Py_DECREF(result);
    }

    self->body_remaining -= to_consume;

    if (self->body_remaining > 0) {
        self->buffer_len = 0;  /* Clear buffer */
    }

    if (self->body_remaining == 0) {
        self->state = STATE_COMPLETE;
        /* Anything past the body belongs to the next message */
        if (H1CProtocol_keep_tail(self, data, len, to_consume,
                                  data_is_buffer) < 0) {
            return -1;
        }
        if (self->on_message_complete) {
            PyObject *result = PyObject_CallNoArgs(self->on_message_complete);
            if (!result) return -1;
            Py_DECREF(result);
        }
    }

    return 0;
}

/*
 * Parse chunked transfer encoding body.
 */
static int
H1CProtocol_feed_body_chunked(H1CProtocol *self)
{
    while (1) {
        if (self->state == STATE_BODY_CHUNKED_SIZE) {
            /* Look for chunk size line */
            char *crlf = memmem(self->buffer, self->buffer_len, "\r\n", 2);
            if (!crlf) {
                return 0;  /* Need more data */
            }

            size_t line_len = crlf - self->buffer;

            /* Parse chunk size (may have extensions after ;) */
            char *semicolon = memchr(self->buffer, ';', line_len);
            size_t size_len = semicolon ? (size_t)(semicolon - self->buffer) : line_len;

            /* RFC 9112: chunk-ext must not contain bare CR */
            if (semicolon) {
                size_t ext_len = line_len - size_len - 1;
                if (memchr(semicolon + 1, '\r', ext_len)) {
                    PyErr_SetString(InvalidChunkExtension,
                        "Invalid chunk extension: bare CR not allowed");
                    return -1;
                }
            }

            /* Reject empty chunk size */
            if (size_len == 0) {
                PyErr_SetString(ParseError, "Empty chunk size");
                return -1;
            }

            /* Reject leading/trailing whitespace (request smuggling vector) */
            if (self->buffer[0] == ' ' || self->buffer[0] == '\t') {
                PyErr_SetString(ParseError, "Whitespace in chunk size");
                return -1;
            }
            if (size_len > 0 && (self->buffer[size_len-1] == ' ' || self->buffer[size_len-1] == '\t')) {
                PyErr_SetString(ParseError, "Whitespace in chunk size");
                return -1;
            }

            /* Parse hex number - strict validation, no whitespace allowed */
            size_t chunk_size = 0;
            for (size_t i = 0; i < size_len; i++) {
                char c = self->buffer[i];
                size_t digit;
                if (c >= '0' && c <= '9') {
                    digit = c - '0';
                } else if (c >= 'a' && c <= 'f') {
                    digit = c - 'a' + 10;
                } else if (c >= 'A' && c <= 'F') {
                    digit = c - 'A' + 10;
                } else {
                    /* Reject any non-hex character including whitespace */
                    PyErr_SetString(ParseError, "Invalid character in chunk size");
                    return -1;
                }
                chunk_size = chunk_size * 16 + digit;
            }

            self->chunk_size = chunk_size;
            self->chunk_remaining = chunk_size;

            /* Remove size line from buffer */
            size_t consumed = line_len + 2;
            memmove(self->buffer, self->buffer + consumed, self->buffer_len - consumed);
            self->buffer_len -= consumed;

            if (chunk_size == 0) {
                /* Last chunk - parse trailers */
                self->state = STATE_BODY_CHUNKED_TRAILER;
            } else {
                self->state = STATE_BODY_CHUNKED_DATA;
            }
        }
        else if (self->state == STATE_BODY_CHUNKED_DATA) {
            if (self->buffer_len == 0) {
                return 0;  /* Need more data */
            }

            size_t available = self->buffer_len;
            if (available > self->chunk_remaining) {
                available = self->chunk_remaining;
            }

            /* Fire on_body callback */
            if (!self->skip_body && self->on_body && available > 0) {
                PyObject *chunk = PyBytes_FromStringAndSize(self->buffer, available);
                if (!chunk) return -1;
                PyObject *result = PyObject_CallOneArg(self->on_body, chunk);
                Py_DECREF(chunk);
                if (!result) return -1;
                Py_DECREF(result);
            }

            /* Remove consumed data from buffer */
            memmove(self->buffer, self->buffer + available, self->buffer_len - available);
            self->buffer_len -= available;
            self->chunk_remaining -= available;

            if (self->chunk_remaining == 0) {
                self->state = STATE_BODY_CHUNKED_CRLF;
            }
        }
        else if (self->state == STATE_BODY_CHUNKED_CRLF) {
            /* Expect CRLF after chunk data */
            if (self->buffer_len < 2) {
                return 0;  /* Need more data */
            }

            if (self->buffer[0] != '\r' || self->buffer[1] != '\n') {
                PyErr_SetString(ParseError, "Missing CRLF after chunk");
                return -1;
            }

            memmove(self->buffer, self->buffer + 2, self->buffer_len - 2);
            self->buffer_len -= 2;
            self->state = STATE_BODY_CHUNKED_SIZE;
        }
        else if (self->state == STATE_BODY_CHUNKED_TRAILER) {
            /* Parse trailers (we just skip them) */
            char *crlf = memmem(self->buffer, self->buffer_len, "\r\n", 2);
            if (!crlf) {
                return 0;  /* Need more data */
            }

            size_t line_len = crlf - self->buffer;
            if (line_len == 0) {
                /* Empty line - end of trailers */
                memmove(self->buffer, self->buffer + 2, self->buffer_len - 2);
                self->buffer_len -= 2;
                self->state = STATE_COMPLETE;
                H1CProtocol_clamp_tail(self);
                if (self->on_message_complete) {
                    PyObject *result = PyObject_CallNoArgs(self->on_message_complete);
                    if (!result) return -1;
                    Py_DECREF(result);
                }
                return 0;
            }

            /* RFC 9110 section 6.5.1: forbid routing/framing/auth fields
             * in a trailer. Find the colon that ends the field-name. */
            char *colon = memchr(self->buffer, ':', line_len);
            if (colon) {
                char *name_start = self->buffer;
                size_t name_len = (size_t)(colon - name_start);
                while (name_len > 0 &&
                       (name_start[name_len - 1] == ' ' ||
                        name_start[name_len - 1] == '\t')) {
                    name_len--;
                }
                if (pico_is_forbidden_trailer_name(name_start, name_len)) {
                    PyErr_Format(InvalidHeaderName,
                        "Header field not allowed in trailer: %.*s",
                        (int)name_len, name_start);
                    return -1;
                }
            }

            /* Skip trailer line */
            size_t consumed = line_len + 2;
            memmove(self->buffer, self->buffer + consumed, self->buffer_len - consumed);
            self->buffer_len -= consumed;
        }
        else {
            break;
        }
    }

    return 0;
}

/*
 * Return the bytes fed to the parser that follow the finished message.
 */
static PyObject *
H1CProtocol_remaining(H1CProtocol *self, PyObject *Py_UNUSED(ignored))
{
    /* Only meaningful once the message is complete. While one is still being
     * parsed, every byte fed so far belongs to it, so report nothing. */
    if (self->state != STATE_COMPLETE) {
        return PyBytes_FromStringAndSize(NULL, 0);
    }

    return PyBytes_FromStringAndSize(self->buffer, (Py_ssize_t)self->buffer_len);
}

/*
 * True if the tail overflowed limit_remaining and bytes were dropped.
 */
static PyObject *
H1CProtocol_get_remaining_truncated(H1CProtocol *self, void *closure)
{
    return PyBool_FromLong(self->remaining_truncated);
}

/*
 * Mark parsing complete for EOF handling.
 * Call when no more data will be received. Handles edge cases like
 * chunked encoding without final trailer CRLF.
 */
static PyObject *
H1CProtocol_finish(H1CProtocol *self, PyObject *Py_UNUSED(ignored))
{
    if (self->state == STATE_BODY_CHUNKED_TRAILER) {
        /* All body data received, just missing final CRLF */
        self->state = STATE_COMPLETE;
        /* EOF: nothing follows, so any half-parsed trailer is not a tail */
        self->buffer_len = 0;
        if (self->on_message_complete) {
            PyObject *result = PyObject_CallNoArgs(self->on_message_complete);
            if (!result) return NULL;
            Py_DECREF(result);
        }
    }
    Py_RETURN_NONE;
}

/*
 * Reset the parser for the next request (keepalive).
 */
static PyObject *
H1CProtocol_reset(H1CProtocol *self, PyObject *Py_UNUSED(ignored))
{
    self->state = STATE_IDLE;
    /* Drops any tail: capture remaining() first, then feed it back after
     * reset() so it is parsed as the next message. */
    self->buffer_len = 0;
    self->remaining_truncated = 0;
    self->last_len = 0;

    Py_XDECREF(self->py_method);
    self->py_method = NULL;
    Py_XDECREF(self->py_path);
    self->py_path = NULL;
    Py_XDECREF(self->py_headers);
    self->py_headers = NULL;
    Py_XDECREF(self->py_asgi_headers);
    self->py_asgi_headers = NULL;

    self->minor_version = 1;
    self->content_length = -1;
    self->is_chunked = 0;
    self->connection_close = -1;
    self->should_upgrade = 0;

    self->body_remaining = 0;
    self->chunk_size = 0;
    self->chunk_remaining = 0;
    self->skip_body = 0;

    Py_RETURN_NONE;
}

/* Property getters */

static PyObject *
H1CProtocol_get_method(H1CProtocol *self, void *closure)
{
    if (self->py_method) {
        Py_INCREF(self->py_method);
        return self->py_method;
    }
    return PyBytes_FromString("");
}

static PyObject *
H1CProtocol_get_path(H1CProtocol *self, void *closure)
{
    if (self->py_path) {
        Py_INCREF(self->py_path);
        return self->py_path;
    }
    return PyBytes_FromString("");
}

static PyObject *
H1CProtocol_get_http_version(H1CProtocol *self, void *closure)
{
    return Py_BuildValue("(ii)", 1, self->minor_version);
}

static PyObject *
H1CProtocol_get_headers(H1CProtocol *self, void *closure)
{
    if (self->py_headers) {
        Py_INCREF(self->py_headers);
        return self->py_headers;
    }
    return PyList_New(0);
}

static PyObject *
H1CProtocol_get_asgi_headers(H1CProtocol *self, void *closure)
{
    if (!self->py_headers) {
        return PyList_New(0);
    }

    if (!self->py_asgi_headers) {
        Py_ssize_t n = PyList_GET_SIZE(self->py_headers);
        self->py_asgi_headers = PyList_New(n);
        if (!self->py_asgi_headers) return NULL;

        for (Py_ssize_t i = 0; i < n; i++) {
            PyObject *tuple = PyList_GET_ITEM(self->py_headers, i);
            PyObject *name = PyTuple_GET_ITEM(tuple, 0);
            PyObject *value = PyTuple_GET_ITEM(tuple, 1);

            const char *name_str = PyBytes_AS_STRING(name);
            Py_ssize_t name_len = PyBytes_GET_SIZE(name);

            PyObject *pair = pico_create_header_tuple_lowercase(
                name_str, name_len,
                PyBytes_AS_STRING(value), PyBytes_GET_SIZE(value));
            if (!pair) {
                Py_DECREF(self->py_asgi_headers);
                self->py_asgi_headers = NULL;
                return NULL;
            }
            PyList_SET_ITEM(self->py_asgi_headers, i, pair);
        }
    }
    Py_INCREF(self->py_asgi_headers);
    return self->py_asgi_headers;
}

static PyObject *
H1CProtocol_get_content_length(H1CProtocol *self, void *closure)
{
    if (self->content_length < 0) {
        Py_RETURN_NONE;
    }
    return PyLong_FromSsize_t(self->content_length);
}

static PyObject *
H1CProtocol_get_is_chunked(H1CProtocol *self, void *closure)
{
    return PyBool_FromLong(self->is_chunked);
}

static PyObject *
H1CProtocol_get_should_keep_alive(H1CProtocol *self, void *closure)
{
    if (self->connection_close == 1) {
        Py_RETURN_FALSE;
    }
    if (self->connection_close == 0) {
        Py_RETURN_TRUE;
    }
    /* Default based on HTTP version */
    if (self->minor_version >= 1) {
        Py_RETURN_TRUE;  /* HTTP/1.1+ defaults to keep-alive */
    }
    Py_RETURN_FALSE;  /* HTTP/1.0 defaults to close */
}

static PyObject *
H1CProtocol_get_should_upgrade(H1CProtocol *self, void *closure)
{
    return PyBool_FromLong(self->should_upgrade);
}

static PyObject *
H1CProtocol_get_is_complete(H1CProtocol *self, void *closure)
{
    return PyBool_FromLong(self->state == STATE_COMPLETE);
}

/* get_header method */
static PyObject *
H1CProtocol_get_header(H1CProtocol *self, PyObject *args)
{
    Py_buffer name_buf;

    if (!PyArg_ParseTuple(args, "y*", &name_buf)) {
        return NULL;
    }

    if (!self->py_headers) {
        PyBuffer_Release(&name_buf);
        Py_RETURN_NONE;
    }

    Py_ssize_t n = PyList_GET_SIZE(self->py_headers);
    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *tuple = PyList_GET_ITEM(self->py_headers, i);
        PyObject *hname = PyTuple_GET_ITEM(tuple, 0);
        PyObject *hvalue = PyTuple_GET_ITEM(tuple, 1);

        /* Case-insensitive compare */
        const char *hname_str = PyBytes_AS_STRING(hname);
        Py_ssize_t hname_len = PyBytes_GET_SIZE(hname);

        if (hname_len == (Py_ssize_t)name_buf.len) {
            int match = 1;
            for (Py_ssize_t j = 0; j < hname_len; j++) {
                char c1 = hname_str[j];
                char c2 = ((char *)name_buf.buf)[j];
                if (c1 >= 'A' && c1 <= 'Z') c1 += 32;
                if (c2 >= 'A' && c2 <= 'Z') c2 += 32;
                if (c1 != c2) {
                    match = 0;
                    break;
                }
            }
            if (match) {
                PyBuffer_Release(&name_buf);
                Py_INCREF(hvalue);
                return hvalue;
            }
        }
    }

    PyBuffer_Release(&name_buf);
    Py_RETURN_NONE;
}

static PyGetSetDef H1CProtocol_getset[] = {
    {"method", (getter)H1CProtocol_get_method, NULL,
     "HTTP method (GET, POST, etc.)", NULL},
    {"path", (getter)H1CProtocol_get_path, NULL,
     "Request path including query string", NULL},
    {"http_version", (getter)H1CProtocol_get_http_version, NULL,
     "HTTP version as (major, minor) tuple", NULL},
    {"headers", (getter)H1CProtocol_get_headers, NULL,
     "List of (name, value) header tuples", NULL},
    {"asgi_headers", (getter)H1CProtocol_get_asgi_headers, NULL,
     "Headers with lowercase names for ASGI", NULL},
    {"content_length", (getter)H1CProtocol_get_content_length, NULL,
     "Content-Length header value, or None if not set", NULL},
    {"is_chunked", (getter)H1CProtocol_get_is_chunked, NULL,
     "True if Transfer-Encoding: chunked", NULL},
    {"should_keep_alive", (getter)H1CProtocol_get_should_keep_alive, NULL,
     "True if the connection should be kept alive", NULL},
    {"should_upgrade", (getter)H1CProtocol_get_should_upgrade, NULL,
     "True if the Upgrade header is present", NULL},
    {"is_complete", (getter)H1CProtocol_get_is_complete, NULL,
     "True if the message parsing is complete", NULL},
    {"remaining_truncated", (getter)H1CProtocol_get_remaining_truncated, NULL,
     "True if the tail exceeded limit_remaining and bytes were dropped", NULL},
    {NULL}
};

static PyMethodDef H1CProtocol_methods[] = {
    {"feed", (PyCFunction)H1CProtocol_feed, METH_VARARGS,
     "Feed data to the parser. Callbacks fire synchronously."},
    {"finish", (PyCFunction)H1CProtocol_finish, METH_NOARGS,
     "Mark parsing complete for EOF handling."},
    {"reset", (PyCFunction)H1CProtocol_reset, METH_NOARGS,
     "Reset the parser for the next request (keepalive)."},
    {"get_header", (PyCFunction)H1CProtocol_get_header, METH_VARARGS,
     "Get header value by name (case-insensitive)."},
    {"remaining", (PyCFunction)H1CProtocol_remaining, METH_NOARGS,
     "Bytes fed after the completed message (b'' if none or not complete)."},
    {NULL}
};

static PyTypeObject H1CProtocolType = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "gunicorn_h1c._protocol.H1CProtocol",
    .tp_doc = "Callback-based HTTP/1.1 parser for asyncio integration",
    .tp_basicsize = sizeof(H1CProtocol),
    .tp_itemsize = 0,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .tp_new = PyType_GenericNew,
    .tp_init = (initproc)H1CProtocol_init,
    .tp_dealloc = (destructor)H1CProtocol_dealloc,
    .tp_getset = H1CProtocol_getset,
    .tp_methods = H1CProtocol_methods,
};

/* Module definition */
static PyMethodDef module_methods[] = {
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef protocol_module = {
    PyModuleDef_HEAD_INIT,
    "gunicorn_h1c._protocol",
    "Callback-based HTTP/1.1 parser for asyncio integration",
    -1,
    module_methods
};

PyMODINIT_FUNC
PyInit__protocol(void)
{
    PyObject *m;

    if (PyType_Ready(&H1CProtocolType) < 0)
        return NULL;

    m = PyModule_Create(&protocol_module);
    if (m == NULL)
        return NULL;

    Py_INCREF(&H1CProtocolType);
    PyModule_AddObject(m, "H1CProtocol", (PyObject *)&H1CProtocolType);

    ParseError = PyErr_NewException(
        "gunicorn_h1c._protocol.ParseError", PyExc_ValueError, NULL);
    if (!ParseError) return NULL;
    Py_INCREF(ParseError);
    PyModule_AddObject(m, "ParseError", ParseError);

    /* Import validation exceptions from _parser module */
    PyObject *parser_module = PyImport_ImportModule("gunicorn_h1c._parser");
    if (!parser_module) return NULL;

    LimitRequestLine = PyObject_GetAttrString(parser_module, "LimitRequestLine");
    if (!LimitRequestLine) { Py_DECREF(parser_module); return NULL; }

    LimitRequestHeaders = PyObject_GetAttrString(parser_module, "LimitRequestHeaders");
    if (!LimitRequestHeaders) { Py_DECREF(parser_module); return NULL; }

    InvalidRequestMethod = PyObject_GetAttrString(parser_module, "InvalidRequestMethod");
    if (!InvalidRequestMethod) { Py_DECREF(parser_module); return NULL; }

    InvalidHTTPVersion = PyObject_GetAttrString(parser_module, "InvalidHTTPVersion");
    if (!InvalidHTTPVersion) { Py_DECREF(parser_module); return NULL; }

    InvalidHeaderName = PyObject_GetAttrString(parser_module, "InvalidHeaderName");
    if (!InvalidHeaderName) { Py_DECREF(parser_module); return NULL; }

    InvalidHeader = PyObject_GetAttrString(parser_module, "InvalidHeader");
    if (!InvalidHeader) { Py_DECREF(parser_module); return NULL; }

    InvalidChunkExtension = PyObject_GetAttrString(parser_module, "InvalidChunkExtension");
    if (!InvalidChunkExtension) { Py_DECREF(parser_module); return NULL; }

    Py_DECREF(parser_module);

    return m;
}
