# %% [markdown]
# # Topic 1.1 · Claude Code Operation: Rules, Skills, Commands, Agents, Memory, Headless, Auto-mode
#
# 🎓 *CCDV-F objective 1 of 25 · exam weight 3.1% · Claude Code lesson*
#
# **Why this matters.** Claude Code is a harness: a model, a tool set, a
# permission gate, and a pile of files. Those files are the whole product surface,
# and almost every real question is "where does this text belong?" Put a standing
# constraint in a slash command and nobody triggers it. Run a nightly job without
# a turn cap and you ship an unbounded bill.
#
# **What you'll learn.**
# - Decide **Rule vs Skill vs Command vs Agent vs Memory** from what the text *is*.
# - See the five components as **real files** in a throwaway repo.
# - Use the **`CLAUDE.md` hierarchy** and the `settings.json` scopes, and know what `/init` does.
# - Manage a **session**: `--continue`, `--resume`, and `/clear` versus `/compact`.
# - Build a **headless** `claude -p` command with an allow-list, a turn cap and JSON output.
# - Pick a **permission mode**, including what "auto-mode" actually means.
#
# **What you need.** Nothing. Every cell runs offline. Files are written only
# under `~/ccdv-practice/10`, never into your own projects. If the `claude` CLI is
# on your `PATH` one cell prints its real version; if it is not, that cell prints
# `command not found`, which is a readable result and not a crash. No cell ever
# runs a `claude -p` prompt, because that spends money — they are printed instead.

# %%
# Load the shared setup: the API client and MODEL.
%run ./00_setup.ipynb

# %% [markdown]
# ### Feature 1 — Five components, one question: *what is this text?*
#
# Claude Code has five places to put instructions. Choosing between them is not a
# style question. Each one answers a different sentence:
#
# - **Rule** — *how we always work here.* Always loaded. Nobody invokes it.
#   Lives in `CLAUDE.md` or `.claude/rules/*.md`.
# - **Skill** — *how to do X.* A reusable procedure, possibly with scripts.
#   Claude picks it by matching the task to its `description`. `.claude/skills/<name>/SKILL.md`.
# - **Command** — *do X now.* A shortcut **you type** as `/name args`.
#   `.claude/commands/<name>.md`, with `$ARGUMENTS` filled in.
# - **Agent** — *do X over there.* A subagent with its **own context window** and
#   its own tools. `.claude/agents/<name>.md`. It returns a summary, not its scraps.
# - **Memory** — *what we learned.* A fact that must survive to the next session.
#
# The exam tells: *"always applies"* → Rule. *"reusable multi-step procedure"* →
# Skill. *"typed shortcut with arguments"* → Command. *"separate context / don't
# pollute the main conversation"* → Agent. *"remember for next session"* → Memory.

# %%
# The decision, written out. Read each requirement and ask only what the text IS.
# The branches are in order of how specific the signal is: the most specific wins.
requirements = [
    ("Always use pnpm, never npm",                                  "rule"),
    ("A reusable release-notes procedure with steps and a script",  "skill"),
    ("A shortcut the developer types to fix a GitHub issue",        "command"),
    ("Review 40 files without polluting the main conversation",     "agent"),
    ("Retain that the staging DB resets nightly, for next session", "memory"),
]

for requirement, expected in requirements:
    words = requirement.lower()
    if any(w in words for w in ("own context", "separate context",
                                "pollut", "delegate", "in parallel")):
        got = "agent"        # isolation is the giveaway
    elif "next session" in words or "remember" in words:
        got = "memory"       # it must outlive the conversation
    elif "types" in words or "shortcut" in words:
        got = "command"      # a human types it — it never fires on its own
    elif "steps" in words or "procedure" in words or "script" in words:
        got = "skill"        # a reusable procedure Claude selects for itself
    else:
        got = "rule"         # a standing constraint with no trigger at all

    print(f"{got:8s} (expected {expected:8s})  <- {requirement}")

# %% [markdown]
# ### Feature 2 — The same five components, as files
#
# The components are not abstractions. They are markdown files in known places,
# and the place is what gives a file its meaning. The cell below builds a
# throwaway repo under `~/ccdv-practice/10` so you can see the layout for real.
#
# Two details worth reading closely in the files it writes:
#
# - the **agent** carries frontmatter — `name`, `description`, `tools`, `model` —
#   and its `tools` line is what makes the isolation *enforced*, not merely asked for.
# - the **command** uses `$ARGUMENTS`, which is filled in from what you type after
#   the slash command.

# %%
%%bash
# Build a throwaway Claude Code repo. Everything is under ~/ccdv-practice/10,
# so nothing you actually work on can be touched by this lesson.
set -e
ROOT=~/ccdv-practice/10/demo-repo
rm -rf "$ROOT"
mkdir -p "$ROOT/.claude/rules" "$ROOT/.claude/skills/release-notes/scripts" \
         "$ROOT/.claude/commands" "$ROOT/.claude/agents"

# A Rule: a standing constraint. Always loaded, never invoked.
cat > "$ROOT/CLAUDE.md" <<'EOF'
# Project rules
- Use pnpm. Never npm.
- Run `pnpm test` before saying a change is done.
EOF

# A Skill: a reusable procedure Claude selects by matching its description.
cat > "$ROOT/.claude/skills/release-notes/SKILL.md" <<'EOF'
---
name: release-notes
description: Draft release notes from the commits since the last tag. Use when asked for a changelog or release notes.
---
1. `git log $(git describe --tags --abbrev=0)..HEAD --oneline`
2. Group commits into Added / Changed / Fixed.
3. Write the notes to CHANGELOG.md under a new version heading.
EOF

# A Command: a shortcut the USER types. $ARGUMENTS is what follows the name.
cat > "$ROOT/.claude/commands/fix-issue.md" <<'EOF'
Fix GitHub issue $ARGUMENTS. Read it with `gh issue view $ARGUMENTS`,
write a failing test first, then the fix, then run the suite.
EOF

# An Agent: its own context window, its own tools, its own model.
cat > "$ROOT/.claude/agents/reviewer.md" <<'EOF'
---
name: reviewer
description: Read-only code review of a diff. Use after edits, before commit.
tools: Read, Grep, Glob, Bash(git diff *)
model: claude-sonnet-5
---
Review the current diff for correctness bugs only.
Return findings as path:line bullets. Never edit a file.
EOF

# Team settings: permissions, model, hooks. settings.local.json is the personal,
# git-ignored twin of this file.
cat > "$ROOT/.claude/settings.json" <<'EOF'
{
  "permissions": {
    "defaultMode": "acceptEdits",
    "allow": ["Bash(pnpm test)", "Bash(git diff *)"],
    "deny":  ["Bash(git push *)", "Read(.env)"]
  },
  "model": "claude-sonnet-5"
}
EOF

echo "created:"
find "$ROOT" -type f | sed "s|$HOME|~|" | sort

# %%
# Walk what we just wrote and label each file with the component it IS.
# The point: the directory decides the meaning, not the file's contents.
import pathlib

repo = pathlib.Path.home() / "ccdv-practice" / "10" / "demo-repo"

labels = {
    "CLAUDE.md":             "Rule    — always loaded into every turn",
    "CLAUDE.local.md":       "Rule    — personal, git-ignored",
    ".claude/rules":         "Rule    — optional `paths:` frontmatter scopes it",
    ".claude/skills":        "Skill   — Claude matches the task to `description`",
    ".claude/commands":      "Command — the user types /name",
    ".claude/agents":        "Agent   — separate context window and tool list",
    ".claude/settings.json": "Config  — team permissions, hooks, model",
}

for f in sorted(repo.rglob("*")):
    if not f.is_file():
        continue
    rel = f.relative_to(repo).as_posix()

    # A path is a component when it IS one of the known names, or sits under one.
    meaning = "(not a Claude Code component)"
    for prefix, text in labels.items():
        if rel == prefix or rel.startswith(prefix + "/"):
            meaning = text
            break

    print(f"{rel:45s} {meaning}")

# %% [markdown]
# ### Feature 3 — The `CLAUDE.md` hierarchy, settings scopes, and `/init`
#
# The same instruction can live at four different scopes, and the scope decides
# who it applies to and whether it is committed:
#
# | Scope | Rules file | Settings file | Committed? |
# |---|---|---|---|
# | Personal, all projects | `~/.claude/CLAUDE.md` | `~/.claude/settings.json` | no — it is yours |
# | Project, whole team | `CLAUDE.md` (+ `.claude/rules/*.md`) | `.claude/settings.json` | **yes** |
# | Project, just you | `CLAUDE.local.md` | `.claude/settings.local.json` | no — git-ignored |
# | Managed by the organisation | — | managed policy settings | pushed by IT |
#
# A managed policy can forbid a setting outright — `bypassPermissions` is the
# usual example — and no local file can argue with it.
#
# `CLAUDE.md` can pull in other files with `@imports`, which is how a long rule
# set stays readable. And `/init` is the **repository initialization** step the
# objective names: it asks Claude to explore the repo and draft a `CLAUDE.md`.
# **That is all it does.** It does not set permissions, hooks, or MCP servers —
# those are `/permissions`, `/hooks`, `/mcp` and `settings.json`.

# %%
# Where does a given instruction belong? Two questions decide it: does it apply
# to the whole team, and is it personal or machine-specific?
cases = [
    ("Team coding standard: use pnpm",            True,  False),
    ("My local Postgres runs on port 5433",       True,  True),
    ("I prefer terse commit messages everywhere", False, False),
]

for text, applies_to_team, is_personal in cases:
    if not applies_to_team:
        where = "~/.claude/CLAUDE.md  (personal, every project)"
    elif is_personal:
        where = "CLAUDE.local.md  (project, git-ignored)"
    else:
        where = "CLAUDE.md  (project, committed)"

    print(f"{where:45s} <- {text}")

print("\n/init drafts CLAUDE.md and nothing else. "
      "Permissions, hooks and MCP are configured separately.")

# %% [markdown]
# ### Feature 4 — Sessions: continue, resume, clear, compact
#
# A session is the conversation plus everything loaded into it. Reattaching to one
# is a flag; managing the context inside one is a slash command.
#
# ```bash
# claude                        # new interactive session in this directory
# claude --continue             # reopen the most recent session here
# claude --resume               # pick from recent sessions
# claude --resume <session-id>  # reopen one specific session
# claude -p "..." --resume <id> # continue a session headlessly
# ```
#
# Inside the session:
#
# | Command | Effect |
# |---|---|
# | `/clear` | Drops the history. `CLAUDE.md` and settings reload fresh. |
# | `/compact [focus]` | Summarises the history, optionally keeping a stated focus. |
# | `/memory` | Opens the memory files for editing. |
# | `# text` | Quick-adds a memory note. |
#
# Auto-compaction fires on its own as the window fills. `/compact` only lets you
# choose *when* and *what to keep*. **Compaction is lossy.** Anything that must
# survive belongs in a file, not only in the transcript.

# %%
# /clear or /compact? The question is whether you still need the thread at all.
for next_task_is_related, context_is_full in [(False, True), (True, True), (True, False)]:
    if not next_task_is_related:
        action = "/clear    — unrelated task; keeping the history only costs tokens"
    elif context_is_full:
        action = "/compact  — still need the thread; accept the summary's detail loss"
    else:
        action = "(nothing)  — the context is fine; do not compact for the sake of it"

    print(f"related={str(next_task_is_related):5s} "
          f"full={str(context_is_full):5s}  ->  {action}")

print("\nRule: state that must survive compaction goes in a file, not the transcript.")

# %% [markdown]
# ### Feature 5 — Headless mode: `claude -p`
#
# No human is present in CI, so nothing can answer a permission prompt. A headless
# run therefore **denies any tool that is not on the allow-list** — which is why
# `--allowedTools` and `--max-turns` always travel together. One bounds *what*, the
# other bounds *how long*.
#
# | Flag | Meaning |
# |---|---|
# | `-p` / `--print` | Non-interactive: run once, print, exit |
# | `--output-format text\|json\|stream-json` | `json` = one final object; `stream-json` = one JSON event per line |
# | `--input-format stream-json` | Accept messages as JSONL on stdin |
# | `--max-turns N` | Hard cap on agentic turns — the cost and loop guard |
# | `--allowedTools` / `--disallowedTools` | Tool allow/deny list, same pattern syntax as `settings.json` |
# | `--permission-mode` | `default`, `acceptEdits`, `plan`, `bypassPermissions` |
# | `--resume <id>` / `--continue` | Attach to a prior session's context |
# | `--model`, `--system-prompt`, `--append-system-prompt` | Override for this run |
#
# And when you ask for `--output-format json`, **parse the JSON.** Regex-scraping
# the text throws away `session_id` and the usage numbers.

# %%
# Build the nightly-audit command from the objective's own scenario, then print it
# the way you would paste it into a CI file. We build it; we never run it.
# shlex is not in the shared setup — it is here only to quote the line for printing.
import shlex

task = "Audit the dependencies and write reports/audit.md"
allowed_tools = ["Read", "Bash(npm audit *)", "Write(reports/**)"]

argv = ["claude", "-p", task]
argv += ["--allowedTools", ",".join(allowed_tools)]   # bounds WHAT it may touch
argv += ["--max-turns", "8"]                          # bounds HOW LONG it runs
argv += ["--output-format", "json"]                   # parseable, not scraped
argv += ["--permission-mode", "acceptEdits"]          # nobody is here to approve

print(shlex.join(argv), "\n")

# What `--output-format json` gives the pipeline. This is an abridged sample so
# the cell runs offline; the point is that you parse it instead of scraping text.
sample_json = """{
  "result": "Audited 412 packages. 2 moderate advisories written to reports/audit.md.",
  "session_id": "1f9c1e2a-0000-4a00-9c3e-2b7d4c5e6f70",
  "usage": {"input_tokens": 18432, "output_tokens": 611}
}"""

parsed = json.loads(sample_json)                 # `json` came from the setup cell
print("result     :", parsed["result"])
print("session_id :", parsed["session_id"], " <- feed this to --resume")
print("tokens     :", parsed["usage"]["input_tokens"], "in /",
      parsed["usage"]["output_tokens"], "out")

# %% [markdown]
# ### Feature 6 — Permission modes, and what "auto-mode" means
#
# ```
#   MODE                 FILE EDITS      SHELL         PROMPTS?    USE WHEN
#   ─────────────────    ───────────     ───────────   ─────────   ──────────────────────────
#   default              ask             ask           yes         exploring; unknown repo
#   plan                 read-only       read-only     n/a         "show me the plan first"
#   acceptEdits          auto-approved   ask           some        trusted repo, human watching
#   bypassPermissions    auto            auto          none        sandbox / CI container only
# ```
#
# Set it with `--permission-mode`, or with `permissions.defaultMode` in
# `settings.json`, or cycle it with **Shift+Tab** in an interactive session.
#
# "Auto-mode" in a question means the non-prompting end of that spectrum:
# `acceptEdits` for edits, `bypassPermissions` for everything. The safe pairing is
# always **sandbox + allow-list + turn cap** — never "bypass on my laptop because
# the prompts are annoying." Plan mode is the opposite end, and it is worth
# knowing why it beats "default mode and I'll just say no": plan mode makes the
# zero-edits guarantee *structural* instead of behavioural.

# %%
# One live call, and it is deliberately the cheapest one there is: --version
# makes no model call, so it costs nothing and returns instantly.
!claude --version

# %%
# A real `claude -p` run spends money and takes time, so this lesson prints the
# command instead of running it. Copy the line into a terminal when you want to
# watch a bounded run happen for real.
print("$ claude -p 'Reply with exactly: ok' \\")
print("      --allowedTools Read \\")
print("      --max-turns 1 \\")
print("      --permission-mode plan \\")
print("      --output-format json")

# %% [markdown]
# **Practical use:** ask *what is this text?* before asking where to put it. A
# standing constraint is a **Rule**, a reusable procedure is a **Skill**, a typed
# shortcut is a **Command**, delegated work that must not pollute your context is
# an **Agent**, and a fact that must outlive the session is **Memory**. Then ask
# *is a human present?* Yes → interactive, with plan mode when you do not trust
# the repo yet. No → `claude -p` with an allow-list, a turn cap, and JSON output,
# inside a sandbox.
#
# **🏋️ Practice:** the nightly job below must be bounded. Someone removed the turn
# cap "because the allow-list already limits it" — but an allow-list bounds *what*
# the run may touch, not *how many turns* it takes. The fix is marked `# <-` and is
# already in place, so the cell passes as shipped.
#
# To feel it: delete the marked line, re-run, and watch the assert fail. Put it
# back. Then add `--resume <session-id>` to the list so the job continues an
# earlier session, and add `"--resume"` to `required_flags` to check it is there.

# %%
# Your turn — see the `# <-` marker.
ci_task = "Summarise the failing tests in the last CI run"
ci_tools = ["Read", "Grep", "Bash(pytest *)"]

ci = ["claude", "-p", ci_task]
ci += ["--allowedTools", ",".join(ci_tools)]   # bounds WHAT it may touch
ci += ["--max-turns", "5"]                     # <- bounds HOW LONG it runs
ci += ["--output-format", "json"]              # parseable, not scraped

print(shlex.join(ci))

required_flags = ["-p", "--allowedTools", "--max-turns", "--output-format"]
missing = [flag for flag in required_flags if flag not in ci]

assert not missing, f"an unattended run is unbounded without: {missing}"
print("\nbounded: what it may touch, how long it may run, and how you read the result")
