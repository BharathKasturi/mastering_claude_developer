# %% [markdown]
# # Topic 4.6 · Configuration Management: CLAUDE.md, settings.json, Model Pinning, Prompt Versioning
#
# 🎓 *CCDV-F objective 14 of 25 · exam weight 4.1% · Claude Code lesson*
#
# **Why this matters.** Configuration is every input to Claude that is *not* the
# user's message: instruction files, permission settings, the model string, the
# prompt text, the plugins you depend on. Each one has a **home**, a
# **precedence rank** and a **version** — and none of them may contain a secret.
# Get the ranking wrong and a personal `allow` silently loses to a team `deny`.
# Get the pinning wrong and accuracy drops overnight with no deploy.
#
# **What you'll learn.**
# - Build the **`CLAUDE.md` hierarchy** — all files load, broadest to narrowest — plus `@imports` and `.claude/rules/`.
# - Resolve a **`settings.json`** conflict: narrowest wins, **deny beats allow**, managed beats everything.
# - **Pin a model**: alias versus dated snapshot, and where the string should live.
# - **Version a prompt** like code: eval gate, canary slice, logged `prompt_version`, rollback.
# - Pin **plugin and MCP dependencies** and keep every secret out of every config file.
#
# **What you need.** Nothing. Every file this lesson writes goes under a scratch
# directory, `~/ccdv-practice/09` — never into a real project. The cells that
# call the `claude` CLI ask only for its version, and print `command not found`
# if the CLI is not installed.

# %%
# Load the shared setup: the API client and MODEL.
%run ./00_setup.ipynb

# %% [markdown]
# ### Feature 1 — The `CLAUDE.md` hierarchy, `@imports` and `.claude/rules/`
#
# Claude Code loads memory files broadest to narrowest and concatenates them all
# into the system context. **Every one of them applies at once.**
#
# ```
# LOADED IN THIS ORDER (all of them, every session)
# 1. managed policy CLAUDE.md   macOS: /Library/Application Support/ClaudeCode/CLAUDE.md
#                               Linux: /etc/claude-code/CLAUDE.md
# 2. ~/.claude/CLAUDE.md        user — your habits in every repo
# 3. <repo>/CLAUDE.md           project — committed, team truth
#    <repo>/.claude/CLAUDE.md   (alternative location, same rank)
# 4. <repo>/CLAUDE.local.md     personal overrides, git-ignored
# 5. <repo>/.claude/rules/*.md  topic rules; optional `paths:` frontmatter
# 6. <subdir>/CLAUDE.md         loaded LAZILY, when Claude reads files there
# ```
#
# **Precedence for instructions is "later text refines earlier text", never
# "later text deletes earlier text".** That is the single biggest difference from
# `settings.json`, which really does resolve conflicts by rank.
#
# Three facts the exam checks: `CLAUDE.local.md` is auto-added to `.gitignore`
# because it is *your* file; a `.claude/rules/` file with no `paths:` frontmatter
# always loads; and `/init` generates the first project `CLAUDE.md` — it does not
# create settings.

# %%
%%bash
# Build a miniature repo in the scratch directory. Nothing here touches a real
# project: every path is under ~/ccdv-practice/09.
BASE=~/ccdv-practice/09
mkdir -p "$BASE/.claude/rules" "$BASE/services/legacy"

# 3. Project memory — committed, the team's truth. @imports pull in other files
#    at load time; they resolve relative to the importing file and nest 5 deep.
cat > "$BASE/CLAUDE.md" <<'EOF'
# Project standards
@README.md
@docs/architecture.md

- Tests are written with pytest.
- Never commit a file containing a credential.
EOF

# 4. Personal overrides — git-ignored by design, yours only.
cat > "$BASE/CLAUDE.local.md" <<'EOF'
- Prefer verbose diffs when explaining a change to me.
EOF

# 5. A path-scoped rule. With `paths:` frontmatter it applies only when Claude
#    touches matching files; without frontmatter it would always load.
cat > "$BASE/.claude/rules/api-standards.md" <<'EOF'
---
paths:
  - "src/api/**/*.py"
---
# API standards
- Every endpoint returns a Pydantic model; never a bare dict.
EOF

# 6. A subdirectory CLAUDE.md — loaded LAZILY, only once Claude reads files here.
cat > "$BASE/services/legacy/CLAUDE.md" <<'EOF'
- This service uses unittest. Do not migrate its tests to pytest.
EOF

echo "--- files created ---"
find "$BASE" -name 'CLAUDE*.md' -o -name '*.md' -path '*rules*' | sort

# %%
# The same scratch root, as a Python path. Every file this notebook writes from
# here on goes under it.
import pathlib

BASE = pathlib.Path.home() / "ccdv-practice" / "09"
print("scratch root:", BASE)

# Which instruction files are in context? All the eager ones, plus any
# subdirectory file whose tree Claude has actually read.
SCENARIOS = [
    ("session started at the repo root, nothing read yet", []),
    ("after Claude opens services/legacy/app.py", ["services/legacy/app.py"]),
]

for label, files_touched in SCENARIOS:
    loaded = [("<managed>/CLAUDE.md", "managed policy — always"),
              ("~/.claude/CLAUDE.md", "user — always"),
              ("CLAUDE.md", "project — always"),
              ("CLAUDE.local.md", "personal, git-ignored — always"),
              (".claude/rules/api-standards.md", "path-scoped rule")]
    # A subdirectory file is lazy: it enters context only when Claude reads there.
    if any(p.startswith("services/legacy/") for p in files_touched):
        loaded.append(("services/legacy/CLAUDE.md", "subdirectory — loaded on first read"))

    print(f"\n{label}:")
    for path, why in loaded:
        print(f"   {path:32s} {why}")

print("\nAll of them apply together — the narrower text refines the broader one.")
print("A rule that must apply whenever those files are involved belongs in")
print(".claude/rules/ with `paths:`, not in a lazily-loaded subdirectory file.")

# %% [markdown]
# ### Feature 2 — `settings.json` precedence and the permission model
#
# Settings are not instructions. They really do resolve conflicts by rank,
# highest first:
#
# ```
# managed  >  command line  >  .claude/settings.local.json
#          >  .claude/settings.json  >  ~/.claude/settings.json
# ```
#
# | Key | Meaning | Note |
# |---|---|---|
# | `permissions.allow` | patterns that run without prompting | `Bash(cmd *)` is a prefix match; `Read(path)` uses gitignore-style globs |
# | `permissions.deny` | patterns that are **never** allowed | **deny beats allow** at every layer |
# | `permissions.ask` | always prompt, even under a broader allow | `git push`, deploy scripts |
# | `permissions.defaultMode` | `default` \| `acceptEdits` \| `plan` \| `bypassPermissions` | `bypassPermissions` can be blocked by `disableBypassPermissionsMode` in managed settings |
# | `env` | variables injected into every session | **non-secret** config only |
# | `model` | default model for the session | overridden by `--model` and `ANTHROPIC_MODEL` |
#
# The subtlety: **scalar keys** (like `model`) are decided by rank, but the
# `allow`/`deny`/`ask` **arrays are merged across every layer** and then `deny`
# is evaluated first. So a user-level `allow: ["Read"]` can never re-permit a
# project `deny: ["Read(.env)"]`.

# %%
# Write a realistic project settings file into the scratch repo.
settings_path = BASE / ".claude/settings.json"
settings_path.parent.mkdir(parents=True, exist_ok=True)
settings_path.write_text(textwrap.dedent("""
    {
      "permissions": {
        "allow": ["Bash(npm test)", "Bash(git diff *)", "Read", "Edit"],
        "deny":  ["Bash(rm -rf *)", "Read(.env)", "Read(./secrets/**)"],
        "ask":   ["Bash(git push *)"],
        "defaultMode": "acceptEdits"
      },
      "env": { "NODE_ENV": "test" },
      "model": "claude-sonnet-5",
      "hooks": {
        "PreToolUse": [
          { "matcher": "Bash",
            "hooks": [{ "type": "command", "command": "./scripts/guard.sh" }] }
        ]
      }
    }
""").lstrip(), encoding="utf-8")
print("wrote", settings_path)

print("\n--- as Claude Code reads it ---")
print(json.dumps(json.loads(settings_path.read_text())["permissions"], indent=2))

# %%
# The layers, lowest rank first. Real files; here as dicts so we can resolve them.
LAYERS = [
    ("user",    {"permissions": {"allow": ["Read"]}, "model": "claude-haiku-4-5"}),
    ("project", {"permissions": {"deny": ["Read(.env)", "Bash(rm -rf *)"]},
                 "model": "claude-sonnet-5"}),
    ("local",   {"permissions": {"allow": ["Bash(npm test)"]}}),
    ("cli",     {"model": "claude-opus-5"}),
    ("managed", {"permissions": {"deny": ["Read(**/.env)", "Bash(curl *)"]}}),
]

# A SCALAR key resolves by rank: the highest layer that sets it wins outright.
model_value, model_layer = None, "unset"
for name, layer in LAYERS:               # LAYERS is ordered lowest rank -> highest
    if "model" in layer:
        model_value, model_layer = layer["model"], name

# The allow/deny/ask ARRAYS behave nothing like that: they merge across every
# layer, and deny is evaluated first against the merged result.
MERGED = {"allow": [], "deny": [], "ask": []}
for _, layer in LAYERS:
    for bucket in MERGED:
        MERGED[bucket] += layer.get("permissions", {}).get(bucket, [])

print("model resolves to:", (model_value, model_layer), "  <- highest layer that sets it")
print("merged deny list :", MERGED["deny"])
print("merged allow list:", MERGED["allow"])

# %% [markdown]
# ### Feature 3 — Model version pinning: aliases versus dated snapshots
#
# ```
# alias   claude-opus-5, claude-sonnet-5, claude-haiku-4-5
#         -> notebooks, evals of "the current model", early dev
# dated   claude-haiku-4-5-20251001, claude-sonnet-4-5-20250929
#         -> anything with an eval baseline, a regression suite, or an SLA
#
# Rule: pin in production; bump on purpose; run the eval suite on the new ID
#       BEFORE flipping the config, not after.
# ```
#
# An alias floats to the newest snapshot of that family, so behaviour can change
# with no deploy and no prompt change. That is the whole of the classic scenario:
# accuracy drops from 97% to 91% overnight, the input distribution is unchanged,
# and the cause is a pin that was never a pin.
#
# Where dated snapshots exist is version-dependent — the Claude 5 family
# currently publishes bare IDs, while the 4.x models carry `-YYYYMMDD`. **Never
# hand-construct a date suffix.** Query `client.models.list()`. Bedrock prefixes
# `anthropic.`; Vertex uses `@` before the date. Same concept, different string.

# %%
# Is this model string production-safe? Vertex writes the date after an '@';
# every other platform uses a -YYYYMMDD suffix.
ALIASES = ("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5")

for model_id in ["claude-haiku-4-5", "claude-haiku-4-5-20251001",
                 "anthropic.claude-opus-5", "claude-opus-4-5@20251101", MODEL]:
    bare = model_id.removeprefix("anthropic.")               # Bedrock prefix
    if "@" in model_id or re.search(r"-\d{8}$", model_id):
        status = "pinned   (dated snapshot — frozen weights and behaviour)"
    elif bare.endswith("-latest") or bare in ALIASES:
        status = "floating (alias — a new snapshot can change behaviour silently)"
    else:
        status = "unknown  (verify it against client.models.list())"
    print(f"{model_id:30s} {status}")

# %%
# Put the model string in ONE place: settings.json `model`, or an env var read at
# startup. Scattering literals through the code means many diffs to review.
models_path = BASE / "config/models.json"
models_path.parent.mkdir(parents=True, exist_ok=True)
models_path.write_text(textwrap.dedent("""
    {
      "production": "claude-haiku-4-5-20251001",
      "eval_baseline": "claude-haiku-4-5-20251001",
      "development": "claude-sonnet-5"
    }
""").lstrip(), encoding="utf-8")
print("wrote", models_path)

# Discover what is actually live instead of guessing at a date suffix.
if client is None:
    print("\n[skipped — set ANTHROPIC_API_KEY to run client.models.list()]")
    print("for m in client.models.list(): print(m.id, m.created_at)")
else:
    for model in list(client.models.list())[:5]:
        print(model.id)

# %%
# The CLI reports its own version — pin that too, alongside the SDK, because a
# version bump can change defaults. Run it exactly as you would in a terminal.
!claude --version

# %% [markdown]
# ### Feature 4 — Prompt versioning and A/B rollout
#
# A prompt is source code. It gets a version, a changelog entry, an eval before
# promotion, and a rollback path. An in-place edit with no version bump is the
# configuration equivalent of force-pushing to `main`: a week later, nobody can
# say which of four edits caused the regression.
#
# Log `prompt_version`, `model` and `request_id` on **every** call. Then a
# regression is attributable to *which* change — prompt, model, or data. And an
# A/B split needs the **same model pin on both arms**, or you are measuring two
# variables at once.

# %%
import hashlib

# Two versions, side by side as files. v4 is not promoted until it beats v3 on
# the eval set; until then it only sees a canary slice.
PROMPT_FILES = {
    "prompts/support_triage_v3.md":
        "Classify the ticket. Reply with a category and a severity from 1 to 5.\n",
    "prompts/support_triage_v4.md":
        "Classify the ticket. Reply with a category, a severity from 1 to 5, "
        "and every numeric fact you were given.\n",
}
for relpath, body in PROMPT_FILES.items():
    prompt_path = BASE / relpath
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(body, encoding="utf-8")
    print("wrote", prompt_path)

PROMPTS = {
    "v3": {"system": (BASE / "prompts/support_triage_v3.md").read_text(),
           "model": "claude-haiku-4-5-20251001",          # SAME pin on both arms
           "eval": "evals/triage_v3.json", "score": 0.91},
    "v4": {"system": (BASE / "prompts/support_triage_v4.md").read_text(),
           "model": "claude-haiku-4-5-20251001",
           "eval": "evals/triage_v4.json", "score": 0.94},
}
ACTIVE = "v3"                          # promoted only after v4 beats v3 on the eval set
CANARY_VERSION, CANARY_SHARE = "v4", 0.10   # 10% of traffic, logged so the two compare

# The canary must be stable PER USER, not random per request. Python's built-in
# hash() is salted per process, so it would move users between arms on every
# restart and make the comparison meaningless.
users = [f"user-{i}" for i in range(200)]
served = []
for user_id in users:
    slice_of_100 = int(hashlib.md5(user_id.encode()).hexdigest(), 16) % 100
    served.append(CANARY_VERSION if slice_of_100 < CANARY_SHARE * 100 else ACTIVE)

print(f"\ncanary split: {served.count('v4')}/200 on v4, {served.count('v3')}/200 on v3")

# One user from each arm, with the log line that makes a regression attributable.
for arm in ("v3", "v4"):
    user = users[served.index(arm)]
    print(json.dumps({"user": user, "prompt_version": arm,
                      "model": PROMPTS[arm]["model"], "request_id": f"req_{user}"}))

# Promotion is gated on the eval, not on a hunch — and only when the model pin
# is identical, or two variables changed and the result means nothing.
candidate, incumbent = "v4", ACTIVE
if PROMPTS[candidate]["model"] != PROMPTS[incumbent]["model"]:
    promoted = incumbent
elif PROMPTS[candidate]["score"] > PROMPTS[incumbent]["score"]:
    promoted = candidate
else:
    promoted = incumbent
print("\npromotion decision:", promoted, "should become ACTIVE")

# %% [markdown]
# ### Feature 5 — Plugin and MCP pinning, and where secrets go
#
# ```
# CONFIG THAT CLAUDE READS           CONFIG THAT CLAUDE MUST NOT READ
# CLAUDE.md / rules / skills         .env, *.pem, cloud credentials
# settings.json (permissions, env)   secrets manager values
# .mcp.json (server list, ${VARS})   the resolved token itself
# prompt files, tool descriptions
# ```
#
# - **Pin the version** for every plugin, MCP server and SDK: `@2025.4.8`, not
#   `@latest`. A floating dependency changes your tool schemas under you, and
#   that changes what Claude does.
# - **Reference secrets by environment-variable expansion** — `${GITHUB_TOKEN}`,
#   never a literal. `.mcp.json` is committed and shared.
# - **Never put a secret in `CLAUDE.md` or `settings.json`.** Both are read into
#   context and both are commonly committed. Add `Read(.env)` and
#   `Read(./secrets/**)` to `permissions.deny` so it cannot be read by accident.
# - **Lockfiles are config too.** Pin the `anthropic` / `@anthropic-ai/sdk`
#   version: an SDK upgrade can change default timeouts and retries.

# %%
# One good .mcp.json, and one deliberately bad one so the scanner has something
# to catch. Both land under the scratch root.
MCP_FILES = {
    ".mcp.json": """
        {
          "mcpServers": {
            "github": {
              "command": "npx",
              "args": ["-y", "@modelcontextprotocol/server-github@2025.4.8"],
              "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}" }
            }
          }
        }
    """,
    "bad_examples/.mcp.json": """
        {
          "mcpServers": {
            "scratch": {
              "command": "npx",
              "args": ["-y", "@somevendor/scratch-server@latest"],
              "env": { "SCRATCH_TOKEN": "sk-live-abcd1234efgh" }
            }
          }
        }
    """,
}
for relpath, body in MCP_FILES.items():
    mcp_path = BASE / relpath
    mcp_path.parent.mkdir(parents=True, exist_ok=True)
    mcp_path.write_text(textwrap.dedent(body).strip() + "\n", encoding="utf-8")
    print("wrote", mcp_path)

# The two things that must never be in a config file. This is the check you put
# in CI, so "don't commit secrets" stops being a request.
SECRET_PATTERNS = [
    (r"sk-[A-Za-z0-9_-]{8,}", "an API key literal"),
    (r"ghp_[A-Za-z0-9]{8,}", "a GitHub token literal"),
    (r"(?i)(password|secret|token)\s*[:=]\s*[\"'][^\"'$][^\"']{6,}", "a credential literal"),
    (r"@latest", "a floating dependency version"),
]

print()
for relpath in (".mcp.json", ".claude/settings.json", "bad_examples/.mcp.json"):
    scanned = BASE / relpath
    text = scanned.read_text(encoding="utf-8")
    findings = [f"{why} in {scanned.name}" for pattern, why in SECRET_PATTERNS
                if re.search(pattern, text)]
    print(f"{'CLEAN' if not findings else 'FAIL '} {relpath}")
    for finding in findings:
        print("       -", finding)

print("\nfiles this lesson created, all under the scratch root:")
for created in sorted(BASE.rglob("*")):
    if created.is_file():
        print("  ", created.relative_to(BASE))

# %% [markdown]
# **Practical use:** ask two questions of every configuration stem. First, *is
# this an instruction or a setting?* Instruction files (`CLAUDE.md`, rules) all
# load and refine each other; settings resolve by rank, with **deny beating
# allow** and managed policy beating everything. Second, *is this pinned?* The
# model id, the plugin version, the SDK version and the prompt text are all
# dependencies — pin them, diff them, and bump them on purpose with an eval in
# front. And no config file Claude reads or git tracks may hold a secret; use
# `${ENV_VAR}` expansion and deny `Read(.env)`.
#
# **🏋️ Practice:** the personal `allow` that did not work. A developer puts
# `"allow": ["Read"]` in `~/.claude/settings.json`, but Claude still refuses to
# open `.env`, because the project and managed layers deny it.
#
# The fix — checking the buckets in the order `deny`, `ask`, `allow` — is marked
# `# <-` below and is already in place, so the cell passes as shipped. To feel
# it: change that order to `("allow", "ask", "deny")`, re-run, and watch `.env`
# become readable and the assert fail. Put it back. Then add another `ask`
# pattern and check that it prompts even though a broader allow matches.

# %%
# Your turn — see the `# <-` marker.
MERGED["ask"].append("Bash(git push *)")

CALLS = [
    ("Read(.env)",         "deny"),    # project + managed deny; a user allow cannot lift it
    ("Read(src/app.py)",   "allow"),   # the bare `Read` allow covers it
    ("Bash(npm test)",     "allow"),
    ("Bash(rm -rf /tmp)",  "deny"),
    ("Bash(git push *)",   "ask"),     # ask wins over a broader allow
]

verdicts = {}
for call, expected in CALLS:
    verdict = "ask"                                    # unmatched calls prompt by default
    for bucket in ("deny", "ask", "allow"):            # <- deny is evaluated FIRST
        matched = False
        for pattern in MERGED[bucket]:
            if "(" not in pattern:
                # `Read` with no argument matches every Read.
                matched = call.startswith(pattern + "(") or call == pattern
            elif "(" in call:
                # `Bash(cmd *)` is a prefix match; without the * it is exact.
                head, arg = pattern.rstrip(")").split("(", 1)
                call_head, call_arg = call.rstrip(")").split("(", 1)
                matched = head == call_head and (
                    call_arg.startswith(arg.rstrip("*")) if arg.endswith("*")
                    else call_arg == arg)
            else:
                matched = False
            if matched:
                break
        if matched:
            verdict = bucket
            break

    verdicts[call] = verdict
    print(f"{verdict:5s} (want {expected:5s})  <- {call}")

assert verdicts["Read(.env)"] == "deny", "a user-level allow must not lift a deny"
assert all(verdicts[c] == want for c, want in CALLS), "a permission resolved wrongly"
print("\ndeny beats allow at every layer; managed policy cannot be undone locally")
