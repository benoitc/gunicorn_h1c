/*
 * Copyright 2026 Benoit Chesneau
 * Licensed under Apache 2.0
 *
 * Shared utilities for gunicorn_h1c parsers.
 * Contains common header parsing functions to reduce code duplication.
 */

#ifndef PICO_UTILS_H
#define PICO_UTILS_H

#include <Python.h>
#include "picohttpparser.h"

/* Shared header extraction info */
typedef struct {
    Py_ssize_t content_length;  /* -1 if not set */
    int has_chunked;            /* 1 if Transfer-Encoding: chunked */
    int connection_close;       /* -1=unset, 0=keep-alive, 1=close */
    int should_upgrade;         /* 1 if Upgrade header present */
} PicoHeaderInfo;

/*
 * Case-insensitive header name comparison.
 * Target must be lowercase.
 */
static inline int
pico_header_name_eq(const char *name, size_t name_len,
                    const char *target, size_t target_len)
{
    if (name_len != target_len) return 0;
    for (size_t i = 0; i < name_len; i++) {
        char c = name[i];
        if (c >= 'A' && c <= 'Z') c += 32;
        if (c != target[i]) return 0;
    }
    return 1;
}

/*
 * Parse Content-Length value from header value string.
 * Returns parsed value or -1 on error.
 */
static inline Py_ssize_t
pico_parse_content_length(const char *value, size_t value_len)
{
    Py_ssize_t cl = 0;
    for (size_t i = 0; i < value_len; i++) {
        char c = value[i];
        if (c >= '0' && c <= '9') {
            cl = cl * 10 + (c - '0');
        } else {
            break;  /* Stop on non-digit */
        }
    }
    return cl;
}

/*
 * Check if value contains "chunked" (case-insensitive).
 */
static inline int
pico_contains_chunked(const char *value, size_t value_len)
{
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
            return 1;
        }
    }
    return 0;
}

/*
 * Check if value contains "close" (case-insensitive).
 */
static inline int
pico_contains_close(const char *value, size_t value_len)
{
    for (size_t j = 0; j + 5 <= value_len; j++) {
        char c0 = value[j];   if (c0 >= 'A' && c0 <= 'Z') c0 += 32;
        char c1 = value[j+1]; if (c1 >= 'A' && c1 <= 'Z') c1 += 32;
        char c2 = value[j+2]; if (c2 >= 'A' && c2 <= 'Z') c2 += 32;
        char c3 = value[j+3]; if (c3 >= 'A' && c3 <= 'Z') c3 += 32;
        char c4 = value[j+4]; if (c4 >= 'A' && c4 <= 'Z') c4 += 32;
        if (c0 == 'c' && c1 == 'l' && c2 == 'o' && c3 == 's' && c4 == 'e') {
            return 1;
        }
    }
    return 0;
}

/*
 * Check if value contains "keep-alive" (case-insensitive).
 */
static inline int
pico_contains_keepalive(const char *value, size_t value_len)
{
    for (size_t j = 0; j + 10 <= value_len; j++) {
        char c0 = value[j];   if (c0 >= 'A' && c0 <= 'Z') c0 += 32;
        char c1 = value[j+1]; if (c1 >= 'A' && c1 <= 'Z') c1 += 32;
        char c2 = value[j+2]; if (c2 >= 'A' && c2 <= 'Z') c2 += 32;
        char c3 = value[j+3]; if (c3 >= 'A' && c3 <= 'Z') c3 += 32;
        char c4 = value[j+4]; /* '-' */
        char c5 = value[j+5]; if (c5 >= 'A' && c5 <= 'Z') c5 += 32;
        char c6 = value[j+6]; if (c6 >= 'A' && c6 <= 'Z') c6 += 32;
        char c7 = value[j+7]; if (c7 >= 'A' && c7 <= 'Z') c7 += 32;
        char c8 = value[j+8]; if (c8 >= 'A' && c8 <= 'Z') c8 += 32;
        char c9 = value[j+9]; if (c9 >= 'A' && c9 <= 'Z') c9 += 32;
        if (c0 == 'k' && c1 == 'e' && c2 == 'e' && c3 == 'p' &&
            c4 == '-' && c5 == 'a' && c6 == 'l' && c7 == 'i' &&
            c8 == 'v' && c9 == 'e') {
            return 1;
        }
    }
    return 0;
}

/*
 * Extract special headers into PicoHeaderInfo.
 * Processes Content-Length, Transfer-Encoding, Connection, and Upgrade headers.
 */
static inline void
pico_extract_headers(struct phr_header *headers, size_t num_headers,
                     PicoHeaderInfo *info)
{
    info->content_length = -1;
    info->has_chunked = 0;
    info->connection_close = -1;
    info->should_upgrade = 0;

    for (size_t i = 0; i < num_headers; i++) {
        const char *name = headers[i].name;
        size_t name_len = headers[i].name_len;
        const char *value = headers[i].value;
        size_t value_len = headers[i].value_len;

        if (pico_header_name_eq(name, name_len, "content-length", 14)) {
            info->content_length = pico_parse_content_length(value, value_len);
        }
        else if (pico_header_name_eq(name, name_len, "transfer-encoding", 17)) {
            if (pico_contains_chunked(value, value_len)) {
                info->has_chunked = 1;
            }
        }
        else if (pico_header_name_eq(name, name_len, "connection", 10)) {
            if (pico_contains_close(value, value_len)) {
                info->connection_close = 1;
            } else if (pico_contains_keepalive(value, value_len)) {
                info->connection_close = 0;
            }
        }
        else if (pico_header_name_eq(name, name_len, "upgrade", 7)) {
            info->should_upgrade = 1;
        }
    }
}

/*
 * Create (name, value) bytes tuple for header.
 * Returns new reference or NULL on error.
 */
static inline PyObject *
pico_create_header_tuple(const char *name, size_t name_len,
                         const char *value, size_t value_len)
{
    PyObject *py_name = PyBytes_FromStringAndSize(name, name_len);
    PyObject *py_value = PyBytes_FromStringAndSize(value, value_len);
    if (!py_name || !py_value) {
        Py_XDECREF(py_name);
        Py_XDECREF(py_value);
        return NULL;
    }

    PyObject *tuple = PyTuple_Pack(2, py_name, py_value);
    Py_DECREF(py_name);
    Py_DECREF(py_value);
    return tuple;
}

/*
 * Find header value by name (case-insensitive).
 * Returns new PyBytes reference or Py_None (new ref) if not found.
 * Returns NULL on error.
 */
static inline PyObject *
pico_find_header(struct phr_header *headers, size_t num_headers,
                 const char *name, size_t name_len)
{
    for (size_t i = 0; i < num_headers; i++) {
        if (headers[i].name_len == name_len) {
            int match = 1;
            for (size_t j = 0; j < name_len; j++) {
                char c1 = headers[i].name[j];
                char c2 = name[j];
                if (c1 >= 'A' && c1 <= 'Z') c1 += 32;
                if (c2 >= 'A' && c2 <= 'Z') c2 += 32;
                if (c1 != c2) {
                    match = 0;
                    break;
                }
            }
            if (match) {
                return PyBytes_FromStringAndSize(
                    headers[i].value, headers[i].value_len);
            }
        }
    }
    Py_RETURN_NONE;
}

#endif /* PICO_UTILS_H */
