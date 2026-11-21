/* Text for memo_bridge.exe (compatibility code, see bridge.h): code-point strings, the Unicode
   classes Python's str and re use, and drafts.py's text functions (_even, locate, closest,
   numbers, stated_values). Each function names the Python it follows. */
#define _CRT_SECURE_NO_WARNINGS
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "bridge.h"

void *xmalloc(size_t n) {
    void *p = malloc(n ? n : 1);
    if (!p) { fputs("out of memory\n", stderr); exit(2); }
    return p;
}

void *xrealloc(void *p, size_t n) {
    p = realloc(p, n ? n : 1);
    if (!p) { fputs("out of memory\n", stderr); exit(2); }
    return p;
}

/* --- Buf ------------------------------------------------------------------------------------- */

void b_put(Buf *b, const char *p, size_t n) {
    if (b->n + n + 1 > b->cap) {
        b->cap = (b->n + n + 1) * 2;
        b->p = xrealloc(b->p, b->cap);
    }
    memcpy(b->p + b->n, p, n);
    b->n += n;
    b->p[b->n] = 0;
}

void b_puts(Buf *b, const char *s) { b_put(b, s, strlen(s)); }
void b_putc(Buf *b, char c) { b_put(b, &c, 1); }

void b_printf(Buf *b, const char *fmt, ...) {
    char small[512];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(small, sizeof small, fmt, ap);
    va_end(ap);
    if (n < (int)sizeof small) { b_put(b, small, (size_t)n); return; }
    char *big = xmalloc((size_t)n + 1);
    va_start(ap, fmt);
    vsnprintf(big, (size_t)n + 1, fmt, ap);
    va_end(ap);
    b_put(b, big, (size_t)n);
    free(big);
}

char *b_cstr(Buf *b) {
    char *s = xmalloc(b->n + 1);
    if (b->n) memcpy(s, b->p, b->n);
    s[b->n] = 0;
    return s;
}

/* --- U32 ------------------------------------------------------------------------------------- */

void u_push(U32 *s, uint32_t c) {
    if (s->n + 1 > s->cap) {
        s->cap = s->cap ? s->cap * 2 : 16;
        s->p = xrealloc(s->p, s->cap * sizeof *s->p);
    }
    s->p[s->n++] = c;
}

void u_append(U32 *s, const uint32_t *p, size_t n) {
    for (size_t i = 0; i < n; i++) u_push(s, p[i]);
}

/* UTF-8 to code points; a malformed byte becomes U+FFFD (Python: errors='replace'). */
U32 u_from_utf8(const char *p, size_t n) {
    U32 s = {0};
    const unsigned char *q = (const unsigned char *)p;
    size_t i = 0;
    while (i < n) {
        unsigned c = q[i];
        uint32_t cp; int more;
        if (c < 0x80) { cp = c; more = 0; }
        else if ((c & 0xE0) == 0xC0) { cp = c & 0x1F; more = 1; }
        else if ((c & 0xF0) == 0xE0) { cp = c & 0x0F; more = 2; }
        else if ((c & 0xF8) == 0xF0) { cp = c & 0x07; more = 3; }
        else { u_push(&s, 0xFFFD); i++; continue; }
        int ok = 1;
        for (int k = 1; k <= more; k++) {
            if (i + k >= n || (q[i + k] & 0xC0) != 0x80) { ok = 0; break; }
            cp = (cp << 6) | (q[i + k] & 0x3F);
        }
        if (!ok) { u_push(&s, 0xFFFD); i++; continue; }
        u_push(&s, cp);
        i += (size_t)more + 1;
    }
    return s;
}

U32 u_from_cstr(const char *utf8) { return u_from_utf8(utf8, strlen(utf8)); }

void u_append_utf8(U32 *s, const char *utf8) {
    U32 t = u_from_cstr(utf8);
    u_append(s, t.p, t.n);
    free(t.p);
}

void u_to_utf8(Buf *b, const uint32_t *p, size_t n) {
    for (size_t i = 0; i < n; i++) {
        uint32_t c = p[i];
        char o[4];
        if (c < 0x80) { o[0] = (char)c; b_put(b, o, 1); }
        else if (c < 0x800) { o[0] = (char)(0xC0 | (c >> 6)); o[1] = (char)(0x80 | (c & 0x3F)); b_put(b, o, 2); }
        else if (c < 0x10000) {
            o[0] = (char)(0xE0 | (c >> 12)); o[1] = (char)(0x80 | ((c >> 6) & 0x3F));
            o[2] = (char)(0x80 | (c & 0x3F)); b_put(b, o, 3);
        } else {
            o[0] = (char)(0xF0 | (c >> 18)); o[1] = (char)(0x80 | ((c >> 12) & 0x3F));
            o[2] = (char)(0x80 | ((c >> 6) & 0x3F)); o[3] = (char)(0x80 | (c & 0x3F)); b_put(b, o, 4);
        }
    }
}

char *u_cstr(const uint32_t *p, size_t n) {
    Buf b = {0};
    u_to_utf8(&b, p, n);
    return b_cstr(&b);
}

U32 u_slice(const U32 *s, size_t a, size_t b) {
    U32 t = {0};
    if (b > s->n) b = s->n;
    if (a < b) u_append(&t, s->p + a, b - a);
    return t;
}

/* str.find */
long u_find(const U32 *hay, const U32 *needle, size_t from) {
    if (needle->n == 0) return from <= hay->n ? (long)from : -1;
    if (hay->n < needle->n) return -1;
    for (size_t i = from; i + needle->n <= hay->n; i++) {
        if (hay->p[i] == needle->p[0] && !memcmp(hay->p + i, needle->p, needle->n * sizeof *needle->p))
            return (long)i;
    }
    return -1;
}

int u_starts(const U32 *s, size_t at, const char *ascii) {
    size_t k = strlen(ascii);
    if (at + k > s->n) return 0;
    for (size_t i = 0; i < k; i++) if (s->p[at + i] != (unsigned char)ascii[i]) return 0;
    return 1;
}

/* Python str comparison: code point by code point. */
int u_cmp(const U32 *a, const U32 *b) {
    size_t n = a->n < b->n ? a->n : b->n;
    for (size_t i = 0; i < n; i++) if (a->p[i] != b->p[i]) return a->p[i] < b->p[i] ? -1 : 1;
    return a->n < b->n ? -1 : a->n > b->n ? 1 : 0;
}

/* --- Unicode classes ------------------------------------------------------------------------- */

/* str.isspace and re's \s (Py_UNICODE_ISSPACE). */
int u_space(uint32_t c) {
    return (c >= 0x09 && c <= 0x0D) || (c >= 0x1C && c <= 0x20) || c == 0x85 || c == 0xA0 || c == 0x1680
        || (c >= 0x2000 && c <= 0x200A) || c == 0x2028 || c == 0x2029 || c == 0x202F || c == 0x205F || c == 0x3000;
}

/* re's \d: the decimal digits (Unicode Nd); each block of ten starts at one of these. */
static const uint32_t ZEROS[] = {
    0x30, 0x660, 0x6F0, 0x7C0, 0x966, 0x9E6, 0xA66, 0xAE6, 0xB66, 0xBE6, 0xC66, 0xCE6, 0xD66, 0xDE6, 0xE50,
    0xED0, 0xF20, 0x1040, 0x1090, 0x17E0, 0x1810, 0x1946, 0x19D0, 0x1A80, 0x1A90, 0x1B50, 0x1BB0, 0x1C40,
    0x1C50, 0xA620, 0xA8D0, 0xA900, 0xA9D0, 0xA9F0, 0xAA50, 0xABF0, 0xFF10, 0x104A0, 0x10D30, 0x11066,
    0x110F0, 0x11136, 0x111D0, 0x112F0, 0x11450, 0x114D0, 0x11650, 0x116C0, 0x11730, 0x118E0, 0x11950,
    0x11C50, 0x11D50, 0x11DA0, 0x11F50, 0x16A60, 0x16AC0, 0x16B50, 0x1D7CE, 0x1D7D8, 0x1D7E2, 0x1D7EC,
    0x1D7F6, 0x1E140, 0x1E2F0, 0x1E4F0, 0x1E950, 0x1FBF0,
};

int u_digit(uint32_t c) {
    for (size_t i = 0; i < sizeof ZEROS / sizeof *ZEROS; i++)
        if (c >= ZEROS[i] && c < ZEROS[i] + 10) return (int)(c - ZEROS[i]);
    return -1;
}

static int utf16(uint32_t c, wchar_t w[2]) {
    if (c < 0x10000) { w[0] = (wchar_t)c; return 1; }
    c -= 0x10000;
    w[0] = (wchar_t)(0xD800 + (c >> 10));
    w[1] = (wchar_t)(0xDC00 + (c & 0x3FF));
    return 2;
}

static WORD ctype1(uint32_t c) {
    wchar_t w[2];
    WORD types[2] = {0, 0};
    int n = utf16(c, w);
    GetStringTypeW(CT_CTYPE1, w, n, types);
    return types[0];
}

/* str.isalpha (letters); Windows' letter class beyond ASCII. */
int u_alpha(uint32_t c) {
    if (c < 0x80) return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z');
    return (ctype1(c) & C1_ALPHA) != 0;
}

/* re's \w: letters, digits and the underscore. */
int u_word(uint32_t c) {
    if (c < 0x80) return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_';
    return u_alpha(c) || u_digit(c) >= 0 || (ctype1(c) & C1_DIGIT) != 0;
}

static wchar_t *to_utf16(const U32 *s, int *len) {
    wchar_t *w = xmalloc((s->n * 2 + 1) * sizeof *w);
    int k = 0;
    for (size_t i = 0; i < s->n; i++) k += utf16(s->p[i], w + k);
    w[k] = 0;
    *len = k;
    return w;
}

static U32 from_utf16(const wchar_t *w, int n) {
    U32 s = {0};
    for (int i = 0; i < n; i++) {
        uint32_t c = (uint16_t)w[i];
        if (c >= 0xD800 && c < 0xDC00 && i + 1 < n && (uint16_t)w[i + 1] >= 0xDC00 && (uint16_t)w[i + 1] < 0xE000) {
            c = 0x10000 + ((c - 0xD800) << 10) + ((uint16_t)w[i + 1] - 0xDC00);
            i++;
        }
        u_push(&s, c);
    }
    return s;
}

/* str.lower */
U32 u_lower(const U32 *s) {
    U32 out = {0};
    int ascii = 1;
    for (size_t i = 0; i < s->n; i++) if (s->p[i] >= 0x80) { ascii = 0; break; }
    if (ascii) {
        for (size_t i = 0; i < s->n; i++) u_push(&out, s->p[i] >= 'A' && s->p[i] <= 'Z' ? s->p[i] + 32 : s->p[i]);
        return out;
    }
    int n;
    wchar_t *w = to_utf16(s, &n);
    if (n) {
        wchar_t *low = xmalloc(((size_t)n + 1) * sizeof *low);
        int m = LCMapStringEx(LOCALE_NAME_INVARIANT, LCMAP_LOWERCASE, w, n, low, n + 1, NULL, NULL, 0);
        out = m > 0 ? from_utf16(low, m) : from_utf16(w, n);
        free(low);
    }
    free(w);
    return out;
}

static int in_chars(uint32_t c, const U32 *set) {
    for (size_t i = 0; i < set->n; i++) if (set->p[i] == c) return 1;
    return 0;
}

U32 u_strip(const U32 *s) {
    size_t a = 0, b = s->n;
    while (a < b && u_space(s->p[a])) a++;
    while (b > a && u_space(s->p[b - 1])) b--;
    return u_slice(s, a, b);
}

/* str.strip(chars) / lstrip(chars) / rstrip(chars) */
U32 u_strip_chars(const U32 *s, const char *chars_utf8, int left, int right) {
    U32 set = u_from_cstr(chars_utf8);
    size_t a = 0, b = s->n;
    if (left) while (a < b && in_chars(s->p[a], &set)) a++;
    if (right) while (b > a && in_chars(s->p[b - 1], &set)) b--;
    free(set.p);
    return u_slice(s, a, b);
}

static int line_break(uint32_t c) {
    return c == '\n' || c == '\r' || c == 0x0B || c == 0x0C || c == 0x1C || c == 0x1D || c == 0x1E
        || c == 0x85 || c == 0x2028 || c == 0x2029;
}

/* str.splitlines() */
void u_splitlines(const U32 *s, U32 **lines, size_t *count) {
    size_t cap = 8, n = 0, start = 0, i = 0;
    U32 *out = xmalloc(cap * sizeof *out);
    while (i < s->n) {
        if (line_break(s->p[i])) {
            if (n == cap) out = xrealloc(out, (cap *= 2) * sizeof *out);
            out[n++] = u_slice(s, start, i);
            if (s->p[i] == '\r' && i + 1 < s->n && s->p[i + 1] == '\n') i++;
            start = ++i;
        } else i++;
    }
    if (start < s->n) {
        if (n == cap) out = xrealloc(out, (cap *= 2) * sizeof *out);
        out[n++] = u_slice(s, start, s->n);
    }
    *lines = out;
    *count = n;
}

/* --- drafts.py: _even, locate, closest -------------------------------------------------------- */

/* unicodedata.normalize('NFKC', char) for one character, then SAME (quotes and dashes). */
static void nfkc_same(uint32_t c, U32 *out) {
    U32 parts = {0};
    if (c < 0x80) u_push(&parts, c);
    else {
        wchar_t w[2], o[32];
        int n = utf16(c, w);
        int m = NormalizeString(NormalizationKC, w, n, o, 32);
        if (m > 0) parts = from_utf16(o, m); else u_push(&parts, c);
    }
    for (size_t i = 0; i < parts.n; i++) {
        uint32_t p = parts.p[i];
        if (p == 0x2018 || p == 0x2019) p = '\'';
        else if (p == 0x201C || p == 0x201D) p = '"';
        else if (p == 0x2013 || p == 0x2014 || p == 0x2212 || p == 0x2010 || p == 0x2011) p = '-';
        u_push(out, p);
    }
    free(parts.p);
}

/* drafts._even: text with ligatures, quotes, dashes, line-end hyphens and line breaks evened out,
   and where[k]: the index in text of out[k]. hyphens: 0 None, 1 'drop', 2 'tight'. */
U32 even(const U32 *text, int hyphens, size_t **where_out) {
    U32 out = {0};
    size_t wcap = text->n * 2 + 16, wn = 0;
    size_t *where = xmalloc(wcap * sizeof *where);
#define KEEP(ch, at) do { u_push(&out, (ch)); if (wn == wcap) where = xrealloc(where, (wcap *= 2) * sizeof *where); where[wn++] = (at); } while (0)
    size_t i = 0;
    while (i < text->n) {
        uint32_t ch = text->p[i];
        if (hyphens && ch == '-' && i > 0 && u_alpha(text->p[i - 1])) {
            size_t gap = i + 1;
            while (gap < text->n && u_space(text->p[gap])) gap++;
            int newline = 0;
            for (size_t k = i + 1; k < gap; k++) if (text->p[k] == '\n') newline = 1;
            if (newline && gap < text->n && u_alpha(text->p[gap])) {
                if (hyphens == 2) KEEP('-', i);
                i = gap;
                continue;
            }
        }
        if (u_space(ch)) {
            if (out.n && out.p[out.n - 1] != ' ') KEEP(' ', i);
        } else {
            U32 parts = {0};
            nfkc_same(ch, &parts);
            for (size_t k = 0; k < parts.n; k++) KEEP(parts.p[k], i);
            free(parts.p);
        }
        i++;
    }
#undef KEEP
    if (where_out) *where_out = where; else free(where);
    return out;
}

/* drafts.locate: the passage's own text for quote (1), or 0 when the quote is not in it. A line
   break typed as \n inside a quote counts as a line break. */
int locate(const U32 *quote, const U32 *passage, U32 *found) {
    U32 q = {0};
    for (size_t i = 0; i < quote->n; i++) {
        if (quote->p[i] == '\\' && i + 1 < quote->n && (quote->p[i + 1] == 'n' || quote->p[i + 1] == 't')) {
            u_push(&q, quote->p[i + 1] == 'n' ? '\n' : '\t');
            i++;
        } else u_push(&q, quote->p[i]);
    }
    U32 evened = even(&q, 0, NULL), wanted = u_strip(&evened);
    free(q.p); free(evened.p);
    if (!wanted.n) { free(wanted.p); return 0; }
    for (int h = 0; h < 3; h++) {
        size_t *where;
        U32 text = even(passage, h, &where);
        long start = u_find(&text, &wanted, 0);
        if (start >= 0) {
            *found = u_slice(passage, where[start], where[start + wanted.n - 1] + 1);
            free(text.p); free(where); free(wanted.p);
            return 1;
        }
        free(text.p); free(where);
    }
    free(wanted.p);
    return 0;
}

/* difflib.SequenceMatcher(None, a, b).ratio(), with its default autojunk. */
typedef struct { uint32_t key; size_t *idx; size_t n, cap; int used; } Slot;

static double ratio(const U32 *a, const U32 *b) {
    size_t la = a->n, lb = b->n;
    if (la + lb == 0) return 1.0;
    size_t cap = 16;
    while (cap < lb * 2 + 2) cap *= 2;
    Slot *map = calloc(cap, sizeof *map);
    if (!map) fail("out of memory");
    for (size_t j = 0; j < lb; j++) {   /* b2j: the positions of each character in b */
        uint32_t c = b->p[j];
        size_t h = (c * 2654435761u) & (cap - 1);
        while (map[h].used && map[h].key != c) h = (h + 1) & (cap - 1);
        Slot *s = &map[h];
        if (!s->used) { s->used = 1; s->key = c; }
        if (s->n == s->cap) s->idx = xrealloc(s->idx, (s->cap = s->cap ? s->cap * 2 : 4) * sizeof *s->idx);
        s->idx[s->n++] = j;
    }
    if (lb >= 200) {   /* popular elements are left out of b2j */
        size_t ntest = lb / 100 + 1;
        for (size_t h = 0; h < cap; h++) if (map[h].used && map[h].n > ntest) map[h].n = 0;
    }
    size_t *len_prev = calloc(lb + 1, sizeof *len_prev), *len_cur = calloc(lb + 1, sizeof *len_cur);
    size_t *touched_prev = xmalloc((lb + 1) * sizeof(size_t)), *touched_cur = xmalloc((lb + 1) * sizeof(size_t));
    if (!len_prev || !len_cur) fail("out of memory");
    size_t matched = 0, sp = 0, scap = 16;
    size_t (*stack)[4] = xmalloc(scap * sizeof *stack);
    stack[sp][0] = 0; stack[sp][1] = la; stack[sp][2] = 0; stack[sp][3] = lb; sp++;
    while (sp) {
        sp--;
        size_t alo = stack[sp][0], ahi = stack[sp][1], blo = stack[sp][2], bhi = stack[sp][3];
        /* find_longest_match */
        size_t besti = alo, bestj = blo, bestsize = 0, nprev = 0;
        for (size_t i = alo; i < ahi; i++) {
            uint32_t c = a->p[i];
            size_t h = (c * 2654435761u) & (cap - 1);
            while (map[h].used && map[h].key != c) h = (h + 1) & (cap - 1);
            size_t ncur = 0;
            if (map[h].used) {
                Slot *s = &map[h];
                for (size_t k = 0; k < s->n; k++) {
                    size_t j = s->idx[k];
                    if (j < blo) continue;
                    if (j >= bhi) break;
                    /* j2len[j] = j2len_previous_row[j-1] + 1; the arrays are indexed by j+1 */
                    size_t v = len_prev[j] + 1;
                    len_cur[j + 1] = v;
                    touched_cur[ncur++] = j + 1;
                    if (v > bestsize) { besti = i - v + 1; bestj = j - v + 1; bestsize = v; }
                }
            }
            for (size_t k = 0; k < nprev; k++) len_prev[touched_prev[k]] = 0;
            size_t *swap = len_prev; len_prev = len_cur; len_cur = swap;
            size_t *tswap = touched_prev; touched_prev = touched_cur; touched_cur = tswap;
            nprev = ncur;
        }
        for (size_t k = 0; k < nprev; k++) len_prev[touched_prev[k]] = 0;
        while (besti > alo && bestj > blo && a->p[besti - 1] == b->p[bestj - 1]) { besti--; bestj--; bestsize++; }
        while (besti + bestsize < ahi && bestj + bestsize < bhi && a->p[besti + bestsize] == b->p[bestj + bestsize]) bestsize++;
        if (bestsize) {
            matched += bestsize;
            if (sp + 2 > scap) stack = xrealloc(stack, (scap *= 2) * sizeof *stack);
            if (alo < besti && blo < bestj) { stack[sp][0] = alo; stack[sp][1] = besti; stack[sp][2] = blo; stack[sp][3] = bestj; sp++; }
            if (besti + bestsize < ahi && bestj + bestsize < bhi) {
                stack[sp][0] = besti + bestsize; stack[sp][1] = ahi; stack[sp][2] = bestj + bestsize; stack[sp][3] = bhi; sp++;
            }
        }
    }
    for (size_t h = 0; h < cap; h++) free(map[h].idx);
    free(map); free(len_prev); free(len_cur); free(touched_prev); free(touched_cur); free(stack);
    return 2.0 * (double)matched / (double)(la + lb);
}

/* drafts.closest: the line or sentence of the passage most like quote (at most 200 characters). */
U32 closest(const U32 *quote, const U32 *passage) {
    U32 *lines; size_t nlines;
    u_splitlines(passage, &lines, &nlines);
    U32 wanted = even(quote, 0, NULL), best = {0};
    double best_ratio = -1;
    for (size_t l = 0; l < nlines; l++) {
        /* re.split(r'(?<=[.;。；])\s+', line): split at whitespace after a sentence end */
        const U32 *line = &lines[l];
        size_t start = 0;
        for (;;) {
            size_t k = start;
            while (k < line->n && !(k > 0 && u_space(line->p[k]) && (line->p[k - 1] == '.' || line->p[k - 1] == ';'
                   || line->p[k - 1] == 0x3002 || line->p[k - 1] == 0xFF1B))) k++;
            U32 piece = u_slice(line, start, k), p = u_strip(&piece);
            free(piece.p);
            if (p.n) {
                U32 e = even(&p, 0, NULL);
                double r = ratio(&wanted, &e);
                free(e.p);
                if (r > best_ratio || (r == best_ratio && u_cmp(&p, &best) > 0)) {   /* max((ratio, piece)) */
                    free(best.p); best = p; best_ratio = r;
                } else free(p.p);
            } else free(p.p);
            if (k >= line->n) break;
            while (k < line->n && u_space(line->p[k])) k++;
            start = k;
        }
        free(lines[l].p);
    }
    free(lines);
    free(wanted.p);
    if (best.n > 200) best.n = 200;
    return best;
}

/* --- drafts.py: numbers and stated values ----------------------------------------------------- */

void values_add(Values *s, double v) {
    if (values_has(s, v)) return;
    if (s->n == s->cap) s->v = xrealloc(s->v, (s->cap = s->cap ? s->cap * 2 : 8) * sizeof *s->v);
    s->v[s->n++] = v;
}

int values_has(const Values *s, double v) {
    for (size_t i = 0; i < s->n; i++) if (s->v[i] == v) return 1;
    return 0;
}

static int cmp_double(const void *a, const void *b) {
    double x = *(const double *)a, y = *(const double *)b;
    return x < y ? -1 : x > y;
}

void values_sort(Values *s) { qsort(s->v, s->n, sizeof *s->v, cmp_double); }

/* Python format(v, 'g') */
void fmt_g(Buf *b, double v) {
    char s[64];
    snprintf(s, sizeof s, "%g", v);
    b_puts(b, s);
}

/* NUMBER = \d+(?:,\d{3})*(?:\.\d+)? at i: the end of the match, or i when there is none; the number's
   characters (ASCII digits and '.', commas left out) go to ascii. */
static size_t number_at(const U32 *t, size_t i, Buf *ascii) {
    size_t j = i;
    while (j < t->n && u_digit(t->p[j]) >= 0) { b_putc(ascii, (char)('0' + u_digit(t->p[j]))); j++; }
    if (j == i) return i;
    while (j + 3 < t->n && t->p[j] == ',' && u_digit(t->p[j + 1]) >= 0 && u_digit(t->p[j + 2]) >= 0
           && u_digit(t->p[j + 3]) >= 0) {
        for (int k = 1; k <= 3; k++) b_putc(ascii, (char)('0' + u_digit(t->p[j + k])));
        j += 4;
    }
    if (j + 1 < t->n && t->p[j] == '.' && u_digit(t->p[j + 1]) >= 0) {
        b_putc(ascii, '.');
        j++;
        while (j < t->n && u_digit(t->p[j]) >= 0) { b_putc(ascii, (char)('0' + u_digit(t->p[j]))); j++; }
    }
    return j;
}

/* drafts.numbers: reported values; whole numbers up to ten are counts and ordinals, not values. */
Values numbers(const U32 *t) {
    Values found = {0};
    size_t i = 0;
    while (i < t->n) {
        Buf ascii = {0};
        size_t j = number_at(t, i, &ascii);
        if (j == i) { free(ascii.p); i++; continue; }
        double value = strtod(ascii.p, NULL);
        int dot = memchr(ascii.p, '.', ascii.n) != NULL;
        uint32_t next = j < t->n ? t->p[j] : 0;
        if (dot || value > 10 || next == '%' || next == 0xFF05) values_add(&found, value);
        free(ascii.p);
        i = j;
    }
    return found;
}

/* \s*\d+(?:\s*[-–]\s*\d+)? at i: its end, or 0 (no match). */
static size_t digits_range(const U32 *t, size_t i) {
    while (i < t->n && u_space(t->p[i])) i++;
    size_t j = i;
    while (j < t->n && u_digit(t->p[j]) >= 0) j++;
    if (j == i) return 0;
    size_t k = j;
    while (k < t->n && u_space(t->p[k])) k++;
    if (k < t->n && (t->p[k] == '-' || t->p[k] == 0x2013)) {
        k++;
        while (k < t->n && u_space(t->p[k])) k++;
        size_t m = k;
        while (m < t->n && u_digit(t->p[m]) >= 0) m++;
        if (m > k) return m;
    }
    return j;
}

static int boundary(const U32 *t, size_t i) {
    int before = i > 0 && u_word(t->p[i - 1]), after = i < t->n && u_word(t->p[i]);
    return before != after;
}

/* PAGE_REFERENCE = (?:\bpp?\.|\bpages?|第|页)\s*\d+(?:\s*[-–]\s*\d+)?|\d+(?:\s*[-–]\s*\d+)?\s*页
   at i: the end of the match, or 0. */
static size_t page_reference(const U32 *t, size_t i) {
    static const char *heads[] = {"pp.", "p.", "pages", "page"};
    for (int h = 0; h < 4; h++) {
        if (u_starts(t, i, heads[h]) && boundary(t, i)) {
            size_t end = digits_range(t, i + strlen(heads[h]));
            if (end) return end;
        }
    }
    if (i < t->n && (t->p[i] == 0x7B2C || t->p[i] == 0x9875)) {   /* 第, 页 */
        size_t end = digits_range(t, i + 1);
        if (end) return end;
    }
    /* \d+(?:\s*[-–]\s*\d+)?\s*页: with the range first, then without it */
    size_t j = i;
    while (j < t->n && u_digit(t->p[j]) >= 0) j++;
    if (j == i) return 0;
    size_t ranged = digits_range(t, i);
    size_t tries[2] = {ranged, j};
    for (int k = 0; k < 2; k++) {
        size_t m = tries[k];
        if (!m) continue;
        while (m < t->n && u_space(t->p[m])) m++;
        if (m < t->n && t->p[m] == 0x9875) return m + 1;
    }
    return 0;
}

/* \d+(?:\.\d+)* at i: its end, or 0. */
static size_t dotted_number(const U32 *t, size_t i) {
    size_t j = i;
    while (j < t->n && u_digit(t->p[j]) >= 0) j++;
    if (j == i) return 0;
    while (j + 1 < t->n && t->p[j] == '.' && u_digit(t->p[j + 1]) >= 0) {
        j += 2;
        while (j < t->n && u_digit(t->p[j]) >= 0) j++;
    }
    return j;
}

static size_t skip_spaces(const U32 *t, size_t i) {
    while (i < t->n && u_space(t->p[i])) i++;
    return i;
}

/* An ASCII word at i, in any case (re.I). */
static int word_at(const U32 *t, size_t i, const char *word) {
    size_t k = strlen(word);
    if (i + k > t->n) return 0;
    for (size_t j = 0; j < k; j++) {
        uint32_t c = t->p[i + j];
        if (c >= 'A' && c <= 'Z') c += 32;
        if (c != (unsigned char)word[j]) return 0;
    }
    return 1;
}

/* The characters after which 表 and 图 count as a place: [\s(（,，.。;；:：见如在由从据及与和] */
static int points_to_place(uint32_t c) {
    static const uint32_t SET[] = {'(', 0xFF08, ',', 0xFF0C, '.', 0x3002, ';', 0xFF1B, ':', 0xFF1A,
                                   0x89C1, 0x5982, 0x5728, 0x7531, 0x4ECE, 0x636E, 0x53CA, 0x4E0E, 0x548C};
    if (u_space(c)) return 1;
    for (size_t i = 0; i < sizeof SET / sizeof *SET; i++) if (SET[i] == c) return 1;
    return 0;
}

/* PLACE_REFERENCE (re.I), at i: the end of the match, or 0. A place in the paper: §2.3, Section 2.2,
   Table 12, Fig. 3, Eq. (4), 第 4 节, 2.3 节, and 表 12 / 图 3 after a boundary or a pointing word. */
static size_t place_reference(const U32 *t, size_t i) {
    static const char *words[] = {"sections", "section", "sec", "tables", "table", "tab", "figures", "figure", "fig",
                                  "equations", "equation", "eq", "appendix"};
    size_t n = t->n, j, e;
    if (t->p[i] == 0xA7) {   /* §+\s*\d+(?:\.\d+)* */
        j = i;
        while (j < n && t->p[j] == 0xA7) j++;
        if ((e = dotted_number(t, skip_spaces(t, j)))) return e;
    }
    if (i == 0 || !u_word(t->p[i - 1])) {   /* \b(?:sections?|...)\.?\s*\(?\d+(?:\.\d+)*\)? */
        for (size_t w = 0; w < sizeof words / sizeof *words; w++) {
            if (!word_at(t, i, words[w])) continue;
            j = i + strlen(words[w]);
            if (j < n && t->p[j] == '.') j++;
            j = skip_spaces(t, j);
            if (j < n && t->p[j] == '(') j++;
            if ((e = dotted_number(t, j))) return e < n && t->p[e] == ')' ? e + 1 : e;
        }
    }
    if (t->p[i] == 0x7B2C && (e = dotted_number(t, skip_spaces(t, i + 1)))) {   /* 第\s*\d+(?:\.\d+)*\s*[节章表图] */
        j = skip_spaces(t, e);
        if (j < n && (t->p[j] == 0x8282 || t->p[j] == 0x7AE0 || t->p[j] == 0x8868 || t->p[j] == 0x56FE)) return j + 1;
    }
    if ((e = dotted_number(t, i))) {   /* \d+(?:\.\d+)*\s*[节章] */
        j = skip_spaces(t, e);
        if (j < n && (t->p[j] == 0x8282 || t->p[j] == 0x7AE0)) return j + 1;
    }
    if ((t->p[i] == 0x8868 || t->p[i] == 0x56FE) && (i == 0 || points_to_place(t->p[i - 1]))) {   /* [表图]\s*\d+(?:\.\d+)* */
        if ((e = dotted_number(t, skip_spaces(t, i + 1)))) return e;
    }
    return 0;
}

/* re.sub(pattern, ' ', text) for a matcher returning a match's end (0: none). */
static U32 blank(const U32 *t, size_t (*match)(const U32 *, size_t)) {
    U32 out = {0};
    size_t i = 0;
    while (i < t->n) {
        size_t end = match(t, i);
        if (end) { u_push(&out, ' '); i = end; } else u_push(&out, t->p[i++]);
    }
    return out;
}

/* drafts.stated_values: the values a written sentence reports (numbers() without page and place
   references, digits of names and years). */
Values stated_values(const U32 *t) {
    U32 a = blank(t, page_reference), p = blank(&a, place_reference), b = {0};
    size_t i = 0;
    while (i < p.n) {    /* NAME_DIGITS = (?<=[A-Za-z])-?\d+ */
        uint32_t prev = i > 0 ? p.p[i - 1] : 0;
        if ((prev >= 'A' && prev <= 'Z') || (prev >= 'a' && prev <= 'z')) {
            size_t j = i + (p.p[i] == '-' && i + 1 < p.n && u_digit(p.p[i + 1]) >= 0);
            size_t k = j;
            while (k < p.n && u_digit(p.p[k]) >= 0) k++;
            if (k > j) { u_push(&b, ' '); i = k; continue; }
        }
        u_push(&b, p.p[i++]);
    }
    Values all = numbers(&b), out = {0};
    for (size_t k = 0; k < all.n; k++) {
        double v = all.v[k];
        if (!(v == (double)(long long)v && v >= 1900 && v <= 2100)) values_add(&out, v);
    }
    free(a.p); free(p.p); free(b.p); free(all.v);
    return out;
}
