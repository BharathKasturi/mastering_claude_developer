# %% [markdown]
# # Topic 8.2 · Agent Construction: Agent SDK, Custom Loops, Managed Agents, Hooks
#
# 🎓 *CCDV-F objective 24 of 25 · exam weight 5.3% · API lesson*
#
# **Why this matters.** "Build an agent" is really two questions: **who writes the
# loop** and **who runs the machine**. Write it yourself and you own every safety
# feature. Import the Claude Agent SDK and you get Claude Code's harness as a
# library. Rent Managed Agents and Anthropic runs both the loop and the sandbox.
# The exam gives you a stem full of constraints and asks which tier it implies.
#
# **What you'll learn.**
# - Pick the tier — **manual loop**, **Agent SDK**, or **Managed Agents** — from the stem.
# - Write the hand-rolled loop and name the **harness duties** it leaves you.
# - Read the `ClaudeAgentOptions` fields, and `query()` vs `ClaudeSDKClient`.
# - Tell **self-hosted** from **Anthropic-hosted**, and the Agent → Session two-step.
# - Use a **hook** for anything that must *always* or *never* happen.
# - Wire the same hook into Claude Code as `settings.json` + an exit-code-2 script.
#
# **What you need.** Nothing. The loop runs against scripted replies, and the hook
# demo really executes. Set `ANTHROPIC_API_KEY` for the live loop; install the
# `claude` CLI to see the last section talk to Claude Code.

# %%
# Load the shared setup: the API client and MODEL.
%run ./00_setup.ipynb

# %% [markdown]
# ### Feature 1 — Three tiers: who writes the loop, who runs the machine
#
# The harness is **the loop plus everything around it** — permissions, context,
# hooks, sessions. You either write it, import it, or rent it.
#
# | | Manual loop | Agent SDK | Managed Agents |
# |---|---|---|---|
# | You write | the loop, dispatch, context | a prompt + `ClaudeAgentOptions` | an agent config, then sessions |
# | Who runs the loop | you | the SDK, on your machine | Anthropic |
# | Where tools run | your process | your machine (Read/Edit/Bash/Grep + MCP) | Anthropic-hosted per-session container |
# | Built-in tools | none — you define all | file, shell, search, web, subagents | sandbox bash/files/code exec + Skills + MCP |
# | Permissions | hand-rolled | `permission_mode`, `can_use_tool`, `hooks` | tool confirmation events, session budgets |
# | State | your `messages` list | sessions, resume, compaction | server-side sessions, memory stores |
# | Deployment | self-hosted | self-hosted | Anthropic-hosted |
#
# **Decision rule.** *Do you want to host the harness?* Yes → Agent SDK. Must you
# own the loop itself? → manual loop. No → Managed Agents. And anything that must
# be deterministic → a hook, never a prompt.

# %%
# The exam tells are stable. Read each stem and name the tier it implies.
TELLS = [
    ("built-in file/bash tools and subagents on our own infra", "agent_sdk"),
    ("we don't want to run the loop or the sandbox",            "managed"),
    ("persisted, versioned agent config",                       "managed"),
    ("long-running or scheduled sessions",                      "managed"),
    ("no external dependency, we define every tool",            "manual_loop"),
    ("a control flow the framework doesn't support",            "manual_loop"),
    ("must always happen regardless of what the model decides", "hook"),
]

for stem, expected in TELLS:
    words = stem.lower()

    # Determinism wins first: "always/never/every time" is a hook, whatever
    # tier the rest of the sentence describes.
    if any(w in words for w in ("always", "never", "every time", "must not")):
        tier = "hook"
    elif any(w in words for w in ("don't want to run", "hosted for us", "versioned agent",
                                  "scheduled", "long-running")):
        tier = "managed"
    elif any(w in words for w in ("no external dependency", "define every tool",
                                  "doesn't support", "own the loop")):
        tier = "manual_loop"
    else:
        tier = "agent_sdk"

    print(f"{tier:11s} (want {expected:11s})  <- {stem}")

# %% [markdown]
# ### Feature 2 — The hand-rolled loop, and the duties it hands you
#
# This is the whole agent at the API level. Four things the exam checks:
#
# - the cap (`max_turns`) is **yours** — the model's stop is `end_turn`, not a cap,
# - append the assistant turn **verbatim** (`reply.content`), thinking blocks included,
# - **one** user message carries **all** the `tool_result` blocks of that turn,
# - a tool that fails returns `is_error: True`, it does not raise.
#
# ```
#    HARNESS DUTY                       MANUAL LOOP        AGENT SDK
#    ────────────────────────────────   ─────────────      ───────────────────────
#    stop condition / turn cap          you (max_turns)    max_turns option
#    tool execution + error → result    you                built-in tools + MCP
#    permission before dangerous tool   you                permission_mode, hooks
#    context growth (prune / compact)   you                automatic compaction
#    parallel tool_use blocks           you                handled
#    session persistence / resume       you                resume / continue_conversation
#    subagents                          you                agents option
#    observability (cost, turns)        you (usage)        ResultMessage.total_cost_usd
#  ```

# %%
# Two tools, described to the model with the real field names.
TOOLS = [
    {"name": "get_status", "description": "Shipping status for an order id.",
     "input_schema": {"type": "object",
                      "properties": {"order_id": {"type": "string"}},
                      "required": ["order_id"]}},
    {"name": "get_eta", "description": "Delivery estimate for an order id.",
     "input_schema": {"type": "object",
                      "properties": {"order_id": {"type": "string"}},
                      "required": ["order_id"]}},
]

# What the "model" sends back, turn by turn. Real blocks carry these same fields.
# Turn 1 asks for BOTH tools at once — the parallel case rule 3 is about.
scripted_turns = [
    {"stop_reason": "tool_use", "content": [
        {"type": "tool_use", "id": "tu_1", "name": "get_status", "input": {"order_id": "A-1001"}},
        {"type": "tool_use", "id": "tu_2", "name": "get_eta", "input": {"order_id": "A-1001"}},
    ]},
    {"stop_reason": "end_turn", "content": [
        {"type": "text",
         "text": "A-1001 has shipped. The ETA service is down, so I cannot give a date."}]},
]

# YOUR cap. The model's stop_reason is not a budget — this line is the budget.
max_turns = 20

messages = [{"role": "user", "content": "Where is order A-1001?"}]
final = "[stopped: max_turns exceeded — the cap you own did its job]"

for turn, reply in enumerate(scripted_turns[:max_turns], start=1):
    # Append the assistant turn WHOLE. Dropping tool_use or thinking blocks is
    # the most common hand-written-loop bug.
    messages.append({"role": "assistant", "content": reply["content"]})

    # end_turn / max_tokens / refusal all mean "stop looping".
    if reply["stop_reason"] != "tool_use":
        final = "".join(b["text"] for b in reply["content"] if b["type"] == "text")
        break

    # Run EVERY tool_use block in this turn and collect the results.
    results = []
    for block in reply["content"]:
        if block["type"] != "tool_use":
            continue
        if block["name"] == "get_status":
            # `input` is already a parsed dict — never string-match it.
            out = {"id": block["input"]["order_id"], "status": "shipped"}
            results.append({"type": "tool_result", "tool_use_id": block["id"],
                            "content": json.dumps(out)})
        else:
            # The ETA service is down. A failed tool is a RESULT, not an
            # exception. The model can recover from a result. It cannot recover
            # from a dead loop.
            results.append({"type": "tool_result", "tool_use_id": block["id"],
                            "content": "carrier API timed out after 5s", "is_error": True})

    # ONE user message, N results. Splitting them teaches the model to stop
    # asking for tools in parallel.
    messages.append({"role": "user", "content": results})

print("FINAL:", final)
print(f"\nmodel calls: {len(scripted_turns)}   (one per turn — this is your cost curve)\n")

for i, m in enumerate(messages):
    kinds = [b["type"] for b in m["content"]] if isinstance(m["content"], list) else ["text"]
    print(f"{i}  {m['role']:9s} {len(kinds)} block(s): {kinds}")
print("\nnote message 2: ONE user message carrying BOTH tool_results")

# %%
# The same loop, live. Nothing about the shape changes — only where the reply
# comes from, and that blocks are objects (`block.type`) instead of dicts.
if client is None:
    print("[skipped the live loop — set ANTHROPIC_API_KEY to run it against the real API]")
else:
    messages = [{"role": "user", "content": "Where is order A-1001?"}]
    final = "[stopped: max_turns exceeded]"

    for turn in range(5):
        reply = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            tools=TOOLS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": reply.content})

        if reply.stop_reason != "tool_use":
            final = "".join(b.text for b in reply.content if b.type == "text")
            break

        results = []
        for block in reply.content:
            if block.type != "tool_use":
                continue
            if block.name == "get_status":
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": json.dumps({"id": block.input["order_id"],
                                                       "status": "shipped"})})
            else:
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": "carrier API timed out after 5s",
                                "is_error": True})
        messages.append({"role": "user", "content": results})

    print("live FINAL:", final)

# %% [markdown]
# ### Feature 3 — The Claude Agent SDK: `ClaudeAgentOptions`
#
# The Agent SDK is Claude Code as a library — `claude-agent-sdk` in Python,
# `@anthropic-ai/claude-agent-sdk` in TypeScript. You supply a prompt and options;
# it supplies the loop, the built-in tools, permissions, sessions and subagents.
#
# | Option | Controls |
# |---|---|
# | `allowed_tools` | which built-in / MCP tools may be called at all |
# | `permission_mode` | `default` \| `acceptEdits` \| `plan` \| `bypassPermissions` |
# | `system_prompt` | replaces the default, or `{"type": "preset", "preset": "claude_code", "append": "…"}` |
# | `mcp_servers` | external tool servers; tools appear as `mcp__<server>__<tool>` |
# | `agents` | named subagents (`AgentDefinition`: description, prompt, tools, model) |
# | `hooks` | deterministic callbacks at lifecycle events |
# | `max_turns` | hard stop on the loop |
# | `setting_sources` | `["project"]` to load `CLAUDE.md` / `.claude/settings.json` — **default loads none** |
#
# Two entry points, and the exam's favourite pair:
# **`query()`** is one-shot — a fresh session per call, right for a CI job.
# **`ClaudeSDKClient`** is a long-lived session — follow-ups land in the *same*
# context, and you can interrupt mid-run.

# %%
# The options an SDK agent would take, as a plain dict so the cell runs with or
# without `claude-agent-sdk` installed. The keys are the real field names.
release_agent_options = {
    "system_prompt": "You are a release engineer. Never push to main directly.",
    "allowed_tools": ["Read", "Grep", "Glob", "Bash", "Edit", "mcp__jira__*"],
    "permission_mode": "acceptEdits",
    "cwd": "/srv/repo",
    "max_turns": 30,
    "setting_sources": ["project"],          # without this, CLAUDE.md is NOT read
    "mcp_servers": {"jira": {"command": "npx", "args": ["-y", "@acme/jira-mcp"]}},
}

for key, value in release_agent_options.items():
    print(f"{key:17s} = {value}")

# Build the real ClaudeAgentOptions when the SDK happens to be installed.
try:
    from claude_agent_sdk import ClaudeAgentOptions
    options = ClaudeAgentOptions(**release_agent_options)
    print("\nclaude-agent-sdk installed — real ClaudeAgentOptions built")
except ImportError:
    print("\n[claude-agent-sdk not installed — `pip install claude-agent-sdk` to build the real object]")

# %%
# query() or ClaudeSDKClient? A follow-up, an interrupt, or a chat UI all mean
# one session has to survive across calls — that is ClaudeSDKClient.
for stem in ["one CI job per pull request",
             "a chat UI that sends follow-up questions",
             "a batch of independent prompts",
             "the operator must interrupt a long run"]:
    needs_session = any(w in stem.lower()
                        for w in ("follow-up", "interrupt", "chat", "same context"))
    print(f"{'ClaudeSDKClient' if needs_session else 'query()':16s} <- {stem}")

# %% [markdown]
# ### Feature 4 — Managed agents: self-hosted vs Anthropic-hosted
#
# "Managed agent deployment models" asks exactly one thing: **where does the
# harness live?**
#
# ```
#    SELF-HOSTED (Agent SDK / manual loop)     ANTHROPIC-HOSTED (Managed Agents)
#    ──────────────────────────────────────    ──────────────────────────────────
#    your process runs the loop                Anthropic's orchestration runs it
#    your machine runs the tools               a per-session container runs them
#    state = your messages / session files     state = server-side sessions
#    your API key, your rate limits            billed to your org; session budgets
#    you deploy, scale, patch, monitor         create an Agent, start Sessions
#    secrets live on your box                  vault credentials at egress
# ```
#
# Managed Agents is a **two-step**: create the **Agent** once — model, system
# prompt and tools live *there* — then create a **Session** for every run. It is
# Claude-API only, so a Bedrock / Vertex / Foundry requirement rules it out.

# %%
# The Agent -> Session two-step, with the real field names. Guarded, because this
# is a beta surface and it needs a key.
if client is None:
    print("[skipped — set ANTHROPIC_API_KEY to create a Managed Agent]")
    print("  agent   = client.beta.agents.create(name=…, model=MODEL, system=…, tools=[…])")
    print("  session = client.beta.sessions.create(agent=agent.id)")
else:
    agent = client.beta.agents.create(
        name="nightly-reporter",
        model=MODEL,                      # the pin lives on the AGENT
        system="Produce the nightly ops summary from the mounted logs.",
        tools=[{"type": "agent_toolset_20260401"}],
    )
    session = client.beta.sessions.create(agent=agent.id)
    print(f"agent={agent.id} session={session.id}")

# Where the harness lives is a requirement, not a preference. Name the driver.
HOSTING = [
    ("tools must run next to our data",             "self-hosted"),
    ("we are on Bedrock for residency",             "self-hosted"),
    ("no ops team, nightly scheduled run",          "anthropic-hosted"),
    ("versioned agent config a session pins to",    "anthropic-hosted"),
    ("memory that survives between sessions",       "anthropic-hosted"),
    ("an existing on-prem control plane",           "self-hosted"),
]
print()
for driver, where in HOSTING:
    print(f"{where:16s} <- {driver}")

# %% [markdown]
# ### Feature 5 — Hooks: deterministic actions the model cannot override
#
# A prompt is a **request**. A hook is a **guarantee**. A hook is code that runs
# at a fixed lifecycle moment; it can block a tool call, rewrite its input,
# inject context, or log.
#
# ```
#    event              fires when                         typical use
#    ────────────────   ────────────────────────────────   ──────────────────────────
#    PreToolUse         before a tool executes             deny destructive commands
#    PostToolUse        after a tool returns               lint after Edit, log Bash
#    UserPromptSubmit   a prompt is sent                   inject date / ticket context
#    Stop               the agent wants to finish          "tests must pass first"
#    SubagentStop       a subagent finishes                validate the worker's output
#    PreCompact         before context compaction          persist state first
# ```
#
# **Anything with the words "always", "never", "must" or "every time" belongs in
# a hook.** In the Agent SDK a hook is an async callback that returns a decision
# dict; the deny shape is `permissionDecision: "deny"`.

# %%
# The PreToolUse decision, in the SDK's real return shape. It is ordinary code,
# so it runs here exactly as it would inside the SDK's callback.
DESTRUCTIVE = ("rm -rf", "DROP TABLE", "git push --force")

for command in ["pytest -q", "rm -rf /var/data", "git push --force origin main", "ls -la"]:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}

    if any(bad in payload["tool_input"]["command"] for bad in DESTRUCTIVE):
        decision = {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "Destructive command blocked by policy",
        }}
    else:
        decision = {}                          # empty dict = allow

    verdict = decision["hookSpecificOutput"]["permissionDecision"] if decision else "allow"
    print(f"{verdict:5s}  {command}")

    # PostToolUse observability: log every call, take no decision.
    print(f"    audit  tool={payload['tool_name']} input={payload['tool_input']}")

# How those hooks attach in the SDK — HookMatcher with no matcher means
# "every tool", and a matcher narrows it to one tool name.
print("\nhooks = {")
print('  "PreToolUse":  [HookMatcher(matcher="Bash", hooks=[block_destructive])],')
print('  "PostToolUse": [HookMatcher(hooks=[audit])],      # no matcher = every tool')
print("}")

# %% [markdown]
# ### Feature 6 — The same guarantee in Claude Code
#
# Claude Code takes the same hooks as **configuration** instead of Python. A
# command hook reads the tool input as JSON on **stdin**. The exit code is the
# decision:
#
# - **exit 2 blocks** the call, and stderr goes back to the model as the reason,
# - **exit 0 allows** it.
#
# We write both files under a scratch directory, `~/ccdv-practice`, and never
# into a real project. Then we run the guard for real — no CLI needed to see the
# exit code do its job.

# %%
# Build the scratch project: a settings.json that registers the hook, plus the
# guard script it points at. Written with pathlib so it works on any OS.
import pathlib

practice = pathlib.Path.home() / "ccdv-practice" / "hooks-demo"
(practice / ".claude" / "hooks").mkdir(parents=True, exist_ok=True)

settings = {
    "hooks": {
        "PreToolUse": [{
            "matcher": "Bash",
            "hooks": [{"type": "command",
                       "command": "python3 .claude/hooks/guard.py"}],
        }],
    },
}
(practice / ".claude" / "settings.json").write_text(json.dumps(settings, indent=2))

# The guard itself. This one really is a script on disk, because that is what a
# Claude Code command hook is: a program, run per tool call, judged by its exit code.
guard_source = '''\
"""PreToolUse guard: read the tool call on stdin, exit 2 to block it."""
import json
import sys

BLOCKED = ("rm -rf", "DROP TABLE", "git push --force")

payload = json.load(sys.stdin)                       # Claude Code pipes JSON in
command = payload.get("tool_input", {}).get("command", "")

if any(bad in command for bad in BLOCKED):
    print(f"blocked by policy: {command}", file=sys.stderr)   # the model reads this
    sys.exit(2)                                      # 2 = deny the tool call
sys.exit(0)                                          # 0 = allow
'''
(practice / ".claude" / "hooks" / "guard.py").write_text(guard_source)

print("wrote:", practice / ".claude" / "settings.json")
print("wrote:", practice / ".claude" / "hooks" / "guard.py")

# %%
%%bash
# Run the guard exactly the way Claude Code runs it: the tool call as JSON on
# stdin, and the decision read off $? — the exit code.
cd ~/ccdv-practice/hooks-demo

echo '{"tool_name":"Bash","tool_input":{"command":"pytest -q"}}' \
  | python3 .claude/hooks/guard.py
echo "  exit $?  <- allowed"

echo '{"tool_name":"Bash","tool_input":{"command":"psql -c \"DROP TABLE customers\""}}' \
  | python3 .claude/hooks/guard.py
echo "  exit $?  <- BLOCKED, and the stderr line above is what the model is told"

# %%
# And the CLI itself. Run it exactly as you would in a terminal — `!` sends the
# line to the shell. If Claude Code is not installed you get `command not found`,
# which is a readable answer, not a crash. Never run `claude -p "…"` from a
# notebook: that starts a real, billed session.
!claude --version

# %% [markdown]
# **Practical use:** answer two questions before writing a line. **Who writes the
# loop?** — you (manual), the SDK, or Anthropic. **Who runs the machine?** —
# your infra (self-hosted) or Anthropic's per-session container (Managed Agents).
# Then, whichever tier you land on, put every "always / never / must" rule in a
# **hook**, because a prompt is advisory and a hook is code. And own your own
# stop condition: the model's `end_turn` is not a budget, `max_turns` is.
#
# **🏋️ Practice:** the guard must also block `DROP TABLE`, which arrives from a
# `Bash` call running `psql`. The fix is marked `# <-` below and is already in
# place, so the cell passes as shipped.
#
# To feel it: delete `"DROP TABLE"` from `POLICY`, re-run, and watch the assert
# fail as a destructive command sails through. Put it back. Then add a rule of
# your own — say, blocking `chmod 777` — and a case to prove it.

# %%
# Your turn — see the `# <-` marker.
POLICY = ("rm -rf", "DROP TABLE", "git push --force")     # <- "DROP TABLE" is the fix

CASES = [
    ("pytest -q",                                   "allow"),
    ("psql -c 'DROP TABLE customers'",              "deny"),
    ("rm -rf /var/data",                            "deny"),
    ("git status",                                  "allow"),
]

# The exam's guarantee: deterministic code decides, not a prompt request.
decided = []
for command, expected in CASES:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    got = "deny" if any(bad in payload["tool_input"]["command"] for bad in POLICY) else "allow"
    decided.append(got)
    print(f"{got:5s} (want {expected:5s})  <- {command}")

assert decided == [want for _, want in CASES], "a destructive command still gets through"
print("\nevery case decided correctly — the guarantee holds")
