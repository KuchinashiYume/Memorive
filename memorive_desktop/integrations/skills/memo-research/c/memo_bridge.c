/* memo_bridge.exe: the commands of scripts/memo_bridge.py (find, read, answer, return, context).
   Compatibility code for computers without Python; bridge.h says who calls it and what keeps it.
   Each function names the Python function it follows. It prints and writes what the Python
   version does, with Windows line ends (CRLF) the way Python's text files and console write them. */
#define _CRT_SECURE_NO_WARNINGS
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <errno.h>
#include <io.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>
#include "bridge.h"

static const char *ANSWER_SCHEMA = "memo-research-answer/1";
static const char *NOT_REACHED =
    "Answer from the extracted texts in materials/ (sources.md says which file is which paper), giving "
    "page numbers, and tell the user the answer could not be returned: approving this command lets it "
    "be returned, or the user can add the saved answer in the Memorive conversation.";
static const char *HELP =
    "memo_bridge.exe: the C version of the memo-research Skill's scripts/memo_bridge.py.\n\n"
    "    find    HANDOFF TERM [TERM ...] [--artifact ID] [--work DIR]\n"
    "    read    HANDOFF [--artifact ID] [--pages A-B] [--work DIR]\n"
    "    answer  HANDOFF --draft DRAFT.md [--gap TEXT] [--request-id ID] [--work DIR] [--out FILE] [--return]\n"
    "    return  HANDOFF --result memo-answer.json\n"
    "    context HANDOFF [--artifact ID] [--cursor N] [--limit N]\n\n"
    "Options before the command: --transport FILE (defaults to transport.json beside HANDOFF).\n"
    "`bind` needs Python: run it with python <skill>/scripts/memo_bridge.py or Memorive.exe --memo-bridge.\n";

/* --- output ---------------------------------------------------------------------------------- */

/* Text to stdout or stderr: UTF-16 to a console, otherwise UTF-8 bytes with \n written as \r\n
   (Python's sys.stdout on Windows). */
static void emit(DWORD which, const char *p, size_t n) {
    HANDLE h = GetStdHandle(which);
    DWORD mode, done;
    if (h == NULL || h == INVALID_HANDLE_VALUE) return;
    if (GetConsoleMode(h, &mode)) {
        int wn = MultiByteToWideChar(CP_UTF8, 0, p, (int)n, NULL, 0);
        wchar_t *w = xmalloc(((size_t)wn + 1) * sizeof *w);
        MultiByteToWideChar(CP_UTF8, 0, p, (int)n, w, wn);
        WriteConsoleW(h, w, (DWORD)wn, &done, NULL);
        free(w);
        return;
    }
    Buf b = {0};
    for (size_t i = 0; i < n; i++) {
        if (p[i] == '\n') b_putc(&b, '\r');
        b_putc(&b, p[i]);
    }
    if (b.n) WriteFile(h, b.p, (DWORD)b.n, &done, NULL);
    free(b.p);
}

static void print_buf(Buf *b) {   /* print(text) */
    emit(STD_OUTPUT_HANDLE, b->p ? b->p : "", b->n);
    emit(STD_OUTPUT_HANDLE, "\n", 1);
}

void fail(const char *utf8) {      /* print(str(error), file=sys.stderr); raise SystemExit(2) */
    emit(STD_ERROR_HANDLE, utf8, strlen(utf8));
    emit(STD_ERROR_HANDLE, "\n", 1);
    exit(2);
}

static void fail_buf(Buf *b) { fail(b->p ? b->p : ""); }

/* --- Python's repr of a str (for messages that quote a path) --------------------------------- */

static void repr_str(Buf *b, const char *s) {
    int dq = strchr(s, '\'') && !strchr(s, '"');
    char q = dq ? '"' : '\'';
    b_putc(b, q);
    for (const char *p = s; *p; p++) {
        if (*p == '\\') b_puts(b, "\\\\");
        else if (*p == q) { b_putc(b, '\\'); b_putc(b, q); }
        else if (*p == '\n') b_puts(b, "\\n");
        else if (*p == '\r') b_puts(b, "\\r");
        else if (*p == '\t') b_puts(b, "\\t");
        else b_putc(b, *p);
    }
    b_putc(b, q);
}

/* --- paths and files ------------------------------------------------------------------------- */

static wchar_t *wide(const char *utf8) {
    int n = MultiByteToWideChar(CP_UTF8, 0, utf8, -1, NULL, 0);
    wchar_t *w = xmalloc((size_t)n * sizeof *w);
    MultiByteToWideChar(CP_UTF8, 0, utf8, -1, w, n);
    return w;
}

static char *narrow(const wchar_t *w) {
    int n = WideCharToMultiByte(CP_UTF8, 0, w, -1, NULL, 0, NULL, NULL);
    char *s = xmalloc((size_t)n);
    WideCharToMultiByte(CP_UTF8, 0, w, -1, s, n, NULL, NULL);
    return s;
}

/* str(Path(p)): backslashes, no repeated separators, no '.' parts, no trailing separator. */
static int separator(char c) { return c == '\\' || c == '/'; }

static char *path_text(const char *p) {
    Buf b = {0};
    size_t n = strlen(p), i = 0;
    if (n >= 2 && separator(p[0]) && separator(p[1])) { b_puts(&b, "\\\\"); i = 2; }            /* \\server */
    else if (n >= 1 && separator(p[0])) { b_putc(&b, '\\'); i = 1; }                              /* \x */
    else if (n >= 2 && p[1] == ':') {                                                             /* C: or C:\ */
        b_put(&b, p, 2);
        i = 2;
        if (n >= 3 && separator(p[2])) { b_putc(&b, '\\'); i = 3; }
    }
    int parts = 0;
    while (i < n) {
        size_t j = i;
        while (j < n && !separator(p[j])) j++;
        if (j > i && !(j - i == 1 && p[i] == '.')) {   /* empty and '.' parts are left out */
            if (parts) b_putc(&b, '\\');
            b_put(&b, p + i, j - i);
            parts++;
        }
        i = j + 1;
    }
    if (!b.n) b_putc(&b, '.');
    return b_cstr(&b);
}

static char *join(const char *dir, const char *name) {
    Buf b = {0};
    b_puts(&b, dir);
    if (b.n && b.p[b.n - 1] != '\\') b_putc(&b, '\\');
    b_puts(&b, name);
    return b_cstr(&b);
}

static char *parent(const char *path) {
    const char *slash = strrchr(path, '\\');
    if (!slash) return _strdup(".");
    Buf b = {0};
    b_put(&b, path, (size_t)(slash - path));
    if (b.n == 2 && b.p[1] == ':') b_putc(&b, '\\');
    return b_cstr(&b);
}

/* Path(p).resolve(): absolute, with links and short names resolved as far as the path exists. */
static char *resolve(const char *p) {
    wchar_t *w = wide(p), full[32768], final[32768];
    DWORD n = GetFullPathNameW(w, 32768, full, NULL);
    free(w);
    if (!n || n >= 32768) return path_text(p);
    HANDLE h = CreateFileW(full, 0, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL, OPEN_EXISTING,
                           FILE_FLAG_BACKUP_SEMANTICS, NULL);
    if (h == INVALID_HANDLE_VALUE) return narrow(full);
    DWORD m = GetFinalPathNameByHandleW(h, final, 32768, FILE_NAME_NORMALIZED | VOLUME_NAME_DOS);
    CloseHandle(h);
    if (!m || m >= 32768) return narrow(full);
    wchar_t *f = final;
    if (!wcsncmp(f, L"\\\\?\\UNC\\", 8)) { f += 6; f[0] = L'\\'; }
    else if (!wcsncmp(f, L"\\\\?\\", 4)) f += 4;
    return narrow(f);
}

static int is_file(const char *p) {
    wchar_t *w = wide(p);
    DWORD a = GetFileAttributesW(w);
    free(w);
    return a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY);
}

static void open_error(Buf *b, const char *path) {   /* str(OSError) of open() */
    int e = errno;
    b_printf(b, "[Errno %d] %s: ", e, e == ENOENT ? "No such file or directory" : strerror(e));
    repr_str(b, path);
}

/* Path.mkdir(parents=True, exist_ok=True) */
static void make_dirs(const char *path) {
    wchar_t *w = wide(path);
    for (wchar_t *p = w; *p; p++) {
        if ((*p == L'\\' || *p == L'/') && p > w && p[-1] != L':' && p[-1] != L'\\') {
            wchar_t keep = *p;
            *p = 0;
            CreateDirectoryW(w, NULL);
            *p = keep;
        }
    }
    if (!CreateDirectoryW(w, NULL) && GetLastError() != ERROR_ALREADY_EXISTS) {
        Buf b = {0};
        b_printf(&b, "[WinError %lu] could not create the folder: ", GetLastError());
        repr_str(&b, path);
        fail_buf(&b);
    }
    free(w);
}

/* Path.read_text(encoding='utf-8-sig'): the BOM left out, line ends read as \n. */
static Buf read_text(const char *path) {
    wchar_t *w = wide(path);
    FILE *f = _wfopen(w, L"rb");
    free(w);
    if (!f) { Buf e = {0}; open_error(&e, path); fail_buf(&e); }
    Buf raw = {0}, out = {0};
    char chunk[65536];
    size_t got;
    while ((got = fread(chunk, 1, sizeof chunk, f)) > 0) b_put(&raw, chunk, got);
    fclose(f);
    size_t i = raw.n >= 3 && !memcmp(raw.p, "\xEF\xBB\xBF", 3) ? 3 : 0;
    for (; i < raw.n; i++) {
        if (raw.p[i] == '\r') { b_putc(&out, '\n'); if (i + 1 < raw.n && raw.p[i + 1] == '\n') i++; }
        else b_putc(&out, raw.p[i]);
    }
    free(raw.p);
    if (!out.p) b_putc(&out, 0), out.n = 0;
    return out;
}

/* Path.write_text(text, encoding='utf-8'): \n written as \r\n. */
static void write_text(const char *path, const Buf *text) {
    wchar_t *w = wide(path);
    FILE *f = _wfopen(w, L"wb");
    free(w);
    if (!f) { Buf e = {0}; open_error(&e, path); fail_buf(&e); }
    for (size_t i = 0; i < text->n; i++) {
        if (text->p[i] == '\n') fputc('\r', f);
        fputc(text->p[i], f);
    }
    fclose(f);
}

static Json *read_json(const char *path) {
    Buf text = read_text(path), error = {0};
    Json *v = json_parse(text.p, text.n, &error);
    if (!v) fail_buf(&error);
    return v;
}

/* --- Memo's local transport (Memo.call) ------------------------------------------------------ */

typedef struct {
    char *handoff;      /* resolved */
    Json *pack, *transport, *binding, *methods;
} Memo;

/* subprocess.list2cmdline */
static void cmdline_arg(Buf *b, const char *arg) {
    if (b->n) b_putc(b, ' ');
    int quote = strchr(arg, ' ') || strchr(arg, '\t') || !*arg;
    if (quote) b_putc(b, '"');
    size_t slashes = 0;
    for (const char *p = arg; *p; p++) {
        if (*p == '\\') slashes++;
        else if (*p == '"') {
            for (size_t k = 0; k < slashes * 2; k++) b_putc(b, '\\');
            slashes = 0;
            b_puts(b, "\\\"");
        } else {
            for (size_t k = 0; k < slashes; k++) b_putc(b, '\\');
            slashes = 0;
            b_putc(b, *p);
        }
    }
    for (size_t k = 0; k < slashes; k++) b_putc(b, '\\');
    if (quote) {
        for (size_t k = 0; k < slashes; k++) b_putc(b, '\\');
        b_putc(b, '"');
    }
}

typedef struct { HANDLE h; Buf data; const char *input; size_t n; } Pipe;

static DWORD WINAPI drain(void *arg) {
    Pipe *p = arg;
    char chunk[65536];
    DWORD got;
    while (ReadFile(p->h, chunk, sizeof chunk, &got, NULL) && got) b_put(&p->data, chunk, got);
    return 0;
}

static DWORD WINAPI feed(void *arg) {
    Pipe *p = arg;
    DWORD done;
    size_t at = 0;
    while (at < p->n && WriteFile(p->h, p->input + at, (DWORD)(p->n - at > 65536 ? 65536 : p->n - at), &done, NULL)) at += done;
    CloseHandle(p->h);
    return 0;
}

/* Text of a child's output: decoded with replacement, line ends read as \n. */
static Buf output_text(const Buf *raw) {
    U32 u = u_from_utf8(raw->p ? raw->p : "", raw->n);
    Buf b = {0}, out = {0};
    u_to_utf8(&b, u.p, u.n);
    for (size_t i = 0; i < b.n; i++) {
        if (b.p[i] == '\r') { b_putc(&out, '\n'); if (i + 1 < b.n && b.p[i + 1] == '\n') i++; }
        else b_putc(&out, b.p[i]);
    }
    free(u.p); free(b.p);
    return out;
}

static int refusal_code(const U32 *m) {   /* re.fullmatch(r'[A-Z][A-Z0-9_:]{1,95}', message) */
    if (m->n < 2 || m->n > 96 || m->p[0] < 'A' || m->p[0] > 'Z') return 0;
    for (size_t i = 1; i < m->n; i++) {
        uint32_t c = m->p[i];
        if (!((c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' || c == ':')) return 0;
    }
    return 1;
}

static const char *refusal_meaning(const char *code) {
    if (!strcmp(code, "HANDOFF_VERSION_CHANGED")) return "the conversation or its sources changed after this hand-off was made";
    if (!strcmp(code, "HANDOFF_SOURCE_CHANGED")) return "a source of this hand-off changed after it was made";
    if (!strcmp(code, "HANDOFF_THREAD_CHANGED")) return "the conversation was archived or moved after this hand-off was made";
    if (!strcmp(code, "HANDOFF_NOT_FOUND")) return "Memorive does not know this hand-off";
    return NULL;
}

/* Memo.call: run transport.json's command with --call method and params as JSON on stdin. A
   refusal (one code on stderr) fails; Memorive not reached returns NULL with the reason in
   not_reached (Python: NotReached). */
static Json *memo_call(Memo *m, const char *method, Json *params, Buf *not_reached) {
    Json *command = json_need(m->transport, "command");
    int valid = command->t == J_ARR && command->len > 0;
    for (size_t i = 0; valid && i < command->len; i++) if (command->items[i]->t != J_STR) valid = 0;
    if (!valid) fail("Invalid configured Memo transport command");
    if (strcmp(method, "memo.capabilities") && strcmp(method, "memo.skill_context") && strcmp(method, "memo.skill_answer"))
        fail("Unsupported bridge method");
    Buf line = {0}, input = {0};
    for (size_t i = 0; i < command->len; i++) cmdline_arg(&line, command->items[i]->s);
    cmdline_arg(&line, "--call");
    cmdline_arg(&line, method);
    json_dump(&input, params, -1);
    SECURITY_ATTRIBUTES sa = {sizeof sa, NULL, TRUE};
    HANDLE in_r, in_w, out_r, out_w, err_r, err_w;
    CreatePipe(&in_r, &in_w, &sa, 0);
    CreatePipe(&out_r, &out_w, &sa, 0);
    CreatePipe(&err_r, &err_w, &sa, 0);
    SetHandleInformation(in_w, HANDLE_FLAG_INHERIT, 0);
    SetHandleInformation(out_r, HANDLE_FLAG_INHERIT, 0);
    SetHandleInformation(err_r, HANDLE_FLAG_INHERIT, 0);
    STARTUPINFOW si = {sizeof si};
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdInput = in_r; si.hStdOutput = out_w; si.hStdError = err_w;
    PROCESS_INFORMATION pi;
    wchar_t *wline = wide(line.p);
    BOOL started = CreateProcessW(NULL, wline, NULL, NULL, TRUE, 0, NULL, NULL, &si, &pi);
    DWORD error = GetLastError();
    free(wline);
    CloseHandle(in_r); CloseHandle(out_w); CloseHandle(err_w);
    if (!started) {
        CloseHandle(in_w); CloseHandle(out_r); CloseHandle(err_r);
        wchar_t *text = NULL;
        FormatMessageW(FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS, NULL,
                       error, 0, (wchar_t *)&text, 0, NULL);
        size_t n = text ? wcslen(text) : 0;
        while (n && (text[n - 1] <= L' ' || text[n - 1] == L'.')) text[--n] = 0;   /* as Python trims it */
        char *message = text ? narrow(text) : _strdup("");
        if (text) LocalFree(text);
        b_printf(not_reached, "Memorive could not be reached: [WinError %lu] %s", error, message);
        return NULL;
    }
    Pipe in = {in_w, {0}, input.p, input.n}, out = {out_r, {0}, NULL, 0}, err = {err_r, {0}, NULL, 0};
    HANDLE threads[3] = {CreateThread(NULL, 0, feed, &in, 0, NULL), CreateThread(NULL, 0, drain, &out, 0, NULL),
                         CreateThread(NULL, 0, drain, &err, 0, NULL)};
    if (WaitForSingleObject(pi.hProcess, 120000) == WAIT_TIMEOUT) {
        TerminateProcess(pi.hProcess, 1);
        WaitForMultipleObjects(3, threads, TRUE, 5000);
        b_puts(not_reached, "Memorive could not be reached: Command '[");
        for (size_t i = 0; i < command->len; i++) { if (i) b_puts(not_reached, ", "); repr_str(not_reached, command->items[i]->s); }
        b_puts(not_reached, ", '--call', ");
        repr_str(not_reached, method);
        b_puts(not_reached, "]' timed out after 120 seconds");
        return NULL;
    }
    WaitForMultipleObjects(3, threads, TRUE, INFINITE);
    DWORD code = 0;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hProcess); CloseHandle(pi.hThread);
    CloseHandle(out_r); CloseHandle(err_r);
    for (int k = 0; k < 3; k++) CloseHandle(threads[k]);
    Buf stdout_text = output_text(&out.data), stderr_text = output_text(&err.data);
    if (code) {
        U32 all = u_from_utf8(stderr_text.p ? stderr_text.p : "", stderr_text.n), message = u_strip(&all);
        if (refusal_code(&message)) {
            char *c = u_cstr(message.p, message.n);
            const char *meaning = refusal_meaning(c);
            Buf b = {0};
            b_puts(&b, c);
            if (meaning) b_printf(&b, ": %s. Keep your answer; a new hand-off is needed to return it.", meaning);
            fail_buf(&b);
        }
        b_puts(not_reached, "Memorive could not be reached: ");
        if (message.n) {
            size_t from = message.n > 300 ? message.n - 300 : 0;
            u_to_utf8(not_reached, message.p + from, message.n - from);
        } else b_puts(not_reached, "the local command failed");
        return NULL;
    }
    Buf jerror = {0};
    Json *v = json_parse(stdout_text.p ? stdout_text.p : "", stdout_text.n, &jerror);
    if (!v) fail_buf(&jerror);
    return v;
}

static Json *call_or_fail(Memo *m, const char *method, Json *params, const char *suffix) {
    Buf not_reached = {0};
    Json *v = memo_call(m, method, params, &not_reached);
    if (!v) {
        if (suffix) { b_putc(&not_reached, '\n'); b_puts(&not_reached, suffix); }
        fail_buf(&not_reached);
    }
    return v;
}

/* Memo.require: older Memo releases offer only some methods. Returns 0 when Memorive is not reached
   (the reason in not_reached); a method Memo does not offer fails. */
static int require_or_not_reached(Memo *m, const char *method, Buf *not_reached) {
    if (!m->methods) {
        Json *caps = memo_call(m, "memo.capabilities", json_new(J_OBJ), not_reached);
        if (!caps) return 0;
        m->methods = json_new(J_OBJ);
        Json *tools = json_get(caps, "tools"), *methods = json_get(caps, "methods");
        if (tools && tools->t == J_ARR)
            for (size_t i = 0; i < tools->len; i++) if (tools->items[i]->t == J_STR) json_set(m->methods, tools->items[i]->s, json_new(J_TRUE));
        if (methods && methods->t == J_OBJ)
            for (size_t i = 0; i < methods->len; i++) json_set(m->methods, methods->keys[i], json_new(J_TRUE));
    }
    if (!json_get(m->methods, method)) {
        Buf b = {0};
        b_printf(&b, "Memo host does not support %s; keep local artifacts, do not claim a return", method);
        fail_buf(&b);
    }
    return 1;
}

static void require(Memo *m, const char *method, const char *suffix) {
    Buf not_reached = {0};
    if (!require_or_not_reached(m, method, &not_reached)) {
        if (suffix) { b_putc(&not_reached, '\n'); b_puts(&not_reached, suffix); }
        fail_buf(&not_reached);
    }
}

static Json *binding_params(Memo *m) {
    Json *p = json_new(J_OBJ);
    for (size_t i = 0; i < m->binding->len; i++) json_set(p, m->binding->keys[i], m->binding->items[i]);
    return p;
}

/* Memo.pages: every evidence chunk of the hand-off (or of one source), page by page. */
static Json *pages(Memo *m, const char *artifact, Json **first_out) {
    require(m, "memo.skill_context", NOT_REACHED);
    Json *cursor = json_int(0), *refs = json_new(J_ARR), *first = NULL;
    while (cursor->t != J_NULL) {
        Json *params = binding_params(m);
        json_set(params, "cursor", cursor);
        json_set(params, "limit", json_int(24));
        if (artifact) json_set(params, "artifact_id", json_str(artifact));
        Json *page = call_or_fail(m, "memo.skill_context", params, NOT_REACHED);
        if (!first) first = page;
        Json *evidence = json_need(page, "evidence");
        for (size_t i = 0; i < evidence->len; i++) json_push(refs, evidence->items[i]);
        cursor = json_need(page, "next_cursor");
    }
    *first_out = first;
    return refs;
}

/* --- read and find ----------------------------------------------------------------------------- */

static char *key_of(const Json *v) {   /* a JSON value as a dictionary key */
    Buf b = {0};
    json_dump(&b, v, -1);
    return b_cstr(&b);
}

static double page_or_zero(const Json *row) {   /* row.get('page') or 0 */
    return json_number(json_get(row, "page"));
}

static void position(Buf *b, const Json *row) {
    Json *page = json_get(row, "page");
    if (page && page->t != J_NULL) { b_puts(b, "p."); json_repr(b, page); b_puts(b, " \xC2\xB7 "); }
    b_puts(b, "lines ");
    json_repr(b, json_need(row, "line_start"));
    b_puts(b, "\xE2\x80\x93");
    json_repr(b, json_need(row, "line_end"));
}

static int source_order(const Json *store, const Json *artifact) {   /* order.get(artifact_id, 99) */
    Json *sources = json_need(store, "sources");
    char *want = key_of(artifact);
    for (size_t i = 0; i < sources->len; i++) {
        char *id = key_of(json_need(sources->items[i], "id"));
        int same = !strcmp(id, want);
        free(id);
        if (same) { free(want); return (int)i; }
    }
    free(want);
    return 99;
}

/* fetch: the kept evidence of work/evidence.json and every passage of the hand-off (or one source). */
static Json *fetch(Memo *m, const char *work, const char *artifact, Json **refs_out) {
    make_dirs(work);
    char *path = join(work, "evidence.json");
    Json *store;
    if (is_file(path)) store = read_json(path);
    else {
        store = json_new(J_OBJ);
        json_set(store, "handoff_id", json_need(m->binding, "handoff_id"));
        json_set(store, "evidence", json_new(J_OBJ));
    }
    char *a = key_of(json_need(store, "handoff_id")), *b = key_of(json_need(m->binding, "handoff_id"));
    if (strcmp(a, b)) fail("work/evidence.json belongs to another hand-off; use a new --work folder");
    Json *first, *refs = pages(m, artifact, &first);
    json_set(store, "question", json_need(first, "question"));
    json_set(store, "sources", json_need(first, "sources"));
    Json *totals = json_get(store, "totals");
    if (!totals) { totals = json_new(J_OBJ); json_set(store, "totals", totals); }
    for (size_t i = 0; i < refs->len; i++) {
        Json *artifact_id = json_need(refs->items[i], "artifact_id");
        if (artifact_id->t != J_STR) continue;
        long long count = 0;
        for (size_t k = 0; k < refs->len; k++) {
            Json *other = json_need(refs->items[k], "artifact_id");
            if (other->t == J_STR && !strcmp(other->s, artifact_id->s)) count++;
        }
        json_set(totals, artifact_id->s, json_int(count));
    }
    *refs_out = refs;
    return store;
}

typedef struct { const Json *row; const char *alias; int order; double page, line; size_t at; } Row;

static int cmp_rows(const void *x, const void *y) {
    const Row *a = x, *b = y;
    if (a->order != b->order) return a->order < b->order ? -1 : 1;
    if (a->page != b->page) return a->page < b->page ? -1 : 1;
    if (a->line != b->line) return a->line < b->line ? -1 : 1;
    return a->at < b->at ? -1 : a->at > b->at;   /* sorted() is stable */
}

static void rstrip_text(Buf *b, const Json *text) {   /* row['text'].rstrip() */
    U32 t = json_text(text);
    size_t n = t.n;
    while (n && u_space(t.p[n - 1])) n--;
    u_to_utf8(b, t.p, n);
    free(t.p);
}

/* evidence_markdown */
static Buf evidence_markdown(const Json *store) {
    Buf b = {0};
    b_puts(&b, "# Evidence\n\nQuestion: ");
    json_repr(&b, json_need(store, "question"));
    b_puts(&b, "\n\nCite a passage as [E3: \"short exact quote\"]. Quotes must be copied from the passage text.\n");
    Json *evidence = json_need(store, "evidence");
    Row *rows = xmalloc((evidence->len + 1) * sizeof *rows);
    for (size_t i = 0; i < evidence->len; i++) {
        const Json *row = evidence->items[i];
        rows[i] = (Row){row, evidence->keys[i], source_order(store, json_need(row, "artifact_id")), page_or_zero(row),
                        json_number(json_need(row, "line_start")), i};
    }
    qsort(rows, evidence->len, sizeof *rows, cmp_rows);
    char *current = NULL;
    for (size_t i = 0; i < evidence->len; i++) {
        const Json *row = rows[i].row;
        char *artifact = key_of(json_need(row, "artifact_id"));
        if (!current || strcmp(artifact, current)) {
            current = artifact;
            Json *title = json_get(row, "title");
            b_puts(&b, "\n## ");
            json_repr(&b, json_truthy(title) ? title : json_need(row, "artifact_id"));
            b_puts(&b, "\n");
        }
        b_printf(&b, "\n### %s \xC2\xB7 ", rows[i].alias);
        position(&b, row);
        b_puts(&b, "\n\n");
        rstrip_text(&b, json_need(row, "text"));
        b_puts(&b, "\n");
    }
    free(rows);
    return b;
}

/* keep: add refs to the kept evidence (aliases E1, E2 ... kept across calls), write
   work/evidence.json and work/evidence.md, and return the aliases of refs. */
static char **keep(Json *store, Json **refs, size_t count, const char *work) {
    static const char *KEYS[] = {"id", "content_hash", "artifact_id", "title", "page", "line_start", "line_end", "text"};
    Json *evidence = json_need(store, "evidence");
    char **aliases = xmalloc((count + 1) * sizeof *aliases);
    for (size_t i = 0; i < count; i++) {
        char *id = key_of(json_need(refs[i], "id")), *alias = NULL;
        for (size_t k = 0; k < evidence->len && !alias; k++) {
            char *other = key_of(json_need(evidence->items[k], "id"));
            if (!strcmp(other, id)) alias = evidence->keys[k];
            free(other);
        }
        Buf name = {0};
        if (alias) b_puts(&name, alias); else b_printf(&name, "E%zu", evidence->len + 1);
        Json *row = json_new(J_OBJ);
        for (size_t k = 0; k < sizeof KEYS / sizeof *KEYS; k++) {
            Json *v = json_get(refs[i], KEYS[k]);
            json_set(row, KEYS[k], v ? v : json_new(J_NULL));
        }
        json_set(evidence, name.p, row);
        aliases[i] = b_cstr(&name);
        free(id);
    }
    Buf text = {0};
    json_dump(&text, store, 1);
    char *json_path = join(work, "evidence.json"), *md_path = join(work, "evidence.md");
    write_text(json_path, &text);
    Buf md = evidence_markdown(store);
    write_text(md_path, &md);
    return aliases;
}

/* read_summary */
static void read_summary(Buf *out, const Json *store) {
    Json *sources = json_need(store, "sources"), *evidence = json_need(store, "evidence");
    for (size_t s = 0; s < sources->len; s++) {
        Json *source = sources->items[s], *title = json_get(source, "title"), *chunks = json_get(source, "chunks");
        char *id = key_of(json_need(source, "id"));
        size_t read = 0;
        for (size_t i = 0; i < evidence->len; i++) {
            char *artifact = key_of(json_need(evidence->items[i], "artifact_id"));
            if (!strcmp(artifact, id)) {
                char *row_id = key_of(json_need(evidence->items[i], "id"));
                int seen = 0;
                for (size_t k = 0; k < i && !seen; k++) {
                    char *a2 = key_of(json_need(evidence->items[k], "artifact_id")), *r2 = key_of(json_need(evidence->items[k], "id"));
                    seen = !strcmp(a2, id) && !strcmp(r2, row_id);
                    free(a2); free(r2);
                }
                if (!seen) read++;
                free(row_id);
            }
            free(artifact);
        }
        b_putc(out, '\n');
        json_repr(out, json_truthy(title) ? title : json_need(source, "id"));
        b_printf(out, ": %zu of ", read);
        if (json_truthy(chunks)) json_repr(out, chunks); else b_putc(out, '?');
        b_puts(out, " chunks read");
        free(id);
    }
}

static int caption(const U32 *line) {   /* CAPTION.match: ^\s*(?:Table|Tab\.|Figure|Fig\.|表|图)\s*[\dA-Z] (any case) */
    static const char *heads[] = {"table", "tab.", "figure", "fig."};
    size_t i = 0;
    while (i < line->n && u_space(line->p[i])) i++;
    size_t ends[6], count = 0;
    for (int h = 0; h < 4; h++) {
        size_t k = strlen(heads[h]), j = 0;
        while (j < k && i + j < line->n) {
            uint32_t c = line->p[i + j];
            if (c >= 'A' && c <= 'Z') c += 32;
            if (c != (unsigned char)heads[h][j]) break;
            j++;
        }
        if (j == k) ends[count++] = i + k;
    }
    if (i < line->n && (line->p[i] == 0x8868 || line->p[i] == 0x56FE)) ends[count++] = i + 1;
    for (size_t e = 0; e < count; e++) {
        size_t j = ends[e];
        while (j < line->n && u_space(line->p[j])) j++;
        if (j < line->n) {
            uint32_t c = line->p[j];
            if (u_digit(c) >= 0 || (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z')) return 1;
        }
    }
    return 0;
}

typedef struct { int distinct, order; double page, line; size_t at; Json *ref; U32 *lines, *evened; size_t nlines; } Hit;

static int cmp_hits(const void *x, const void *y) {
    const Hit *a = x, *b = y;
    if (a->distinct != b->distinct) return a->distinct > b->distinct ? -1 : 1;   /* most terms first */
    if (a->order != b->order) return a->order < b->order ? -1 : 1;
    if (a->page != b->page) return a->page < b->page ? -1 : 1;
    if (a->line != b->line) return a->line < b->line ? -1 : 1;
    return a->at < b->at ? -1 : a->at > b->at;
}

static U32 lowered_even(const U32 *t) {
    U32 e = even(t, 0, NULL), low = u_lower(&e);
    free(e.p);
    return low;
}

/* find_passages: keep the passages holding any term (most terms first, then in reading order) and
   print each passage's alias, source, page and its matching lines, with table and figure titles. */
static void find_passages(Memo *m, const char *work, char **terms, size_t nterms, const char *artifact, Buf *out) {
    const size_t shown = 12;
    Json *refs, *store = fetch(m, work, artifact, &refs);
    U32 *wanted = xmalloc((nterms + 1) * sizeof *wanted);
    size_t nwanted = 0;
    for (size_t t = 0; t < nterms; t++) {
        U32 term = u_from_cstr(terms[t]), stripped = u_strip(&term);
        if (stripped.n) {
            U32 e = even(&term, 0, NULL), s = u_strip(&e);
            wanted[nwanted++] = u_lower(&s);
            free(e.p); free(s.p);
        }
        free(term.p); free(stripped.p);
    }
    Hit *hits = xmalloc((refs->len + 1) * sizeof *hits);
    size_t nhits = 0;
    for (size_t r = 0; r < refs->len; r++) {
        Json *ref = refs->items[r];
        U32 text = json_text(json_need(ref, "text")), *lines, *evened;
        size_t nlines;
        u_splitlines(&text, &lines, &nlines);
        evened = xmalloc((nlines + 1) * sizeof *evened);
        U32 whole = {0};
        for (size_t l = 0; l < nlines; l++) {
            evened[l] = lowered_even(&lines[l]);
            if (l) u_push(&whole, ' ');
            u_append(&whole, evened[l].p, evened[l].n);
        }
        int distinct = 0;
        for (size_t t = 0; t < nwanted; t++) {
            if (u_find(&whole, &wanted[t], 0) < 0) continue;
            int repeat = 0;
            for (size_t s = 0; s < t; s++) if (!u_cmp(&wanted[s], &wanted[t])) repeat = 1;
            if (!repeat) distinct++;
        }
        if (distinct) {
            hits[nhits] = (Hit){distinct, source_order(store, json_need(ref, "artifact_id")), page_or_zero(ref),
                                json_number(json_need(ref, "line_start")), nhits, ref, lines, evened, nlines};
            nhits++;
        }
        free(whole.p); free(text.p);
    }
    qsort(hits, nhits, sizeof *hits, cmp_hits);
    size_t kept = nhits < shown ? nhits : shown;
    Json **chosen = xmalloc((kept + 1) * sizeof *chosen);
    for (size_t h = 0; h < kept; h++) chosen[h] = hits[h].ref;
    char **aliases = keep(store, chosen, kept, work);
    for (size_t h = 0; h < kept; h++) {
        Hit *hit = &hits[h];
        char *rows = calloc(hit->nlines + 1, 1);
        for (size_t l = 0; l < hit->nlines; l++) {
            int match = 0;
            for (size_t t = 0; t < nwanted && !match; t++) match = u_find(&hit->evened[l], &wanted[t], 0) >= 0;
            if (match) for (size_t k = l >= 2 ? l - 2 : 0; k < hit->nlines && k < l + 3; k++) rows[k] = 1;
            if (caption(&hit->lines[l])) rows[l] = 1;
        }
        Json *title = json_get(hit->ref, "title");
        b_printf(out, "%s\n%s \xC2\xB7 ", out->n ? "\n" : "", aliases[h]);
        json_repr(out, json_truthy(title) ? title : json_need(hit->ref, "artifact_id"));
        b_puts(out, " \xC2\xB7 ");
        position(out, hit->ref);
        for (size_t l = 0; l < hit->nlines; l++) {
            if (!rows[l]) continue;
            int match = 0;
            for (size_t t = 0; t < nwanted && !match; t++) match = u_find(&hit->evened[l], &wanted[t], 0) >= 0;
            b_puts(out, match ? "\n  > " : "\n    ");
            u_to_utf8(out, hit->lines[l].p, hit->lines[l].n);
        }
        free(rows);
    }
    if (nhits > shown)
        b_printf(out, "%s\n%zu more passages match; search with more specific terms to see them.", out->n ? "\n" : "", nhits - shown);
    if (!nhits)
        b_printf(out, "%sNo passage contains these terms. Try other words or the numbers you expect, or read the "
                      "extracted texts in materials/.", out->n ? "\n" : "");
}

/* --- answer ------------------------------------------------------------------------------------ */

typedef struct {
    Json *store, *cited, *notes;
    U32 *texts;          /* the text of each kept passage, in store['evidence'] order */
} Answer;

static Json *evidence_row(Answer *a, const char *alias, size_t *index) {
    Json *evidence = json_need(a->store, "evidence");
    for (size_t i = 0; i < evidence->len; i++) if (!strcmp(evidence->keys[i], alias)) { *index = i; return evidence->items[i]; }
    return NULL;
}

/* build_answer's resolve(alias, where, quote) */
static Resolved resolve_support(void *ctx, const U32 *alias_u, const U32 *where, const U32 *quote) {
    (void)where;
    Answer *a = ctx;
    Resolved r = {0};
    char *alias = u_cstr(alias_u->p, alias_u->n);
    size_t index;
    Json *row = evidence_row(a, alias, &index), *evidence = json_need(a->store, "evidence");
    if (!row) {
        Buf b = {0};
        b_printf(&b, "%s has not been read: run `find` or `read` for its source first", alias);
        r.problem = b_cstr(&b);
        return r;
    }
    U32 exact;
    if (!locate(quote, &a->texts[index], &exact)) {
        size_t *elsewhere = xmalloc((evidence->len + 1) * sizeof *elsewhere), n = 0;
        for (size_t i = 0; i < evidence->len; i++) {
            U32 found;
            if (i != index && locate(quote, &a->texts[i], &found)) { elsewhere[n++] = i; free(found.p); }
        }
        if (n == 1) {
            Buf note = {0};
            b_printf(&note, "%s \xE2\x86\x92 %s: \"", alias, evidence->keys[elsewhere[0]]);
            u_to_utf8(&note, quote->p, quote->n < 60 ? quote->n : 60);
            b_printf(&note, "\" is in %s, which is cited instead", evidence->keys[elsewhere[0]]);
            json_push(a->notes, json_strn(note.p, note.n));
            index = elsewhere[0];
            row = evidence->items[index];
            locate(quote, &a->texts[index], &exact);
        } else {
            Buf b = {0};
            b_printf(&b, "%s: the quote is not in the passage: \"", alias);
            u_to_utf8(&b, quote->p, quote->n < 120 ? quote->n : 120);
            b_puts(&b, "\"; ");
            if (n) {
                b_puts(&b, "it is in ");
                for (size_t k = 0; k < n; k++) { if (k) b_puts(&b, ", "); b_puts(&b, evidence->keys[elsewhere[k]]); }
                b_puts(&b, ": cite one of them");
            } else {
                U32 nearest = closest(quote, &a->texts[index]);
                b_printf(&b, "the closest text in %s is: \"", alias);
                u_to_utf8(&b, nearest.p, nearest.n);
                b_putc(&b, '"');
            }
            r.problem = b_cstr(&b);
            return r;
        }
    }
    r.support = json_new(J_OBJ);
    json_set(r.support, "evidence_id", json_need(row, "id"));
    json_set(r.support, "content_hash", json_need(row, "content_hash"));
    json_set(r.support, "quote", json_u32(&exact));
    r.passage = &a->texts[index];
    return r;
}

/* build_answer's render(supports): [n], n numbering the passages in first-use order */
static void render_marks(void *ctx, Json *supports, U32 *out) {
    Answer *a = ctx;
    Json *ids = json_new(J_ARR);
    for (size_t i = 0; i < supports->len; i++) {
        char *id = key_of(json_need(supports->items[i], "evidence_id"));
        int seen = 0;
        for (size_t k = 0; k < ids->len; k++) if (!strcmp(ids->items[k]->s, id)) seen = 1;
        if (!seen) json_push(ids, json_str(id));
        free(id);
    }
    for (size_t i = 0; i < ids->len; i++) {
        size_t at = a->cited->len;
        for (size_t k = 0; k < a->cited->len; k++) if (!strcmp(a->cited->items[k]->s, ids->items[i]->s)) at = k;
        if (at == a->cited->len) json_push(a->cited, ids->items[i]);
        char mark[32];
        snprintf(mark, sizeof mark, "[%zu]", at + 1);
        u_append_utf8(out, mark);
    }
}

/* build_answer's where_value(value): the passages reporting value */
static void where_value(void *ctx, double value, Buf *names) {
    Answer *a = ctx;
    Json *evidence = json_need(a->store, "evidence");
    for (size_t i = 0; i < evidence->len; i++) {
        Values v = numbers(&a->texts[i]);
        if (values_has(&v, value)) { if (names->n) b_puts(names, ", "); b_puts(names, evidence->keys[i]); }
        free(v.v);
    }
}

static void replace_all(U32 *text, const U32 *what) {   /* text.replace(what, ' ') */
    if (!what->n) return;
    U32 out = {0};
    size_t i = 0;
    while (i < text->n) {
        if (i + what->n <= text->n && !memcmp(text->p + i, what->p, what->n * sizeof *what->p)) { u_push(&out, ' '); i += what->n; }
        else u_push(&out, text->p[i++]);
    }
    free(text->p);
    *text = out;
}

/* build_answer: the memo-answer.json of a draft; every problem is reported at once. */
static Json *build_answer(Json *store, const U32 *draft, const char *gaps, const char *request_id, Json *notes) {
    Json *evidence = json_need(store, "evidence");
    Answer a = {store, json_new(J_ARR), notes, xmalloc((evidence->len + 1) * sizeof(U32))};
    for (size_t i = 0; i < evidence->len; i++) a.texts[i] = json_text(json_need(evidence->items[i], "text"));
    U32 answer;
    Json *claims = json_new(J_ARR), *problems = json_new(J_ARR);
    apply_draft(draft, resolve_support, render_marks, where_value, &a, &answer, claims, problems);
    collapse_marks(&answer);
    if (!problems->len) {   /* uncited_values */
        U32 text = u_slice(&answer, 0, answer.n);
        blank_marks(&text);
        for (size_t c = 0; c < claims->len; c++) {
            U32 claim = json_text(json_need(claims->items[c], "text"));
            replace_all(&text, &claim);
            free(claim.p);
        }
        Json *question = json_get(store, "question");
        U32 q = {0};
        if (json_truthy(question)) q = json_text(question);
        Values known = numbers(&q), stated = stated_values(&text), loose = {0};
        for (size_t i = 0; i < evidence->len; i++) {
            Values v = numbers(&a.texts[i]);
            for (size_t k = 0; k < v.n; k++) values_add(&known, v.v[k]);
            free(v.v);
        }
        for (size_t k = 0; k < stated.n; k++) if (!values_has(&known, stated.v[k])) values_add(&loose, stated.v[k]);
        values_sort(&loose);
        if (loose.n) {
            Buf b = {0};
            for (size_t k = 0; k < loose.n; k++) { if (k) b_puts(&b, ", "); fmt_g(&b, loose.v[k]); }
            b_puts(&b, ": in the answer without a cited passage. Put each value in a sentence with its passage and quote "
                       "(find it with the `find` command), mark a value you computed as [inference ...], or delete it: a "
                       "value from outside the material cannot be cited here.");
            json_push(problems, json_strn(b.p, b.n));
        }
    }
    if (problems->len) {
        Buf b = {0};
        b_puts(&b, "The draft needs changes:");
        for (size_t k = 0; k < problems->len; k++) { b_puts(&b, "\n- "); b_put(&b, problems->items[k]->s, problems->items[k]->n); }
        fail_buf(&b);
    }
    Json *coverage = json_new(J_ARR), *sources = json_need(store, "sources"), *totals = json_get(store, "totals");
    for (size_t s = 0; s < sources->len; s++) {
        Json *source = sources->items[s];
        char *id = key_of(json_need(source, "id"));
        Row *read = xmalloc((evidence->len + 1) * sizeof *read);
        size_t nread = 0;
        for (size_t i = 0; i < evidence->len; i++) {
            char *artifact = key_of(json_need(evidence->items[i], "artifact_id"));
            if (!strcmp(artifact, id)) {
                Json *row = evidence->items[i];
                read[nread++] = (Row){row, evidence->keys[i], 0, page_or_zero(row), json_number(json_need(row, "line_start")), i};
            }
            free(artifact);
        }
        qsort(read, nread, sizeof *read, cmp_rows);
        Json *total_v = totals && json_need(source, "id")->t == J_STR ? json_get(totals, json_need(source, "id")->s) : NULL;
        double total = json_number(total_v);
        const char *status = !nread ? "unread" : total && (double)nread >= total ? "read" : "partial";
        Buf note = {0};
        if (!strcmp(status, "unread")) b_puts(&note, "Not read.");
        else if (!strcmp(status, "partial")) {
            b_printf(&note, "%zu of ", nread);
            if (total_v) json_repr(&note, total_v); else b_putc(&note, '0');
            b_puts(&note, " chunks read");
            Json *first_page = json_get(read[0].row, "page");
            if (first_page && first_page->t != J_NULL) {
                b_puts(&note, " (pages ");
                json_repr(&note, first_page);
                b_puts(&note, "\xE2\x80\x93");
                json_repr(&note, json_get(read[nread - 1].row, "page"));
                b_putc(&note, ')');
            }
            b_putc(&note, '.');
        }
        if (strcmp(status, "read") && gaps && *gaps) { b_putc(&note, ' '); b_puts(&note, gaps); }
        U32 nu = u_from_utf8(note.p ? note.p : "", note.n), stripped = u_strip(&nu);
        Json *entry = json_new(J_OBJ);
        json_set(entry, "artifact_id", json_need(source, "id"));
        json_set(entry, "status", json_str(status));
        json_set(entry, "gaps", json_u32(&stripped));
        json_push(coverage, entry);
        free(read); free(id); free(note.p); free(nu.p); free(stripped.p);
    }
    Json *answer_json = json_u32(&answer);
    Json *rid;
    if (request_id && *request_id) rid = json_str(request_id);
    else {
        Json *basis = json_new(J_ARR);
        json_push(basis, json_need(store, "handoff_id"));
        json_push(basis, answer_json);
        json_push(basis, claims);
        Buf text = {0};
        json_dump(&text, basis, -1);
        char hex[65];
        sha256_hex((const unsigned char *)(text.p ? text.p : ""), text.n, hex);
        Buf r = {0};
        b_puts(&r, "skill-");
        b_put(&r, hex, 24);
        rid = json_strn(r.p, r.n);
    }
    Json *built = json_new(J_OBJ);
    json_set(built, "answer", answer_json);
    json_set(built, "claims", claims);
    json_set(built, "coverage", coverage);
    json_set(built, "request_id", rid);
    return built;
}

/* return_answer: returns NULL when Memorive is not reached (reason in not_reached). */
static Json *return_answer(Memo *m, Json *result, Buf *not_reached) {
    Json *schema = json_get(result, "schema");
    if (!schema || schema->t != J_STR || strcmp(schema->s, ANSWER_SCHEMA)) fail("Unsupported answer file");
    Json *params = json_new(J_OBJ);
    for (size_t i = 0; i < result->len; i++) if (strcmp(result->keys[i], "schema")) json_set(params, result->keys[i], result->items[i]);
    for (size_t i = 0; i < m->binding->len; i++) {
        Json *v = json_get(params, m->binding->keys[i]);
        char *x = v ? key_of(v) : _strdup("null"), *y = key_of(m->binding->items[i]);
        if (strcmp(x, y)) fail("Answer belongs to another handoff");
    }
    if (!require_or_not_reached(m, "memo.skill_answer", not_reached)) return NULL;
    return memo_call(m, "memo.skill_answer", params, not_reached);
}

/* --- command line ------------------------------------------------------------------------------ */

typedef struct { const char *name; int takes_value; const char *value; int given; } Option;

static void usage_error(const char *message) {
    Buf b = {0};
    b_puts(&b, "usage: memo_bridge.exe [--transport TRANSPORT] {find,read,answer,return,context} ...\n");
    b_printf(&b, "memo_bridge.exe: error: %s", message);
    fail_buf(&b);
}

/* The command's options and positionals (argparse: an option may come anywhere after the command). */
static char **parse(int argc, char **argv, int from, Option *options, size_t noptions, size_t *npositional) {
    char **positional = xmalloc(((size_t)argc + 1) * sizeof *positional);
    size_t n = 0;
    for (int i = from; i < argc; i++) {
        const char *arg = argv[i];
        if (arg[0] == '-' && arg[1] == '-' && arg[2]) {
            const char *eq = strchr(arg, '=');
            size_t len = eq ? (size_t)(eq - arg) : strlen(arg);
            Option *o = NULL;
            for (size_t k = 0; k < noptions; k++) if (strlen(options[k].name) == len && !strncmp(options[k].name, arg, len)) o = &options[k];
            if (!o) { Buf b = {0}; b_printf(&b, "unrecognized arguments: %s", arg); usage_error(b.p); }
            o->given = 1;
            if (o->takes_value) {
                if (eq) o->value = eq + 1;
                else if (i + 1 < argc) o->value = argv[++i];
                else { Buf b = {0}; b_printf(&b, "argument %s: expected one argument", o->name); usage_error(b.p); }
            }
        } else positional[n++] = argv[i];
    }
    *npositional = n;
    return positional;
}

static long long int_option(const Option *o, long long fallback) {
    if (!o->given) return fallback;
    char *end;
    long long v = strtoll(o->value, &end, 10);
    if (!*o->value || *end) { Buf b = {0}; b_printf(&b, "argument %s: invalid int value: '%s'", o->name, o->value); usage_error(b.p); }
    return v;
}

static Option *opt(Option *options, size_t n, const char *name) {
    for (size_t k = 0; k < n; k++) if (!strcmp(options[k].name, name)) return &options[k];
    return NULL;
}

static void page_span(const char *text, double *low, double *high) {   /* page_range */
    char *copy = _strdup(text), *dash = strchr(copy, '-');
    if (dash) *dash = 0;
    char *end;
    long a = strtol(copy, &end, 10);
    while (*end == ' ') end++;
    if (!*copy || *end) { Buf b = {0}; b_printf(&b, "invalid literal for int() with base 10: '%s'", copy); fail_buf(&b); }
    long z = a;
    if (dash && dash[1]) {
        z = strtol(dash + 1, &end, 10);
        while (*end == ' ') end++;
        if (*end) { Buf b = {0}; b_printf(&b, "invalid literal for int() with base 10: '%s'", dash + 1); fail_buf(&b); }
    }
    *low = (double)a;
    *high = (double)z;
}

int wmain(int argc, wchar_t **wargv) {
    char **argv = xmalloc(((size_t)argc + 1) * sizeof *argv);
    for (int i = 0; i < argc; i++) argv[i] = narrow(wargv[i]);
    int at = 1;
    const char *transport = NULL;
    while (at < argc && argv[at][0] == '-') {
        if (!strcmp(argv[at], "-h") || !strcmp(argv[at], "--help")) { Buf b = {0}; b_puts(&b, HELP); print_buf(&b); return 0; }
        if (!strcmp(argv[at], "--transport") && at + 1 < argc) { transport = argv[at + 1]; at += 2; }
        else if (!strncmp(argv[at], "--transport=", 12)) { transport = argv[at] + 12; at++; }
        else { Buf b = {0}; b_printf(&b, "unrecognized arguments: %s", argv[at]); usage_error(b.p); }
    }
    if (at >= argc) usage_error("the following arguments are required: action");
    const char *action = argv[at++];
    for (int i = at; i < argc; i++)
        if (!strcmp(argv[i], "-h") || !strcmp(argv[i], "--help")) { Buf b = {0}; b_puts(&b, HELP); print_buf(&b); return 0; }
    if (!strcmp(action, "bind"))
        fail("bind needs Python: run it with python <skill>/scripts/memo_bridge.py or Memorive.exe --memo-bridge");
    Option options[] = {{"--artifact", 1}, {"--work", 1}, {"--pages", 1}, {"--draft", 1}, {"--gap", 1}, {"--request-id", 1},
                        {"--out", 1}, {"--return", 0}, {"--result", 1}, {"--cursor", 1}, {"--limit", 1}};
    const char *allowed;
    if (!strcmp(action, "find")) allowed = "--artifact --work";
    else if (!strcmp(action, "read")) allowed = "--artifact --pages --work";
    else if (!strcmp(action, "answer")) allowed = "--draft --gap --request-id --work --out --return";
    else if (!strcmp(action, "return")) allowed = "--result";
    else if (!strcmp(action, "context")) allowed = "--artifact --cursor --limit";
    else {
        Buf b = {0};
        b_printf(&b, "argument action: invalid choice: '%s' (choose from 'find', 'read', 'answer', 'return', 'context', 'bind')", action);
        usage_error(b.p);
        return 2;
    }
    Option mine[16];
    size_t nmine = 0;
    for (size_t k = 0; k < sizeof options / sizeof *options; k++) {
        const char *hit = strstr(allowed, options[k].name);
        size_t len = strlen(options[k].name);
        if (hit && (hit[len] == ' ' || hit[len] == 0)) mine[nmine++] = options[k];
    }
    size_t npositional;
    char **positional = parse(argc, argv, at, mine, nmine, &npositional);
    if (!npositional) usage_error("the following arguments are required: handoff");
    if (!strcmp(action, "find") && npositional < 2) usage_error("the following arguments are required: terms");
    if (strcmp(action, "find") && npositional > 1) {
        Buf b = {0};
        b_printf(&b, "unrecognized arguments: %s", positional[1]);
        usage_error(b.p);
    }
    if (!strcmp(action, "answer") && !opt(mine, nmine, "--draft")->given) usage_error("the following arguments are required: --draft");
    if (!strcmp(action, "return") && !opt(mine, nmine, "--result")->given) usage_error("the following arguments are required: --result");

    Memo m = {0};
    m.handoff = resolve(positional[0]);
    m.pack = read_json(m.handoff);
    char *transport_path = transport ? path_text(transport) : join(parent(m.handoff), "transport.json");
    m.transport = read_json(transport_path);
    m.binding = json_new(J_OBJ);
    json_set(m.binding, "project", json_need(m.pack, "project"));
    json_set(m.binding, "handoff_id", json_need(m.pack, "id"));
    json_set(m.binding, "handoff_hash", json_need(m.pack, "content_hash"));
    Option *work_opt = opt(mine, nmine, "--work");
    char *work = work_opt && work_opt->given ? path_text(work_opt->value) : join(parent(m.handoff), "work");
    Option *artifact_opt = opt(mine, nmine, "--artifact");
    const char *artifact = artifact_opt && artifact_opt->given ? artifact_opt->value : NULL;
    char *evidence_md = join(work, "evidence.md");
    Buf out = {0};

    if (!strcmp(action, "find")) {
        find_passages(&m, work, positional + 1, npositional - 1, artifact, &out);
        b_printf(&out, "\n\nThese passages are kept in %s; cite them as [E1: \"quote\"].", evidence_md);
        print_buf(&out);
    } else if (!strcmp(action, "read")) {
        Option *pages_opt = opt(mine, nmine, "--pages");
        Json *refs, *store = fetch(&m, work, artifact, &refs);
        double low = 0, high = 0;
        int span = pages_opt->given && *pages_opt->value;
        if (span) page_span(pages_opt->value, &low, &high);
        Json **chosen = xmalloc((refs->len + 1) * sizeof *chosen);
        size_t n = 0;
        for (size_t i = 0; i < refs->len; i++) {
            Json *page = json_get(refs->items[i], "page");
            double p = json_number(page);
            if (!span || !page || page->t == J_NULL || (low <= p && p <= high)) chosen[n++] = refs->items[i];
        }
        keep(store, chosen, n, work);
        size_t count = json_need(store, "evidence")->len;
        b_printf(&out, "Read into %s (%zu passages, E1\xE2\x80\x93" "E%zu).", evidence_md, count, count);
        read_summary(&out, store);
        print_buf(&out);
    } else if (!strcmp(action, "answer")) {
        char *store_path = join(work, "evidence.json");
        if (!is_file(store_path)) fail("No passages kept yet: run `find` or `read` first");
        Json *store = read_json(store_path), *notes = json_new(J_ARR);
        Buf draft_text = read_text(path_text(opt(mine, nmine, "--draft")->value));
        U32 draft = u_from_utf8(draft_text.p, draft_text.n);
        Option *gap = opt(mine, nmine, "--gap"), *rid = opt(mine, nmine, "--request-id"), *out_opt = opt(mine, nmine, "--out");
        Json *built = build_answer(store, &draft, gap->given ? gap->value : NULL, rid->given ? rid->value : NULL, notes);
        Json *result = json_new(J_OBJ);
        for (size_t i = 0; i < m.binding->len; i++) json_set(result, m.binding->keys[i], m.binding->items[i]);
        json_set(result, "schema", json_str(ANSWER_SCHEMA));
        for (size_t i = 0; i < built->len; i++) json_set(result, built->keys[i], built->items[i]);
        char *answer_path = out_opt->given ? path_text(out_opt->value) : join(parent(m.handoff), "memo-answer.json");
        Buf saved = {0};
        json_dump(&saved, result, 2);
        write_text(answer_path, &saved);
        Json *report = json_new(J_OBJ), *claims = json_need(result, "claims"), *coverage = json_new(J_OBJ);
        json_set(report, "status", json_str("SAVED"));
        Buf next = {0};
        b_printf(&next, "The answer is checked and saved in %s. Return it with the `return` command, or run `answer` again with --return.", answer_path);
        json_set(report, "next", json_strn(next.p, next.n));
        json_set(report, "saved", json_str(answer_path));
        json_set(report, "claims", json_int((long long)claims->len));
        Json *ids = json_new(J_OBJ);
        for (size_t c = 0; c < claims->len; c++) {
            Json *supports = json_need(claims->items[c], "supports");
            for (size_t s = 0; s < supports->len; s++) {
                char *id = key_of(json_need(supports->items[s], "evidence_id"));
                json_set(ids, id, json_new(J_TRUE));
                free(id);
            }
        }
        json_set(report, "citations", json_int((long long)ids->len));
        Json *covered = json_need(result, "coverage");
        for (size_t c = 0; c < covered->len; c++) {
            Json *artifact_id = json_need(covered->items[c], "artifact_id");
            char *k = artifact_id->t == J_STR ? artifact_id->s : key_of(artifact_id);
            json_set(coverage, k, json_need(covered->items[c], "status"));
        }
        json_set(report, "coverage", coverage);
        if (notes->len) json_set(report, "corrected", notes);
        if (opt(mine, nmine, "--return")->given) {
            Buf not_reached = {0};
            Json *copy = json_new(J_OBJ);
            for (size_t i = 0; i < result->len; i++) json_set(copy, result->keys[i], result->items[i]);
            Json *returned = return_answer(&m, copy, &not_reached);
            if (returned) {
                json_set(report, "returned", returned);
                json_set(report, "status", json_str("RETURNED"));
                json_set(report, "next", json_str("The answer is in the Memorive conversation with its citations. Give the user the "
                                                  "answer in brief and say it was returned; nothing else is needed."));
            } else {
                Json *r = json_new(J_OBJ);
                json_set(r, "status", json_str("NOT_RETURNED"));
                json_set(r, "reason", json_strn(not_reached.p, not_reached.n));
                json_set(report, "returned", r);
                json_set(report, "status", json_str("NOT_RETURNED"));
                Buf n2 = {0};
                b_printf(&n2, "The answer is complete and saved in %s, but Memorive could not be reached. Give the user the answer, "
                              "and tell them they can approve this command and run it again, or add that file with the attachment "
                              "button of the Memorive conversation.", answer_path);
                json_set(report, "next", json_strn(n2.p, n2.n));
            }
        }
        json_dump(&out, report, 1);
        print_buf(&out);
    } else if (!strcmp(action, "return")) {
        Buf not_reached = {0};
        Json *returned = return_answer(&m, read_json(path_text(opt(mine, nmine, "--result")->value)), &not_reached);
        if (!returned) fail_buf(&not_reached);
        json_dump(&out, returned, -1);
        print_buf(&out);
    } else {
        require(&m, "memo.skill_context", NULL);
        Json *params = binding_params(&m);
        json_set(params, "cursor", json_int(int_option(opt(mine, nmine, "--cursor"), 0)));
        json_set(params, "limit", json_int(int_option(opt(mine, nmine, "--limit"), 12)));
        if (artifact) json_set(params, "artifact_id", json_str(artifact));
        json_dump(&out, call_or_fail(&m, "memo.skill_context", params, NULL), -1);
        print_buf(&out);
    }
    return 0;
}
