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
    int has_content_length;     /* 1 if Content-Length header present */
    int chunked_count;          /* Number of "chunked" values seen */
    int has_te_after_chunked;   /* 1 if T-E value after chunked */
    int has_unknown_te;         /* 1 if unknown Transfer-Encoding value */
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
 *
 * RFC 9112 section 6.2: Content-Length = 1*DIGIT. The list form
 * ("5, 5") is also a classic smuggling vector even when values match,
 * so we reject anything that is not purely digits.
 *
 * Returns parsed value or -1 on error.
 */
static inline Py_ssize_t
pico_parse_content_length(const char *value, size_t value_len)
{
    if (value_len == 0) return -1;
    Py_ssize_t cl = 0;
    for (size_t i = 0; i < value_len; i++) {
        char c = value[i];
        if (c >= '0' && c <= '9') {
            cl = cl * 10 + (c - '0');
        } else {
            return -1;
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
 * Check if a Transfer-Encoding token matches a known value.
 * Returns: 1=chunked, 2=identity, 3=gzip/deflate/compress, 0=unknown
 */
static inline int
pico_check_te_token(const char *token, size_t token_len)
{
    /* Skip leading/trailing whitespace */
    while (token_len > 0 && (*token == ' ' || *token == '\t')) {
        token++;
        token_len--;
    }
    while (token_len > 0 && (token[token_len-1] == ' ' || token[token_len-1] == '\t')) {
        token_len--;
    }

    if (token_len == 0) return 0;

    /* Check for known values (case-insensitive) */
    if (token_len == 7) {
        /* "chunked" */
        char buf[7];
        for (size_t i = 0; i < 7; i++) {
            buf[i] = token[i];
            if (buf[i] >= 'A' && buf[i] <= 'Z') buf[i] += 32;
        }
        if (buf[0] == 'c' && buf[1] == 'h' && buf[2] == 'u' && buf[3] == 'n' &&
            buf[4] == 'k' && buf[5] == 'e' && buf[6] == 'd') {
            return 1;  /* chunked */
        }
    }
    else if (token_len == 8) {
        /* "identity" */
        char buf[8];
        for (size_t i = 0; i < 8; i++) {
            buf[i] = token[i];
            if (buf[i] >= 'A' && buf[i] <= 'Z') buf[i] += 32;
        }
        if (buf[0] == 'i' && buf[1] == 'd' && buf[2] == 'e' && buf[3] == 'n' &&
            buf[4] == 't' && buf[5] == 'i' && buf[6] == 't' && buf[7] == 'y') {
            return 2;  /* identity */
        }
        /* "compress" */
        if (buf[0] == 'c' && buf[1] == 'o' && buf[2] == 'm' && buf[3] == 'p' &&
            buf[4] == 'r' && buf[5] == 'e' && buf[6] == 's' && buf[7] == 's') {
            return 3;  /* compress */
        }
    }
    else if (token_len == 4) {
        /* "gzip" */
        char buf[4];
        for (size_t i = 0; i < 4; i++) {
            buf[i] = token[i];
            if (buf[i] >= 'A' && buf[i] <= 'Z') buf[i] += 32;
        }
        if (buf[0] == 'g' && buf[1] == 'z' && buf[2] == 'i' && buf[3] == 'p') {
            return 3;  /* gzip */
        }
    }
    else if (token_len == 7) {
        /* "deflate" */
        char buf[7];
        for (size_t i = 0; i < 7; i++) {
            buf[i] = token[i];
            if (buf[i] >= 'A' && buf[i] <= 'Z') buf[i] += 32;
        }
        if (buf[0] == 'd' && buf[1] == 'e' && buf[2] == 'f' && buf[3] == 'l' &&
            buf[4] == 'a' && buf[5] == 't' && buf[6] == 'e') {
            return 3;  /* deflate */
        }
    }

    return 0;  /* unknown */
}

/*
 * Parse and validate Transfer-Encoding header value.
 * Updates info with validation flags.
 */
static inline void
pico_parse_transfer_encoding(const char *value, size_t value_len, PicoHeaderInfo *info)
{
    const char *p = value;
    const char *end = value + value_len;

    while (p < end) {
        /* Find end of token (comma or end) */
        const char *token_start = p;
        const char *token_end = p;

        while (token_end < end && *token_end != ',') {
            token_end++;
        }

        size_t token_len = token_end - token_start;
        int te_type = pico_check_te_token(token_start, token_len);

        if (te_type == 1) {
            /* chunked */
            if (info->has_chunked) {
                info->chunked_count++;  /* Stacked chunked */
            }
            info->has_chunked = 1;
        }
        else if (te_type == 2 || te_type == 3) {
            /* identity, gzip, deflate, compress */
            if (info->has_chunked) {
                info->has_te_after_chunked = 1;  /* T-E value after chunked */
            }
        }
        else if (te_type == 0 && token_len > 0) {
            /* Unknown transfer encoding */
            /* Skip empty tokens */
            const char *t = token_start;
            size_t tlen = token_len;
            while (tlen > 0 && (*t == ' ' || *t == '\t')) { t++; tlen--; }
            while (tlen > 0 && (t[tlen-1] == ' ' || t[tlen-1] == '\t')) { tlen--; }
            if (tlen > 0) {
                info->has_unknown_te = 1;
            }
        }

        /* Move past comma */
        p = token_end;
        if (p < end && *p == ',') p++;
    }
}

/*
 * Extract special headers into PicoHeaderInfo.
 * Processes Content-Length, Transfer-Encoding, Connection, and Upgrade headers.
 * Also performs validation for request smuggling prevention.
 */
static inline void
pico_extract_headers(struct phr_header *headers, size_t num_headers,
                     PicoHeaderInfo *info)
{
    info->content_length = -1;
    info->has_chunked = 0;
    info->connection_close = -1;
    info->should_upgrade = 0;
    info->has_content_length = 0;
    info->chunked_count = 0;
    info->has_te_after_chunked = 0;
    info->has_unknown_te = 0;

    for (size_t i = 0; i < num_headers; i++) {
        const char *name = headers[i].name;
        size_t name_len = headers[i].name_len;
        const char *value = headers[i].value;
        size_t value_len = headers[i].value_len;

        if (pico_header_name_eq(name, name_len, "content-length", 14)) {
            /* Track if we already saw Content-Length (duplicate detection) */
            if (info->has_content_length) {
                /* Second Content-Length - mark for rejection */
                info->content_length = -2;  /* Special value for duplicate */
            } else {
                info->content_length = pico_parse_content_length(value, value_len);
                info->has_content_length = 1;
            }
        }
        else if (pico_header_name_eq(name, name_len, "transfer-encoding", 17)) {
            /* Parse and validate Transfer-Encoding value */
            pico_parse_transfer_encoding(value, value_len, info);
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
 * Validate headers for request smuggling prevention.
 * Returns 0 on success, -1 on error (sets Python exception).
 *
 * Checks:
 * - Duplicate Content-Length headers
 * - Content-Length with Transfer-Encoding (CL+TE conflict)
 * - Chunked encoding in HTTP/1.0
 * - Stacked chunked encoding
 * - Unknown Transfer-Encoding values
 * - Transfer-Encoding values after chunked
 */
static inline int
pico_validate_header_framing(PicoHeaderInfo *info, int minor_version,
                              PyObject *InvalidHeader)
{
    /* Check for duplicate Content-Length */
    if (info->content_length == -2) {
        PyErr_SetString(InvalidHeader, "Duplicate Content-Length header");
        return -1;
    }

    /* Check for negative Content-Length (invalid) */
    if (info->has_content_length && info->content_length < 0) {
        PyErr_SetString(InvalidHeader, "Invalid Content-Length value");
        return -1;
    }

    /* Check for unknown Transfer-Encoding */
    if (info->has_unknown_te) {
        PyErr_SetString(InvalidHeader, "Unsupported Transfer-Encoding");
        return -1;
    }

    /* Check for stacked chunked encoding */
    if (info->chunked_count > 0) {
        PyErr_SetString(InvalidHeader, "Stacked chunked encoding");
        return -1;
    }

    /* Check for Transfer-Encoding value after chunked */
    if (info->has_te_after_chunked) {
        PyErr_SetString(InvalidHeader, "Invalid Transfer-Encoding after chunked");
        return -1;
    }

    /* If chunked encoding is present */
    if (info->has_chunked) {
        /* Reject chunked in HTTP/1.0 (RFC 9112 Section 6.1) */
        if (minor_version == 0) {
            PyErr_SetString(InvalidHeader, "Chunked encoding not allowed in HTTP/1.0");
            return -1;
        }

        /* Reject Content-Length with Transfer-Encoding (request smuggling vector) */
        if (info->has_content_length) {
            PyErr_SetString(InvalidHeader, "Content-Length with Transfer-Encoding");
            return -1;
        }
    }

    return 0;
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
 * Create (name, value) bytes tuple for header with lowercase name.
 * Used for ASGI compliance where header names should be lowercase.
 * Returns new reference or NULL on error.
 */
static inline PyObject *
pico_create_header_tuple_lowercase(const char *name, size_t name_len,
                                   const char *value, size_t value_len)
{
    char *lower_name = (char *)PyMem_Malloc(name_len);
    if (!lower_name) {
        PyErr_NoMemory();
        return NULL;
    }
    for (size_t i = 0; i < name_len; i++) {
        char c = name[i];
        if (c >= 'A' && c <= 'Z') {
            c += 32;
        }
        lower_name[i] = c;
    }

    PyObject *py_name = PyBytes_FromStringAndSize(lower_name, name_len);
    PyMem_Free(lower_name);

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

/*
 * Validation configuration for limit enforcement.
 */
typedef struct {
    Py_ssize_t limit_request_line;
    Py_ssize_t limit_request_fields;
    Py_ssize_t limit_request_field_size;
    int permit_unconventional_http_method;
    int permit_unconventional_http_version;
} PicoValidationConfig;

/* Default validation config values matching gunicorn */
#define PICO_DEFAULT_LIMIT_REQUEST_LINE 8190
#define PICO_DEFAULT_LIMIT_REQUEST_FIELDS 100
#define PICO_DEFAULT_LIMIT_REQUEST_FIELD_SIZE 8190

/*
 * RFC 9110 token character table.
 * token = 1*tchar
 * tchar = "!" / "#" / "$" / "%" / "&" / "'" / "*"
 *       / "+" / "-" / "." / "0"-"9" / "A"-"Z" / "^" / "_"
 *       / "`" / "a"-"z" / "|" / "~"
 */
static const unsigned char pico_token_chars[256] = {
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,  /* 0x00-0x0F */
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,  /* 0x10-0x1F */
    0, 1, 0, 1, 1, 1, 1, 1, 0, 0, 1, 1, 0, 1, 1, 0,  /* 0x20-0x2F: ! # $ % & ' * + - . */
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0,  /* 0x30-0x3F: 0-9 */
    0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,  /* 0x40-0x4F: A-O */
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 1, 1,  /* 0x50-0x5F: P-Z ^ _ */
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,  /* 0x60-0x6F: ` a-o */
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1, 0, 1, 0,  /* 0x70-0x7F: p-z | ~ */
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,  /* 0x80-0x8F */
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,  /* 0x90-0x9F */
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,  /* 0xA0-0xAF */
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,  /* 0xB0-0xBF */
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,  /* 0xC0-0xCF */
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,  /* 0xD0-0xDF */
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,  /* 0xE0-0xEF */
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0   /* 0xF0-0xFF */
};

/*
 * Check if character is valid RFC 9110 token character.
 */
static inline int
pico_is_token_char(unsigned char c)
{
    return pico_token_chars[c];
}

/*
 * Validate HTTP method.
 * Returns 0 on success, -1 on error (sets Python exception).
 *
 * Rules:
 * - All characters must be RFC 9110 token chars
 * - Unless permit_unconventional: no lowercase, no '#', length 3-20
 */
static inline int
pico_validate_method(const char *method, size_t method_len,
                     int permit_unconventional,
                     PyObject *InvalidRequestMethod)
{
    /* Check token characters */
    for (size_t i = 0; i < method_len; i++) {
        unsigned char c = (unsigned char)method[i];
        if (!pico_is_token_char(c)) {
            PyErr_Format(InvalidRequestMethod,
                "Invalid character '\\x%02x' in HTTP method", c);
            return -1;
        }
    }

    if (!permit_unconventional) {
        /* Check length (gunicorn requires 3-20) */
        if (method_len < 3 || method_len > 20) {
            PyErr_Format(InvalidRequestMethod,
                "HTTP method length %zu out of range (3-20)", method_len);
            return -1;
        }

        /* Check for lowercase and '#' */
        for (size_t i = 0; i < method_len; i++) {
            unsigned char c = (unsigned char)method[i];
            if (c >= 'a' && c <= 'z') {
                PyErr_SetString(InvalidRequestMethod,
                    "Lowercase letters not allowed in HTTP method");
                return -1;
            }
            if (c == '#') {
                PyErr_SetString(InvalidRequestMethod,
                    "'#' not allowed in HTTP method");
                return -1;
            }
        }
    }

    return 0;
}

/*
 * Case-insensitive equality check against a lowercase ASCII literal.
 */
static inline int
pico_name_ieq(const char *name, size_t name_len,
              const char *lower, size_t lower_len)
{
    if (name_len != lower_len) return 0;
    for (size_t i = 0; i < name_len; i++) {
        char c = name[i];
        if (c >= 'A' && c <= 'Z') c += 32;
        if (c != lower[i]) return 0;
    }
    return 1;
}

/*
 * RFC 9110 section 6.5.1: reject header fields that must not appear in a
 * trailer section because they alter routing, framing, or authentication.
 * Returns 1 if name is forbidden, 0 otherwise.
 */
static inline int
pico_is_forbidden_trailer_name(const char *name, size_t name_len)
{
    return pico_name_ieq(name, name_len, "host", 4)
        || pico_name_ieq(name, name_len, "content-length", 14)
        || pico_name_ieq(name, name_len, "transfer-encoding", 17)
        || pico_name_ieq(name, name_len, "trailer", 7)
        || pico_name_ieq(name, name_len, "authorization", 13)
        || pico_name_ieq(name, name_len, "te", 2);
}

/*
 * Validate request-target form against method (RFC 9112 sections 3.2.3 and 3.2.4).
 * Returns 0 on success, -1 on error (sets Python exception).
 *
 * Enforces:
 *  - asterisk-form ("*") requires OPTIONS.
 *  - authority-form (host:port with no scheme, no leading "/") requires CONNECT.
 *  - relative-reference is rejected (falls out of authority-form check).
 */
static inline int
pico_validate_request_target(const char *method, size_t method_len,
                             const char *path, size_t path_len,
                             PyObject *ParseError)
{
    if (path_len == 0) {
        PyErr_SetString(ParseError, "Empty request-target");
        return -1;
    }

    int is_connect = (method_len == 7 &&
                      method[0] == 'C' && method[1] == 'O' && method[2] == 'N' &&
                      method[3] == 'N' && method[4] == 'E' && method[5] == 'C' &&
                      method[6] == 'T');
    int is_options = (method_len == 7 &&
                      method[0] == 'O' && method[1] == 'P' && method[2] == 'T' &&
                      method[3] == 'I' && method[4] == 'O' && method[5] == 'N' &&
                      method[6] == 'S');

    /* asterisk-form */
    if (path_len == 1 && path[0] == '*') {
        if (!is_options) {
            PyErr_SetString(ParseError,
                "asterisk-form request-target requires OPTIONS method");
            return -1;
        }
        return 0;
    }

    /* origin-form */
    if (path[0] == '/') {
        return 0;
    }

    /* absolute-form: contains "://" */
    for (size_t i = 0; i + 2 < path_len; i++) {
        if (path[i] == ':' && path[i + 1] == '/' && path[i + 2] == '/') {
            return 0;
        }
    }

    /* Remaining shape is authority-form or a relative reference; both
     * are only legal in a CONNECT request. */
    if (!is_connect) {
        PyErr_SetString(ParseError,
            "authority-form or relative request-target requires CONNECT method");
        return -1;
    }

    return 0;
}

/*
 * Validate HTTP version.
 * Returns 0 on success, -1 on error (sets Python exception).
 *
 * Unless permit_unconventional, only HTTP/1.0 and HTTP/1.1 allowed.
 */
static inline int
pico_validate_version(int minor_version, int permit_unconventional,
                      PyObject *InvalidHTTPVersion)
{
    if (!permit_unconventional) {
        if (minor_version != 0 && minor_version != 1) {
            PyErr_Format(InvalidHTTPVersion,
                "Invalid HTTP version: HTTP/1.%d (only 1.0 and 1.1 allowed)",
                minor_version);
            return -1;
        }
    }
    return 0;
}

/*
 * Validate header name.
 * Returns 0 on success, -1 on error (sets Python exception).
 *
 * All characters must be RFC 9110 token chars.
 */
static inline int
pico_validate_header_name(const char *name, size_t name_len,
                          PyObject *InvalidHeaderName)
{
    for (size_t i = 0; i < name_len; i++) {
        unsigned char c = (unsigned char)name[i];
        if (!pico_is_token_char(c)) {
            PyErr_Format(InvalidHeaderName,
                "Invalid character '\\x%02x' in header name", c);
            return -1;
        }
    }
    return 0;
}

/*
 * Validate header value.
 * Returns 0 on success, -1 on error (sets Python exception).
 *
 * RFC 9110 section 5.5: field-vchar = VCHAR / obs-text; plus SP / HTAB.
 * Any other control byte (0x00-0x08, 0x0A-0x1F, 0x7F) is invalid.
 */
static inline int
pico_validate_header_value(const char *value, size_t value_len,
                           PyObject *InvalidHeader)
{
    for (size_t i = 0; i < value_len; i++) {
        unsigned char c = (unsigned char)value[i];
        if (c <= 0x08 || (c >= 0x0A && c <= 0x1F) || c == 0x7F) {
            PyErr_Format(InvalidHeader,
                "Invalid control character '\\x%02x' in header value", c);
            return -1;
        }
    }
    return 0;
}

/*
 * Calculate request line length by finding first CRLF.
 * Returns length or -1 if not found.
 */
static inline Py_ssize_t
pico_find_request_line_length(const char *buf, size_t buf_len)
{
    for (size_t i = 0; i + 1 < buf_len; i++) {
        if (buf[i] == '\r' && buf[i + 1] == '\n') {
            return (Py_ssize_t)i;
        }
    }
    return -1;
}

/*
 * Analyze buffer when phr_parse_request returns -1 and set specific exception.
 * Returns -1 (always sets an exception).
 *
 * Analysis order:
 * 1. Find method and validate characters
 * 2. Find HTTP version and validate
 * 3. Scan headers for invalid characters
 * 4. Fallback to generic ParseError
 */
static inline int
pico_analyze_parse_error(const char *buf, size_t buf_len,
                         int permit_unconventional_method,
                         int permit_unconventional_version,
                         PyObject *ParseError,
                         PyObject *InvalidRequestMethod,
                         PyObject *InvalidHTTPVersion,
                         PyObject *InvalidHeaderName,
                         PyObject *InvalidHeader)
{
    /* 1. Find and validate method (before first space) */
    size_t method_end = 0;
    for (size_t i = 0; i < buf_len; i++) {
        if (buf[i] == ' ') {
            method_end = i;
            break;
        }
    }

    if (method_end > 0) {
        /* Check method for invalid chars */
        for (size_t i = 0; i < method_end; i++) {
            unsigned char c = (unsigned char)buf[i];
            if (!pico_is_token_char(c)) {
                PyErr_Format(InvalidRequestMethod,
                    "Invalid character '\\x%02x' in HTTP method", c);
                return -1;
            }
            if (!permit_unconventional_method && c >= 'a' && c <= 'z') {
                PyErr_SetString(InvalidRequestMethod,
                    "Lowercase letters not allowed in HTTP method");
                return -1;
            }
        }
    }

    /* 2. Find and validate HTTP version (after path, look for "HTTP/") */
    const char *http_marker = NULL;
    for (size_t i = 0; i + 5 <= buf_len; i++) {
        if (buf[i] == 'H' && buf[i+1] == 'T' && buf[i+2] == 'T' &&
            buf[i+3] == 'P' && buf[i+4] == '/') {
            http_marker = &buf[i];
            break;
        }
    }

    if (http_marker && !permit_unconventional_version) {
        /* Check if it's HTTP/1.0 or HTTP/1.1 */
        size_t remaining = buf_len - (http_marker - buf);
        if (remaining >= 8) {
            /* HTTP/X.Y followed by \r or end */
            char major = http_marker[5];
            char dot = http_marker[6];
            char minor = http_marker[7];
            if (major != '1' || dot != '.' || (minor != '0' && minor != '1')) {
                PyErr_SetString(InvalidHTTPVersion,
                    "Invalid HTTP version (only HTTP/1.0 and HTTP/1.1 allowed)");
                return -1;
            }
        }
    }

    /* Check for non-HTTP protocol markers (e.g., FTP/1.1) */
    if (!http_marker && !permit_unconventional_version) {
        /* Find second space (after path) to locate version field */
        size_t second_space = 0;
        int space_count = 0;
        for (size_t i = 0; i < buf_len && i < 8192; i++) {
            if (buf[i] == ' ') {
                space_count++;
                if (space_count == 2) {
                    second_space = i;
                    break;
                }
            }
            if (buf[i] == '\r' || buf[i] == '\n') break;
        }

        if (second_space > 0 && second_space + 1 < buf_len) {
            /* Check if there's something after second space that looks like a version */
            const char *ver_start = &buf[second_space + 1];
            size_t ver_remaining = buf_len - second_space - 1;
            /* Look for X/X.X pattern but not HTTP/ */
            if (ver_remaining >= 3) {
                /* If it starts with letters and has a '/', it's a protocol version */
                int has_slash = 0;
                for (size_t i = 0; i < ver_remaining && i < 10; i++) {
                    if (ver_start[i] == '/') { has_slash = 1; break; }
                    if (ver_start[i] == '\r' || ver_start[i] == '\n') break;
                }
                if (has_slash) {
                    PyErr_SetString(InvalidHTTPVersion,
                        "Invalid HTTP version (only HTTP/1.0 and HTTP/1.1 allowed)");
                    return -1;
                }
            }
        }
    }

    /* 3. Find and scan headers (after first \r\n) */
    const char *headers_start = NULL;
    for (size_t i = 0; i + 1 < buf_len; i++) {
        if (buf[i] == '\r' && buf[i+1] == '\n') {
            headers_start = &buf[i + 2];
            break;
        }
    }

    if (headers_start) {
        size_t headers_len = buf_len - (headers_start - buf);
        const char *p = headers_start;
        const char *end = headers_start + headers_len;

        while (p < end) {
            /* Find colon (end of header name) */
            const char *colon = NULL;
            const char *line_end = NULL;

            for (const char *q = p; q < end; q++) {
                if (*q == ':' && !colon) colon = q;
                if (q + 1 < end && *q == '\r' && *(q+1) == '\n') {
                    line_end = q;
                    break;
                }
            }

            if (!line_end) break;  /* Incomplete */

            /* Empty line = end of headers */
            if (p == line_end) break;

            /* Validate header name */
            if (colon && colon > p) {
                for (const char *q = p; q < colon; q++) {
                    unsigned char c = (unsigned char)*q;
                    if (!pico_is_token_char(c)) {
                        PyErr_Format(InvalidHeaderName,
                            "Invalid character '\\x%02x' in header name", c);
                        return -1;
                    }
                }
            }

            /* Validate header value: RFC 9110 section 5.5 field-vchar. */
            if (colon && colon + 1 < line_end) {
                const char *value_start = colon + 1;
                while (value_start < line_end &&
                       (*value_start == ' ' || *value_start == '\t')) {
                    value_start++;
                }
                for (const char *q = value_start; q < line_end; q++) {
                    unsigned char c = (unsigned char)*q;
                    if (c <= 0x08 || (c >= 0x0A && c <= 0x1F) || c == 0x7F) {
                        PyErr_Format(InvalidHeader,
                            "Invalid control character '\\x%02x' in header value", c);
                        return -1;
                    }
                }
            }

            p = line_end + 2;  /* Skip \r\n */
        }
    }

    /* Fallback to generic error */
    PyErr_SetString(ParseError, "Invalid HTTP request");
    return -1;
}

/*
 * Validate all headers against limits, character rules, and framing rules.
 * Returns 0 on success, -1 on error (sets Python exception).
 */
static inline int
pico_validate_headers_full(struct phr_header *headers, size_t num_headers,
                           int minor_version,
                           const PicoValidationConfig *config,
                           PyObject *LimitRequestHeaders,
                           PyObject *InvalidHeaderName,
                           PyObject *InvalidHeader)
{
    /* Check header count */
    if ((Py_ssize_t)num_headers > config->limit_request_fields) {
        PyErr_Format(LimitRequestHeaders,
            "Number of headers (%zu) exceeds limit (%zd)",
            num_headers, config->limit_request_fields);
        return -1;
    }

    for (size_t i = 0; i < num_headers; i++) {
        /* Check header line size (name + ": " + value + CRLF) */
        size_t header_size = headers[i].name_len + 2 + headers[i].value_len + 2;
        if ((Py_ssize_t)header_size > config->limit_request_field_size) {
            PyErr_Format(LimitRequestHeaders,
                "Header size (%zu) exceeds limit (%zd)",
                header_size, config->limit_request_field_size);
            return -1;
        }

        /* Validate header name */
        if (pico_validate_header_name(headers[i].name, headers[i].name_len,
                                       InvalidHeaderName) < 0) {
            return -1;
        }

        /* Validate header value */
        if (pico_validate_header_value(headers[i].value, headers[i].value_len,
                                        InvalidHeader) < 0) {
            return -1;
        }
    }

    /* Extract header info and validate framing for request smuggling prevention */
    PicoHeaderInfo info;
    pico_extract_headers(headers, num_headers, &info);
    if (pico_validate_header_framing(&info, minor_version, InvalidHeader) < 0) {
        return -1;
    }

    return 0;
}

/*
 * Validate all headers against limits and character rules (legacy, no framing check).
 * Returns 0 on success, -1 on error (sets Python exception).
 */
static inline int
pico_validate_headers(struct phr_header *headers, size_t num_headers,
                      const PicoValidationConfig *config,
                      PyObject *LimitRequestHeaders,
                      PyObject *InvalidHeaderName,
                      PyObject *InvalidHeader)
{
    /* Check header count */
    if ((Py_ssize_t)num_headers > config->limit_request_fields) {
        PyErr_Format(LimitRequestHeaders,
            "Number of headers (%zu) exceeds limit (%zd)",
            num_headers, config->limit_request_fields);
        return -1;
    }

    for (size_t i = 0; i < num_headers; i++) {
        /* Check header line size (name + ": " + value + CRLF) */
        size_t header_size = headers[i].name_len + 2 + headers[i].value_len + 2;
        if ((Py_ssize_t)header_size > config->limit_request_field_size) {
            PyErr_Format(LimitRequestHeaders,
                "Header size (%zu) exceeds limit (%zd)",
                header_size, config->limit_request_field_size);
            return -1;
        }

        /* Validate header name */
        if (pico_validate_header_name(headers[i].name, headers[i].name_len,
                                       InvalidHeaderName) < 0) {
            return -1;
        }

        /* Validate header value */
        if (pico_validate_header_value(headers[i].value, headers[i].value_len,
                                        InvalidHeader) < 0) {
            return -1;
        }
    }

    return 0;
}

#endif /* PICO_UTILS_H */
