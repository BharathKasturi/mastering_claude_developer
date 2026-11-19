# CCDV-F practice notebooks — authoring spec

One notebook per CCDV-F exam objective, 25 in all. A learner opens one, runs it top
to bottom with **no API key and no setup**, and comes out able to do the thing the
objective names.

## Where everything lives

| Path | What |
|---|---|
| `manifest.json` | the 25 objectives: `n`, `skill`, `weight`, `title`, `statement`, `mode`, `file` |
| `src/NN_slug.py` | **you write this** — the lesson, in percent format |
| `NN_slug.ipynb` | build output, `python3 nbbuild.py NN` |
| `00_setup.ipynb` | shared connection setup; every lesson runs it first |
| `nbbuild.py` | converter + `--check` validator |

Source material for the lesson content, both read-only:

- deep dive — `/Users/satyak/iceberg-mcp-main/iceberg_mcp/quiz/claude_developer_quiz/objective3/oNN.md`
- slides — `/Users/satyak/mastering/cca_dev_class/NN_*.pptx`
- question bank — `.../claude_developer_quiz/questions_domain*.md` (search for the skill code)

House style to match — read one before you start:
`/Users/satyak/mastering/mastering_claude_ccaf/module_1/1.1_your_first_agent_loop.ipynb`

## Percent format

```python
# %% [markdown]
# # Topic 1.1 · Agent Architecture
# Prose. Every markdown line is prefixed with `# `. A blank line is a bare `#`.

# %%
print("code cells are verbatim — no prefix")
```

Build and validate:

```bash
cd /Users/satyak/mastering/mastering_claude_developer
python3 nbbuild.py 07          # build just lesson 07
python3 nbbuild.py --check     # structural check on every built notebook
```

## Required shape

1. **Header cell** (markdown). Must contain, in this order:
   - `# Topic <skill> · <title>` — the skill code from the manifest, e.g. `# Topic 2.3 · Claude API Mechanics`
   - a line: `🎓 *CCDV-F objective <n> of 25 · exam weight <weight>% · <API lesson|Claude Code lesson>*`
   - `**Why this matters.**` — two or three sentences on where this shows up in real work.
   - `**What you'll learn.**` — 4-6 bullets, one per Feature section below.
   - `**What you need.**` — one line, honest: what runs offline and what needs a key or the CLI.
2. **Setup cell** (code), exactly: `# Load the shared setup: the API client and MODEL.` then `%run ./00_setup.ipynb`
3. **No helper cell.** Go straight from the setup cell into Feature 1.
4. **Feature sections** — 4 to 6 of them. Each is a markdown cell `### Feature N — <short name>` explaining the idea, then one or more code cells demonstrating it.
5. **Closing cell** (markdown) with `**Practical use:**` — the one rule to remember.
6. **Practice cell** — a markdown `**🏋️ Practice:**` prompt, then a code cell that
   ends in an `assert`. **Ship it already passing**, with the key line marked `# <-`,
   exactly as the reference notebook does. Tell the learner to break it deliberately
   (delete the marked bit, watch the assert fail), put it back, then extend it. A
   practice cell that fails as shipped would break the "runs top to bottom" promise
   and there would be no way to validate the notebook.

`nbbuild.py --check` enforces 1, 6, and that every code cell parses.

## Rules

1. **Every cell runs offline.** No key, no CLI, no network — the notebook still completes.
   Guard an API call with `if client is None:` and print a clearly-labelled pretend result.
   A `!claude` cell needs no guard — a missing CLI prints `command not found`, which is
   output, not an exception. Never let a cell raise.
2. **Write straight-line code. Avoid `def`.** This is the rule that matters most.
   A lesson is read once, top to bottom — a function the learner must scroll back to
   is a worse teacher than the same three lines written out. Use plain variables, a
   `for` loop, an `if/else`, a dict literal. Lesson 01 has **zero** functions; aim for
   that. Write a `def` only when the function *is* the thing being taught and the
   lesson would be wrong without it, and say so in a comment when you do.
2b. **Show the real API call inline**, not behind a wrapper:

    ```python
    if client is None:
        text = "[PRETEND ANSWER — set ANTHROPIC_API_KEY for a real reply]"
    else:
        reply = client.messages.create(
            model=MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        text = reply.content[0].text
    ```

    Calling it several times? Put the prompts in a list and loop. The learner should
    be able to copy the call straight into a script.
2c. **Comment every code cell.** A line above each block saying what it does and why.
3. **Teach the exam objective, not Python.** The `statement` in the manifest is the
   contract. Every Feature section must map to part of it.
4. **Real API shapes.** When you show a request, show the real field names —
   `model`, `max_tokens`, `messages`, `stop_reason`, `tools`, `input_schema`,
   `tool_use_id`, `cache_control`, `usage`. A learner should be able to copy the cell
   into a script. Take these from the deep dive; do not invent parameters.
5. **Pin the model.** Use the `MODEL` from setup. Never `claude-3-*` or "latest".
6. **`mode` in the manifest decides the tool.**
   - `api` — the `anthropic` SDK via `client`.
   - `cli` — the `claude` command line, run exactly as in a terminal: `!claude --version`
     for one line, a `%%bash` cell for several (`mkdir`, `cat > … <<'EOF'`). **There is no
     Python wrapper** — do not write one, and do not import `subprocess`. If the CLI is
     missing the cell prints `command not found`, which is a readable result, not a crash,
     so no `HAVE_CLI` guard is needed either. Write config files under a scratch dir like
     `~/ccdv-practice/NN`, never into the learner's real projects. Never run a `claude -p`
     prompt — it costs money; print the command instead.
   - `both` — API features first, then one Claude Code Feature section.
7. **No secrets.** Read keys from the environment only. Never print a key, not even
   partially.
8. **Short sentences.** Same discipline as the slides: one idea per sentence, plain words.
9. Keep each notebook to **14-22 cells**. Precise beats exhaustive.
10. Do not edit `nbbuild.py`, `manifest.json`, `00_setup.ipynb`, or another lesson's files.

## Report per lesson

`file · cells · features · offline-clean (yes/no) · anything from the objective you
could not demonstrate in code and why`.
