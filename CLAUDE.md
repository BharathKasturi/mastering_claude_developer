# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Practice notebooks for the **Claude Certified Developer — Foundations (CCDV-F)** exam. Not an application: there is no package, test suite, or linter. The deliverables are Jupyter notebooks, generated from Python sources, plus small tooling scripts at the root.

- `workshop_2/` — 25 exam-objective lessons (`01_…` to `25_…`) plus `00_setup.ipynb` (shared setup). This is the CCDV-F course proper.
- `workshop_1/` — separate Claude Code CLI lab track (Day 1 workshop, notebooks 00–05 and 09–11; see `workshop_1/README.md`). Not exam content and not in `manifest.json`.
- `src/NN_slug.py` — source of truth for the 25 lessons, in Jupyter percent format.
- `manifest.json` — the 25 objectives (`n`, `skill`, `weight`, `title`, `statement`, `mode`, `file`). `mode` is `api`, `cli`, or `both` and decides which tool the lesson drives.

## Commands

```bash
python3 nbbuild.py 07          # rebuild lesson 07 from src/07_*.py
python3 nbbuild.py             # rebuild all lessons
python3 nbbuild.py --check     # structural check of built notebooks
python3 verify.py [NN ...]     # check the learner-facing promises (see below)
python3 gen_learning_path.py   # regenerate learning_path.yaml from manifest.json (never hand-edit the yaml)
jupyter nbconvert --to notebook --execute --inplace NN_*.ipynb   # re-execute after a build
```

Deps: `pip install anthropic jupyter`. No API key is needed — every cell must run offline.

## Workflow rules

- **Never hand-edit a `.ipynb`.** Edit `src/NN_*.py` and rebuild. A build writes cells with *empty outputs*, so re-execute afterwards or `verify.py` reports "never executed".
- `AUTHORING.md` is the full lesson spec; read it before writing or changing a lesson. Key points: required header cell (`# Topic <skill> · <title>`, the `🎓 *CCDV-F objective…*` line, **Why this matters / What you'll learn / What you need**), first code cell is exactly `%run ./00_setup.ipynb` (with its comment), 4–6 `### Feature N —` sections, a `**Practical use:**` cell, then a `**🏋️ Practice:**` cell that ships *already passing* and ends in an `assert`. 14–22 cells per notebook.
- Straight-line code, avoid `def`; show the real API call inline guarded by `if client is None:` with a labelled pretend result. Comment every code cell.
- Use `MODEL` from setup, never a hard-coded or `*-latest` id. Never print a key.
- `cli`/`both` lessons run the `claude` CLI via `!claude …` or `%%bash` (no `subprocess`), never run a `claude -p` prompt (costs money — print it), and write only under `~/ccdv-practice/<NN>`.
- `verify.py` enforces: header names the right skill/objective, notebook executed with no error outputs, `%run ./00_setup.ipynb` present, no stale model id, no key printed, writes/destructive commands stay in the scratch dir, and the manifest `mode` matches the tool used.

## Known path drift (check before relying on the scripts)

`nbbuild.py`, `verify.py`, and `gen_learning_path.py` resolve everything relative to the repo root (`ROOT`): `nbbuild.py` writes `NN_slug.ipynb` *beside* `src/`'s parent and `verify.py` reads `ROOT/<file>.ipynb`, and `gen_learning_path.py` expects a `workshop/` directory. But the built notebooks currently live in `workshop_2/` and the lab track in `workshop_1/`. Expect `verify.py`/`--check` to find no notebooks, and the generated yaml to omit the workshop section, until notebooks are moved back to the root or the scripts are pointed at the new directories.

`README.md`, `AUTHORING.md`, and `workshop_1/README.md` also contain absolute `/Users/satyak/...` paths (sibling source material, the quiz app, the Ranger data-catalog project that workshop notebooks 09–11 need, a `jupyter lab` command) that do not exist on this machine. `00_setup.ipynb` defaults `MODEL` to `claude-sonnet-5` unless `CLAUDE_MODEL` is set.
