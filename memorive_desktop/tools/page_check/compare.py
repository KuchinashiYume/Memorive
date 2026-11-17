"""Compare two page-check runs and say what differs.

Usage:
    python compare.py <run A out dir> <run B out dir> [--map pairs.json] [--report file.md] [--examples N]

A is the page before a change, B the page after. Elements are matched by their label (tag, id, classes, key
attribute, own text, and an ordinal among equal labels). For matched elements the tool compares text, the
recorded attributes, position and size (more than half a pixel), and every computed property of the element and
of its ::before and ::after. Equal differences are grouped: one line per (what changed, old value, new value),
with the number of elements and a few examples. Which element has the focus is compared per scene.

--map takes a JSON list of [old, new] text pairs. They are applied to everything A recorded before comparing,
so an intended change (for example one colour replaced by another) does not show up, and everything else does.

Normalised in both runs before comparing: file URLs and Windows paths of the checked folder (ROOT), paths in the
run's output folder and throw-away folder (RUN), clock times and dates (<time>), and in a scene whose name ends
in "+armed" (a countdown is running) every digit of a text.

Exit status 0 when nothing differs, 1 otherwise.
"""
import argparse
import collections
import gzip
import json
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT_URL = re.compile(r"file:///[^\"')\s]*?/memorive_desktop/")
# A Windows path to the checked folder, as JSON writes it (G:\\...\\memorive_desktop), shown in settings fields.
DESKTOP_PATH = re.compile(r'[A-Za-z]:(?:\\\\[^"\\]+?)*?\\\\memorive_desktop(?=\\\\|")')
# Clock times and dates the page shows (a sync time, a record's date) differ between any two runs.
CLOCK = re.compile(r"(?<![0-9])20[0-9]{2}[/-][0-9]{1,2}[/-][0-9]{1,2}(?![0-9])"
                   r"|(?<![0-9:.])[0-9]{1,2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]+)?)?(?![0-9])")
# The texts of a scene with a running countdown, as JSON writes them: "text": "..." and the attribute values.
COUNTDOWN_TEXT = re.compile(r'("(?:text|aria-label|title|\.value)": ")((?:[^"\\]|\\.)*)"')


def run_folders(run_dir):
    """The run's output folder and its throw-away folder (run.json names it), as JSON writes them."""
    folders = [str(Path(run_dir).resolve())]
    run = Path(run_dir) / "run.json"
    if run.is_file():
        sandbox = json.loads(run.read_text(encoding="utf-8")).get("sandbox")
        if sandbox:
            folders += [sandbox, str(Path(sandbox).resolve())]
    return [json.dumps(folder, ensure_ascii=False)[1:-1] for folder in folders]


def normalise(text, run_dir, pairs=(), countdown=False):
    for own in run_folders(run_dir):
        text = text.replace(own, "RUN")
    text = CLOCK.sub("<time>", ROOT_URL.sub("ROOT/", DESKTOP_PATH.sub("ROOT", text)))
    if countdown:
        text = COUNTDOWN_TEXT.sub(lambda hit: hit.group(1) + re.sub(r"[0-9]", "#", hit.group(2)) + '"', text)
    for old, new in pairs:
        text = text.replace(old, new)
    return text


def load_scene(path, run_dir, pairs=()):
    """viewport, focus, and the scene's rows by label; each style part is kept as (text, dict) so equal styles
    compare as text."""
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        data = json.loads(normalise(handle.read(), run_dir, pairs, countdown=path.name.endswith("+armed.json.gz")))
    styles = [(json.dumps(style, sort_keys=True), style) for style in data["styles"]]
    rows = {}
    for row in data["rows"]:
        row = dict(row)
        row["style"] = (styles[row["style"]], row.pop("sized"))
        for pseudo in ("::before", "::after"):
            if pseudo in row:
                index, sized = row[pseudo]
                row[pseudo] = (styles[index], sized)
        rows[row["label"]] = row
    return data["viewport"], data.get("focus"), rows


def compare_scene(job):
    """Lines about elements and focus, and grouped differences, for one scene present in both runs."""
    file_name, a, b, pairs, examples = job
    scene = file_name[:-len(".json.gz")].replace("__", "/")
    viewport_a, focus_a, rows_a = load_scene(Path(a) / "scenes" / file_name, a, pairs)
    viewport_b, focus_b, rows_b = load_scene(Path(b) / "scenes" / file_name, b)
    lines, groups = [], collections.defaultdict(list)
    if focus_a != focus_b:
        lines.append(f"- {scene}: focus {focus_a} -> {focus_b}")
    if viewport_a != viewport_b:
        lines.append(f"- {scene}: window {viewport_a} -> {viewport_b}")
    only_a, only_b = sorted(rows_a.keys() - rows_b.keys()), sorted(rows_b.keys() - rows_a.keys())
    if only_a or only_b:
        lines.append(f"- {scene}: {len(only_a)} elements only in A, {len(only_b)} only in B")
        lines += [f"    A: {label}" for label in only_a[:examples * 4]]
        lines += [f"    B: {label}" for label in only_b[:examples * 4]]
    for label in rows_a.keys() & rows_b.keys():
        x, y = rows_a[label], rows_b[label]
        where = f"{scene} | {label}"
        if x.get("text") != y.get("text"):
            groups[("text", x.get("text"), y.get("text"))].append(where)
        for name in (x.get("attrs") or {}).keys() | (y.get("attrs") or {}).keys():
            old, new = (x.get("attrs") or {}).get(name), (y.get("attrs") or {}).get(name)
            if old != new:
                groups[("attr " + name, old, new)].append(where)
        if any(abs(p - q) > 0.5 for p, q in zip(x["rect"], y["rect"])):
            groups[("position/size", None, None)].append(f"{where}  {x['rect']} -> {y['rect']}")
        for part in ("style", "::before", "::after"):
            px, py = x.get(part), y.get(part)
            if px is None or py is None:
                if (px is None) != (py is None):
                    groups[(part + " present", px is not None, py is not None)].append(where)
                continue
            (text_x, style_x), sized_x = px
            (text_y, style_y), sized_y = py
            if text_x == text_y and sized_x == sized_y:
                continue
            sx, sy = {**style_x, **sized_x}, {**style_y, **sized_y}
            for prop in sx.keys() | sy.keys():
                if sx.get(prop) != sy.get(prop):
                    key = ("" if part == "style" else part + " ") + prop
                    groups[(key, sx.get(prop), sy.get(prop))].append(where)
    return lines, dict(groups)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("a")
    parser.add_argument("b")
    parser.add_argument("--map")
    parser.add_argument("--report")
    parser.add_argument("--examples", type=int, default=3)
    args = parser.parse_args()
    a, b = Path(args.a), Path(args.b)
    pairs = json.loads(Path(args.map).read_text(encoding="utf-8")) if args.map else []
    lines, differs = [], False

    run_a = json.loads((a / "run.json").read_text(encoding="utf-8"))
    run_b = json.loads((b / "run.json").read_text(encoding="utf-8"))
    for name, run in (("A", run_a), ("B", run_b)):
        if run.get("status") != "DONE":
            lines.append(f"run {name} did not finish: {run.get('status')}")
            differs = True
    errors_a, errors_b = run_a.get("errors") or [], run_b.get("errors") or []
    if errors_a != errors_b:
        differs = True
        lines.append("## page errors")
        lines += [f"- only A: {e}" for e in errors_a if e not in errors_b]
        lines += [f"- only B: {e}" for e in errors_b if e not in errors_a]
    elif errors_a:
        lines.append(f"(both runs have the same {len(errors_a)} page errors)")

    globals_a = json.loads((a / "globals.json").read_text(encoding="utf-8"))
    globals_b = json.loads((b / "globals.json").read_text(encoding="utf-8"))
    if globals_a != globals_b:
        differs = True
        lines.append("## page globals")
        for name in sorted(globals_a.keys() | globals_b.keys()):
            if globals_a.get(name) != globals_b.get(name):
                lines.append(f"- {name}: {globals_a.get(name)} -> {globals_b.get(name)}")

    with gzip.open(a / "rules.json.gz", "rt", encoding="utf-8") as handle:
        rules_a = collections.Counter(json.loads(normalise(handle.read(), a, pairs)))
    with gzip.open(b / "rules.json.gz", "rt", encoding="utf-8") as handle:
        rules_b = collections.Counter(json.loads(normalise(handle.read(), b)))
    rules_gone, rules_new = rules_a - rules_b, rules_b - rules_a
    lines.append(f"## style rules: A {sum(rules_a.values())}, B {sum(rules_b.values())}; "
                 f"{sum(rules_gone.values())} only in A, {sum(rules_new.values())} only in B")
    if rules_gone or rules_new:
        differs = True

    groups = collections.defaultdict(list)
    scene_lines = []
    names_a = {p.name for p in (a / "scenes").glob("*.json.gz")}
    names_b = {p.name for p in (b / "scenes").glob("*.json.gz")}
    for missing in sorted(names_a ^ names_b):
        differs = True
        scene_lines.append(f"- scene only in {'A' if missing in names_a else 'B'}: {missing}")
    jobs = [(name, str(a), str(b), pairs, args.examples) for name in sorted(names_a & names_b)]
    with ProcessPoolExecutor() as pool:
        for found_lines, found_groups in pool.map(compare_scene, jobs, chunksize=4):
            scene_lines += found_lines
            for key, places in found_groups.items():
                groups[key] += places
    if scene_lines:
        differs = True
        lines.append("## elements present in one run only, focus and window")
        lines += scene_lines
    if groups:
        differs = True
        lines.append(f"## differences on matched elements: {len(groups)} kinds")
        for (what, old, new), places in sorted(groups.items(), key=lambda item: (-len(item[1]), str(item[0]))):
            lines.append(f"- {what}: {json.dumps(old, ensure_ascii=False)} -> {json.dumps(new, ensure_ascii=False)}"
                         f"  ({len(places)} places)")
            lines += [f"    {place}" for place in places[:args.examples]]
    if not differs:
        lines.append("NO DIFFERENCES")
    if rules_gone or rules_new:
        lines.append("## rules only in A")
        lines += [f"- {rule[:400]}" for rule in list(rules_gone.elements())[:40]]
        lines.append("## rules only in B")
        lines += [f"- {rule[:400]}" for rule in list(rules_new.elements())[:40]]
    text = "\n".join(lines)
    if args.report:
        Path(args.report).write_text(text + "\n", encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(text if len(text) < 20000 else text[:20000] + "\n... (cut; see --report)")
    return 1 if differs else 0


if __name__ == "__main__":
    sys.exit(main())
