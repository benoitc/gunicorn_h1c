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
 * No NUL, CR, LF characters allowed.
 */
static inline int
pico_validate_header_value(const char *value, size_t value_len,
                           PyObject *InvalidHeader)
{
    for (size_t i = 0; i < value_len; i++) {
        unsigned char c = (unsigned char)value[i];
        if (c == '\0') {
            PyErr_SetString(InvalidHeader,
                "NUL character not allowed in header value");
            return -1;
        }
        if (c == '\r') {
            PyErr_SetString(InvalidHeader,
                "CR character not allowed in header value");
            return -1;
        }
        if (c == '\n') {
            PyErr_SetString(InvalidHeader,
                "LF character not allowed in header value");
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

            /* Validate header value (look for NUL, CR, LF) */
            if (colon && colon + 1 < line_end) {
                const char *value_start = colon + 1;
                /* Skip leading whitespace */
                while (value_start < line_end &&
                       (*value_start == ' ' || *value_start == '\t')) {
                    value_start++;
                }
                for (const char *q = value_start; q < line_end; q++) {
                    if (*q == '\0') {
                        PyErr_SetString(InvalidHeader,
                            "NUL character not allowed in header value");
                        return -1;
                    }
                    if (*q == '\n') {
                        PyErr_SetString(InvalidHeader,
                            "LF character not allowed in header value");
                        return -1;
                    }
                    if (*q == '\r') {
                        PyErr_SetString(InvalidHeader,
                            "CR character not allowed in header value");
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
 * Validate all headers against limits and character rules.
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
        /* Check header line size (name + ": " + value) */
        size_t header_size = headers[i].name_len + 2 + headers[i].value_len;
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
