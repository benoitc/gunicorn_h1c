/*
 * Copyright 2026 Benoit Chesneau
 * Licensed under Apache 2.0
 *
 * Python C extension for picohttpparser.
 * Provides fast HTTP request/response parsing using SIMD when available.
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "picohttpparser.h"

#define MAX_HEADERS 256

/* Forward declarations */
static PyObject *PicoError;
static PyObject *IncompleteError;

/*
 * parse_request(data: bytes, last_len: int = 0) -> dict
 *
 * Parse HTTP request and return dict with:
 *   method, path, minor_version, headers, consumed
 */
static PyObject *
pico_parse_request(PyObject *self, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"data", "last_len", NULL};
    Py_buffer buf;
    Py_ssize_t last_len = 0;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "y*|n", kwlist,
                                     &buf, &last_len)) {
        return NULL;
    }

    const char *method;
    size_t method_len;
    const char *path;
    size_t path_len;
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

    PyBuffer_Release(&buf);

    if (ret > 0) {
        /* Success - build result dict */
        PyObject *result = PyDict_New();
        if (!result) return NULL;

        PyObject *py_method = PyBytes_FromStringAndSize(method, method_len);
        PyObject *py_path = PyBytes_FromStringAndSize(path, path_len);
        PyObject *py_version = PyLong_FromLong(minor_version);
        PyObject *py_consumed = PyLong_FromLong(ret);

        /* Build headers list */
        PyObject *py_headers = PyList_New(num_headers);
        if (!py_headers) {
            Py_DECREF(result);
            Py_XDECREF(py_method);
            Py_XDECREF(py_path);
            Py_XDECREF(py_version);
            Py_XDECREF(py_consumed);
            return NULL;
        }

        for (size_t i = 0; i < num_headers; i++) {
            PyObject *name = PyBytes_FromStringAndSize(
                headers[i].name, headers[i].name_len);
            PyObject *value = PyBytes_FromStringAndSize(
                headers[i].value, headers[i].value_len);
            PyObject *tuple = PyTuple_Pack(2, name, value);
            Py_DECREF(name);
            Py_DECREF(value);
            PyList_SET_ITEM(py_headers, i, tuple);
        }

        PyDict_SetItemString(result, "method", py_method);
        PyDict_SetItemString(result, "path", py_path);
        PyDict_SetItemString(result, "minor_version", py_version);
        PyDict_SetItemString(result, "headers", py_headers);
        PyDict_SetItemString(result, "consumed", py_consumed);

        Py_DECREF(py_method);
        Py_DECREF(py_path);
        Py_DECREF(py_version);
        Py_DECREF(py_headers);
        Py_DECREF(py_consumed);

        return result;
    }
    else if (ret == -2) {
        PyErr_SetString(IncompleteError, "Incomplete request, need more data");
        return NULL;
    }
    else {
        PyErr_SetString(PicoError, "Invalid HTTP request");
        return NULL;
    }
}

/*
 * parse_response(data: bytes, last_len: int = 0) -> dict
 */
static PyObject *
pico_parse_response(PyObject *self, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"data", "last_len", NULL};
    Py_buffer buf;
    Py_ssize_t last_len = 0;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "y*|n", kwlist,
                                     &buf, &last_len)) {
        return NULL;
    }

    int minor_version;
    int status;
    const char *msg;
    size_t msg_len;
    struct phr_header headers[MAX_HEADERS];
    size_t num_headers = MAX_HEADERS;

    int ret = phr_parse_response(
        buf.buf, buf.len,
        &minor_version,
        &status,
        &msg, &msg_len,
        headers, &num_headers,
        (size_t)last_len
    );

    PyBuffer_Release(&buf);

    if (ret > 0) {
        PyObject *result = PyDict_New();
        if (!result) return NULL;

        PyObject *py_status = PyLong_FromLong(status);
        PyObject *py_message = PyBytes_FromStringAndSize(msg, msg_len);
        PyObject *py_version = PyLong_FromLong(minor_version);
        PyObject *py_consumed = PyLong_FromLong(ret);

        PyObject *py_headers = PyList_New(num_headers);
        for (size_t i = 0; i < num_headers; i++) {
            PyObject *name = PyBytes_FromStringAndSize(
                headers[i].name, headers[i].name_len);
            PyObject *value = PyBytes_FromStringAndSize(
                headers[i].value, headers[i].value_len);
            PyObject *tuple = PyTuple_Pack(2, name, value);
            Py_DECREF(name);
            Py_DECREF(value);
            PyList_SET_ITEM(py_headers, i, tuple);
        }

        PyDict_SetItemString(result, "status", py_status);
        PyDict_SetItemString(result, "message", py_message);
        PyDict_SetItemString(result, "minor_version", py_version);
        PyDict_SetItemString(result, "headers", py_headers);
        PyDict_SetItemString(result, "consumed", py_consumed);

        Py_DECREF(py_status);
        Py_DECREF(py_message);
        Py_DECREF(py_version);
        Py_DECREF(py_headers);
        Py_DECREF(py_consumed);

        return result;
    }
    else if (ret == -2) {
        PyErr_SetString(IncompleteError, "Incomplete response, need more data");
        return NULL;
    }
    else {
        PyErr_SetString(PicoError, "Invalid HTTP response");
        return NULL;
    }
}

/*
 * Helper: Split path into path_info and query_string
 * Returns pointer to '?' or NULL if not found
 */
static const char *
find_query_string(const char *path, size_t path_len)
{
    for (size_t i = 0; i < path_len; i++) {
        if (path[i] == '?') {
            return &path[i];
        }
    }
    return NULL;
}

/*
 * Helper: Convert header name to WSGI HTTP_* format
 * - Uppercase the name
 * - Replace "-" with "_"
 * - Prepend "HTTP_" (except for CONTENT_TYPE and CONTENT_LENGTH)
 */
static int
header_is_content_type(const char *name, size_t name_len)
{
    if (name_len != 12) return 0;
    const char *target = "content-type";
    for (size_t i = 0; i < 12; i++) {
        char c = name[i];
        if (c >= 'A' && c <= 'Z') c += 32;
        if (c != target[i]) return 0;
    }
    return 1;
}

static int
header_is_content_length(const char *name, size_t name_len)
{
    if (name_len != 14) return 0;
    const char *target = "content-length";
    for (size_t i = 0; i < 14; i++) {
        char c = name[i];
        if (c >= 'A' && c <= 'Z') c += 32;
        if (c != target[i]) return 0;
    }
    return 1;
}

/*
 * parse_to_wsgi_environ(data, server=None, client=None, url_scheme="http") -> dict
 *
 * Parse HTTP request and return WSGI environ dict.
 */
static PyObject *
pico_parse_to_wsgi_environ(PyObject *self, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"data", "server", "client", "url_scheme", NULL};
    Py_buffer buf;
    PyObject *server = NULL;
    PyObject *client = NULL;
    const char *url_scheme = "http";
    Py_ssize_t url_scheme_len = 4;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "y*|OOs#", kwlist,
                                     &buf, &server, &client,
                                     &url_scheme, &url_scheme_len)) {
        return NULL;
    }

    const char *method;
    size_t method_len;
    const char *path;
    size_t path_len;
    int minor_version;
    struct phr_header headers[MAX_HEADERS];
    size_t num_headers = MAX_HEADERS;

    int ret = phr_parse_request(
        buf.buf, buf.len,
        &method, &method_len,
        &path, &path_len,
        &minor_version,
        headers, &num_headers,
        0
    );

    PyBuffer_Release(&buf);

    if (ret == -2) {
        PyErr_SetString(IncompleteError, "Incomplete request, need more data");
        return NULL;
    }
    else if (ret < 0) {
        PyErr_SetString(PicoError, "Invalid HTTP request");
        return NULL;
    }

    /* Success - build WSGI environ dict */
    PyObject *environ = PyDict_New();
    if (!environ) return NULL;

    /* Split path into PATH_INFO and QUERY_STRING */
    const char *query_start = find_query_string(path, path_len);
    size_t path_info_len;
    const char *query_string;
    size_t query_string_len;

    if (query_start) {
        path_info_len = query_start - path;
        query_string = query_start + 1;
        query_string_len = path_len - path_info_len - 1;
    } else {
        path_info_len = path_len;
        query_string = "";
        query_string_len = 0;
    }

    /* REQUEST_METHOD (str, latin-1) */
    PyObject *py_method = PyUnicode_DecodeLatin1(method, method_len, NULL);
    if (!py_method) goto error;
    PyDict_SetItemString(environ, "REQUEST_METHOD", py_method);
    Py_DECREF(py_method);

    /* PATH_INFO (str, latin-1) */
    PyObject *py_path_info = PyUnicode_DecodeLatin1(path, path_info_len, NULL);
    if (!py_path_info) goto error;
    PyDict_SetItemString(environ, "PATH_INFO", py_path_info);
    Py_DECREF(py_path_info);

    /* QUERY_STRING (str) */
    PyObject *py_query = PyUnicode_DecodeLatin1(query_string, query_string_len, NULL);
    if (!py_query) goto error;
    PyDict_SetItemString(environ, "QUERY_STRING", py_query);
    Py_DECREF(py_query);

    /* SERVER_PROTOCOL */
    PyObject *py_protocol = minor_version == 0 ?
        PyUnicode_FromString("HTTP/1.0") :
        PyUnicode_FromString("HTTP/1.1");
    if (!py_protocol) goto error;
    PyDict_SetItemString(environ, "SERVER_PROTOCOL", py_protocol);
    Py_DECREF(py_protocol);

    /* wsgi.url_scheme */
    PyObject *py_scheme = PyUnicode_DecodeLatin1(url_scheme, url_scheme_len, NULL);
    if (!py_scheme) goto error;
    PyDict_SetItemString(environ, "wsgi.url_scheme", py_scheme);
    Py_DECREF(py_scheme);

    /* SERVER_NAME, SERVER_PORT from server tuple */
    if (server && PyTuple_Check(server) && PyTuple_Size(server) >= 2) {
        PyObject *server_name = PyTuple_GetItem(server, 0);
        PyObject *server_port = PyTuple_GetItem(server, 1);
        PyDict_SetItemString(environ, "SERVER_NAME", server_name);
        /* Convert port to string */
        if (PyLong_Check(server_port)) {
            PyObject *port_str = PyObject_Str(server_port);
            if (port_str) {
                PyDict_SetItemString(environ, "SERVER_PORT", port_str);
                Py_DECREF(port_str);
            }
        } else {
            PyDict_SetItemString(environ, "SERVER_PORT", server_port);
        }
    }

    /* REMOTE_ADDR, REMOTE_PORT from client tuple */
    if (client && PyTuple_Check(client) && PyTuple_Size(client) >= 2) {
        PyObject *remote_addr = PyTuple_GetItem(client, 0);
        PyObject *remote_port = PyTuple_GetItem(client, 1);
        PyDict_SetItemString(environ, "REMOTE_ADDR", remote_addr);
        /* Convert port to string */
        if (PyLong_Check(remote_port)) {
            PyObject *port_str = PyObject_Str(remote_port);
            if (port_str) {
                PyDict_SetItemString(environ, "REMOTE_PORT", port_str);
                Py_DECREF(port_str);
            }
        } else {
            PyDict_SetItemString(environ, "REMOTE_PORT", remote_port);
        }
    }

    /* Process headers - need to handle duplicates by joining with "," */
    /* First, build a temporary dict to merge duplicate headers */
    PyObject *header_dict = PyDict_New();
    if (!header_dict) goto error;

    for (size_t i = 0; i < num_headers; i++) {
        const char *name = headers[i].name;
        size_t name_len = headers[i].name_len;
        const char *value = headers[i].value;
        size_t value_len = headers[i].value_len;

        /* Build the environ key name */
        char key_buf[256];
        size_t key_len = 0;
        int is_content_type = header_is_content_type(name, name_len);
        int is_content_length = header_is_content_length(name, name_len);

        if (is_content_type) {
            memcpy(key_buf, "CONTENT_TYPE", 12);
            key_len = 12;
        } else if (is_content_length) {
            memcpy(key_buf, "CONTENT_LENGTH", 14);
            key_len = 14;
        } else {
            /* HTTP_* prefix */
            memcpy(key_buf, "HTTP_", 5);
            key_len = 5;
            /* Copy and transform header name */
            for (size_t j = 0; j < name_len && key_len < 255; j++) {
                char c = name[j];
                if (c >= 'a' && c <= 'z') {
                    c -= 32;  /* lowercase to uppercase */
                } else if (c == '-') {
                    c = '_';  /* dash to underscore */
                }
                key_buf[key_len++] = c;
            }
        }
        key_buf[key_len] = '\0';

        /* Decode value as latin-1 */
        PyObject *py_value = PyUnicode_DecodeLatin1(value, value_len, NULL);
        if (!py_value) {
            Py_DECREF(header_dict);
            goto error;
        }

        /* Check if key already exists (duplicate header) */
        PyObject *existing = PyDict_GetItemString(header_dict, key_buf);
        if (existing) {
            /* Join with comma */
            PyObject *comma = PyUnicode_FromString(",");
            PyObject *joined = PyUnicode_Concat(existing, comma);
            Py_DECREF(comma);
            if (joined) {
                PyObject *final = PyUnicode_Concat(joined, py_value);
                Py_DECREF(joined);
                if (final) {
                    PyDict_SetItemString(header_dict, key_buf, final);
                    Py_DECREF(final);
                }
            }
        } else {
            PyDict_SetItemString(header_dict, key_buf, py_value);
        }
        Py_DECREF(py_value);
    }

    /* Merge header_dict into environ */
    PyObject *key, *value;
    Py_ssize_t pos = 0;
    while (PyDict_Next(header_dict, &pos, &key, &value)) {
        PyDict_SetItem(environ, key, value);
    }
    Py_DECREF(header_dict);

    /* Add consumed bytes count */
    PyObject *py_consumed = PyLong_FromLong(ret);
    if (!py_consumed) goto error;
    PyDict_SetItemString(environ, "_consumed", py_consumed);
    Py_DECREF(py_consumed);

    return environ;

error:
    Py_DECREF(environ);
    return NULL;
}

/*
 * parse_to_asgi_scope(data, server=None, client=None, scheme="http", root_path="") -> dict
 *
 * Parse HTTP request and return ASGI scope dict.
 */
static PyObject *
pico_parse_to_asgi_scope(PyObject *self, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"data", "server", "client", "scheme", "root_path", NULL};
    Py_buffer buf;
    PyObject *server = NULL;
    PyObject *client = NULL;
    const char *scheme = "http";
    Py_ssize_t scheme_len = 4;
    const char *root_path = "";
    Py_ssize_t root_path_len = 0;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "y*|OOs#s#", kwlist,
                                     &buf, &server, &client,
                                     &scheme, &scheme_len,
                                     &root_path, &root_path_len)) {
        return NULL;
    }

    const char *method;
    size_t method_len;
    const char *path;
    size_t path_len;
    int minor_version;
    struct phr_header headers[MAX_HEADERS];
    size_t num_headers = MAX_HEADERS;

    int ret = phr_parse_request(
        buf.buf, buf.len,
        &method, &method_len,
        &path, &path_len,
        &minor_version,
        headers, &num_headers,
        0
    );

    PyBuffer_Release(&buf);

    if (ret == -2) {
        PyErr_SetString(IncompleteError, "Incomplete request, need more data");
        return NULL;
    }
    else if (ret < 0) {
        PyErr_SetString(PicoError, "Invalid HTTP request");
        return NULL;
    }

    /* Success - build ASGI scope dict */
    PyObject *scope = PyDict_New();
    if (!scope) return NULL;

    /* type: "http" */
    PyObject *py_type = PyUnicode_FromString("http");
    if (!py_type) goto error;
    PyDict_SetItemString(scope, "type", py_type);
    Py_DECREF(py_type);

    /* asgi: {"version": "3.0", "spec_version": "2.4"} */
    PyObject *asgi_dict = PyDict_New();
    if (!asgi_dict) goto error;
    PyObject *asgi_version = PyUnicode_FromString("3.0");
    PyObject *spec_version = PyUnicode_FromString("2.4");
    if (!asgi_version || !spec_version) {
        Py_XDECREF(asgi_version);
        Py_XDECREF(spec_version);
        Py_DECREF(asgi_dict);
        goto error;
    }
    PyDict_SetItemString(asgi_dict, "version", asgi_version);
    PyDict_SetItemString(asgi_dict, "spec_version", spec_version);
    Py_DECREF(asgi_version);
    Py_DECREF(spec_version);
    PyDict_SetItemString(scope, "asgi", asgi_dict);
    Py_DECREF(asgi_dict);

    /* http_version: "1.0" or "1.1" */
    PyObject *py_http_version = minor_version == 0 ?
        PyUnicode_FromString("1.0") :
        PyUnicode_FromString("1.1");
    if (!py_http_version) goto error;
    PyDict_SetItemString(scope, "http_version", py_http_version);
    Py_DECREF(py_http_version);

    /* method: str */
    PyObject *py_method = PyUnicode_DecodeLatin1(method, method_len, NULL);
    if (!py_method) goto error;
    PyDict_SetItemString(scope, "method", py_method);
    Py_DECREF(py_method);

    /* scheme: str */
    PyObject *py_scheme = PyUnicode_DecodeLatin1(scheme, scheme_len, NULL);
    if (!py_scheme) goto error;
    PyDict_SetItemString(scope, "scheme", py_scheme);
    Py_DECREF(py_scheme);

    /* Split path into path and query_string */
    const char *query_start = find_query_string(path, path_len);
    size_t path_only_len;
    const char *query_string;
    size_t query_string_len;

    if (query_start) {
        path_only_len = query_start - path;
        query_string = query_start + 1;
        query_string_len = path_len - path_only_len - 1;
    } else {
        path_only_len = path_len;
        query_string = "";
        query_string_len = 0;
    }

    /* path: str (decoded) */
    PyObject *py_path = PyUnicode_DecodeLatin1(path, path_only_len, NULL);
    if (!py_path) goto error;
    PyDict_SetItemString(scope, "path", py_path);
    Py_DECREF(py_path);

    /* raw_path: bytes (path without query string) */
    PyObject *py_raw_path = PyBytes_FromStringAndSize(path, path_only_len);
    if (!py_raw_path) goto error;
    PyDict_SetItemString(scope, "raw_path", py_raw_path);
    Py_DECREF(py_raw_path);

    /* query_string: bytes */
    PyObject *py_query = PyBytes_FromStringAndSize(query_string, query_string_len);
    if (!py_query) goto error;
    PyDict_SetItemString(scope, "query_string", py_query);
    Py_DECREF(py_query);

    /* root_path: str */
    PyObject *py_root_path = PyUnicode_DecodeLatin1(root_path, root_path_len, NULL);
    if (!py_root_path) goto error;
    PyDict_SetItemString(scope, "root_path", py_root_path);
    Py_DECREF(py_root_path);

    /* server: tuple or None */
    if (server && server != Py_None) {
        PyDict_SetItemString(scope, "server", server);
    } else {
        Py_INCREF(Py_None);
        PyDict_SetItemString(scope, "server", Py_None);
        Py_DECREF(Py_None);
    }

    /* client: tuple or None */
    if (client && client != Py_None) {
        PyDict_SetItemString(scope, "client", client);
    } else {
        Py_INCREF(Py_None);
        PyDict_SetItemString(scope, "client", Py_None);
        Py_DECREF(Py_None);
    }

    /* headers: list of (bytes, bytes) tuples with lowercase names */
    PyObject *py_headers = PyList_New(num_headers);
    if (!py_headers) goto error;

    for (size_t i = 0; i < num_headers; i++) {
        const char *name = headers[i].name;
        size_t name_len = headers[i].name_len;
        const char *value = headers[i].value;
        size_t value_len = headers[i].value_len;

        /* Create lowercase name */
        char *lower_name = PyMem_Malloc(name_len);
        if (!lower_name) {
            Py_DECREF(py_headers);
            PyErr_NoMemory();
            goto error;
        }
        for (size_t j = 0; j < name_len; j++) {
            char c = name[j];
            if (c >= 'A' && c <= 'Z') {
                c += 32;  /* uppercase to lowercase */
            }
            lower_name[j] = c;
        }

        PyObject *py_name = PyBytes_FromStringAndSize(lower_name, name_len);
        PyMem_Free(lower_name);
        if (!py_name) {
            Py_DECREF(py_headers);
            goto error;
        }

        PyObject *py_value = PyBytes_FromStringAndSize(value, value_len);
        if (!py_value) {
            Py_DECREF(py_name);
            Py_DECREF(py_headers);
            goto error;
        }

        PyObject *tuple = PyTuple_Pack(2, py_name, py_value);
        Py_DECREF(py_name);
        Py_DECREF(py_value);
        if (!tuple) {
            Py_DECREF(py_headers);
            goto error;
        }

        PyList_SET_ITEM(py_headers, i, tuple);
    }
    PyDict_SetItemString(scope, "headers", py_headers);
    Py_DECREF(py_headers);

    /* Add consumed bytes count */
    PyObject *py_consumed = PyLong_FromLong(ret);
    if (!py_consumed) goto error;
    PyDict_SetItemString(scope, "_consumed", py_consumed);
    Py_DECREF(py_consumed);

    return scope;

error:
    Py_DECREF(scope);
    return NULL;
}

/*
 * parse_headers(data: bytes, last_len: int = 0) -> list
 */
static PyObject *
pico_parse_headers(PyObject *self, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"data", "last_len", NULL};
    Py_buffer buf;
    Py_ssize_t last_len = 0;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "y*|n", kwlist,
                                     &buf, &last_len)) {
        return NULL;
    }

    struct phr_header headers[MAX_HEADERS];
    size_t num_headers = MAX_HEADERS;

    int ret = phr_parse_headers(
        buf.buf, buf.len,
        headers, &num_headers,
        (size_t)last_len
    );

    PyBuffer_Release(&buf);

    if (ret > 0) {
        PyObject *py_headers = PyList_New(num_headers);
        for (size_t i = 0; i < num_headers; i++) {
            PyObject *name = PyBytes_FromStringAndSize(
                headers[i].name, headers[i].name_len);
            PyObject *value = PyBytes_FromStringAndSize(
                headers[i].value, headers[i].value_len);
            PyObject *tuple = PyTuple_Pack(2, name, value);
            Py_DECREF(name);
            Py_DECREF(value);
            PyList_SET_ITEM(py_headers, i, tuple);
        }
        return py_headers;
    }
    else if (ret == -2) {
        PyErr_SetString(IncompleteError, "Incomplete headers");
        return NULL;
    }
    else {
        PyErr_SetString(PicoError, "Invalid headers");
        return NULL;
    }
}

/* Module method table */
static PyMethodDef pico_methods[] = {
    {"parse_request", (PyCFunction)pico_parse_request,
     METH_VARARGS | METH_KEYWORDS,
     "Parse HTTP request.\n\n"
     "Args:\n"
     "    data: Raw HTTP request bytes\n"
     "    last_len: Previously parsed length for incremental parsing\n\n"
     "Returns:\n"
     "    dict with method, path, minor_version, headers, consumed"},

    {"parse_response", (PyCFunction)pico_parse_response,
     METH_VARARGS | METH_KEYWORDS,
     "Parse HTTP response.\n\n"
     "Args:\n"
     "    data: Raw HTTP response bytes\n"
     "    last_len: Previously parsed length for incremental parsing\n\n"
     "Returns:\n"
     "    dict with status, message, minor_version, headers, consumed"},

    {"parse_headers", (PyCFunction)pico_parse_headers,
     METH_VARARGS | METH_KEYWORDS,
     "Parse HTTP headers only.\n\n"
     "Args:\n"
     "    data: Raw header bytes\n"
     "    last_len: Previously parsed length\n\n"
     "Returns:\n"
     "    list of (name, value) tuples"},

    {"parse_to_wsgi_environ", (PyCFunction)pico_parse_to_wsgi_environ,
     METH_VARARGS | METH_KEYWORDS,
     "Parse HTTP request and build WSGI environ dict.\n\n"
     "Args:\n"
     "    data: Raw HTTP request bytes\n"
     "    server: (host, port) tuple for SERVER_NAME/SERVER_PORT\n"
     "    client: (addr, port) tuple for REMOTE_ADDR/REMOTE_PORT\n"
     "    url_scheme: URL scheme (default 'http')\n\n"
     "Returns:\n"
     "    WSGI environ dict"},

    {"parse_to_asgi_scope", (PyCFunction)pico_parse_to_asgi_scope,
     METH_VARARGS | METH_KEYWORDS,
     "Parse HTTP request and build ASGI scope dict.\n\n"
     "Args:\n"
     "    data: Raw HTTP request bytes\n"
     "    server: (host, port) tuple\n"
     "    client: (addr, port) tuple\n"
     "    scheme: URL scheme (default 'http')\n"
     "    root_path: ASGI root_path (default '')\n\n"
     "Returns:\n"
     "    ASGI scope dict"},

    {NULL, NULL, 0, NULL}
};

/* Module definition */
static struct PyModuleDef pico_module = {
    PyModuleDef_HEAD_INIT,
    "gunicorn_h1c._parser",
    "Fast HTTP parser using picohttpparser.\n\n"
    "This module provides high-performance HTTP parsing using the\n"
    "picohttpparser library with SIMD optimizations.",
    -1,
    pico_methods
};

/* Module initialization */
PyMODINIT_FUNC
PyInit__parser(void)
{
    PyObject *m = PyModule_Create(&pico_module);
    if (m == NULL) return NULL;

    /* Create exception types */
    PicoError = PyErr_NewException("gunicorn_h1c._parser.ParseError", PyExc_ValueError, NULL);
    Py_INCREF(PicoError);
    PyModule_AddObject(m, "ParseError", PicoError);

    IncompleteError = PyErr_NewException("gunicorn_h1c._parser.IncompleteError", PyExc_Exception, NULL);
    Py_INCREF(IncompleteError);
    PyModule_AddObject(m, "IncompleteError", IncompleteError);

    return m;
}
