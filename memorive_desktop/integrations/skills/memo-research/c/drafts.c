/* drafts.py for memo_bridge.exe (compatibility code, see bridge.h): a draft's support marks
   [E3: "quote"] / [inference E3: "quote"; E5: "quote"], the claims they support and the problems
   found. The regular expressions of drafts.py are matched by hand here; each matcher names its
   pattern and follows how Python's re backtracks on it. */
#define _CRT_SECURE_NO_WARNINGS
#include <stdlib.h>
#include <string.h>
#include "bridge.h"

static int upper(uint32_t c) { return c >= 'A' && c <= 'Z'; }

/* Characters a support's locator ([^:：\[\]\n"“]) cannot hold. */
static int stops_locator(uint32_t c) {
    return c == ':' || c == 0xFF1A || c == '[' || c == ']' || c == '\n' || c == '"' || c == 0x201C;
}

typedef struct { size_t alias0, alias1, where0, where1, quote0, quote1; } Support;

/* SUPPORT = ([A-Z]\d+)([^:：\[\]\n"“]*)[:：]\s*(?:"([^"\n]+)"|“([^”\n]+)”) at p. */
static int support_at(const U32 *d, size_t p, size_t *end, Support *s) {
    size_t n = d->n, q = p + 1;
    if (p >= n || !upper(d->p[p])) return 0;
    while (q < n && u_digit(d->p[q]) >= 0) q++;
    if (q == p + 1) return 0;
    s->alias0 = p; s->alias1 = q; s->where0 = q;
    while (q < n && !stops_locator(d->p[q])) q++;
    s->where1 = q;
    if (q >= n || (d->p[q] != ':' && d->p[q] != 0xFF1A)) return 0;
    q++;
    while (q < n && u_space(d->p[q])) q++;
    if (q >= n) return 0;
    uint32_t open = d->p[q], close;
    if (open == '"') close = '"';
    else if (open == 0x201C) close = 0x201D;
    else return 0;
    size_t r = q + 1;
    while (r < n && d->p[r] != close && d->p[r] != '\n') r++;
    if (r >= n || d->p[r] != close || r == q + 1) return 0;
    s->quote0 = q + 1; s->quote1 = r;
    *end = r + 1;
    return 1;
}

typedef struct { size_t start, end; int inference; Support *supports; size_t count; } Mark;

/* The supports of a mark from p to its ']': (SUPPORT\s*[;；]?\s*)+\] */
static int supports_from(const U32 *d, size_t p, Mark *m) {
    size_t cap = 4, end;
    Support s;
    m->supports = xmalloc(cap * sizeof *m->supports);
    m->count = 0;
    while (support_at(d, p, &end, &s)) {
        if (m->count == cap) m->supports = xrealloc(m->supports, (cap *= 2) * sizeof *m->supports);
        m->supports[m->count++] = s;
        p = end;
        while (p < d->n && u_space(d->p[p])) p++;
        if (p < d->n && (d->p[p] == ';' || d->p[p] == 0xFF1B)) p++;
        while (p < d->n && u_space(d->p[p])) p++;
    }
    if (m->count && p < d->n && d->p[p] == ']') { m->end = p + 1; return 1; }
    free(m->supports);
    return 0;
}

/* MARK = \[(inference\s+)?((?:SUPPORT\s*[;；]?\s*)+)\] at i. */
static int mark_at(const U32 *d, size_t i, Mark *m) {
    if (i >= d->n || d->p[i] != '[') return 0;
    m->start = i;
    size_t p = i + 1;
    if (u_starts(d, p, "inference") && p + 9 < d->n && u_space(d->p[p + 9])) {
        size_t q = p + 9;
        while (q < d->n && u_space(d->p[q])) q++;
        if (supports_from(d, q, m)) { m->inference = 1; return 1; }
    }
    m->inference = 0;
    return supports_from(d, p, m);
}

/* LEFTOVER = \[(?:inference\s+)?[A-Z]\d+[\s:：\]] at i: its end, or 0. */
static size_t leftover_at(const U32 *d, size_t i) {
    if (i >= d->n || d->p[i] != '[') return 0;
    size_t starts[2], count = 0, p = i + 1;
    if (u_starts(d, p, "inference") && p + 9 < d->n && u_space(d->p[p + 9])) {
        size_t q = p + 9;
        while (q < d->n && u_space(d->p[q])) q++;
        starts[count++] = q;
    }
    starts[count++] = p;
    for (size_t k = 0; k < count; k++) {
        size_t q = starts[k];
        if (q >= d->n || !upper(d->p[q])) continue;
        size_t r = q + 1;
        while (r < d->n && u_digit(d->p[r]) >= 0) r++;
        if (r == q + 1 || r >= d->n) continue;
        uint32_t c = d->p[r];
        if (u_space(c) || c == ':' || c == 0xFF1A || c == ']') return r + 1;
    }
    return 0;
}

/* BOUNDARY = [。！？!?；;]|\.\s|\n|\*\*[^*\n]*\*\*\s*[：:]? at i, within [.., end): its end, or 0. */
static size_t boundary_at(const U32 *t, size_t i, size_t end) {
    uint32_t c = t->p[i];
    if (c == 0x3002 || c == 0xFF01 || c == 0xFF1F || c == '!' || c == '?' || c == 0xFF1B || c == ';') return i + 1;
    if (c == '.' && i + 1 < end && u_space(t->p[i + 1])) return i + 2;
    if (c == '\n') return i + 1;
    if (c == '*' && i + 1 < end && t->p[i + 1] == '*') {
        size_t k = i + 2;
        while (k < end && t->p[k] != '*' && t->p[k] != '\n') k++;
        if (k + 1 < end && t->p[k] == '*' && t->p[k + 1] == '*') {
            k += 2;
            while (k < end && u_space(t->p[k])) k++;
            if (k < end && (t->p[k] == 0xFF1A || t->p[k] == ':')) k++;
            return k;
        }
    }
    return 0;
}

/* drafts._claim: the claim a mark at the end of text supports, from the last sentence end, mark or
   line start (not before floor) to the mark; a mark after the sentence's own full stop supports
   that sentence. */
static U32 claim_before(const U32 *text, size_t floor) {
    size_t end = text->n;
    U32 claim = {0};
    for (int round = 0; round < 2; round++) {
        size_t last_start = 0, last_end = 0;
        int found = 0;
        size_t i = floor;
        while (i < end) {
            size_t e = boundary_at(text, i, end);
            if (e) { found = 1; last_start = i; last_end = e; i = e; } else i++;
        }
        size_t start = found ? last_end : floor;
        U32 piece = u_slice(text, start, end), a = u_strip(&piece);
        U32 b = u_strip_chars(&a, "-*>#\xC2\xB7\xE2\x80\xA2|", 1, 0), c = u_strip(&b);
        U32 d = u_strip_chars(&c, "\xEF\xBC\x8C,\xEF\xBC\x9A:|", 0, 1);
        free(claim.p);
        claim = u_strip(&d);
        free(piece.p); free(a.p); free(b.p); free(c.p); free(d.p);
        if (claim.n || !found) return claim;
        end = last_start;
    }
    return claim;
}

static Json *json_slice(const U32 *s, size_t a, size_t b) {
    U32 t = u_slice(s, a, b);
    Json *v = json_u32(&t);
    free(t.p);
    return v;
}

static void problem_u32(Json *problems, Buf *b) {
    json_push(problems, json_strn(b->p ? b->p : "", b->n));
    free(b->p);
    memset(b, 0, sizeof *b);
}

/* drafts.apply: a marked draft becomes text, claims and problems. */
void apply_draft(const U32 *draft, ResolveFn resolve, RenderFn render, WhereFn where, void *ctx,
                 U32 *text_out, Json *claims, Json *problems) {
    U32 text = {0};
    size_t last = 0, boundary = 0, nclaims = 0, cap = 8;
    int claimed = 0;
    /* passages_of[k]: the passages cited by claims[k] */
    const U32 ***passages_of = xmalloc(cap * sizeof *passages_of);
    size_t *npassages = xmalloc(cap * sizeof *npassages);
    size_t i = 0;
    while (i < draft->n) {
        Mark m;
        if (!mark_at(draft, i, &m)) { i++; continue; }
        u_append(&text, draft->p + last, m.start - last);
        last = m.end;
        U32 tail = u_slice(&text, boundary, text.n), rest = u_strip_chars(&tail, " \t\xEF\xBC\x8C,\xE3\x80\x81;\xEF\xBC\x9B", 1, 1);
        int follows = claimed && rest.n == 0;
        free(tail.p); free(rest.p);
        U32 claim = {0};
        if (!follows) claim = claim_before(&text, boundary);
        Json *supports = json_new(J_ARR);
        const U32 **passages = xmalloc((m.count + 1) * sizeof *passages);
        size_t np = 0;
        for (size_t k = 0; k < m.count; k++) {
            Support *s = &m.supports[k];
            U32 alias = u_slice(draft, s->alias0, s->alias1), w = u_slice(draft, s->where0, s->where1);
            U32 where_text = u_strip(&w), quote = u_slice(draft, s->quote0, s->quote1);
            Resolved r = resolve(ctx, &alias, &where_text, &quote);
            if (r.problem) json_push(problems, json_str(r.problem));
            else { json_push(supports, r.support); passages[np++] = r.passage; }
            free(alias.p); free(w.p); free(where_text.p); free(quote.p);
        }
        if (follows) {
            Json *prev = json_need(claims->items[nclaims - 1], "supports");
            for (size_t k = 0; k < supports->len; k++) json_push(prev, supports->items[k]);
            size_t had = npassages[nclaims - 1];
            passages_of[nclaims - 1] = xrealloc((void *)passages_of[nclaims - 1], (had + np + 1) * sizeof **passages_of);
            for (size_t k = 0; k < np; k++) passages_of[nclaims - 1][had + k] = passages[k];
            npassages[nclaims - 1] = had + np;
            free((void *)passages);
        } else if (claim.n) {
            Json *c = json_new(J_OBJ);
            json_set(c, "text", json_u32(&claim));
            json_set(c, "kind", json_str(m.inference ? "inference" : "source_statement"));
            json_set(c, "supports", supports);
            json_push(claims, c);
            if (nclaims == cap) {
                cap *= 2;
                passages_of = xrealloc((void *)passages_of, cap * sizeof *passages_of);
                npassages = xrealloc(npassages, cap * sizeof *npassages);
            }
            passages_of[nclaims] = passages;
            npassages[nclaims++] = np;
        } else {
            Buf b = {0};
            U32 whole = u_slice(draft, m.start, m.end);
            b_puts(&b, "a mark has no claim before it: ");
            u_to_utf8(&b, whole.p, whole.n < 80 ? whole.n : 80);
            problem_u32(problems, &b);
            free(whole.p);
            free((void *)passages);
        }
        claimed = follows || claim.n > 0;
        free(claim.p);
        render(ctx, supports, &text);
        boundary = text.n;
        free(m.supports);
        i = m.end;
    }
    /* A source statement's numbers must be in the passages it cites. */
    for (size_t k = 0; k < nclaims; k++) {
        Json *c = claims->items[k];
        if (strcmp(json_need(c, "kind")->s, "source_statement") || !npassages[k]) continue;
        Values cited = {0};
        for (size_t p = 0; p < npassages[k]; p++) {
            Values v = numbers(passages_of[k][p]);
            for (size_t q = 0; q < v.n; q++) values_add(&cited, v.v[q]);
            free(v.v);
        }
        U32 claim_text = json_text(json_need(c, "text"));
        Values stated = stated_values(&claim_text), missing = {0};
        for (size_t q = 0; q < stated.n; q++) if (!values_has(&cited, stated.v[q])) values_add(&missing, stated.v[q]);
        values_sort(&missing);
        for (size_t q = 0; q < missing.n; q++) {
            Buf names = {0}, b = {0};
            where(ctx, missing.v[q], &names);
            b_putc(&b, '"');
            u_to_utf8(&b, claim_text.p, claim_text.n < 80 ? claim_text.n : 80);
            b_puts(&b, "\": ");
            fmt_g(&b, missing.v[q]);
            b_puts(&b, " not in the cited passages; ");
            if (names.n) { b_puts(&b, "it is in "); b_put(&b, names.p, names.n); b_puts(&b, ": cite that passage"); }
            else b_puts(&b, "cite the passage that reports it");
            b_puts(&b, ", or mark a computed value as [inference ...]");
            problem_u32(problems, &b);
            free(names.p);
        }
        free(cited.v); free(stated.v); free(missing.v); free(claim_text.p);
    }
    u_append(&text, draft->p + last, draft->n - last);
    *text_out = u_strip(&text);
    free(text.p);
    /* rest = MARK.sub(' ', draft): rendered locators such as [L7] are fine, other bracketed aliases are not marks */
    U32 rest = {0};
    i = 0;
    while (i < draft->n) {
        Mark m;
        if (mark_at(draft, i, &m)) { u_push(&rest, ' '); i = m.end; free(m.supports); }
        else u_push(&rest, draft->p[i++]);
    }
    i = 0;
    while (i < rest.n) {
        size_t e = leftover_at(&rest, i);
        if (!e) { i++; continue; }
        long close = -1;
        for (size_t k = i; k < rest.n; k++) if (rest.p[k] == ']') { close = (long)k; break; }
        size_t stop = close >= 0 && (size_t)close - i < 160 ? (size_t)close + 1 : i + 80;
        if (stop > rest.n) stop = rest.n;
        Buf b = {0};
        b_putc(&b, '"');
        u_to_utf8(&b, rest.p + i, stop - i);
        b_puts(&b, "\" is not a citation mark: write [E3: \"quote\"] / [S1 p.3 L120-124: \"quote\"] or [inference ...; ...], "
                   "every mark with a quote copied from its passage");
        problem_u32(problems, &b);
        i = e;
    }
    free(rest.p);
    if (!nclaims) json_push(problems, json_str("the draft cites no passage"));
    for (size_t k = 0; k < nclaims; k++) free((void *)passages_of[k]);
    free((void *)passages_of);
    free(npassages);
}

/* \[\d+\] at i: its end, or 0. */
static size_t number_mark(const U32 *t, size_t i) {
    if (i >= t->n || t->p[i] != '[') return 0;
    size_t j = i + 1;
    while (j < t->n && u_digit(t->p[j]) >= 0) j++;
    return j > i + 1 && j < t->n && t->p[j] == ']' ? j + 1 : 0;
}

/* re.sub(r'(\[\d+\])(?:\s*\1)+', r'\1', answer): marks one after another on one passage, [4] [4]. */
void collapse_marks(U32 *answer) {
    U32 out = {0};
    size_t i = 0;
    while (i < answer->n) {
        size_t e = number_mark(answer, i);
        if (!e) { u_push(&out, answer->p[i++]); continue; }
        size_t len = e - i, pos = e;
        int repeats = 0;
        for (;;) {
            size_t q = pos;
            while (q < answer->n && u_space(answer->p[q])) q++;
            if (q + len <= answer->n && !memcmp(answer->p + q, answer->p + i, len * sizeof *answer->p)) { pos = q + len; repeats++; }
            else break;
        }
        if (repeats) { u_append(&out, answer->p + i, len); i = pos; }
        else u_push(&out, answer->p[i++]);
    }
    free(answer->p);
    *answer = out;
}

/* re.sub(r'\[\d+\]', ' ', text) */
void blank_marks(U32 *text) {
    U32 out = {0};
    size_t i = 0;
    while (i < text->n) {
        size_t e = number_mark(text, i);
        if (e) { u_push(&out, ' '); i = e; } else u_push(&out, text->p[i++]);
    }
    free(text->p);
    *text = out;
}
