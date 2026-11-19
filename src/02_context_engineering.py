# %% [markdown]
# # Topic 2.1 · Context Engineering: Window Management, Drift & Bloat, Compaction, Isolation
#
# 🎓 *CCDV-F objective 2 of 25 · exam weight 3.8% · API lesson*
#
# **Why this matters.** A long-running agent does not fail because the model got
# worse. It fails because the window filled with things that stopped mattering.
# Bigger windows moved that failure mode; they did not remove it. Fill a 1M-token
# window with 200 stale tool results and it loses to a 50K window holding only
# what the next step needs — and the API is stateless, so you pay for the junk on
# every single turn.
#
# **What you'll learn.**
# - Account for a window: where the tokens actually go, and why
#   `input_tokens + max_tokens` must fit or the request is a **400**.
# - Tell **bloat** from **drift** — two different diseases with different fixes.
# - **Prune** at the tool boundary, then server-side with context editing.
# - Use **compaction** for a long session and **memory** for the next one, and
#   never confuse the two.
# - **Isolate** a reading-heavy sub-task so its tokens never enter the main window.
# - Pick the technique from the symptom, instead of reaching for the biggest one.
#
# **What you need.** Nothing. The transcripts below are simulated so the whole
# lesson runs offline. Set `ANTHROPIC_API_KEY` and one cell will count a real
# request with `count_tokens` instead of printing a note.

# %%
# Load the shared setup: the API client and MODEL.
%run ./00_setup.ipynb

# %% [markdown]
# ### Feature 1 — Window accounting
#
# ```
# ONE REQUEST  (1M on Opus 5 / Sonnet 5; 200K on Haiku 4.5)
# ┌──────────────────────────────────────────────────────────────────────┐
# │ tools │ system │ turn 1 │ turn 2 │ … │ tool_result │ tool_result │ out │
# └──────────────────────────────────────────────────────────────────────┘
#    ~2K     ~3K                          ← this is where agents actually spend
#
# input_tokens + max_tokens must fit the window, or the request is a 400.
# The API is STATELESS: turn k re-sends turns 1..k-1, so a bloated window is
# billed again on every turn (lesson 15).
# ```
#
# Two facts drive every technique in this lesson. **Attention is not uniform**
# across the window — an instruction near the end carries more weight than the
# same text buried in the middle (*lost in the middle*). And **the system prompt
# is almost never the problem**; in an agent loop, the tool results are.

# %%
# A repo-maintenance agent 30 turns in, as plain dicts. This is OUR toy model of
# a context window — a real count comes from client.messages.count_tokens(). It
# lets us show the accounting with no key and no 400K-token fixture.
transcript = (
    [{"kind": "tools",  "tokens": 2_000, "note": "12 tool schemas"},
     {"kind": "system", "tokens": 3_000, "note": "role, rules, output contract"},
     {"kind": "user",   "tokens": 200,   "note": "turn 1: the actual objective"}]
    + [{"kind": "tool_result", "tokens": 9_000, "note": f"turn {t}: grep / file read dump"}
       for t in range(2, 32)]
    + [{"kind": "user", "tokens": 150, "note": "turn 31: the current question"}]
)

# Total the window, then total it again per kind. Note where the mass sits.
window = sum(b["tokens"] for b in transcript)

by_kind = {}
for b in transcript:
    by_kind[b["kind"]] = by_kind.get(b["kind"], 0) + b["tokens"]

print(f"window total {window:,} tokens")
for kind, total in sorted(by_kind.items(), key=lambda kv: -kv[1]):
    print(f"  {kind:12s} {total:>8,}  {total / window:>5.0%}")

# Instrument this — do not wait for the 400. Count, compare to a soft limit,
# and prune before the hard limit is anywhere near.
SOFT_LIMIT = 150_000
print(f"\nover the {SOFT_LIMIT:,}-token soft limit? "
      f"{'YES — prune now' if window > SOFT_LIMIT else 'no'}")

# The real thing, for comparison: count_tokens sizes an actual request.
if client is None:
    print("[skipped — set ANTHROPIC_API_KEY to size a real request with count_tokens]")
else:
    counted = client.messages.count_tokens(
        model=MODEL,
        system="You are a repo maintenance agent.",
        messages=[{"role": "user", "content": "Which files changed?"}],
    )
    print(f"count_tokens on a real (tiny) request: {counted.input_tokens} tokens")

# %% [markdown]
# ### Feature 2 — Bloat and drift are different diseases
#
# ```
# BLOAT                                  DRIFT
# ─────                                  ─────
# symptom: slower, pricier, misses       symptom: confidently uses a fact that was
#          instructions it once obeyed            superseded three turns ago
# cause:   accumulated tool output,      cause:   old plans, corrected values, stale
#          re-read files, verbose                 errors left in the window with
#          results, unpruned history              nothing marking them dead
# fix:     prune / compact / isolate     fix:     prune the superseded turn, and
#                                                 re-state current state LATE in the
#                                                 window — recency wins
# ```
#
# The anti-patterns the exam draws its stems from: **re-reading the same file
# every turn** so the window holds five near-identical versions; **dumping a
# 200-row result** when three rows were needed; **leaving a failed attempt
# verbatim**, so "command not found" from ten turns back reads as current
# environment state; and **never restating the goal**, so by turn 60 the original
# objective sits in the least-attended region of the window.

# %%
# The decision rule as a lookup: ask what the failure actually IS, then pick.
SYMPTOM_TO_FIX = {
    "quality degrades after N turns":            "bloat — prune first (tool boundary, then context editing)",
    "acts on information already corrected":     "drift — prune the superseded turn, restate current state late",
    "conversation will exceed the window":       "compaction",
    "must survive a restart / next week":        "memory tool or your own store — NEVER compaction",
    "one sub-task reads huge documents":         "isolation — subagent or programmatic tool calling",
    "ignores an instruction in a long transcript": "lost-in-the-middle — move it to the end",
}
for symptom, fix in SYMPTOM_TO_FIX.items():
    print(f"{symptom:45s} -> {fix}")

# Reaching for compaction when the real problem is 200 uncleaned tool results
# throws away the good context along with the bad.

# %% [markdown]
# ### Feature 3 — Pruning: at the tool boundary first, then server-side
#
# The best token is the one that never entered. Filter in your own tool handler
# before the result is ever appended to the window.
#
# Then let the API clear what has gone stale. **Context editing deletes; it does
# not summarise.**
#
# ```python
# r = client.beta.messages.create(
#     model="claude-opus-5", max_tokens=8000,
#     betas=["context-management-2025-06-27"],
#     context_management={"edits": [
#         {"type": "clear_tool_uses_20250919", "clear_tool_inputs": True},
#         {"type": "clear_thinking_20251015"},
#     ]},
#     tools=TOOLS, messages=messages,
# )
# ```
#
# - `clear_tool_uses_20250919` clears tool **results**; `clear_tool_inputs: true`
#   also drops the `tool_use` parameters that produced them.
# - `clear_thinking_20251015` drops prior thinking blocks.
# - Thresholds (when clearing triggers, how many recent tool uses to keep) are
#   configurable and version-dependent — read the context-management docs rather
#   than guessing a field name.
# - Do **not** confuse the betas: `context-management-2025-06-27` with `clear_*`
#   edits is pruning; `compact-2026-01-12` with `compact_20260112` is compaction.
#   Two features on the same `context_management` parameter.
# - Both rewrite the message prefix, so they **invalidate the messages cache** at
#   the point they edit. Trigger on a threshold, never on every turn.

# %%
# Pruning where it is cheapest: inside your own tool handler, before the result
# ever enters the window. A query returned 200 rows; return a count and a sample.
ROWS, ROW_TOKENS = 200, 45
dumped = ROWS * ROW_TOKENS

if ROWS > 20:
    # Return the count plus a sample. The agent can ask for more if it needs it.
    result_text = f"{ROWS} rows. First 20 shown."
    pruned = 20 * ROW_TOKENS + 10
else:
    result_text = f"{ROWS} rows."
    pruned = ROWS * ROW_TOKENS

print(f"raw dump                    {dumped:>6,} tokens  (billed again on EVERY later turn)")
print(f"pruned at the tool boundary {pruned:>6,} tokens  -> {1 - pruned / dumped:.0%} smaller")
print(f"what the model actually sees: {result_text!r}")

# %%
# Server-side clearing, applied on a THRESHOLD — this is what
# clear_tool_uses_20250919 does. It invalidates the cache from the edit point
# on, so it is worth doing once and wasteful every turn.
KEEP_RECENT = 4

if window > SOFT_LIMIT:
    positions = [i for i, b in enumerate(transcript) if b["kind"] == "tool_result"]
    drop = set(positions[:-KEEP_RECENT])          # keep the newest few, clear the rest
    after = [b for i, b in enumerate(transcript) if i not in drop]
else:
    after = transcript                            # under the limit: leave it alone

print(f"window before {window:>8,}"
      f"\nwindow after  {sum(b['tokens'] for b in after):>8,}"
      f"  ({len(transcript) - len(after)} tool results cleared)")

# %% [markdown]
# ### Feature 4 — Compaction summarises; memory outlives
#
# ```
#                   within one session          across sessions
#                ┌───────────────────────────┬──────────────────────────┐
# delete content │  context editing (prune)  │            —             │
# summarise      │  compaction               │  your own session digest │
# externalise    │  files / DB / memory tool │  memory tool, DB, files  │
#                └───────────────────────────┴──────────────────────────┘
#
# "It must remember X tomorrow" is NEVER answered by compaction.
# ```
#
# **Compaction** runs server-side, replacing earlier turns with a generated
# summary as the conversation approaches a trigger threshold (default around
# 150K input tokens). Beta `compact-2026-01-12`, edit `compact_20260112`,
# supported on Opus 5 / Opus 4.6–4.8 / Sonnet 5 / Sonnet 4.6 — **not** Haiku 4.5.
#
# One mistake breaks it, and it is the exam's favourite: appending
# `r.content[0].text` instead of the whole `r.content` list. The compaction block
# lives in `content`, and it is what the API uses to replace the compacted
# history. Throw it away and the history silently rebuilds from scratch.
#
# ```python
# r = client.beta.messages.create(
#     betas=["compact-2026-01-12"],
#     model="claude-opus-5", max_tokens=16000,
#     context_management={"edits": [{"type": "compact_20260112"}]},
#     messages=messages,
# )
# messages.append({"role": "assistant", "content": r.content})    # the WHOLE list
# ```
#
# **Memory** is the other axis — not smaller context, but context that outlives
# the window:
#
# ```python
# r = client.messages.create(
#     model="claude-opus-5", max_tokens=8000,
#     tools=[{"type": "memory_20250818", "name": "memory"}, *OTHER_TOOLS],
#     messages=messages,
# )
# # Client-side tool: Claude reads and writes a /memories directory.
# # YOU implement the backend — and you own its security boundary (lesson 19).
# ```
#
# Compaction is lossy by design. Anything that must be exact — an id, a decision,
# a file path — belongs in memory or your own store, not in a summary.

# %%
# Which technique answers which requirement. This table is the whole objective.
TECHNIQUES = [
    #  technique                            lossy   survives session   use when
    ("tool-output pruning (clear_tool_uses)", "yes", "no",  "tool results dominate and old ones are dead"),
    ("clearing thinking (clear_thinking)",    "yes", "no",  "long reasoning you never replay"),
    ("compaction (compact_20260112)",         "yes", "no",  "the conversation will hit the window"),
    ("memory tool (memory_20250818)",         "no",  "YES", "facts or progress needed next session"),
    ("isolation (subagents, workflows)",      "no",  "your harness", "reading-heavy or fan-out sub-tasks"),
    ("programmatic tool calling",             "no",  "no",  "many calls with large throwaway results"),
]
print(f"{'technique':40s} {'lossy':6s} {'outlives':14s} use when")
for name, lossy, outlives, when in TECHNIQUES:
    print(f"{name:40s} {lossy:6s} {outlives:14s} {when}")

# %% [markdown]
# ### Feature 5 — Isolation: give the mess its own window
#
# ```
# WITHOUT ISOLATION                    WITH SUBAGENT ISOLATION
# main window                          main window
# ┌──────────────────────────┐         ┌──────────────────────┐
# │ system + tools           │         │ system + tools       │
# │ doc A  (40K)             │         │ "summarise 12 docs"  │
# │ doc B  (40K)             │         │ ← 12 × ~400-token    │
# │ … 12 docs … (480K)       │         │    findings          │
# │ the actual question      │         │ the actual question  │
# └──────────────────────────┘         └──────────────────────┘
# ```
#
# Three shapes, in rising order of machinery:
#
# - **Multi-step workflow** — your code runs step 1, keeps only its output, and
#   starts step 2 with a fresh, small message list. No framework. The right answer
#   whenever the steps are known in advance (lesson 1's workflow-not-agent choice).
# - **Subagents** — a separate call with its own window and often a cheaper model.
#   Narrow contract: do this one thing, return a short structured result. Caches
#   are model-scoped, so a subagent on another model reads none of the parent's.
# - **Programmatic tool calling** — Claude writes a script in the code-execution
#   container that calls your tools as functions; intermediate results stay in the
#   sandbox and only the final output returns. Declare
#   `{"type": "code_execution_20260120", "name": "code_execution"}` and mark your
#   tool `"allowed_callers": ["code_execution_20260120"]`.
#
# A different bloat has its own fix: **tool search**
# (`tool_search_tool_regex_20251119` / `tool_search_tool_bm25_20251119`) with
# `defer_loading: true` on the deferred tools. Schemas are *appended* rather than
# swapped, so the cached prefix survives. Never defer every tool — the search tool
# and at least one other must stay loaded, or the request is a 400.

# %%
# Put a number on why isolation is the strongest of the four techniques.
N_DOCS, DOC_TOKENS, FINDING_TOKENS, QUESTION_TOKENS = 12, 40_000, 400, 150

# Without: every document sits in the ONE window, re-sent on every later turn.
without = N_DOCS * DOC_TOKENS + QUESTION_TOKENS

# With: each subagent reads one document in ITS window and is then discarded.
with_isolation = N_DOCS * FINDING_TOKENS + QUESTION_TOKENS

print(f"main window, all 12 reports loaded   {without:>9,} tokens")
print(f"main window, 12 subagent findings    {with_isolation:>9,} tokens"
      f"   ({with_isolation / without:.1%} of the original)")

# The caching consequence, so isolation does not read as free: N parallel workers
# with different prompts write N cache entries and read none of each other's, and
# none can read an entry still being written. Send one, await its first token,
# then fire the rest.
print("\nfan-out caveat: send one, wait for its first token, THEN fire the other 11")

# %%
# Tool search: keep 60 schemas out of the fixed prefix without breaking the cache.
TOOL_SEARCH = {"type": "tool_search_tool_regex_20251119", "name": "tool_search"}
DEFERRED = [{"name": f"tool_{i}", "description": "...", "input_schema": {"type": "object"},
             "defer_loading": True} for i in range(3)]
ALWAYS_LOADED = {"name": "read_file", "description": "Read one file.",
                 "input_schema": {"type": "object"}}     # at least one must stay loaded

tools = [TOOL_SEARCH, ALWAYS_LOADED, *DEFERRED]
deferred_count = sum(1 for t in tools if t.get("defer_loading"))
print(f"{len(tools)} tools declared, {deferred_count} deferred, "
      f"{len(tools) - deferred_count} always loaded")
print("all deferred -> 400: the search tool and at least one other must stay loaded")

# %% [markdown]
# **Practical use:** ask what the failure actually is before you pick a technique.
# Old junk in the window is **bloat** — prune, at the tool boundary first. A fact
# that was corrected and is still being used is **drift** — delete the superseded
# turn and restate current state in the *latest* turn, because recency beats
# position one. Genuinely too long is **compaction**. Needed after this session is
# **memory**, never compaction. A sub-task that would flood the window is
# **isolation**. And a bigger window is not a fix: it just buys room for more junk
# and multiplies the per-turn re-send cost.
#
# **🏋️ Practice:** a team enables compaction and the agent still forgets earlier
# decisions. Their loop appends only the first text block. The append below keeps
# the whole `reply.content` list (marked `# <-`), so the compaction block survives
# and the cell passes as shipped.
#
# To feel it: change `reply.content` to `[reply.content[0]]`, re-run, and watch
# the assert fail — the compaction block is gone and the history rebuilds from
# scratch on the next request. Put it back. Then add a thinking block to the
# simulated response and confirm it survives too.

# %%
# Your turn — see the `# <-` marker.
from collections import namedtuple

# Block/Response stand in for the SDK's response objects. Real blocks expose
# .type the same way. The second block's type name is version-dependent — the
# point is only that it is NOT a text block, so indexing [0].text drops it.
Block = namedtuple("Block", "type text", defaults=("", ""))
Response = namedtuple("Response", "content")

reply = Response(content=[
    Block(type="text", text="Renamed the module and updated the imports."),
    Block(type="compaction", text=""),      # stand-in for the block the API returns
])

# Append the assistant turn to the history, keeping EVERY block.
history = [{"role": "user", "content": "rename the module"}]
history.append({"role": "assistant", "content": reply.content})   # <- the whole list, not reply.content[0].text

kept = [b.type for b in history[-1]["content"]]
print("blocks kept in the history:", kept)

assert len(history[-1]["content"]) == len(reply.content), (
    f"dropped {len(reply.content) - len(history[-1]['content'])} block(s) — "
    "the compaction block is state, not decoration")
print("every block survived — compaction can replace the history next request")
