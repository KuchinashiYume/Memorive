/* memo_bridge.exe: the C version of scripts/memo_bridge.py and scripts/drafts.py.

   Compatibility code. It exists for computers without Python: an agent runs
   "<skill>/bin/memo_bridge.exe find handoff.json ..." with the same commands and output as
   "python <skill>/scripts/memo_bridge.py find handoff.json ...". Its callers are agents following
   SKILL.md and the hand-off's START_HERE.md, not code in this repository, so nothing here looks
   used to a search of the repository. DEVELOPING.md lists it under "Compatibility code";
   tests/test_skill_runners.py builds it and checks that it prints and writes what the Python
   version does on the same hand-off. When the Python scripts change, change this version too.

   Windows only (Memorive is a Windows program): Unicode normalization, lower case and letter
   classes come from Windows. Not ported: `bind` (it needs PyMuPDF through scripts/sources.py).

   Files: text.c (text as code points, Unicode classes, drafts.py's evening, locate, closest,
   numbers), json.c (JSON read and written as Python's json module writes it), sha256.c,
   drafts.c (drafts.py's marks and claims), memo_bridge.c (the commands). */
#ifndef MEMO_BRIDGE_H
#define MEMO_BRIDGE_H

#include <stddef.h>
#include <stdint.h>

/* Text as Unicode code points, the way Python indexes a str. */
typedef struct { uint32_t *p; size_t n, cap; } U32;
/* Bytes (UTF-8 text). */
typedef struct { char *p; size_t n, cap; } Buf;

void *xmalloc(size_t n);
void *xrealloc(void *p, size_t n);
void fail(const char *utf8);              /* print to stderr, exit 2 (Python: ValueError at the top) */

/* Buf */
void b_put(Buf *b, const char *p, size_t n);
void b_puts(Buf *b, const char *s);
void b_putc(Buf *b, char c);
void b_printf(Buf *b, const char *fmt, ...);
char *b_cstr(Buf *b);                     /* NUL-terminated copy of the bytes */

/* U32 */
void u_push(U32 *s, uint32_t c);
void u_append(U32 *s, const uint32_t *p, size_t n);
void u_append_utf8(U32 *s, const char *utf8);
U32 u_from_utf8(const char *p, size_t n);
U32 u_from_cstr(const char *utf8);
void u_to_utf8(Buf *b, const uint32_t *p, size_t n);
char *u_cstr(const uint32_t *p, size_t n); /* UTF-8, NUL-terminated */
U32 u_slice(const U32 *s, size_t a, size_t b);
long u_find(const U32 *hay, const U32 *needle, size_t from);
int u_starts(const U32 *s, size_t at, const char *ascii);
int u_cmp(const U32 *a, const U32 *b);

/* Unicode classes as Python's str methods and re see them */
int u_space(uint32_t c);                  /* str.isspace, re \s */
int u_digit(uint32_t c);                  /* re \d (decimal digit): its value 0..9, or -1 */
int u_alpha(uint32_t c);                  /* str.isalpha */
int u_word(uint32_t c);                   /* re \w */
U32 u_lower(const U32 *s);                /* str.lower */
U32 u_strip(const U32 *s);                /* str.strip() */
U32 u_strip_chars(const U32 *s, const char *chars_utf8, int left, int right);
void u_splitlines(const U32 *s, U32 **lines, size_t *count);   /* str.splitlines() */

/* drafts.py */
U32 even(const U32 *text, int hyphens, size_t **where);  /* _even; hyphens 0 none, 1 drop, 2 tight */
int locate(const U32 *quote, const U32 *passage, U32 *found);   /* 1 and the passage's own text, or 0 */
U32 closest(const U32 *quote, const U32 *passage);
typedef struct { double *v; size_t n, cap; } Values;
Values numbers(const U32 *text);
Values stated_values(const U32 *text);
int values_has(const Values *s, double v);
void values_add(Values *s, double v);
void values_sort(Values *s);
void fmt_g(Buf *b, double v);             /* Python format(v, 'g') */

/* JSON */
typedef enum { J_NULL, J_FALSE, J_TRUE, J_NUM, J_STR, J_ARR, J_OBJ } JType;
typedef struct Json Json;
struct Json {
    JType t;
    char *s; size_t n;                    /* J_NUM: the number as written; J_STR: UTF-8 */
    Json **items; char **keys; size_t len, cap;   /* J_ARR, J_OBJ (keys in insertion order) */
};
Json *json_parse(const char *text, size_t n, Buf *error);
Json *json_new(JType t);
Json *json_str(const char *utf8);
Json *json_strn(const char *utf8, size_t n);
Json *json_u32(const U32 *s);
Json *json_int(long long v);
Json *json_get(const Json *obj, const char *key);       /* NULL when absent */
Json *json_need(const Json *obj, const char *key);      /* fails like a Python KeyError */
void json_set(Json *obj, const char *key, Json *value); /* replace in place or append */
void json_push(Json *arr, Json *value);
void json_dump(Buf *b, const Json *v, int indent);      /* indent < 0: json.dumps defaults */
int json_truthy(const Json *v);
double json_number(const Json *v);        /* J_NUM value; 0 for null */
U32 json_text(const Json *v);             /* J_STR as code points */
void json_repr(Buf *b, const Json *v);    /* str() of a JSON value the way Python prints it in an f-string */

/* SHA-256 */
void sha256_hex(const unsigned char *data, size_t n, char out[65]);

/* drafts.c */
typedef struct { Json *support; const U32 *passage; char *problem; } Resolved;
typedef Resolved (*ResolveFn)(void *ctx, const U32 *alias, const U32 *where, const U32 *quote);
typedef void (*RenderFn)(void *ctx, Json *supports, U32 *out);
typedef void (*WhereFn)(void *ctx, double value, Buf *names);  /* "E1, E3" or empty */
void apply_draft(const U32 *draft, ResolveFn resolve, RenderFn render, WhereFn where, void *ctx,
                 U32 *text, Json *claims, Json *problems);
void collapse_marks(U32 *answer);         /* re.sub(r'(\[\d+\])(?:\s*\1)+', r'\1', answer) */
void blank_marks(U32 *text);              /* re.sub(r'\[\d+\]', ' ', text) */

#endif
