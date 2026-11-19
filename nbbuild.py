"""Turn a percent-format source file into a Jupyter notebook.

Notebook JSON is a bad authoring format — a stray comma breaks the file and the
error surfaces only when Jupyter opens it. So each lesson is written in `src/` as
plain text using the standard percent markers, and this builds the .ipynb:

    # %% [markdown]
    # # Topic 1.1 · Title
    # prose, one `# ` per line

    # %%
    print("code goes here verbatim")

Usage:
    python3 nbbuild.py            build every src/*.py into a notebook beside it
    python3 nbbuild.py 01 07      build only those lesson numbers
    python3 nbbuild.py --check    validate the built notebooks, build nothing

A build writes fresh cells with EMPTY outputs, so it discards whatever the
notebook had stored. Always re-execute what you just built, or `verify.py` will
(correctly) report the lesson as never run:

    jupyter nbconvert --to notebook --execute --inplace 07_*.ipynb
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent
SRC = ROOT / "src"
MARK = re.compile(r"^# %%(?:\s*\[(\w+)\])?\s*$")

KERNEL = {
    "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
    "language_info": {"name": "python", "version": "3.11"},
}


def parse(text):
    """Split percent-format text into [(kind, source), ...]."""
    cells, kind, buf = [], None, []

    def flush():
        if kind is None:
            return
        body = "\n".join(buf).strip("\n")
        if body.strip():
            cells.append((kind, body))

    for line in text.split("\n"):
        m = MARK.match(line)
        if m:
            flush()
            kind, buf = (m.group(1) or "code"), []
            continue
        if kind == "markdown":
            # markdown lines are comment-prefixed in the source; strip one level
            line = line[2:] if line.startswith("# ") else ("" if line == "#" else line)
        buf.append(line)
    flush()
    return cells


def notebook(cells):
    out = []
    for kind, body in cells:
        cell = {"cell_type": kind, "metadata": {},
                "source": body.split("\n")}
        # nbformat wants every line to keep its newline except the last
        cell["source"] = [l + "\n" for l in cell["source"][:-1]] + cell["source"][-1:]
        if kind == "code":
            cell["outputs"], cell["execution_count"] = [], None
        out.append(cell)
    return {"cells": out, "metadata": KERNEL, "nbformat": 4, "nbformat_minor": 5}


def check(path):
    """Structural checks that catch the ways these notebooks actually go wrong."""
    nb = json.loads(path.read_text(encoding="utf-8"))
    cells = nb["cells"]
    src = lambda c: "".join(c["source"])
    bad = []
    if not cells or cells[0]["cell_type"] != "markdown":
        bad.append("first cell is not the markdown header")
    elif not src(cells[0]).lstrip().startswith("# "):
        bad.append("header cell has no H1 title")
    head = src(cells[0])
    for want in ("**Why this matters.**", "**What you'll learn.**"):
        if want not in head:
            bad.append(f"header missing {want}")
    whole = "\n".join(src(c) for c in cells)
    if "🏋️ Practice" not in whole:
        bad.append("no practice exercise")
    if not any(c["cell_type"] == "code" and "assert " in src(c) for c in cells):
        bad.append("no assert — the practice cell must check itself")
    for c in cells:
        if c["cell_type"] != "code":
            continue
        body = src(c)
        # IPython magics (%run, %%bash) and shell escapes (!ls) are not Python,
        # so a cell that uses one is not something `compile` can judge
        if any(l.lstrip().startswith(("%", "!")) for l in body.split("\n")):
            continue
        try:
            compile(body, str(path), "exec")
        except SyntaxError as e:
            bad.append(f"code cell does not parse: {e}")
    return bad


def main(args):
    if "--check" in args:
        fail = 0
        # 00_setup is the shared connection notebook, not a lesson
        for nb_path in sorted(ROOT.glob("[0-9][0-9]_*.ipynb")):
            if nb_path.name.startswith("00_"):
                continue
            bad = check(nb_path)
            fail += bool(bad)
            print(f"{'FAIL' if bad else 'ok  '} {nb_path.name}"
                  + ("".join(f"\n       - {b}" for b in bad)))
        print(f"\n{'all notebooks pass' if not fail else f'{fail} notebook(s) failed'}")
        return 1 if fail else 0
    want = [a for a in args if a.isdigit()]
    built = 0
    for s in sorted(SRC.glob("[0-9][0-9]_*.py")):
        if want and s.name[:2] not in want:
            continue
        cells = parse(s.read_text(encoding="utf-8"))
        out = ROOT / (s.stem + ".ipynb")
        out.write_text(json.dumps(notebook(cells), indent=1, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        print(f"{len(cells):3d} cells  {out.name}")
        built += 1
    if not built:
        print("nothing to build — no matching src/*.py")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
