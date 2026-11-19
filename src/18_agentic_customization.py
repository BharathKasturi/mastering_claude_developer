# %% [markdown]
# # Topic 6.3 · Agentic Customization: Built-in Tools vs Custom Tools vs Skills vs MCPs
#
# 🎓 *CCDV-F objective 18 of 25 · exam weight 4.1% · Claude Code lesson*
#
# **Why this matters.** There are four ways to extend Claude, and they are not
# four flavours of the same thing — they answer different questions. **Tools give
# Claude capability. Skills give Claude procedure. MCP gives an organisation one
# implementation every client can reach.** Pick by asking what is actually
# missing: if Claude *cannot* do the thing, no Skill will help; if Claude *can*
# and just does it inconsistently, no tool will help.
#
# **What you'll learn.**
# - Split the problem on the first fork: missing **capability** or missing **procedure**.
# - Use the **built-in** tools, and know which ones *you* still execute.
# - Write a **custom tool** for one app — and spot when it should be promoted.
# - Write a real **Skill** (`SKILL.md` + `reference/` + `scripts/`) and see progressive disclosure.
# - Read the **context bill**: tool lists are re-sent every request, Skills are not.
# - Run the four-way decision as code, on requirements you feed it.
#
# **What you need.** Nothing. Every cell runs offline. You will write a Skill
# under `~/ccdv-practice/25` and read it back — it is never installed into your
# real `.claude/skills/`. Set `ANTHROPIC_API_KEY` only if you want the built-in
# tool request in Feature 2 to be more than a printed shape.

# %%
# Load the shared setup: the API client and MODEL.
%run ./00_setup.ipynb

# %% [markdown]
# ### Feature 1 — The first fork: capability or procedure?
#
# Before comparing four options, answer one question about the *gap*.
#
# - **Claude cannot do it at all** → the gap is **capability** → some kind of
#   **tool** (built-in, custom, or an MCP server's tool).
# - **Claude can already do it, but does it inconsistently, in the wrong format,
#   or without your house conventions** → the gap is **procedure** → a **Skill**.
#
# A workshop makes the four concrete. The **two-post lift** came with the
# building — a **built-in tool**, Anthropic ships and maintains it. The **jig you
# machined** for one weird bracket is a **custom tool**. The **workshop manual**
# is a **Skill**: torque sequence, panel order, the bolt everyone strips — no new
# equipment at all, pure know-how. The **parts warehouse down the road** is an
# **MCP server**: not in your shop, every shop in town has an account, it
# upgrades without asking you.
#
# The exam tell for this fork: *"Claude already can, it just does it wrong"* is
# **never** a tool.

# %%
# The fork, run over real-sounding requirements. Note the third and fifth: nothing
# is missing capability-wise, so a tool would be the wrong answer no matter how
# well written.
GAPS = [
    ("Read our on-prem policy index",                        False),
    ("Search the public web for this quarter's rule change", False),
    ("Produce the QBR in our brand template, every time",    True),
    ("Refund a charge in our billing system",                False),
    ("Investigate a billing dispute the way our team does",  True),
]

for requirement, claude_can_already in GAPS:
    # One question, one branch. Answer it and half the options leave the table.
    if claude_can_already:
        gap = "procedure -> Skill"
    else:
        gap = "capability -> a tool of some kind"
    print(f"{gap:33s} <- {requirement}")

# %% [markdown]
# ### Feature 2 — Built-in tools, and the "built-in ≠ Anthropic-executed" split
#
# Anthropic ships tool families you enable by name. You did not write the schema
# — that is what "built-in" means. But **who runs them splits in two**, and
# confusing the halves is a reliable exam trap:
#
# ```
#   SERVER-SIDE  (Anthropic runs it; results come back in the same response)
#   ─────────────────────────────────────────────────────────────────────
#   web search       Claude issues the searches, you get cited results
#   web fetch        Claude retrieves a URL's content
#   code execution   Python runs in an Anthropic-hosted sandbox container
#
#   CLIENT-SIDE  (Anthropic defines the schema; YOU execute and return the result)
#   ─────────────────────────────────────────────────────────────────────
#   bash             you run the command on your machine
#   text editor      you apply the view / create / str_replace operations
#   computer use     you drive the screen, mouse and keyboard
# ```
#
# The rule: **do not hand-roll a capability Anthropic already ships.** Wrap a
# third-party search API in a custom `web_search` and you now own the crawler
# quota, the ranking, the citation formatting and the freshness — for a strictly
# worse result. The one legitimate reason to write your own is a different
# **corpus**: your on-prem document index is not the public web.

# %%
# Enabling a built-in is a dated `type` plus the fixed `name` from its docs. The
# date suffix pins behaviour — copy the current version string from the tool page.
BUILTINS = {
    "web_search":     ("web_search_20250305",   "Anthropic (server-side)"),
    "web_fetch":      ("web_fetch_20250910",    "Anthropic (server-side)"),
    "code_execution": ("code_execution_20250825", "Anthropic (server-side)"),
    "bash":           ("bash_20250124",         "you (client-side)"),
    "computer":       ("computer_20250124",     "you (client-side)"),
}
for name, (type_id, executor) in BUILTINS.items():
    print(f"{name:16s} type={type_id:26s} executed by {executor}")

# The request shape. Printed offline; sent for real when a key is set.
request = {
    "model": MODEL,
    "max_tokens": 4096,
    "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
    "messages": [{"role": "user", "content": "What changed in the EU AI Act this quarter?"}],
}
print("\nrequest:", json.dumps(request, indent=2)[:340], "…")

if client is None:
    print("\n[skipped — set ANTHROPIC_API_KEY to send this and get cited results back]")
else:
    resp = client.messages.create(**request)
    print("\nstop_reason:", resp.stop_reason,
          "| server-side tools left you nothing to return:",
          not any(b.type == "tool_use" for b in resp.content))

# %% [markdown]
# ### Feature 3 — Custom tools: one app, one bespoke action
#
# A custom tool is three fields and your code, exactly as in O23: `name`,
# `description`, `input_schema`. The description **is** the prompt that decides
# whether the model calls it correctly.
#
# Custom tools are right when the capability is **specific to this application**
# — your billing system, your feature flags, your internal scoring model. Nothing
# outside this app will ever call it.
#
# The moment a second application wants the same thing, you get duplicated
# schemas, duplicated auth, and drift from the first release onward. That is the
# signal to promote it to an MCP server. Conversely, building an MCP server for a
# capability exactly one app will ever call is ceremony: a process to deploy, a
# transport to secure and a protocol version to negotiate, all for one function
# call.

# %%
# One bespoke action, defined properly.
inventory_tool = {
    "name": "adjust_stock",
    "description": (
        "Adjust on-hand quantity for a SKU in the warehouse system. "
        "Use for goods receipts, shrinkage write-offs and audit corrections. "
        "Do NOT use to reserve stock for an order — use reserve_stock for that. "
        "delta is signed (negative reduces stock). Returns the new on-hand count."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "sku":    {"type": "string", "description": "SKU code, e.g. AX-19"},
            "delta":  {"type": "integer", "description": "Signed change in units"},
            "reason": {"type": "string", "enum": ["receipt", "shrinkage", "correction"]},
        },
        "required": ["sku", "delta", "reason"],
    },
}
print(inventory_tool["name"], "-> required:", inventory_tool["input_schema"]["required"])

# Then the promotion test: "when does this become MCP?" is a count and an owner,
# not a feeling. Either a second consumer or a second team is enough.
print()
for consumers, owned_by_another_team in ((1, False), (3, False), (1, True)):
    if consumers > 1 or owned_by_another_team:
        verdict = "MCP server (reuse and/or independent ownership)"
    else:
        verdict = "custom tool (one app, one owner — a server would be ceremony)"
    print(f"consumers={consumers} other_team={owned_by_another_team!s:5s} -> {verdict}")

# %% [markdown]
# ### Feature 4 — Skills: procedure, not capability
#
# A Skill is a **folder**. `SKILL.md` carries YAML frontmatter — a `name` and a
# `description` — and a body of instructions. Alongside it you ship reference
# documents, templates and scripts:
#
# ```
#   .claude/skills/quarterly-report/
#   ├── SKILL.md              # frontmatter + the procedure
#   ├── reference/
#   │   └── metric-definitions.md
#   ├── assets/
#   │   └── brand-template.docx
#   └── scripts/
#       └── build_pdf.py      # deterministic work — code, not model judgement
# ```
#
# Two facts decide several exam questions:
#
# - **A Skill grants no new abilities.** A step that runs a script only works
#   because some tool can execute Python — the **code execution** tool on the
#   API, or **Bash** in Claude Code.
# - **Skills are model-invoked.** Claude reads the `description` and decides. So
#   the description is the trigger, not a human-facing title: say what it does
#   *and when to use it*, in the words a user would use.
#
# The next cell writes a real one under a scratch directory, `~/ccdv-practice/25`,
# deliberately outside `~/.claude/skills`, so nothing here changes your real setup.

# %%
%%bash
mkdir -p ~/ccdv-practice/25/.claude/skills/quarterly-report/reference
mkdir -p ~/ccdv-practice/25/.claude/skills/quarterly-report/scripts
cat > ~/ccdv-practice/25/.claude/skills/quarterly-report/SKILL.md <<'EOF'
---
name: quarterly-report
description: Build the branded quarterly business review from the metrics CSV.
  Use when asked for the QBR, the quarterly report, or a branded metrics PDF.
---

# Quarterly report

1. Read the CSV; validate it has the eight required columns (see
   `reference/metric-definitions.md` for the exact names and units).
2. Compute QoQ and YoY deltas. Never round before computing a delta.
3. Fill `assets/brand-template.docx`; the exec summary is at most 150 words.
4. Run `scripts/build_pdf.py` to render. Do not hand-format the PDF.
5. Report the output path and any metric that moved more than 20%.
EOF
cat > ~/ccdv-practice/25/.claude/skills/quarterly-report/reference/metric-definitions.md <<'EOF'
# Metric definitions

| Column | Unit | Definition |
|---|---|---|
| `arr` | USD | Annual recurring revenue at period end |
| `churn` | % of accounts | Accounts that lapsed in the quarter, over accounts at period start |
| `nrr` | % | Net revenue retention, expansion included, new logos excluded |

Churn is counted on ACCOUNTS, never on seats. That is the definition two teams
kept getting wrong, and the reason this file exists.
EOF
echo "wrote the skill under ~/ccdv-practice/25/.claude/skills/quarterly-report"

# %%
# Read the Skill back with line numbers, split the frontmatter from the body, and
# price the three levels of progressive disclosure. That pricing is the whole
# efficiency argument for Skills.
import pathlib

LAB = pathlib.Path.home() / "ccdv-practice" / "25"            # our scratch dir
SKILL_DIR = LAB / ".claude" / "skills" / "quarterly-report"
SKILL_MD = SKILL_DIR / "SKILL.md"
REFERENCE = SKILL_DIR / "reference" / "metric-definitions.md"

for i, line in enumerate(SKILL_MD.read_text(encoding="utf-8").splitlines(), 1):
    print(f"{i:3d} | {line}")

raw = SKILL_MD.read_text(encoding="utf-8")
_, frontmatter, body = raw.split("---", 2)          # YAML sits between the two fences
# The description is the trigger Claude reads. It is what LEVEL 1 costs.
description = frontmatter.split("description:", 1)[1].strip()

# Rough token count: about four characters per token. Crude, but enough to
# compare the three levels against each other.
level1 = len(description) // 4
level2 = len(body) // 4
level3 = len(REFERENCE.read_text(encoding="utf-8")) // 4

print("\nprogressive disclosure — what is in context, and when:")
print(f"  LEVEL 1  name + description       always                       ~{level1:4d} tokens")
print(f"  LEVEL 2  SKILL.md body            when Claude matches the task ~{level2:4d} tokens")
print(f"  LEVEL 3  reference/ + scripts/    only when a step needs them  ~{level3:4d} tokens")
print(f"\n100 skills cost roughly {100 * level1} tokens of descriptions —")
print("the same procedure pasted into the system prompt costs its full size on EVERY request.")

# %%
# Claude Code discovers Skills from .claude/skills in the project or your home
# directory. Ours is under the scratch dir, so it is NOT installed — confirm the
# CLI is there, then list what we wrote where a real install would go.
!claude --version
!ls ~/ccdv-practice/25/.claude/skills/quarterly-report

# %% [markdown]
# ### Feature 5 — MCP, and the context bill
#
# MCP is not a fourth way to define a tool; it is a way to define a **boundary**.
# Three words in a stem decide it almost every time:
#
# ```
#   "reusable"                 more than one client will call it
#   "maintained independently" another team ships it on its own schedule
#   "several applications"     you refuse to write the integration N times
# ```
#
# The cost side is what teams underestimate. **Tool definitions and connected MCP
# tool lists are re-sent on every request.** Skills sit at one line each until
# triggered.
#
# | choice | ongoing cost you take on |
# |---|---|
# | built-in | almost none — enable it, watch the versioned `type` on upgrade |
# | custom tool | the implementation, its auth, its errors, its tokens every request |
# | Skill | content maintenance; only as good as its `description`'s trigger |
# | MCP server | a deployed service: transport, auth, versioning, uptime — plus tool-list tokens in every connected client |
#
# That asymmetry explains the commonest complaint: *"we have 40 tools and Claude
# picks the wrong one."* The fix is **removing** tools and moving the know-how
# into Skills — not writing longer tool descriptions, which adds tokens to every
# request and does nothing about two tools that plausibly answer the same thing.

# %%
# Price two designs for the same agent, per request. Numbers are illustrative;
# the SHAPE is the point — tool lists recur, skill descriptions barely move.
TOKENS_PER_TOOL = 180        # one tool definition, re-sent on EVERY request
TOKENS_PER_SKILL = 25        # one Skill's level-1 entry, until it triggers

# Before: five MCP servers connected wholesale, nothing filtered, no Skills.
before = (12 + 28) * TOKENS_PER_TOOL + 0 * TOKENS_PER_SKILL
# After: the tool surface trimmed, and the procedure moved into Skills.
after = (4 + 6) * TOKENS_PER_TOOL + 6 * TOKENS_PER_SKILL

print(f"before: 12 custom + 28 MCP tools, 0 skills -> ~{before:5d} tokens every request")
print(f"after :  4 custom +  6 MCP tools, 6 skills -> ~{after:5d} tokens every request")
print(f"saved : ~{before - after} tokens per request, and far fewer plausible wrong choices")

# %% [markdown]
# ### Feature 6 — The decision, as code
#
# Take the first rung that fits:
#
# ```
#   START: what is actually missing?
#     │
#     ├─ Claude CAN'T do it at all ──────────────────────────────────┐
#     │                                                              │
#     └─ Claude CAN, but does it wrong / inconsistently / off-brand   │
#              │                                                      │
#              └─► SKILL  (procedure + assets + scripts,              │
#                          progressive disclosure, model-invoked)     ▼
#                                             Does Anthropic already ship it?
#                                                │yes                 │no
#                                                ▼                    ▼
#                                         BUILT-IN TOOL       Who else needs it?
#                                    (web search, web fetch,   │         │
#                                     code exec, bash,         │ one app │ many
#                                     editor, computer use)    ▼         ▼
#                                                        CUSTOM TOOL  MCP SERVER
# ```
#
# And they **compose**. A Skill's steps can call MCP tools and the built-in code
# execution tool. That is the normal shape of a mature agent, not an exotic one.

# %%
# The same three questions, asked in order, over six requirements.
# (requirement, can-already, anthropic-ships-it, consumers)
CASES = [
    ("Search the public web for a rule change",           False, True,  1),
    ("Adjust stock in our one warehouse app",             False, False, 1),
    ("Read/update customer records for three assistants", False, False, 3),
    ("Produce the QBR in our template, consistently",     True,  False, 1),
    ("Run Python to render a PDF",                        False, True,  1),
    ("Search our on-prem policy corpus, three clients",   False, False, 3),
]

for requirement, claude_can_already, anthropic_ships_it, consumers in CASES:
    if claude_can_already:                 # the gap is procedure, not capability
        rung = "Skill"
    elif anthropic_ships_it:               # never rebuild what Anthropic maintains
        rung = "built-in tool"
    elif consumers > 1:                    # a second client means a boundary
        rung = "MCP server"
    else:
        rung = "custom tool"
    print(f"{rung:14s} <- {requirement}")

# %% [markdown]
# **Practical use:** ask what is missing before you ask which of the four. Missing
# **procedure** is always a Skill — no tool fixes inconsistency. Missing
# **capability** goes down one ladder: a built-in if Anthropic already ships it,
# a custom tool if exactly one app needs it, an MCP server the moment a second
# client or a second owning team appears. Then remember the bill: tool
# definitions and connected MCP tool lists are re-sent on **every** request,
# while Skills cost one description until they trigger.
#
# **🏋️ Practice:** a team asks for a `generate_qbr` custom tool because "the
# reports come out different every time". Nothing is missing capability-wise —
# Claude already reads CSVs and writes documents — so the answer is a **Skill**.
# The router only spots that when `SKILL_SIGNALS` contains the word the ticket
# actually used. The fix is marked `# <-` below and is already in place, so the
# cell passes as shipped.
#
# To feel it: delete `"off-brand"` from `SKILL_SIGNALS`, re-run, and watch the
# assert fail as that ticket becomes a custom tool. Put it back. Then add a
# ticket of your own that should reach **MCP server** and check it lands there.
#
# 🎉 That is objective 25 of 25 — the last one. Well done.

# %%
# Your turn — see the `# <-` marker.
SKILL_SIGNALS = ("inconsistent", "different every time", "off-brand", "our format")  # <- "off-brand"

TICKETS = [
    ("The QBRs come out different every time",           False, 1, "Skill"),
    ("Our decks land off-brand half the time",           False, 1, "Skill"),
    ("Let Claude search the public web",                 True,  1, "built-in tool"),
    ("Refund a charge in our billing system",            False, 1, "custom tool"),
    ("Customer records, needed by three assistants",     False, 3, "MCP server"),
]

routed = []
for ticket, anthropic_ships_it, consumers, expected in TICKETS:
    if any(signal in ticket.lower() for signal in SKILL_SIGNALS):   # procedure, not capability
        rung = "Skill"
    elif anthropic_ships_it:
        rung = "built-in tool"
    elif consumers > 1:
        rung = "MCP server"
    else:
        rung = "custom tool"
    routed.append(rung)
    print(f"{rung:14s} (want {expected:14s})  <- {ticket}")

assert routed == [want for _, _, _, want in TICKETS], "one ticket routes to the wrong rung"
assert routed[1] == "Skill", "an inconsistency complaint must never become a tool"
print("\nall five land on the right rung")
