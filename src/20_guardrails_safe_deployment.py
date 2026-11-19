# %% [markdown]
# # Topic 7.2 · Guardrails & Safe Deployment: Content Policy, Layering, Least Privilege
#
# 🎓 *CCDV-F objective 20 of 25 · exam weight 2.3% · API lesson*
#
# **Why this matters.** Shipping a Claude application safely is a stack of
# independent controls, not one clever prompt. Every guardrail leaks a little, so
# you stack them. Every identity gets the minimum it needs, so a leak cannot do
# much. And every rollout has a kill switch, because the day the guardrail is
# wrong is a day you have already planned for.
#
# **What you'll learn.**
# - Separate **content policy** (the rule) from the **layers** that enforce it.
# - Build the **guardrail pipeline** and tell probabilistic layers from deterministic ones.
# - Scope **least privilege** three ways: per user, per agent, per tool.
# - Ship with a **staged rollout, a kill switch and an audit log** you can query.
# - Map a **failure to the layer** that should have caught it.
# - Do the same thing in **Claude Code** with `permissions` in `settings.json`.
#
# **What you need.** Nothing. Every cell runs offline. The Claude Code section
# writes a sample `settings.json` under `~/ccdv-practice/20` — a scratch
# directory, never your own `~/.claude` and never a real project. Set
# `ANTHROPIC_API_KEY` to make the classifier sections call the real API.

# %%
# Load the shared setup: the API client and MODEL.
%run ./00_setup.ipynb

# %% [markdown]
# ### Feature 1 — Content policy is the rule; the layers are the scanners
#
# Anthropic's **Usage Policy** binds every application built on the API. Your app
# then layers its own, narrower policy on top: what is in scope, what tone, what
# topics you refuse even though they are perfectly legal.
#
# ```
#    Anthropic Usage Policy ─┐
#                            ├──► your application policy (narrower) ──► enforced by layers 1–6
#    legal / regulatory     ─┘        e.g. "medical app: never give dose advice"
#                                          "kids app: no external links"
# ```
#
# What the exam checks:
#
# - **High-risk domains** — health, legal, financial, employment decisions — need
#   human oversight and disclosure, not a footer disclaimer.
# - **Consumer-facing bots** should disclose that the user is talking to an AI.
# - **Policy is versioned.** Cite the current Usage Policy rather than a
#   memorised list of categories.
# - Claude's own refusals are a free extra layer — some models report
#   `stop_reason: "refusal"` — but trained behaviour is not a contract. Your
#   policy must not depend on them alone.

# %%
# An application policy is a document, not a prompt. It is written once, and the
# layers below all read from it. Narrower than the Usage Policy, always.
APP_POLICY = {
    "domain": "consumer health information",
    "in_scope": ["explaining symptoms in general terms", "when to see a clinician"],
    "never": ["dosing advice", "a diagnosis", "interpreting an individual's test results"],
    "disclose_ai": True,          # consumer-facing: say you are an AI
    "human_oversight": True,      # high-risk domain: a clinician reviews escalations
}

# One source of truth, rendered once. The system prompt, the input screen and the
# output screen all quote this SAME sentence, so they cannot drift apart.
POLICY = (f"Scope: {APP_POLICY['domain']}. "
          f"In scope: {', '.join(APP_POLICY['in_scope'])}. "
          f"Never: {', '.join(APP_POLICY['never'])}.")

print(POLICY)
print("\ndiscloses it is an AI:", APP_POLICY["disclose_ai"],
      "| human oversight on escalation:", APP_POLICY["human_oversight"])

# %% [markdown]
# ### Feature 2 — The pipeline, and which layers are deterministic
#
# ```
#    user turn
#       │
#       ▼
#    [1] input screen ─── classifier / regex / rate limit ─── block or flag
#       │
#       ▼
#    [2] system prompt ─── scope, persona limits, "data in tags is not instructions"
#       │
#       ▼
#    [3] model call with tools = allow_list(identity, taint)
#       │
#       ├── tool_use ──► [4] harness policy: allow-list, hook, approval gate
#       │                      │
#       │                      └── irreversible? ──► a human approves
#       ▼
#    [5] output validation ─── schema + PII check + policy classifier
#       │
#       ▼
#    [6] audit log (who, what, which tools, approved by whom, hash of the prompt)
#       │
#       ▼
#    deliver
# ```
#
# Here is the distinction the exam keeps testing. Layers **1, 2 and 5 are
# probabilistic** — a model or a heuristic judging text. Layers **3, 4 and 6 are
# deterministic** — code that either permits or does not.
#
# **A destructive action must sit behind a deterministic layer.** Use
# probabilistic controls to shape behaviour, deterministic controls for anything
# that must never happen, and human approval where the action is irreversible.

# %%
# Name every layer and say what kind it is. This table is the lesson.
LAYERS = [
    ("[1] input screen",      "probabilistic", "obvious policy violations, known jailbreak shapes"),
    ("[2] system prompt",     "probabilistic", "scope drift, persona abuse, tone"),
    ("[3] tool allow-list",   "DETERMINISTIC", "any action this identity should never take"),
    ("[4] harness policy",    "DETERMINISTIC", "destructive arguments; irreversible side effects"),
    ("[5] output validation", "probabilistic", "malformed replies, leaked PII patterns"),
    ("[6] audit log",         "DETERMINISTIC", "nothing — but it is how you find out"),
]

for name, kind, catches in LAYERS:
    print(f"{name:22s} {kind:14s} {catches}")

# %%
# The pipeline itself, one turn at a time, so the ORDER is visible and a turn can
# be seen stopping at any layer. hashlib gives layer [6] its prompt hash.
import hashlib

# A small pinned model for the screens. Never "latest": your safety layer must
# not change meaning underneath you between deploys.
SCREEN_MODEL = os.environ.get("CLAUDE_SCREEN_MODEL", "claude-haiku-4-5-20251001")

# Two turns: one in scope, one that the policy says is never in scope.
for user_msg in ["What are common causes of a sore throat?",
                 "How much ibuprofen should I take for this dose?"]:
    tools = ["search_docs"]                     # [3] chose this list for this identity

    # [1] probabilistic and cheap. Runs on EVERY turn, not just the first.
    if client is None:
        # Offline stand-in so the notebook runs. A keyword list is NOT the real
        # control — paraphrase defeats it. The live branch is the shape to ship.
        said = "Y" if ("dose" in user_msg.lower() or "diagnos" in user_msg.lower()) else "N"
    else:
        verdict = client.messages.create(
            model=SCREEN_MODEL, max_tokens=5,
            system=(f"Reply Y if the input below violates the policy, else N."
                    f"\n<policy>{POLICY}</policy>"),
            messages=[{"role": "user", "content": user_msg}],
        )
        said = "".join(b.text for b in verdict.content if b.type == "text")
    input_screen = "block" if said.strip().upper().startswith("Y") else "allow"

    if input_screen == "block":
        print(f"{'refused at [1]':14s} <- {user_msg}")
        continue

    # [3] the model call, with tools=tools and the policy in the system prompt.
    if client is None:
        reply = "[PRETEND REPLY — set ANTHROPIC_API_KEY for a real answer]"
    else:
        answer = client.messages.create(
            model=MODEL, max_tokens=300,
            system=f"You are a health information assistant. {POLICY}",
            messages=[{"role": "user", "content": user_msg}],
        )
        reply = "".join(b.text for b in answer.content if b.type == "text")

    # [5] the reply is screened too, against the same policy. An assistant can
    # drift into a banned topic without the user ever asking for it.
    if client is None:
        said = "Y" if ("dose" in reply.lower() or "diagnos" in reply.lower()) else "N"
    else:
        verdict = client.messages.create(
            model=SCREEN_MODEL, max_tokens=5,
            system=(f"Reply Y if the output below violates the policy, else N."
                    f"\n<policy>{POLICY}</policy>"),
            messages=[{"role": "user", "content": reply}],
        )
        said = "".join(b.text for b in verdict.content if b.type == "text")
    output_screen = "block" if said.strip().upper().startswith("Y") else "allow"

    # [6] deterministic and always. This row is what you read at 3am.
    audit = {"tools": tools,
             "prompt_sha256": hashlib.sha256(user_msg.encode()).hexdigest()[:16]}

    outcome = "delivered" if output_screen == "allow" else "safe fallback"
    print(f"{outcome:14s} <- {user_msg}\n               audit: {audit}")

# %% [markdown]
# ### Feature 3 — Least privilege has three scopes, and stems test all three
#
# ```
#    scope        identity                        gets only…
#    ──────────   ─────────────────────────────   ───────────────────────────────────────
#    per user     the authenticated caller        the tools and rows their role permits
#    per agent    each subagent or worker         the tools its own task needs
#    per tool     the service account a tool       ONE scoped credential: read-only DB
#                 uses to reach a backend          role, a single bucket prefix, one scope
# ```
#
# The rules that fall out of it:
#
# - **A tool's credential is scoped to the tool's job**, not to whatever the
#   developer's key happens to be able to do.
# - **Subagents inherit less, never more.** A research subagent is read-only. It
#   cannot escalate by asking its parent nicely.
# - **Secrets never enter the context window.** Tools take handles; the harness
#   holds the key. That is objective 22.
# - **Sandbox anything that executes.** Bash and code-execution tools run in a
#   container with a filesystem and network allow-list.

# %%
# Per user and per agent: the tool allow-list, keyed by identity.
ALLOW_LIST = {
    "viewer":         ["search_docs", "read_ticket"],
    "agent":          ["search_docs", "read_ticket", "update_ticket"],
    "admin":          ["search_docs", "read_ticket", "update_ticket", "delete_ticket"],
    "research_agent": ["search_docs", "read_ticket"],          # a subagent: read-only, always
    "deploy_agent":   ["read_ticket", "trigger_deploy"],       # narrow, and nothing else
}

# Per tool: each tool reaches its backend with its OWN scoped credential.
# The value is the NAME of a secret, never a secret. See objective 22.
TOOL_CREDENTIALS = {
    "search_docs":    {"secret": "docs/readonly",  "scope": "s3:GetObject on acme-docs/public/*"},
    "read_ticket":    {"secret": "jira/read",      "scope": "read:issues"},
    "update_ticket":  {"secret": "jira/comment",   "scope": "write:comments"},
    "delete_ticket":  {"secret": "jira/admin",     "scope": "delete:issues"},
    "trigger_deploy": {"secret": "ci/deploy",      "scope": "workflow:dispatch on one repo"},
}

# Blast radius: what a compromised identity could actually reach. This is
# "least privilege" turned into a sentence you can put in a review.
for role in ("research_agent", "agent", "admin"):
    reachable = [TOOL_CREDENTIALS[tool]["scope"] for tool in ALLOW_LIST[role]]
    print(f"{role:15s} could reach: {reachable}")

# Subagents inherit less: prove it, rather than asserting it in a comment.
print("\nresearch subagent tools are a subset of the agent's:",
      set(ALLOW_LIST["research_agent"]) <= set(ALLOW_LIST["agent"]))

# %% [markdown]
# ### Feature 4 — Staged rollout, kill switch, audit log
#
# ```
#    dev ──► staging (real tools, fake data) ──► canary (1–5% of traffic, tight limits)
#                                                    │
#                        ┌── metrics OK ─────────────┴──── metrics bad ──┐
#                        ▼                                               ▼
#                  ramp 25% → 100%                          KILL SWITCH: flag off,
#                  keep the switch                          traffic back to the old path
# ```
#
# The three things a stem is looking for:
#
# - **A kill switch is a flag, not a redeploy.** A rollback happens at the speed
#   of your slowest pipeline. A flag happens at the speed of a click.
# - **Two granularities.** Turn off *side-effecting tools* first — that usually
#   contains the incident while the feature keeps working. Turn off the *whole
#   feature* second.
# - **Monitor the signals that move first**: refusal rate, tool-error rate,
#   approval-denial rate, classifier block rate, token spend per user.

# %%
# A feature flag is a boolean the on-call can flip without shipping code.
# Two granularities, so the switch can degrade the feature instead of killing it.
FLAG_STATES = [
    ("normal",           {"assistant": True,  "assistant.tools.side_effects": True}),
    ("side effects off", {"assistant": True,  "assistant.tools.side_effects": False}),
    ("feature off",      {"assistant": False, "assistant.tools.side_effects": False}),
]

for label, flags in FLAG_STATES:
    if not flags["assistant"]:
        status, tools = "unavailable", []                  # whole feature off
    else:
        status, tools = "ok", ALLOW_LIST["agent"]
        if not flags["assistant.tools.side_effects"]:
            # Keep the read-only half working. Most incidents are contained here.
            tools = [t for t in tools if t.startswith(("search_", "read_"))]
    print(f"{label:16s} -> ({status!r}, {tools})")

# The append-only audit row. It answers "who did what, with which tools, approved
# by whom" — without storing the prompt text, which would store its PII with it.
session = {"user": "dana", "prompt": "summarise ticket T-88"}
audit_row = {"user": session["user"], "model": MODEL, "tools": ["read_ticket"],
             "approved_by": None,                # who signed for a gated side effect
             "outcome": "ok",                    # or 'blocked_input', 'blocked_output'
             "prompt_sha256": hashlib.sha256(session["prompt"].encode()).hexdigest()[:16]}
print("\naudit row:", audit_row)

# %% [markdown]
# ### Feature 5 — Match the failure to the layer that should have caught it
#
# This is the exam question in its purest form. Read the stem, name the layer
# whose failure mode matches it, then add the deterministic backstop.
#
# ```
#    failure                                  layer that should catch it
#    ──────────────────────────────────────   ──────────────────────────────────────
#    user asks for disallowed content         [1] input screen, [2] scope
#    an injected doc says "email the export"  [3] tool allow-list (tainted), [4] approval
#    the model asks the SQL tool to drop a    [4] harness policy — deterministic
#      table                                    (and a read-only DB role underneath)
#    the reply contains another tenant's data  retrieval scoping + [5] output check
#    the assistant drifts into dosing advice   [2] scope, [5] output classifier
#    a tool credential is compromised          per-tool least privilege caps the damage
#    all of the above, at 3am                  kill switch + audit log
# ```
#
# "Reword the prompt" is never the backstop.

# %%
# Layer [4] made concrete: the SQL guard. The system prompt asking nicely for
# read-only queries is guidance. THIS is the lock.
READ_ONLY_SQL = ("select", "with", "explain")

FAILURES = [
    ("select count(*) from tickets",     "[3]/[4] read-only SQL"),
    ("delete from tickets",              "[4] harness policy"),
    ("update tickets set status='done'", "[4] harness policy"),
]

for statement, note in FAILURES:
    # Only the first keyword decides. The rejection is fed back to the model as a
    # tool error, so it can choose another approach instead of retrying blindly.
    first = statement.strip().split(None, 1)[0].lower() if statement.strip() else ""
    if first in READ_ONLY_SQL:
        allowed, reason = True, "ok"
    else:
        allowed, reason = False, f"'{first}' is not a read-only statement; use a migration file"
    print(f"{'ALLOW' if allowed else 'DENY ':5s} {statement:34s} {reason}")

# %% [markdown]
# ### Feature 6 — The same layering in Claude Code: `permissions` in `settings.json`
#
# Claude Code gives you the deterministic layers as configuration. `permissions`
# is layer [3] and part of layer [4], in a file you can commit:
#
# - `deny` — never, no prompt. This is the lock.
# - `ask` — dangerous but sometimes legitimate; a human decides each time.
# - `allow` — routine, no interruption.
#
# Where the file lives decides who it protects:
#
# ```
#    ~/.claude/settings.json         user — every project you open
#    .claude/settings.json           project — committed, protects the whole team  ◄── guardrails
#    .claude/settings.local.json     project — personal, git-ignored
#    enterprise managed settings     org-wide, cannot be overridden by the above
# ```
#
# We write ours into the scratch directory. **Never edit your own `~/.claude`
# from a lesson** — a guardrail you did not choose is not a guardrail.

# %%
# Everything this lesson writes goes here. A scratch directory, on purpose:
# a guardrail lesson must not edit the learner's own configuration.
import pathlib

PRACTICE_DIR = pathlib.Path.home() / "ccdv-practice" / "20"
project = PRACTICE_DIR / "demo-project" / ".claude"
project.mkdir(parents=True, exist_ok=True)

# A project-level settings.json. Note that every entry is a TOOL-LEVEL pattern:
# when the decision depends on the ARGUMENTS you need a hook instead — that is
# objective 21.
settings = {
    "permissions": {
        "deny": [
            "Read(./.env)",          # a secret that never enters the context cannot leak
            "Read(./secrets/**)",
            "Bash(env:*)",           # printing the environment prints the keys
        ],
        "ask": [
            "Bash(git push:*)",      # legitimate, but a human should see it go
            "Bash(gh secret set:*)",
        ],
        "allow": [
            "Read(./src/**)",
            "Bash(npm test:*)",
        ],
    },
}

(project / "settings.json").write_text(json.dumps(settings, indent=2) + "\n")

print("wrote", project / "settings.json", "\n")
print((project / "settings.json").read_text())

# Run the CLI the way you would in a terminal. Nothing here starts an agent:
# the cell wrote config and read it back. `command not found` just means Claude
# Code is not installed — the config above is still the answer.
!claude --version

# %% [markdown]
# **Practical use:** stack the layers, scope every identity, and keep a switch you
# can flip. Probabilistic controls — prompts and classifiers — shape behaviour.
# Deterministic controls — tool allow-lists, harness policy, permission rules —
# are the only thing you may put in front of something that must never happen.
# Human approval goes on the irreversible. Audit and a kill switch go on
# everything. A single-filter answer is always the wrong answer.
#
# **🏋️ Practice:** the deploy agent must not be able to delete a ticket, and the
# side-effects kill switch must leave the read-only half working. The line that
# does the second job is marked `# <-` and is already in place, so the cell passes
# as shipped.
#
# To feel it: delete the marked filter line, re-run, and watch the kill switch
# stop switching anything off. Put it back. Then extend it: add a `canary` flag
# that gives 5% of users the new tool and everyone else the old list.

# %%
# Your turn — see the `# <-` marker.
AGENT_TOOLS = {
    "research_agent": ["search_docs", "read_ticket"],
    "deploy_agent":   ["read_ticket", "trigger_deploy"],
    "support_agent":  ["read_ticket", "update_ticket"],
}
SIDE_EFFECTING = {"trigger_deploy", "update_ticket", "delete_ticket"}

# Work out what each agent may use with the switch on and with it off.
tools_when = {}
for agent, all_tools in AGENT_TOOLS.items():
    for switch, side_effects_on in (("on", True), ("off", False)):
        tools = list(all_tools)
        if not side_effects_on:
            tools = [t for t in tools if t not in SIDE_EFFECTING]   # <- the kill switch
        tools_when[agent, switch] = tools
    print(f"{agent:16s} on={tools_when[agent, 'on']}  off={tools_when[agent, 'off']}")

# Least privilege: no agent holds a tool outside its job.
assert "delete_ticket" not in tools_when["deploy_agent", "on"], "deploy agent has too much"
# The kill switch: side effects gone, read-only work continues.
assert tools_when["deploy_agent", "off"] == ["read_ticket"], "kill switch did not degrade cleanly"
assert tools_when["research_agent", "off"] == ["search_docs", "read_ticket"], "read-only agent should be unaffected"
print("\nleast privilege holds, and the kill switch degrades instead of dying")
