/*
 * Copyright 2026 Benoit Chesneau
 * Licensed under Apache 2.0
 *
 * Optimized Python C extension for picohttpparser.
 *
 * Optimizations:
 * 1. Direct bytes: Returns PyBytes objects (avoids memoryview->bytes conversion)
 * 2. Lazy evaluation: Only creates Python objects when accessed
 * 3. Pre-allocated: Reuses header array
 * 4. Minimal allocations: Uses tuple instead of dict
 * 5. Common header extraction: content_length, has_chunked, connection_close
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "picohttpparser.h"
#include "pico_utils.h"

#define MAX_HEADERS 256

/* Exceptions */
static PyObject *ParseError;
static PyObject *IncompleteError;

/*
 * HttpRequest type - holds parsed request with zero-copy access
 */
typedef struct {
    PyObject_HEAD
    /* Original buffer (kept alive) */
    PyObject *buffer;
    Py_buffer view;

    /* Parsed pointers (into buffer) */
    const char *method;
    size_t method_len;
    const char *path;
    size_t path_len;
    int minor_version;

    /* Headers */
    struct phr_header headers[MAX_HEADERS];
    size_t num_headers;

    /* Consumed bytes */
    int consumed;

    /* Common headers (extracted during parse) */
    Py_ssize_t content_length;   /* -1 if not set */
    int has_chunked;             /* 1 if Transfer-Encoding: chunked */
    int connection_close;        /* 1=close, 0=keep-alive, -1=unset */

    /* Cached Python objects (lazy) */
    PyObject *py_method;
    PyObject *py_path;
    PyObject *py_headers;
} HttpRequest;

static void
HttpRequest_dealloc(HttpRequest *self)
{
    Py_XDECREF(self->py_method);
    Py_XDECREF(self->py_path);
    Py_XDECREF(self->py_headers);
    if (self->view.buf) {
        PyBuffer_Release(&self->view);
    }
    Py_XDECREF(self->buffer);
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *
HttpRequest_get_method(HttpRequest *self, void *closure)
{
    if (!self->py_method) {
        /* Return bytes directly - avoids memoryview->bytes conversion overhead */
        self->py_method = PyBytes_FromStringAndSize(self->method, self->method_len);
        if (!self->py_method) return NULL;
    }
    Py_INCREF(self->py_method);
    return self->py_method;
}

static PyObject *
HttpRequest_get_path(HttpRequest *self, void *closure)
{
    if (!self->py_path) {
        /* Return bytes directly - avoids memoryview->bytes conversion overhead */
        self->py_path = PyBytes_FromStringAndSize(self->path, self->path_len);
        if (!self->py_path) return NULL;
    }
    Py_INCREF(self->py_path);
    return self->py_path;
}

static PyObject *
HttpRequest_get_version(HttpRequest *self, void *closure)
{
    return PyLong_FromLong(self->minor_version);
}

static PyObject *
HttpRequest_get_headers(HttpRequest *self, void *closure)
{
    if (!self->py_headers) {
        self->py_headers = PyTuple_New(self->num_headers);
        if (!self->py_headers) return NULL;

        for (size_t i = 0; i < self->num_headers; i++) {
            PyObject *pair = pico_create_header_tuple(
                self->headers[i].name, self->headers[i].name_len,
                self->headers[i].value, self->headers[i].value_len);
            if (!pair) {
                Py_DECREF(self->py_headers);
                self->py_headers = NULL;
                return NULL;
            }
            PyTuple_SET_ITEM(self->py_headers, i, pair);
        }
    }
    Py_INCREF(self->py_headers);
    return self->py_headers;
}

static PyObject *
HttpRequest_get_consumed(HttpRequest *self, void *closure)
{
    return PyLong_FromLong(self->consumed);
}

/* Fast header lookup by name - returns bytes */
static PyObject *
HttpRequest_get_header(HttpRequest *self, PyObject *args)
{
    const char *name;
    Py_ssize_t name_len;

    if (!PyArg_ParseTuple(args, "s#", &name, &name_len)) {
        return NULL;
    }

    return pico_find_header(self->headers, self->num_headers, name, (size_t)name_len);
}

/* Get header count without creating list */
static PyObject *
HttpRequest_get_header_count(HttpRequest *self, void *closure)
{
    return PyLong_FromSize_t(self->num_headers);
}

/* Getters for common headers (extracted during parse) */
static PyObject *
HttpRequest_get_content_length(HttpRequest *self, void *closure)
{
    return PyLong_FromSsize_t(self->content_length);
}

static PyObject *
HttpRequest_get_has_chunked(HttpRequest *self, void *closure)
{
    return PyBool_FromLong(self->has_chunked);
}

static PyObject *
HttpRequest_get_connection_close(HttpRequest *self, void *closure)
{
    return PyLong_FromLong(self->connection_close);
}

/* header_name_eq moved to pico_utils.h */

/* Extract common headers using shared utility */
static void
extract_special_headers(HttpRequest *self)
{
    PicoHeaderInfo info;
    pico_extract_headers(self->headers, self->num_headers, &info);
    self->content_length = info.content_length;
    self->has_chunked = info.has_chunked;
    self->connection_close = info.connection_close;
}

static PyGetSetDef HttpRequest_getset[] = {
    {"method", (getter)HttpRequest_get_method, NULL, "HTTP method", NULL},
    {"path", (getter)HttpRequest_get_path, NULL, "Request path", NULL},
    {"minor_version", (getter)HttpRequest_get_version, NULL, "HTTP minor version", NULL},
    {"headers", (getter)HttpRequest_get_headers, NULL, "Request headers", NULL},
    {"consumed", (getter)HttpRequest_get_consumed, NULL, "Bytes consumed", NULL},
    {"header_count", (getter)HttpRequest_get_header_count, NULL, "Number of headers", NULL},
    {"content_length", (getter)HttpRequest_get_content_length, NULL, "Content-Length (-1 if not set)", NULL},
    {"has_chunked", (getter)HttpRequest_get_has_chunked, NULL, "True if Transfer-Encoding: chunked", NULL},
    {"connection_close", (getter)HttpRequest_get_connection_close, NULL, "1=close, 0=keep-alive, -1=unset", NULL},
    {NULL}
};

static PyMethodDef HttpRequest_methods[] = {
    {"get_header", (PyCFunction)HttpRequest_get_header, METH_VARARGS,
     "Get header value by name (case-insensitive)"},
    {NULL}
};

static PyTypeObject HttpRequestType = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "gunicorn_h1c._parser_fast.HttpRequest",
    .tp_doc = "Parsed HTTP request with zero-copy access",
    .tp_basicsize = sizeof(HttpRequest),
    .tp_itemsize = 0,
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_dealloc = (destructor)HttpRequest_dealloc,
    .tp_getset = HttpRequest_getset,
    .tp_methods = HttpRequest_methods,
};

/*
 * parse_request(data: bytes) -> HttpRequest
 *
 * Parse HTTP request with zero-copy optimization.
 * Returns HttpRequest object that references original buffer.
 */
static PyObject *
pico_parse_request_fast(PyObject *self, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"data", "last_len", NULL};
    PyObject *data;
    Py_ssize_t last_len = 0;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O|n", kwlist,
                                     &data, &last_len)) {
        return NULL;
    }

    /* Create request object */
    HttpRequest *req = PyObject_New(HttpRequest, &HttpRequestType);
    if (!req) return NULL;

    /* Initialize */
    req->buffer = NULL;
    req->view.buf = NULL;
    req->py_method = NULL;
    req->py_path = NULL;
    req->py_headers = NULL;

    /* Get buffer */
    if (PyObject_GetBuffer(data, &req->view, PyBUF_SIMPLE) < 0) {
        Py_DECREF(req);
        return NULL;
    }

    /* Keep reference to original buffer */
    Py_INCREF(data);
    req->buffer = data;

    /* Parse */
    req->num_headers = MAX_HEADERS;
    int ret = phr_parse_request(
        req->view.buf, req->view.len,
        &req->method, &req->method_len,
        &req->path, &req->path_len,
        &req->minor_version,
        req->headers, &req->num_headers,
        (size_t)last_len
    );

    if (ret > 0) {
        req->consumed = ret;
        extract_special_headers(req);
        return (PyObject *)req;
    }
    else if (ret == -2) {
        Py_DECREF(req);
        PyErr_SetString(IncompleteError, "Incomplete request");
        return NULL;
    }
    else {
        Py_DECREF(req);
        PyErr_SetString(ParseError, "Invalid HTTP request");
        return NULL;
    }
}

/*
 * parse_request_raw(data: bytes) -> tuple
 *
 * Ultra-fast parsing that returns raw tuple:
 * (method_offset, method_len, path_offset, path_len, version,
 *  header_count, consumed, header_data)
 *
 * header_data is bytes containing packed header offsets/lengths
 */
static PyObject *
pico_parse_request_raw(PyObject *self, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"data", "last_len", NULL};
    Py_buffer buf;
    Py_ssize_t last_len = 0;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "y*|n", kwlist,
                                     &buf, &last_len)) {
        return NULL;
    }

    const char *method, *path;
    size_t method_len, path_len;
    int minor_version;
    struct phr_header headers[MAX_HEADERS];
    size_t num_headers = MAX_HEADERS;

    int ret = phr_parse_request(
        buf.buf, buf.len,
        &method, &method_len,
        &path, &path_len,
        &minor_version,
        headers, &num_headers,
        (size_t)last_len
    );

    if (ret > 0) {
        /* Calculate offsets relative to buffer start */
        Py_ssize_t method_offset = method - (const char *)buf.buf;
        Py_ssize_t path_offset = path - (const char *)buf.buf;

        /* Pack header offsets into bytes */
        /* Each header: 4 bytes name_offset, 2 bytes name_len, 4 bytes value_offset, 2 bytes value_len */
        PyObject *header_data = PyBytes_FromStringAndSize(NULL, num_headers * 12);
        if (!header_data) {
            PyBuffer_Release(&buf);
            return NULL;
        }

        char *hdata = PyBytes_AS_STRING(header_data);
        for (size_t i = 0; i < num_headers; i++) {
            uint32_t name_off = (uint32_t)(headers[i].name - (const char *)buf.buf);
            uint16_t name_len = (uint16_t)headers[i].name_len;
            uint32_t val_off = (uint32_t)(headers[i].value - (const char *)buf.buf);
            uint16_t val_len = (uint16_t)headers[i].value_len;

            /* Little-endian pack */
            memcpy(hdata + i * 12, &name_off, 4);
            memcpy(hdata + i * 12 + 4, &name_len, 2);
            memcpy(hdata + i * 12 + 6, &val_off, 4);
            memcpy(hdata + i * 12 + 10, &val_len, 2);
        }

        PyBuffer_Release(&buf);

        PyObject *result = Py_BuildValue("(nnnniiiO)",
            method_offset, (Py_ssize_t)method_len,
            path_offset, (Py_ssize_t)path_len,
            minor_version,
            (int)num_headers,
            ret,  /* consumed */
            header_data);
        Py_DECREF(header_data);
        return result;
    }

    PyBuffer_Release(&buf);

    if (ret == -2) {
        PyErr_SetString(IncompleteError, "Incomplete request");
    } else {
        PyErr_SetString(ParseError, "Invalid HTTP request");
    }
    return NULL;
}

/* Module methods */
static PyMethodDef pico_methods[] = {
    {"parse_request", (PyCFunction)pico_parse_request_fast,
     METH_VARARGS | METH_KEYWORDS,
     "Parse HTTP request (zero-copy, lazy evaluation)"},
    {"parse_request_raw", (PyCFunction)pico_parse_request_raw,
     METH_VARARGS | METH_KEYWORDS,
     "Parse HTTP request (returns raw offsets for maximum speed)"},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef pico_module = {
    PyModuleDef_HEAD_INIT,
    "gunicorn_h1c._parser_fast",
    "Ultra-fast HTTP parser with zero-copy optimization",
    -1,
    pico_methods
};

PyMODINIT_FUNC
PyInit__parser_fast(void)
{
    PyObject *m;

    if (PyType_Ready(&HttpRequestType) < 0)
        return NULL;

    m = PyModule_Create(&pico_module);
    if (m == NULL)
        return NULL;

    Py_INCREF(&HttpRequestType);
    PyModule_AddObject(m, "HttpRequest", (PyObject *)&HttpRequestType);

    ParseError = PyErr_NewException("gunicorn_h1c._parser_fast.ParseError", PyExc_ValueError, NULL);
    Py_INCREF(ParseError);
    PyModule_AddObject(m, "ParseError", ParseError);

    IncompleteError = PyErr_NewException("gunicorn_h1c._parser_fast.IncompleteError", PyExc_Exception, NULL);
    Py_INCREF(IncompleteError);
    PyModule_AddObject(m, "IncompleteError", IncompleteError);

    return m;
}
