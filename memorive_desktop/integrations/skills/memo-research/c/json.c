/* JSON for memo_bridge.exe (compatibility code, see bridge.h), read with json.loads' meaning and
   written exactly as Python's json.dumps(value, ensure_ascii=False[, indent=n]) writes it: the
   answer's request_id is a hash of such text, so a different space would change it. Numbers keep
   the text they were read with (Python writes back what Memo wrote). */
#define _CRT_SECURE_NO_WARNINGS
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "bridge.h"

Json *json_new(JType t) {
    Json *v = xmalloc(sizeof *v);
    memset(v, 0, sizeof *v);
    v->t = t;
    return v;
}

Json *json_strn(const char *utf8, size_t n) {
    Json *v = json_new(J_STR);
    v->s = xmalloc(n + 1);
    memcpy(v->s, utf8, n);
    v->s[n] = 0;
    v->n = n;
    return v;
}

Json *json_str(const char *utf8) { return json_strn(utf8, strlen(utf8)); }

Json *json_u32(const U32 *s) {
    Buf b = {0};
    u_to_utf8(&b, s->p, s->n);
    Json *v = json_strn(b.p ? b.p : "", b.n);
    free(b.p);
    return v;
}

Json *json_int(long long n) {
    char s[32];
    snprintf(s, sizeof s, "%lld", n);
    Json *v = json_new(J_NUM);
    v->s = _strdup(s);
    v->n = strlen(s);
    return v;
}

static void grow(Json *v) {
    if (v->len == v->cap) {
        v->cap = v->cap ? v->cap * 2 : 4;
        v->items = xrealloc(v->items, v->cap * sizeof *v->items);
        if (v->t == J_OBJ) v->keys = xrealloc(v->keys, v->cap * sizeof *v->keys);
    }
}

void json_push(Json *arr, Json *value) {
    grow(arr);
    arr->items[arr->len++] = value;
}

Json *json_get(const Json *obj, const char *key) {
    if (!obj || obj->t != J_OBJ) return NULL;
    for (size_t i = obj->len; i-- > 0;) if (!strcmp(obj->keys[i], key)) return obj->items[i];
    return NULL;
}

/* obj[key]: a missing key fails as Python's KeyError prints it ('key'). */
Json *json_need(const Json *obj, const char *key) {
    Json *v = json_get(obj, key);
    if (!v) {
        Buf b = {0};
        b_printf(&b, "'%s'", key);
        fail(b.p);
    }
    return v;
}

void json_set(Json *obj, const char *key, Json *value) {
    for (size_t i = 0; i < obj->len; i++) if (!strcmp(obj->keys[i], key)) { obj->items[i] = value; return; }
    grow(obj);
    obj->keys[obj->len] = _strdup(key);
    obj->items[obj->len++] = value;
}

int json_truthy(const Json *v) {
    if (!v) return 0;
    switch (v->t) {
    case J_NULL: case J_FALSE: return 0;
    case J_TRUE: return 1;
    case J_NUM: return strtod(v->s, NULL) != 0;
    case J_STR: return v->n > 0;
    default: return v->len > 0;
    }
}

double json_number(const Json *v) { return v && v->t == J_NUM ? strtod(v->s, NULL) : 0; }

U32 json_text(const Json *v) {
    U32 empty = {0};
    return v && v->t == J_STR ? u_from_utf8(v->s, v->n) : empty;
}

/* str(value) in an f-string: strings as they are, numbers as written, None/True/False. */
void json_repr(Buf *b, const Json *v) {
    if (!v || v->t == J_NULL) b_puts(b, "None");
    else if (v->t == J_TRUE) b_puts(b, "True");
    else if (v->t == J_FALSE) b_puts(b, "False");
    else if (v->t == J_STR || v->t == J_NUM) b_put(b, v->s, v->n);
    else json_dump(b, v, -1);
}

/* --- reading --------------------------------------------------------------------------------- */

typedef struct { const char *p; size_t n, i; Buf *error; int failed; } Reader;

static void bad(Reader *r, const char *what) {
    if (!r->failed) {
        b_printf(r->error, "%s: char %zu", what, r->i);
        r->failed = 1;
    }
}

static void ws(Reader *r) {
    while (r->i < r->n && (r->p[r->i] == ' ' || r->p[r->i] == '\t' || r->p[r->i] == '\n' || r->p[r->i] == '\r')) r->i++;
}

static Json *value(Reader *r);

static int hex4(Reader *r, unsigned *out) {
    if (r->i + 4 > r->n) return 0;
    unsigned v = 0;
    for (int k = 0; k < 4; k++) {
        char c = r->p[r->i + k];
        v <<= 4;
        if (c >= '0' && c <= '9') v |= (unsigned)(c - '0');
        else if (c >= 'a' && c <= 'f') v |= (unsigned)(c - 'a' + 10);
        else if (c >= 'A' && c <= 'F') v |= (unsigned)(c - 'A' + 10);
        else return 0;
    }
    r->i += 4;
    *out = v;
    return 1;
}

static Json *string(Reader *r) {
    Buf b = {0};
    r->i++;   /* the opening quote */
    while (r->i < r->n) {
        char c = r->p[r->i++];
        if (c == '"') {
            Json *v = json_strn(b.p ? b.p : "", b.n);
            free(b.p);
            return v;
        }
        if (c != '\\') { b_putc(&b, c); continue; }
        if (r->i >= r->n) break;
        char e = r->p[r->i++];
        unsigned u;
        switch (e) {
        case '"': b_putc(&b, '"'); break;
        case '\\': b_putc(&b, '\\'); break;
        case '/': b_putc(&b, '/'); break;
        case 'b': b_putc(&b, '\b'); break;
        case 'f': b_putc(&b, '\f'); break;
        case 'n': b_putc(&b, '\n'); break;
        case 'r': b_putc(&b, '\r'); break;
        case 't': b_putc(&b, '\t'); break;
        case 'u':
            if (!hex4(r, &u)) { bad(r, "Invalid \\uXXXX escape"); free(b.p); return NULL; }
            if (u >= 0xD800 && u < 0xDC00 && r->i + 6 <= r->n && r->p[r->i] == '\\' && r->p[r->i + 1] == 'u') {
                size_t save = r->i;
                unsigned low;
                r->i += 2;
                if (hex4(r, &low) && low >= 0xDC00 && low < 0xE000) u = 0x10000 + ((u - 0xD800) << 10) + (low - 0xDC00);
                else r->i = save;
            }
            {
                uint32_t cp = u;
                u_to_utf8(&b, &cp, 1);
            }
            break;
        default: bad(r, "Invalid \\escape"); free(b.p); return NULL;
        }
    }
    bad(r, "Unterminated string");
    free(b.p);
    return NULL;
}

static Json *value(Reader *r) {
    ws(r);
    if (r->i >= r->n) { bad(r, "Expecting value"); return NULL; }
    char c = r->p[r->i];
    if (c == '"') return string(r);
    if (c == '{') {
        Json *o = json_new(J_OBJ);
        r->i++;
        ws(r);
        if (r->i < r->n && r->p[r->i] == '}') { r->i++; return o; }
        for (;;) {
            ws(r);
            if (r->i >= r->n || r->p[r->i] != '"') { bad(r, "Expecting property name enclosed in double quotes"); return NULL; }
            Json *k = string(r);
            if (!k) return NULL;
            ws(r);
            if (r->i >= r->n || r->p[r->i] != ':') { bad(r, "Expecting ':' delimiter"); return NULL; }
            r->i++;
            Json *v = value(r);
            if (!v) return NULL;
            json_set(o, k->s, v);
            ws(r);
            if (r->i < r->n && r->p[r->i] == ',') { r->i++; continue; }
            if (r->i < r->n && r->p[r->i] == '}') { r->i++; return o; }
            bad(r, "Expecting ',' delimiter");
            return NULL;
        }
    }
    if (c == '[') {
        Json *a = json_new(J_ARR);
        r->i++;
        ws(r);
        if (r->i < r->n && r->p[r->i] == ']') { r->i++; return a; }
        for (;;) {
            Json *v = value(r);
            if (!v) return NULL;
            json_push(a, v);
            ws(r);
            if (r->i < r->n && r->p[r->i] == ',') { r->i++; continue; }
            if (r->i < r->n && r->p[r->i] == ']') { r->i++; return a; }
            bad(r, "Expecting ',' delimiter");
            return NULL;
        }
    }
    if (r->n - r->i >= 4 && !strncmp(r->p + r->i, "null", 4)) { r->i += 4; return json_new(J_NULL); }
    if (r->n - r->i >= 4 && !strncmp(r->p + r->i, "true", 4)) { r->i += 4; return json_new(J_TRUE); }
    if (r->n - r->i >= 5 && !strncmp(r->p + r->i, "false", 5)) { r->i += 5; return json_new(J_FALSE); }
    size_t start = r->i;
    if (r->p[r->i] == '-') r->i++;
    while (r->i < r->n && strchr("0123456789.eE+-", r->p[r->i])) r->i++;
    if (r->i == start || (r->i == start + 1 && r->p[start] == '-')) { bad(r, "Expecting value"); return NULL; }
    Json *v = json_new(J_NUM);
    v->n = r->i - start;
    v->s = xmalloc(v->n + 1);
    memcpy(v->s, r->p + start, v->n);
    v->s[v->n] = 0;
    return v;
}

Json *json_parse(const char *text, size_t n, Buf *error) {
    Reader r = {text, n, 0, error, 0};
    Json *v = value(&r);
    if (v) {
        ws(&r);
        if (r.i != r.n) { bad(&r, "Extra data"); return NULL; }
    }
    return r.failed ? NULL : v;
}

/* --- writing --------------------------------------------------------------------------------- */

static void dump_string(Buf *b, const char *s, size_t n) {
    b_putc(b, '"');
    for (size_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)s[i];
        switch (c) {
        case '"': b_puts(b, "\\\""); break;
        case '\\': b_puts(b, "\\\\"); break;
        case '\b': b_puts(b, "\\b"); break;
        case '\f': b_puts(b, "\\f"); break;
        case '\n': b_puts(b, "\\n"); break;
        case '\r': b_puts(b, "\\r"); break;
        case '\t': b_puts(b, "\\t"); break;
        default:
            if (c < 0x20) b_printf(b, "\\u%04x", c);
            else b_putc(b, (char)c);
        }
    }
    b_putc(b, '"');
}

static void newline(Buf *b, int indent, int level) {
    b_putc(b, '\n');
    for (int k = 0; k < indent * level; k++) b_putc(b, ' ');
}

static void dump(Buf *b, const Json *v, int indent, int level) {
    switch (v->t) {
    case J_NULL: b_puts(b, "null"); return;
    case J_TRUE: b_puts(b, "true"); return;
    case J_FALSE: b_puts(b, "false"); return;
    case J_NUM: b_put(b, v->s, v->n); return;
    case J_STR: dump_string(b, v->s, v->n); return;
    case J_ARR: case J_OBJ: break;
    }
    char open = v->t == J_ARR ? '[' : '{', close = v->t == J_ARR ? ']' : '}';
    b_putc(b, open);
    if (!v->len) { b_putc(b, close); return; }
    for (size_t i = 0; i < v->len; i++) {
        if (indent >= 0) {
            if (i) b_putc(b, ',');
            newline(b, indent, level + 1);
        } else if (i) b_puts(b, ", ");
        if (v->t == J_OBJ) {
            dump_string(b, v->keys[i], strlen(v->keys[i]));
            b_puts(b, ": ");
        }
        dump(b, v->items[i], indent, level + 1);
    }
    if (indent >= 0) newline(b, indent, level);
    b_putc(b, close);
}

void json_dump(Buf *b, const Json *v, int indent) { dump(b, v, indent, 0); }
