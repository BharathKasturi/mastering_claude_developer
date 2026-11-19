"""Independent check on the built lesson notebooks.

`nbbuild.py --check` guards structure. This guards the promises the notebooks make
to a learner, which are the ones a self-report is least likely to notice breaking:
executed with no key and no error output, the header states the right objective, the
model is pinned, nothing writes outside the scratch dir, and no key is ever printed.

Usage: python3 verify.py            all lessons
       python3 verify.py 23 24 25   only those
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent
MAN = {f"{m['n']:02d}": m for m in json.load(open(ROOT / "manifest.json"))}

# a path written outside these is a lesson touching the learner's real files
SCRATCH_OK = re.compile(r"ccdv-practice|/tmp/|tempfile|TemporaryDirectory")
WRITES = re.compile(r"(?:cat\s*>|mkdir\s+-p|open\(|write_text\(|\.mkdir\()")
# a lesson names its scratch root once and writes through the variable thereafter,
# often via a chain (LAB -> SKILL_DIR -> ...), so resolve those names to a fixed
# point first: a per-cell text match alone reads `SKILL_DIR / "x"` as an escape
ASSIGN = re.compile(r"^\s*(\w+)\s*=\s*(.+)$", re.M)


def scratch_names(text):
    """Every variable that ultimately points inside the scratch dir."""
    names, pairs = set(), ASSIGN.findall(text)
    for _ in range(6):                       # chains are short; this settles fast
        grew = False
        for name, rhs in pairs:
            if name in names:
                continue
            if SCRATCH_OK.search(rhs) or any(re.search(rf"\b{n}\b", rhs) for n in names):
                names.add(name)
                grew = True
        if not grew:
            break
    return names
# printing a key, even partly redacted, is a leak in a notebook that gets shared.
# the env var NAME in a skip message is fine, so drop string literals before matching
LEAKY = re.compile(r"print\([^)]*(?:API_KEY|api_key)", re.I)
STRINGS = re.compile(r"""(['"]{3}).*?\1|'[^'\n]*'|"[^"\n]*\"""", re.S)
# only a stale id actually PASSED to a call is a bug. Several lessons name
# "claude-sonnet-latest" on purpose, as the drifting config they teach you to catch
STALE_MODEL = re.compile(r"""model\s*=\s*['"]claude-(?:3[.-]|[a-z]+-latest)""")


# a line that STARTS with one of these is executing it, not quoting it in a policy list
DESTRUCTIVE = re.compile(r"(?:rm\s+-[rf]{2}|mkfs|dd\s+if=|shutdown|:\(\)\s*\{)")


def cells_of(nb):
    return [("".join(c["source"]), c) for c in nb["cells"]]


def verify(num):
    m = MAN[num]
    path = ROOT / f"{m['file']}.ipynb"
    bad = []
    if not path.exists():
        return [f"notebook missing: {path.name}"]
    nb = json.loads(path.read_text(encoding="utf-8"))
    cells = cells_of(nb)
    head = cells[0][0]

    # 1. header names the right objective
    if f"Topic {m['skill']} " not in head:
        bad.append(f"header does not say 'Topic {m['skill']}'")
    if f"objective {m['n']} of 25" not in head:
        bad.append(f"header does not say 'objective {m['n']} of 25'")

    # 2. it was actually executed, and executed clean
    code = [c for _, c in cells if c["cell_type"] == "code"]
    if not any(c.get("outputs") for c in code):
        bad.append("never executed — no cell has output")
    for src, c in cells:
        for o in c.get("outputs", []):
            if o.get("output_type") == "error":
                bad.append(f"error output: {o.get('ename')}: {str(o.get('evalue'))[:70]}")

    whole = "\n".join(s for s, _ in cells)

    # 3. runs the shared setup, and pins the model
    if "%run ./00_setup.ipynb" not in whole:
        bad.append("does not %run ./00_setup.ipynb")
    if STALE_MODEL.search(whole):
        bad.append("hard-codes an old or floating model id instead of MODEL")

    # 4. never prints a key. the env var NAME inside a message is not a key, so
    #    blank out string literals before looking
    if LEAKY.search(STRINGS.sub('""', whole)):
        bad.append("prints an API key or part of one")

    # 5. anything it writes goes to the scratch dir, directly or via its scratch var
    roots = scratch_names(whole)
    for src, c in cells:
        if c["cell_type"] != "code" or not WRITES.search(src):
            continue
        # a commented-out example is not a write; these lessons show a lot of them
        lines = [l for l in src.split("\n")
                 if WRITES.search(l) and not l.lstrip().startswith("#")]
        stray = [l for l in lines
                 if not SCRATCH_OK.search(l) and not any(r in l for r in roots)]
        if stray:
            bad.append(f"writes outside the scratch dir: {stray[0].strip()[:64]}")
            break

    # 6. a destructive command may appear as a deny-list STRING (that is the whole
    #    point of the guardrail lessons) but must never execute outside the scratch dir
    for src, c in cells:
        if c["cell_type"] != "code":
            continue
        for line in src.split("\n"):
            stripped = line.strip()
            if not DESTRUCTIVE.match(stripped):
                continue
            if not SCRATCH_OK.search(line) and not any(r in line for r in roots):
                bad.append(f"runs a destructive command outside scratch: {stripped[:60]}")

    # 7. the tool the manifest says it should drive
    if m["mode"] in ("cli", "both") and "claude" not in whole:
        bad.append(f"mode={m['mode']} but never mentions the claude CLI")
    if m["mode"] in ("api", "both") and "client" not in whole:
        bad.append(f"mode={m['mode']} but never uses the SDK client")

    return bad


def main(args):
    want = [a for a in args if a.isdigit()] or sorted(MAN)
    fail = 0
    for num in want:
        bad = verify(f"{int(num):02d}")
        fail += bool(bad)
        print(f"{'FAIL' if bad else 'ok  '} {num} {MAN[f'{int(num):02d}']['file']}"
              + "".join(f"\n       - {b}" for b in bad))
    print(f"\n{len(want) - fail}/{len(want)} lessons verified")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
