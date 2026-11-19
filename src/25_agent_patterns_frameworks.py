# %% [markdown]
# # Topic 8.3 · Agent Patterns & Frameworks: Tool Loops, Memory, Strands / LangGraph / PydanticAI
#
# 🎓 *CCDV-F objective 25 of 25 · exam weight 4.9% · API lesson*
#
# **Why this matters.** Every agent, in every framework, is the same four-beat
# loop: **model → tool_use → execute → tool_result → model**. A framework does not
# change what the model sees. It changes who owns **control flow**, **state** and
# **types**. Get that straight and the framework questions answer themselves, and
# so do the "our agent forgot the brief" questions, which are never about the model.
#
# **What you'll learn.**
# - Run the four-beat loop and the four rules the exam probes about it.
# - Use **sub-agents** for isolation, and see the token bill they keep out of the parent.
# - Tell the **three kinds of memory** apart: context, working, long-term.
# - Apply the **four context-window levers** in the right order: prune, isolate, compact.
# - Pick **Strands / LangGraph / PydanticAI / plain SDK** from one property of the stem.
#
# **What you need.** Nothing. The loop runs against scripted replies and the memory
# tool writes into a scratch folder. No framework needs installing — their code is
# shown and explained, not imported. Set `ANTHROPIC_API_KEY` to make the live cells
# call the real API.

# %%
# Load the shared setup: the API client and MODEL.
%run ./00_setup.ipynb

# %% [markdown]
# ### Feature 1 — The four-beat loop, and the four rules
#
# ```
#    messages = [user: task]
#          │
#          ▼
#    POST /v1/messages (tools=[...])
#          ├── stop_reason == "end_turn"    ──► done, read the text
#          ├── stop_reason == "max_tokens"  ──► truncated, retry or raise the cap
#          ├── stop_reason == "refusal"     ──► handle, do not retry
#          └── stop_reason == "tool_use"
#                   │
#                   ▼
#      for each tool_use block (there may be SEVERAL — run them concurrently)
#          run it ──► tool_result {tool_use_id, content, is_error?}
#                   │
#                   ▼
#      append the assistant turn verbatim (thinking + tool_use blocks included)
#      append ONE user turn with ALL tool_results  ──► loop
# ```
#
# The four rules, independent of framework:
#
# 1. **Append the assistant message verbatim** — `response.content`, not just its
#    text. Thinking and `tool_use` blocks must survive or the next call fails.
# 2. **One user message per tool round**, holding every `tool_result`.
#    Splitting them teaches the model to stop parallelising.
# 3. **Errors are results, not exceptions** — `is_error: True`. The model can
#    recover from a result; it cannot recover from a crashed loop.
# 4. **`tool_use.input` is a parsed dict.** Never string-match it; the model's
#    JSON escaping varies between releases.

# %%
# The tools, described to the model with the real field names.
TOOLS = [
    {"name": "grep_logs", "description": "Search the logs for a pattern.",
     "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"}},
                      "required": ["pattern"]}},
    {"name": "read_config", "description": "Read one config key.",
     "input_schema": {"type": "object", "properties": {"key": {"type": "string"}},
                      "required": ["key"]}},
]

# What the "model" replies, turn by turn, so the loop runs with no API key. Turn 1
# asks for two tools at once and carries a thinking block — both must survive.
scripted_turns = [
    {"stop_reason": "tool_use", "content": [
        {"type": "thinking", "thinking": "Check the logs and the timeout together."},
        {"type": "tool_use", "id": "tu_1", "name": "grep_logs", "input": {"pattern": "timeout"}},
        {"type": "tool_use", "id": "tu_2", "name": "read_config", "input": {"key": "http.timeout"}},
    ]},
    {"stop_reason": "end_turn", "content": [
        {"type": "text", "text": "Three timeouts; http.timeout is 30s. Raise it."}]},
]

messages = [{"role": "user", "content": "Why are we seeing timeouts?"}]
answer = "[stopped: hit max_turns]"
max_turns = 10

for reply in scripted_turns[:max_turns]:
    # RULE 1 — the whole content list, blocks and all.
    messages.append({"role": "assistant", "content": reply["content"]})

    if reply["stop_reason"] != "tool_use":
        answer = "".join(b["text"] for b in reply["content"] if b["type"] == "text")
        break

    results = []
    for b in reply["content"]:
        if b["type"] != "tool_use":
            continue
        # RULE 4 — b["input"] is already a dict, so read its keys directly.
        if b["name"] == "grep_logs":
            out = {"hits": 3, "first": f"...{b['input']['pattern']}..."}
            results.append({"type": "tool_result", "tool_use_id": b["id"],
                            "content": json.dumps(out)})
        elif b["name"] == "read_config":
            out = {"key": b["input"]["key"], "value": "30s"}
            results.append({"type": "tool_result", "tool_use_id": b["id"],
                            "content": json.dumps(out)})
        else:
            # RULE 3 — an unknown tool is a result the model can read, not a crash.
            results.append({"type": "tool_result", "tool_use_id": b["id"],
                            "content": f"no such tool: {b['name']}", "is_error": True})

    # RULE 2 — one user message, every result inside it.
    messages.append({"role": "user", "content": results})

print("FINAL:", answer, "\n")
for i, m in enumerate(messages):
    kinds = [b["type"] for b in m["content"]] if isinstance(m["content"], list) else ["text"]
    print(f"{i}  {m['role']:9s} {len(kinds)} block(s): {kinds}")

print("\nnote message 2: ONE user message carrying BOTH tool_results")

# %% [markdown]
# ### Feature 2 — Sub-agents: isolation is the point
#
# A parent delegates a bounded task to a child. The child runs **its own loop** in
# **its own context** and returns a **short** result.
#
# ```
#    parent context:  [task] [plan] ─────────────────────────► [child summary] [next]
#                                    │                              ▲
#                                    │  brief (self-contained)      │  ≤ 1 KB result
#                                    ▼                              │
#    child context:               [brief] [read 40 files] [tool noise ×30] [answer]
#                                         └── never enters the parent's window
# ```
#
# Three properties make it a pattern and not just another call:
#
# - **Isolation** — the child's 150K tokens of file reads never bloat the parent.
# - **Least privilege** — the child gets only the tools its job needs.
# - **Parallelism** — independent children run at once; wall clock ≈ max, not sum.
#
# Every framework names it differently — a LangGraph supervisor node with worker
# subgraphs, a Strands agent exposed as a tool, the Claude Agent SDK's `agents`
# option, a plain call that starts a fresh `messages` list. Same shape.

# %%
# Put a number on the isolation. Tokens ≈ characters / 4 is close enough to make
# the point; the exam cares about the ratio, not the estimator.
FILES = {f"service_{i}.py": "x" * 6000 for i in range(40)}     # 40 files to read

# If the PARENT read all forty files, this is what its window would carry.
inline_tokens = sum(len(body) for body in FILES.values()) // 4

# The child reads exactly the same forty files — inside its own window. Big
# input, small output: that is the whole sub-agent contract.
brief = "Where is the retry policy?"
child_tokens = sum(len(body) for body in FILES.values()) // 4
finding = f"{brief} -> retries are configured in service_7.py, max 3."
parent_tokens = len(finding) // 4                              # what the parent receives

print(f"read inline in the parent : {inline_tokens:>7,} tokens")
print(f"read inside the sub-agent : {child_tokens:>7,} tokens  (isolated — parent never sees it)")
print(f"returned to the parent    : {parent_tokens:>7,} tokens  <- {finding}")
print(f"\nparent window saved       : {inline_tokens - parent_tokens:>7,} tokens")

# Least privilege is the other half: the brief AND the tool list are narrowed.
SUBAGENTS = {"researcher": ["read_file", "grep"],          # cannot write, by construction
             "implementer": ["read_file", "write_file", "run_tests"]}
for name, tools in SUBAGENTS.items():
    print(f"\n{name:12s} tools={tools}")

# %% [markdown]
# ### Feature 3 — Memory: three kinds, three mechanisms
#
# | Kind | Lives | Lifetime | Mechanism |
# |---|---|---|---|
# | **Context (short-term)** | inside `messages` | this request chain | the conversation; grows every turn |
# | **Working (scratchpad)** | a structured object beside the conversation | this task | LangGraph state, PydanticAI deps, a `plan.md` the agent rewrites |
# | **Long-term** | outside the model entirely | across sessions | a file or DB the agent reads and writes through a tool |
#
# The exam's memory question is almost always *"which kind?"*:
# "remembers across sessions" → long-term. "loses the plan mid-task" → working.
# "forgets what was said 50 turns ago" → context management (the next Feature).
#
# Long-term memory has a first-party shape: the `memory_20250818` tool. Anthropic
# defines it, **your client executes it**, and you map its commands —
# `view` / `create` / `str_replace` / `delete` — onto a `/memories` directory
# you persist.

# %%
# The tool definition is one line. The store is yours, and it is a filesystem.
import pathlib

memory_tool = [{"type": "memory_20250818", "name": "memory"}]

MEM_DIR = pathlib.Path.home() / "ccdv-practice" / "memories"
MEM_DIR.mkdir(parents=True, exist_ok=True)

# The four commands the model can send, as it would send them. Your client
# executes each one against the directory and returns the text as tool_result
# content. Session 1 writes the fact; session 2 — a whole different
# conversation — reads it back.
commands = [
    {"command": "create", "path": "travel.md", "file_text": "Berlin: Hotel Adlon, liked it.\n"},
    {"command": "view", "path": "travel.md"},
    {"command": "str_replace", "path": "travel.md",
     "old_str": "liked it", "new_str": "liked it, book again"},
    {"command": "view", "path": "travel.md"},
]

for c in commands:
    target = MEM_DIR / c["path"]
    if c["command"] == "create":
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(c["file_text"])
        result = f"created {c['path']}"
    elif c["command"] == "view":
        result = target.read_text().strip() if target.exists() else f"{c['path']} does not exist"
    elif c["command"] == "str_replace":
        target.write_text(target.read_text().replace(c["old_str"], c["new_str"]))
        result = f"edited {c['path']}"
    elif c["command"] == "delete":
        target.unlink(missing_ok=True)
        result = f"deleted {c['path']}"
    else:
        result = f"unknown command {c['command']!r}"
    print(f"{c['command']:12s} -> {result}")

# The three kinds, side by side, on one task.
context_memory = [{"role": "user", "content": "book the same hotel as my last Berlin trip"}]
working_memory = {"plan": ["look up Berlin", "check dates", "book"], "step": 0}
print(f"\ncontext : {len(context_memory)} message(s), gone when the chain ends")
print(f"working : {working_memory}, gone when the task ends")
print(f"long-term: {MEM_DIR}/travel.md, survives everything")

# %% [markdown]
# ### Feature 4 — Context-window management: four levers, in order
#
# ```
#    LEVER                        WHAT IT DOES                        WHEN
#    ──────────────────────────   ─────────────────────────────────   ──────────────────
#    prune tool output            truncate/summarise big results      every loop —
#                                 BEFORE appending as tool_result     the cheapest lever
#    context editing              the API clears old tool_use and     long tool-heavy runs
#    (clear_tool_uses_20250919)   tool_result blocks, server-side     (clears, not summarises)
#    compaction                   the API summarises earlier context  conversations that
#    (compact_20260112)           into a block you pass back          outlive the window
#    sub-agents                   keep the bloat in another window    reading-heavy subtasks
# ```
#
# Two symptoms map here. **Drift** is the agent gradually ignoring the original
# instruction, because it is now far behind a wall of tool noise. **Bloat** is
# cost and latency climbing until a context-length error. Fix in this order:
# **prune, then isolate with sub-agents, then compact.** A bigger context window
# postpones both and costs more per turn.

# %%
# Lever 1 — prune. Your code, no beta header, biggest win per line. Truncate the
# raw output BEFORE it becomes a tool_result, or an 80 KB grep lives in the
# window for the rest of the run.
raw = "matched line\n" * 6000                       # a realistic grep result
cap = 4000                                          # the most characters to keep

if len(raw) > cap:
    kept = raw[:cap] + f"\n…[truncated {len(raw) - cap} chars; ask for a range if needed]"
else:
    kept = raw

pruned = {"type": "tool_result", "tool_use_id": "tu_1", "content": kept}

print(f"raw    : {len(raw):>7,} chars ≈ {len(raw)//4:>6,} tokens")
print(f"pruned : {len(pruned['content']):>7,} chars ≈ {len(pruned['content'])//4:>6,} tokens")
print(f"saved  : {(len(raw) - len(pruned['content']))//4:>6,} tokens on ONE tool call")

# %%
# Levers 2 and 3 — hand the job to the API. Both are betas, and they are NOT the
# same thing: editing CLEARS old tool blocks, compaction SUMMARISES the context.
context_managed_request = {
    "model": MODEL,
    "max_tokens": 4096,
    "betas": ["context-management-2025-06-27"],
    "context_management": {"edits": [{"type": "clear_tool_uses_20250919"}]},
    "messages": [{"role": "user", "content": "continue the investigation"}],
}
print("beta request shape:", json.dumps(context_managed_request["context_management"]))

if client is None:
    print("[skipped the live beta call — set ANTHROPIC_API_KEY to send it]")
else:
    # A beta surface can be off for an account, so report a refusal instead of
    # killing the notebook. Pass reply.content back verbatim on the next turn.
    try:
        reply = client.beta.messages.create(**context_managed_request)
        print("stop_reason:", reply.stop_reason)
    except Exception as exc:
        print(f"[live beta call refused: {exc}]")

# The order matters more than the levers. Rehearse it as a list.
print("\nfix order: 1 prune tool output  2 isolate in sub-agents  3 compaction  "
      "(a bigger window is not on this list)")

# %% [markdown]
# ### Feature 5 — The frameworks, by what each makes explicit
#
# ```
#    Strands      tools + model            (control flow implicit — the model's)
#    LangGraph    control flow + state     (nodes, edges, checkpoints — durable)
#    PydanticAI   types + dependencies     (schemas validated, deps injected)
#    plain SDK    nothing                  (you write all three)
# ```
#
# ```python
# # STRANDS — model-driven; the loop is inside Agent()
# agent = Agent(model=AnthropicModel(model_id=MODEL, max_tokens=8000),
#               tools=[lookup_order], system_prompt="You are a support agent.")
# agent("Where is order 8812?")
#
# # LANGGRAPH — you draw the graph; the checkpointer makes it resumable
# graph.add_conditional_edges("act", lambda s: "act" if s["plan"] else END)
# app = graph.compile(checkpointer=MemorySaver())
# app.invoke({"messages": [("user", task)]}, config={"configurable": {"thread_id": "job-42"}})
#
# # PYDANTIC AI — typed output; the loop retries until the schema validates
# agent = Agent("anthropic:" + MODEL, output_type=Triage, deps_type=Db)
# result.output.severity      # a validated int, not a string you have to parse
# ```
#
# **Decision rule.** *Who decides the next step?* The model → Strands (or an SDK
# loop). Your code, with branches and resumability → LangGraph. *Is the hard part
# the shape of the output?* → PydanticAI. "No third-party framework" → plain SDK.
#
# Careful with one trap: LangGraph is an explicit graph, so it is **workflow**-shaped;
# Strands is a model-driven loop, so it is **agent**-shaped. Many people say it backwards.

# %%
# The tells, as code. Read a stem, name the property it needs owned.
STEMS = [
    ("an explicit state machine with branching",              "langgraph"),
    ("must resume after a pod restart, mid-approval",         "langgraph"),
    ("validated, typed structured output",                    "pydantic_ai"),
    ("the model decides, minimal code, AWS-native",           "strands"),
    ("no third-party framework may enter the build",          "plain_sdk"),
]

for stem, expected in STEMS:
    words = stem.lower()

    # Control flow and durability first — they are the strongest signals.
    if any(w in words for w in ("state machine", "branch", "resume", "checkpoint",
                                "human approval mid-flow")):
        framework = "langgraph"
    elif any(w in words for w in ("typed", "schema", "validated", "dependency injection")):
        framework = "pydantic_ai"
    elif any(w in words for w in ("no framework", "no third-party", "zero dependencies")):
        framework = "plain_sdk"
    else:
        framework = "strands"

    print(f"{framework:12s} (want {expected:12s})  <- {stem}")

# What none of them change: the request the model actually receives.
print("\nsame Messages API underneath all four:",
      json.dumps({"model": MODEL, "max_tokens": 1024, "tools": ["…"],
                  "messages": ["…"]}))

# %% [markdown]
# **Practical use:** the loop is always the same four beats, so debug it with the
# four rules — assistant turn appended whole, one user message per round, errors
# returned as results, `input` treated as a dict. When a long run goes wrong, ask
# **which kind of memory** was missing (context, working, long-term) and reach for
# the levers in order: prune, isolate, compact. And pick a framework by the one
# property you need owned — control flow, state, or types — never by popularity.
#
# **🏋️ Practice:** the stem *"the run must survive a pod restart and resume where
# it left off"* has to route to **LangGraph**, but the router only knows the word
# `resume`. The fix is marked `# <-` below and is already in place, so the cell
# passes as shipped.
#
# To feel it: delete `"restart"` from `DURABLE_WORDS`, re-run, and watch the
# assert fail as a durability stem lands on Strands. Put it back. Then add a
# fifth stem of your own — try one about long-term memory — and teach the router.

# %%
# Your turn — see the `# <-` marker.
DURABLE_WORDS = ("state machine", "branch", "resume", "checkpoint", "restart")  # <- "restart"

CASES = [
    ("the run must survive a pod restart and pick up where it left off", "langgraph"),
    ("downstream code crashes on a missing field; we need validated output", "pydantic_ai"),
    ("ship fast, let the model plan, keep our code tiny", "strands"),
    ("security forbids any third-party agent framework", "plain_sdk"),
]

# Route each stem to the framework that owns the property it needs.
routed = []
for stem, expected in CASES:
    words = stem.lower()
    if any(w in words for w in DURABLE_WORDS):
        framework = "langgraph"
    elif any(w in words for w in ("typed", "schema", "validated")):
        framework = "pydantic_ai"
    elif any(w in words for w in ("no framework", "third-party", "zero dependencies")):
        framework = "plain_sdk"
    else:
        framework = "strands"
    routed.append(framework)
    print(f"{framework:12s} (want {expected:12s})  <- {stem}")

assert routed == [want for _, want in CASES], "a durability stem still routes wrong"
print("\nall four stems route to the right framework")
