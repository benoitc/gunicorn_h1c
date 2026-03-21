/*
 * Copyright 2026 Benoit Chesneau
 * Licensed under Apache 2.0
 *
 * Callback-based HTTP/1.1 parser for asyncio integration.
 *
 * This module provides H1CProtocol, a zero-copy callback parser
 * optimized for asyncio's data_received() pattern.
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "picohttpparser.h"

#define MAX_HEADERS 256
#define INITIAL_BUFFER_SIZE 4096

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

    /* Parser state */
    int state;
    char *buffer;
    size_t buffer_size;
    size_t buffer_len;
    size_t last_len;

    /* Request data (valid after headers complete) */
    PyObject *py_method;
    PyObject *py_path;
    PyObject *py_headers;  /* list of (name, value) tuples */
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
static int H1CProtocol_feed_body_content_length(H1CProtocol *self, const char *data, size_t len);
static int H1CProtocol_feed_body_chunked(H1CProtocol *self);

/* Case-insensitive string comparison */
static int
header_name_eq(const char *name, size_t name_len, const char *target, size_t target_len)
{
    if (name_len != target_len) return 0;
    for (size_t i = 0; i < name_len; i++) {
        char c = name[i];
        if (c >= 'A' && c <= 'Z') c += 32;
        if (c != target[i]) return 0;
    }
    return 1;
}

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
        NULL
    };

    PyObject *on_message_begin = NULL;
    PyObject *on_url = NULL;
    PyObject *on_header = NULL;
    PyObject *on_headers_complete = NULL;
    PyObject *on_body = NULL;
    PyObject *on_message_complete = NULL;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|OOOOOO", kwlist,
                                     &on_message_begin, &on_url, &on_header,
                                     &on_headers_complete, &on_body,
                                     &on_message_complete)) {
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
        int ret = H1CProtocol_feed_body_content_length(self, buf.buf, buf.len);
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
        PyErr_SetString(ParseError, "Invalid HTTP request");
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

    self->content_length = -1;
    self->is_chunked = 0;
    self->connection_close = -1;
    self->should_upgrade = 0;

    for (size_t i = 0; i < num_headers; i++) {
        const char *name = headers[i].name;
        size_t name_len = headers[i].name_len;
        const char *value = headers[i].value;
        size_t value_len = headers[i].value_len;

        /* Create header tuple */
        PyObject *py_name = PyBytes_FromStringAndSize(name, name_len);
        PyObject *py_value = PyBytes_FromStringAndSize(value, value_len);
        if (!py_name || !py_value) {
            Py_XDECREF(py_name);
            Py_XDECREF(py_value);
            return -1;
        }

        PyObject *tuple = PyTuple_Pack(2, py_name, py_value);
        Py_DECREF(py_name);
        Py_DECREF(py_value);
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

        /* Extract special headers */
        if (header_name_eq(name, name_len, "content-length", 14)) {
            Py_ssize_t cl = 0;
            for (size_t j = 0; j < value_len; j++) {
                char c = value[j];
                if (c >= '0' && c <= '9') {
                    cl = cl * 10 + (c - '0');
                } else {
                    break;
                }
            }
            self->content_length = cl;
        }
        else if (header_name_eq(name, name_len, "transfer-encoding", 17)) {
            /* Check for "chunked" */
            for (size_t j = 0; j + 7 <= value_len; j++) {
                char c0 = value[j];   if (c0 >= 'A' && c0 <= 'Z') c0 += 32;
                char c1 = value[j+1]; if (c1 >= 'A' && c1 <= 'Z') c1 += 32;
                char c2 = value[j+2]; if (c2 >= 'A' && c2 <= 'Z') c2 += 32;
                char c3 = value[j+3]; if (c3 >= 'A' && c3 <= 'Z') c3 += 32;
                char c4 = value[j+4]; if (c4 >= 'A' && c4 <= 'Z') c4 += 32;
                char c5 = value[j+5]; if (c5 >= 'A' && c5 <= 'Z') c5 += 32;
                char c6 = value[j+6]; if (c6 >= 'A' && c6 <= 'Z') c6 += 32;
                if (c0 == 'c' && c1 == 'h' && c2 == 'u' && c3 == 'n' &&
                    c4 == 'k' && c5 == 'e' && c6 == 'd') {
                    self->is_chunked = 1;
                    break;
                }
            }
        }
        else if (header_name_eq(name, name_len, "connection", 10)) {
            for (size_t j = 0; j + 5 <= value_len; j++) {
                char c0 = value[j];   if (c0 >= 'A' && c0 <= 'Z') c0 += 32;
                char c1 = value[j+1]; if (c1 >= 'A' && c1 <= 'Z') c1 += 32;
                char c2 = value[j+2]; if (c2 >= 'A' && c2 <= 'Z') c2 += 32;
                char c3 = value[j+3]; if (c3 >= 'A' && c3 <= 'Z') c3 += 32;
                char c4 = value[j+4]; if (c4 >= 'A' && c4 <= 'Z') c4 += 32;
                if (c0 == 'c' && c1 == 'l' && c2 == 'o' && c3 == 's' && c4 == 'e') {
                    self->connection_close = 1;
                    break;
                }
            }
            if (self->connection_close != 1) {
                for (size_t j = 0; j + 10 <= value_len; j++) {
                    char c0 = value[j];   if (c0 >= 'A' && c0 <= 'Z') c0 += 32;
                    char c1 = value[j+1]; if (c1 >= 'A' && c1 <= 'Z') c1 += 32;
                    char c2 = value[j+2]; if (c2 >= 'A' && c2 <= 'Z') c2 += 32;
                    char c3 = value[j+3]; if (c3 >= 'A' && c3 <= 'Z') c3 += 32;
                    char c4 = value[j+4];
                    char c5 = value[j+5]; if (c5 >= 'A' && c5 <= 'Z') c5 += 32;
                    char c6 = value[j+6]; if (c6 >= 'A' && c6 <= 'Z') c6 += 32;
                    char c7 = value[j+7]; if (c7 >= 'A' && c7 <= 'Z') c7 += 32;
                    char c8 = value[j+8]; if (c8 >= 'A' && c8 <= 'Z') c8 += 32;
                    char c9 = value[j+9]; if (c9 >= 'A' && c9 <= 'Z') c9 += 32;
                    if (c0 == 'k' && c1 == 'e' && c2 == 'e' && c3 == 'p' &&
                        c4 == '-' && c5 == 'a' && c6 == 'l' && c7 == 'i' &&
                        c8 == 'v' && c9 == 'e') {
                        self->connection_close = 0;
                        break;
                    }
                }
            }
        }
        else if (header_name_eq(name, name_len, "upgrade", 7)) {
            self->should_upgrade = 1;
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

    /* Determine body parsing mode */
    if (skip_body || self->should_upgrade) {
        self->state = STATE_COMPLETE;
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
                self, self->buffer, remaining);
        }
    }
    else {
        /* No body */
        self->state = STATE_COMPLETE;
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
H1CProtocol_feed_body_content_length(H1CProtocol *self, const char *data, size_t len)
{
    if (self->skip_body) {
        if ((Py_ssize_t)len >= self->body_remaining) {
            self->body_remaining = 0;
            self->state = STATE_COMPLETE;
            if (self->on_message_complete) {
                PyObject *result = PyObject_CallNoArgs(self->on_message_complete);
                if (!result) return -1;
                Py_DECREF(result);
            }
        } else {
            self->body_remaining -= len;
        }
        /* Clear buffer since we're not using buffered data for body */
        self->buffer_len = 0;
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
    self->buffer_len = 0;  /* Clear buffer */

    if (self->body_remaining == 0) {
        self->state = STATE_COMPLETE;
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

            /* Parse hex number */
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
                } else if (c == ' ' || c == '\t') {
                    continue;  /* Skip whitespace */
                } else {
                    PyErr_SetString(ParseError, "Invalid chunk size");
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
                if (self->on_message_complete) {
                    PyObject *result = PyObject_CallNoArgs(self->on_message_complete);
                    if (!result) return -1;
                    Py_DECREF(result);
                }
                return 0;
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
 * Reset the parser for the next request (keepalive).
 */
static PyObject *
H1CProtocol_reset(H1CProtocol *self, PyObject *Py_UNUSED(ignored))
{
    self->state = STATE_IDLE;
    self->buffer_len = 0;
    self->last_len = 0;

    Py_XDECREF(self->py_method);
    self->py_method = NULL;
    Py_XDECREF(self->py_path);
    self->py_path = NULL;
    Py_XDECREF(self->py_headers);
    self->py_headers = NULL;

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
    {NULL}
};

static PyMethodDef H1CProtocol_methods[] = {
    {"feed", (PyCFunction)H1CProtocol_feed, METH_VARARGS,
     "Feed data to the parser. Callbacks fire synchronously."},
    {"reset", (PyCFunction)H1CProtocol_reset, METH_NOARGS,
     "Reset the parser for the next request (keepalive)."},
    {"get_header", (PyCFunction)H1CProtocol_get_header, METH_VARARGS,
     "Get header value by name (case-insensitive)."},
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
    Py_INCREF(ParseError);
    PyModule_AddObject(m, "ParseError", ParseError);

    return m;
}
