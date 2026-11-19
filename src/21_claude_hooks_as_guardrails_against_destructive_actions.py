# %% [markdown]
# # Topic 7.3 · Claude Hooks as Guardrails Against Destructive Actions
#
# 🎓 *CCDV-F objective 21 of 25 · exam weight 1.0% · Claude Code lesson*
#
# **Why this matters.** CLAUDE.md is advice. A system prompt is advice. Under
# context pressure, advice is exactly the kind of thing that gets dropped. A
# **hook** is a program that runs at a fixed point in the loop, sees the actual
# tool arguments, and decides. The model can be persuaded. A hook cannot.
#
# **What you'll learn.**
# - Choose between **CLAUDE.md, a permission rule and a hook** from the wording of the requirement.
# - Name the **hook events** and what each one can block.
# - Write a real `PreToolUse` hook and see **exit 2 + stderr** cancel a tool call.
# - Wire hooks up in **`settings.json`** with a `matcher`, and know which file the team gets.
# - Return a **structured `permissionDecision`** — `allow`, `deny` or `ask`.
# - Use a **`Stop` hook** to refuse "done", and guard it with `stop_hook_active`.
#
# **What you need.** Nothing but Python and a shell. Every hook in this lesson is
# written to `~/ccdv-practice/21`, a scratch directory — never your own
# `~/.claude` and never a real project. We run the hooks from `%%bash` cells
# exactly the way Claude Code runs them — one JSON object on stdin — so you see
# the exit codes yourself. No API key required.

# %%
# Load the shared setup: the API client and MODEL.
%run ./00_setup.ipynb

# %% [markdown]
# ### Feature 1 — Three places a rule can live, and the words that pick one
#
# | Control | Where it lives | What it sees | Deterministic? | Can block a call? |
# |---|---|---|---|---|
# | **CLAUDE.md / system prompt** | the context window | nothing at runtime — it is text | no | no |
# | **Permission rule** (`allow`/`ask`/`deny`) | `settings.json` → `permissions` | tool name + a simple pattern | yes | yes (`deny`) |
# | **`PreToolUse` hook** | `settings.json` → `hooks` | the full `tool_input` JSON | yes | **yes** |
# | **`PostToolUse` hook** | same | `tool_input` + `tool_response` | yes | no — it already ran |
# | **`Stop` hook** | same | the transcript path, `stop_hook_active` | yes | it can refuse the stop |
# | **Sandbox** | the OS or a container | syscalls, network | yes | yes, by construction |
#
# **The decision rule.** If it *must never happen*, it goes in a hook or a deny
# rule — and under a sandbox where you can. If it *should usually happen*, it
# goes in CLAUDE.md.
#
# The exam tells you which one it wants by its verbs. *"Prevent"*, *"never
# allow"*, *"guarantee"*, *"deterministic"*, *"regardless of what the model
# decides"* → a hook. *"Prefer"*, *"usually"*, *"remind"* → CLAUDE.md.
#
# One more split, and it is the one people get wrong. A **permission rule** is
# tool-level: it can say "never this tool". A **hook** is argument-level: it can
# say "this tool, but not with *these* arguments". When the agent needs the tool
# to do its job, a blanket deny removes the capability it exists for.

# %%
# The verbs that decide the control. Read a requirement, pick a home for it.
HARD_WORDS = ("never", "must not", "prevent", "guarantee",
              "always", "only", "deterministic")

REQUIREMENTS = [
    # (the requirement in plain words,                      does the verdict need the arguments?)
    ("Prefer pytest over unittest",                         False),
    ("Never force-push to a shared branch",                 True),
    ("The agent must not use the web search tool at all",   False),
    ("Only read-only SQL may reach the database tool",      True),
    ("Use British spelling in the docs",                    False),
]

for text, needs_args in REQUIREMENTS:
    # Two questions, in this order, and the answer falls out. No taste involved.
    if not any(word in text.lower() for word in HARD_WORDS):
        control = "CLAUDE.md"                  # a preference, not a constraint
    elif needs_args:
        control = "PreToolUse hook"            # only a hook sees tool_input
    else:
        control = "permission deny rule"       # a tool-name pattern is enough
    print(f"{control:20s} <- {text}")

# %% [markdown]
# ### Feature 2 — The events, and what each one can block
#
# ```
#    event               fires when…                             blocking power
#    ─────────────────   ─────────────────────────────────────   ────────────────────────────
#    PreToolUse          before a tool call executes             exit 2 / deny → call cancelled
#    PostToolUse         after a tool call returns               exit 2 → Claude sees the error
#    PermissionRequest   when a permission prompt would appear   can auto-allow or deny
#    UserPromptSubmit    the user sends a prompt                 exit 2 → the prompt is dropped
#    Stop                Claude is about to end its turn         block → Claude keeps working
#    SubagentStop        a subagent is about to finish           block → the subagent continues
#    Notification        Claude Code sends a notification        none — side effects only
#    PreCompact          before context compaction               none
#    SessionStart        a session begins                        none; stdout is ADDED to context
#    SessionEnd          a session ends                          none
# ```
#
# The exact set is version-dependent — Claude Code adds events over time. The
# four the exam leans on are `PreToolUse`, `PostToolUse`, `Stop` and
# `UserPromptSubmit`.
#
# Hook **types** matter too. `"type": "command"` is a shell command: the classic,
# and the deterministic one. Newer versions add `"type": "prompt"` and
# `"type": "agent"`, where a model judges the decision. For a destructive-action
# guardrail the exam wants `command`. A shell script is not a model, so it cannot
# be argued with.

# %%
# The events as data, so "which event do I need?" is a lookup, not a memory test.
EVENTS = {
    "PreToolUse":       {"blocks": True,  "sees": "tool_name, tool_input"},
    "PostToolUse":      {"blocks": False, "sees": "tool_name, tool_input, tool_response"},
    "UserPromptSubmit": {"blocks": True,  "sees": "the user's prompt"},
    "Stop":             {"blocks": True,  "sees": "transcript_path, stop_hook_active"},
    "SubagentStop":     {"blocks": True,  "sees": "transcript_path, stop_hook_active"},
    "PreCompact":       {"blocks": False, "sees": "the session, before compaction"},
    "SessionStart":     {"blocks": False, "sees": "the session; stdout is added to context"},
}

for name, meta in EVENTS.items():
    print(f"{name:18s} can block: {str(meta['blocks']):5s}  sees: {meta['sees']}")

# Pick the event that can actually do the job. A stem's wording ("after every
# edit", "before it runs") is the clue, but the blocking power is the decision.
print()
for goal in ["prevent a force-push before it runs",
             "run the formatter after an edit",
             "do not let Claude finish until the tests pass"]:
    words = goal.lower()
    if "before" in words or "prevent" in words or "block" in words:
        event = "PreToolUse"
    elif "after" in words or "format" in words or "lint" in words:
        event = "PostToolUse"
    elif "finish" in words or "done" in words or "tests pass" in words:
        event = "Stop"
    else:
        event = "UserPromptSubmit"
    print(f"{event:14s} <- {goal}")

# %% [markdown]
# ### Feature 3 — The protocol: JSON on stdin, a verdict in the exit code
#
# ```
#    stdin (every event)     {"session_id", "transcript_path", "cwd", "hook_event_name",
#                             "tool_name", "tool_input": {…}}
#        PostToolUse adds    "tool_response": {…}
#        Stop adds           "stop_hook_active": true|false   ◄── guards against a loop
#
#    exit 0    allow / success      stdout goes to the transcript; for UserPromptSubmit
#                                   and SessionStart it is ADDED to Claude's context
#    exit 2    BLOCK                stderr is fed to Claude as the reason, call cancelled
#    other     non-blocking error   stderr shown to the user; the tool still runs
# ```
#
# Two mistakes cost people the question every time.
#
# - **Exit 1 does not block.** The user sees the message and the tool runs anyway.
# - **The reason must go to stderr.** On exit 2, stderr is what Claude receives.
#   Print it to stdout and Claude is told "no" with no explanation, so it retries
#   variations of the same command forever.
#
# The guard below is the argument-level rule from Feature 1. Every pattern it
# blocks is one that damages shared history or shared data. The classic version
# of this script is Bash with `jq`; we write it in Python so it runs everywhere.
#
# A hook is a **file on disk that Claude Code executes**, so we write it as a
# file — a shell cell with a heredoc, exactly as you would in a terminal.

# %%bash
# Everything this lesson writes goes under this scratch directory, on purpose: a
# lesson about guardrails must not install guardrails you did not ask for.
mkdir -p ~/ccdv-practice/21/.claude/hooks

# A real PreToolUse hook. Claude Code would run it with matcher "Bash".
cat > ~/ccdv-practice/21/.claude/hooks/guard-bash.py <<'EOF'
#!/usr/bin/env python3
"""PreToolUse hook for the Bash tool — block commands that rewrite shared state.

Purpose:
    Deterministic guardrail. Reads the hook payload on stdin, inspects the
    actual command string, exits 2 with a reason on stderr to cancel the call.
Args:
    stdin (json): {"tool_name": "Bash", "tool_input": {"command": "..."}}
Returns:
    exit 0 to allow, exit 2 to block. The reason goes to stderr.
"""
import json
import sys

# Each rule is (substrings that must ALL appear, the reason Claude is told).
RULES = [
    (("git push", "--force"), "force-push rewrites shared history; open a pull request"),
    (("git push", "-f"),      "force-push rewrites shared history; open a pull request"),
    (("--no-verify",),        "the repository's own git hooks must run"),
    (("DROP TABLE",),         "destructive SQL; put it in a reviewed migration file"),
    (("DROP DATABASE",),      "destructive SQL; put it in a reviewed migration file"),
    (("TRUNCATE",),           "destructive SQL; put it in a reviewed migration file"),
]

payload = json.load(sys.stdin)
command = payload.get("tool_input", {}).get("command", "")

for needles, reason in RULES:
    if all(n in command for n in needles):
        # stderr + exit 2 is the whole mechanism: cancel the call, tell Claude why.
        print(f"BLOCKED: {reason}", file=sys.stderr)
        sys.exit(2)

sys.exit(0)          # 0 = allow
EOF

chmod +x ~/ccdv-practice/21/.claude/hooks/guard-bash.py
echo "wrote ~/ccdv-practice/21/.claude/hooks/guard-bash.py"

# %%bash
# Run the hook the way Claude Code runs it: pipe ONE JSON object in on stdin and
# read the exit code back out. Nothing here executes the commands themselves —
# they are only strings inside the payload, which is the point of a PreToolUse hook.
cd ~/ccdv-practice/21

for command in "git status --short" \
               "git push --force origin main" \
               "git commit --no-verify -m wip"; do
  echo "{\"session_id\": \"demo\", \"hook_event_name\": \"PreToolUse\", \
         \"tool_name\": \"Bash\", \"tool_input\": {\"command\": \"$command\"}}" \
    | python3 .claude/hooks/guard-bash.py
  # $? is the hook's exit code: 0 allows, 2 BLOCKS the call, anything else does
  # not block. Any stderr line above is what Claude is told as the reason.
  echo "  exit $?  <-  $command"
done

# %% [markdown]
# ### Feature 4 — Wiring it up in `settings.json`
#
# `matcher` is a regular expression against the **tool name**: `Bash`,
# `Edit|Write|MultiEdit`, or `mcp__<server>__<tool>` for an MCP tool. Omit it, or
# use `""`, to match every tool.
#
# Where the file lives decides who is protected:
#
# ```
#    ~/.claude/settings.json         user — every project you open
#    .claude/settings.json           project — committed, protects the whole team  ◄── guardrails
#    .claude/settings.local.json     project — personal, git-ignored
#    enterprise managed settings     org-wide, cannot be overridden by the above
# ```
#
# Team guardrails go in the **committed** project file. Putting them in
# `settings.local.json` protects exactly one laptop.
#
# Two operational facts worth remembering: `/hooks` in the CLI shows what is
# active, and hooks are **snapshotted at session start**, so editing the file
# mid-session does not take effect until you review or restart.

# %%bash
# The config that binds the events to the scripts. Every command here is either
# one of our scratch guards or a harmless no-op — this is a lesson, not a policy.
cat > ~/ccdv-practice/21/.claude/settings.json <<'EOF'
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/guard-bash.py",
            "timeout": 10
          }
        ]
      },
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/guard-paths.py"
          }
        ]
      },
      {
        "matcher": "mcp__postgres__.*",
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/guard-sql.py"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "echo formatted || true"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/require-checks.py"
          }
        ]
      }
    ]
  }
}
EOF

echo "wrote ~/ccdv-practice/21/.claude/settings.json"
cat ~/ccdv-practice/21/.claude/settings.json

# %%
# A matcher is a regex against the TOOL NAME, so which tools it catches is
# something you can check yourself rather than guess at.
TOOL_NAMES = ("Bash", "Edit", "Write", "Read", "mcp__postgres__query")

for matcher in ("Bash", "Edit|Write|MultiEdit", "mcp__postgres__.*"):
    caught = [t for t in TOOL_NAMES if re.fullmatch(matcher, t)]
    print(f"matcher {matcher!r:24s} catches {caught}")

# The CLI is only reported on here — nothing in this lesson starts an agent.
# If it prints `command not found`, the config above is still the answer.
!claude --version

# %% [markdown]
# ### Feature 5 — Structured output: `allow`, `deny`, or `ask`
#
# A bare exit code is a yes or a no. Printing JSON on stdout instead lets the
# hook carry a **decision plus a reason**, and adds a third option:
#
# - `"allow"` — permit it without a prompt.
# - `"deny"` — cancel it, and tell Claude why.
# - `"ask"` — turn a silent block into a permission prompt for the human.
#
# `"ask"` is the right answer whenever an action is dangerous *but sometimes
# legitimate*. A migration really does have to drop a column one day.
#
# Field names here are version-dependent. This is the current shape.

# %%bash
# A PreToolUse hook for Edit|Write|MultiEdit: protect paths, not commands.
cat > ~/ccdv-practice/21/.claude/hooks/guard-paths.py <<'EOF'
#!/usr/bin/env python3
"""PreToolUse hook for Edit|Write|MultiEdit — protect sensitive paths.

Purpose:
    Show structured hook output: a permissionDecision with a reason, instead of
    a bare exit code. Secrets are denied outright; infrastructure is 'ask'.
Args:
    stdin (json): {"tool_input": {"file_path": "..."}}
Returns:
    exit 0 always. The verdict is carried in the JSON printed on stdout.
"""
import json
import pathlib
import sys

DENY = (".env", "secrets/", "/.git/", ".pem")        # never, silently
ASK  = ("terraform/", "migrations/")                 # dangerous, sometimes right

payload = json.load(sys.stdin)
path = str(pathlib.Path(payload.get("tool_input", {}).get("file_path", "")))

decision, reason = "allow", "not a protected path"
if any(p in path for p in DENY):
    decision = "deny"
    reason = f"{path} is protected; propose the change in chat instead"
elif any(p in path for p in ASK):
    decision = "ask"
    reason = f"{path} changes infrastructure; a human should approve this edit"

print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": decision,               # "allow" | "deny" | "ask"
    "permissionDecisionReason": reason,
}}))
sys.exit(0)          # the decision is in the JSON, not in the exit code
EOF

chmod +x ~/ccdv-practice/21/.claude/hooks/guard-paths.py

# Three edits, exactly as Claude Code would hand them to the hook. The exit code
# is 0 every time now — read the JSON on stdout for the verdict instead.
cd ~/ccdv-practice/21
for file_path in "src/app.py" ".env" "terraform/main.tf"; do
  printf '%-18s -> ' "$file_path"
  echo "{\"hook_event_name\": \"PreToolUse\", \"tool_name\": \"Edit\", \
         \"tool_input\": {\"file_path\": \"$file_path\"}}" \
    | python3 .claude/hooks/guard-paths.py
done

# %% [markdown]
# ### Feature 6 — The `Stop` hook, and the loop it can cause
#
# Claude ends its turn saying "implementation complete" while the tests are red.
# A `Stop` hook is the checklist at the end of the shift: it runs the check, and
# returns `{"decision": "block", "reason": …}` when the work is not done. Claude
# is told to keep going.
#
# The trap is obvious once you see it. A `Stop` hook that always blocks makes a
# session that can never end. So the payload carries **`stop_hook_active`**:
# `true` means this stop was *already* triggered by a hook. Check it first and
# let the turn end.
#
# A `PostToolUse` hook on `Edit` is the wrong answer to this stem. It fires after
# every single edit — noisy and slow — and it cannot stop a turn from ending.

# %%bash
# A Stop hook. The "check" here is a marker file so the lesson has something
# real and harmless to succeed or fail on; in a project it would be your tests.
cat > ~/ccdv-practice/21/.claude/hooks/require-checks.py <<'EOF'
#!/usr/bin/env python3
"""Stop hook — refuse to finish while the project's checks have not passed.

Purpose:
    Show a blocking Stop decision, and the stop_hook_active guard that keeps it
    from looping forever.
Args:
    stdin (json): {"stop_hook_active": bool, "transcript_path": "..."}
Returns:
    exit 0 always; prints a block decision on stdout when work remains.
"""
import json
import os
import pathlib
import sys

payload = json.load(sys.stdin)

# ALWAYS first. True means a hook already blocked one stop, so let this one end.
if payload.get("stop_hook_active"):
    sys.exit(0)

# Stand-in for "the test suite passed". Harmless, and the notebook controls it.
passed = pathlib.Path(os.environ.get("CCDV_CHECKS_MARKER", "/nonexistent")).exists()

if not passed:
    print(json.dumps({"decision": "block",
                      "reason": "the project checks have not passed yet; run them and fix the failures"}))
sys.exit(0)
EOF

chmod +x ~/ccdv-practice/21/.claude/hooks/require-checks.py

# %%bash
cd ~/ccdv-practice/21

# Point the hook at a marker file that does not exist: the checks are "failing".
export CCDV_CHECKS_MARKER=~/ccdv-practice/21/checks-not-run-yet

echo '{"hook_event_name": "Stop", "stop_hook_active": false}' \
  | python3 .claude/hooks/require-checks.py
echo "  ^ checks failing, first stop  — a block decision, so Claude keeps working"

echo '{"hook_event_name": "Stop", "stop_hook_active": true}' \
  | python3 .claude/hooks/require-checks.py
echo "  ^ checks failing, second stop — silent, because stop_hook_active is true"

# Now make the checks 'pass', and the same hook lets the turn end.
export CCDV_CHECKS_MARKER=~/ccdv-practice/21/checks-passed
echo ok > ~/ccdv-practice/21/checks-passed

echo '{"hook_event_name": "Stop", "stop_hook_active": false}' \
  | python3 .claude/hooks/require-checks.py
echo "  ^ checks passing, first stop  — no output at all, the turn may end"

# %% [markdown]
# **Practical use:** if the requirement says *must never*, put it in code — a
# `PreToolUse` hook when the verdict depends on the arguments, a permission deny
# rule when a tool-name pattern is enough. Block with **exit 2** and put the
# reason on **stderr**, or return a `permissionDecision` and use `ask` when the
# action is dangerous but sometimes right. Commit team guardrails to
# `.claude/settings.json`. And remember what a hook cannot do: it catches the
# cases you thought of, so scoped credentials and a sandbox still sit underneath.
#
# **🏋️ Practice:** the guard below blocks a force-push but lets a normal push
# through. The `--no-verify` rule that skips the repository's own git hooks is
# marked `# <-` and is already in place, so the cell passes as shipped.
#
# To feel it: delete the `("--no-verify",)` rule, re-run, and watch that command
# come back allowed while the assert fails. Put it back. Then extend it: return
# `"ask"` instead of exit 2 when the branch is not `main`, so a force-push to a
# personal branch just needs a human to nod.

# %%
# Your turn — see the `# <-` marker. This is the same rule table as
# guard-bash.py, inlined so you can edit it without leaving the notebook.
GUARD_RULES = [
    (("git push", "--force"), "force-push rewrites shared history"),
    (("git push", "-f"),      "force-push rewrites shared history"),
    (("--no-verify",),        "the repository's own git hooks must run"),   # <- the rule
    (("DROP TABLE",),         "destructive SQL; use a reviewed migration"),
]

CASES = [
    # (the command in tool_input,   the exit code Claude Code should see)
    ("git status --short",           0),
    ("git push origin feature/x",    0),
    ("git push --force origin main", 2),
    ("git commit --no-verify -m x",  2),
    ("psql -c 'DROP TABLE orders'",  2),
]

codes = []
for command, expected in CASES:
    # 2 blocks the call and sends the reason to Claude. 0 allows it.
    # 1 would NOT block — that is the trap.
    code, reason = 0, "allowed"
    for needles, why in GUARD_RULES:
        if all(n in command for n in needles):
            code, reason = 2, why
            break
    codes.append(code)
    print(f"exit {code} (want {expected})  {command:32s} {reason}")

assert codes == [want for _, want in CASES], "a destructive command got through"
assert 1 not in codes, "blocking must be exit 2, never 1"
print("\nevery destructive command is blocked with exit 2")
