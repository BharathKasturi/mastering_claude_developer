# %% [markdown]
# # Topic 3.4 · Cost & Token Management: Usage Tracking, Cost Modelling, Prompt Caching
#
# 🎓 *CCDV-F objective 8 of 25 · exam weight 2.8% · API lesson*
#
# **Why this matters.** The API is **stateless**, so every token you send is
# billed again on every turn. The same 30 KB of instructions is paid for over and
# over unless you do something about it. The something is **prompt caching**, and
# it is the only lever that cuts cost without trading quality — but it fails
# silently. No error, just a bigger bill. The only ground truth is `usage`.
#
# **What you'll learn.**
# - Read the four **`usage`** fields, and why `input_tokens` is *not* the prompt size.
# - Price a request **before** you send it with **`count_tokens`**.
# - Build a **cost model** you can plug your own numbers into — including the
#   1.25× / 2× write premium and the 0.1× read.
# - Place **`cache_control`** correctly: render order, the 4-breakpoint limit, and
#   the model-dependent minimum below which a marker silently does nothing.
# - **Check-point** a growing conversation so each turn reads the prefix and
#   writes only the delta.
# - Use the levers that *do* cost something — **batch** (50% off), effort,
#   `max_tokens` vs `task_budget` — in the right order.
#
# **What you need.** Nothing. Every number below is computed offline from the
# published price list. Set `ANTHROPIC_API_KEY` and one cell will call the live
# `count_tokens` endpoint instead of printing a note.

# %%
# Load the shared setup: the API client and MODEL.
%run ./00_setup.ipynb

# %% [markdown]
# ### Feature 1 — Four numbers on every response
#
# ```python
# u = response.usage
# u.input_tokens                  # processed at FULL price — the uncached remainder ONLY
# u.cache_creation_input_tokens   # written to cache this request (1.25× for 5m, 2× for 1h)
# u.cache_read_input_tokens       # served from cache this request (0.1×)
# u.output_tokens                 # generated — thinking tokens are billed here
#
# u.cache_creation.ephemeral_5m_input_tokens    # the write, broken down by TTL
# u.cache_creation.ephemeral_1h_input_tokens
# ```
#
# The single most-tested fact on this objective: **`input_tokens` is not the
# prompt size.** An agent that ran for an hour can report `input_tokens: 4000`
# because everything else was a cache read.
#
# ```
# total prompt = input_tokens + cache_creation_input_tokens + cache_read_input_tokens
# ```

# %%
# `Usage` is OUR stand-in for `response.usage` — NOT part of the Anthropic SDK.
# The four field names are exactly the real ones, so every line of arithmetic in
# this lesson works unchanged when you point it at a real response's usage.
from collections import namedtuple

Usage = namedtuple(
    "Usage",
    "input_tokens cache_creation_input_tokens cache_read_input_tokens output_tokens",
    defaults=(0, 0, 0, 0),
)

# A healthy cached turn deep inside an agent session: a big read, a small write,
# and a tiny uncached tail. Read input_tokens alone and you under-report 12×.
turn = Usage(input_tokens=400, cache_creation_input_tokens=1_200,
             cache_read_input_tokens=48_000, output_tokens=900)

# The real prompt size is all THREE input fields added together, never just one.
real_prompt = turn.input_tokens + turn.cache_creation_input_tokens + turn.cache_read_input_tokens

print(f"input_tokens                 {turn.input_tokens:>7,}   <- the tail only")
print(f"cache_creation_input_tokens  {turn.cache_creation_input_tokens:>7,}   <- this turn's delta")
print(f"cache_read_input_tokens      {turn.cache_read_input_tokens:>7,}   <- the whole prior prefix")
print(f"output_tokens                {turn.output_tokens:>7,}   <- thinking is billed here")
print(f"\nreal prompt size             {real_prompt:>7,}"
      f"   ({real_prompt / turn.input_tokens:.0f}× what input_tokens suggests)")

# %% [markdown]
# ### Feature 2 — Price it *before* you send it
#
# `messages.count_tokens` counts the exact request you are about to make — the
# same `system`, `tools` and `messages` the model will see. It is free, has its
# own rate limit, and is the only honest way to size a prompt.
#
# **Never estimate from word count.** Code and JSON tokenise far denser than
# prose, and a tokenizer change between releases moves the number by up to 1.35×.

# %%
# The exact request we are about to size. `tools` would render FIRST and count
# too, so pass it here as well whenever the real request carries tools.
SYSTEM = "You are a claims triage assistant. Answer with the category only."
MSGS = [{"role": "user", "content": "The roof leaked into the server room at 3am."}]

# The count_tokens call, exactly as you would write it in a script.
if client is None:
    print("count_tokens -> [skipped — set ANTHROPIC_API_KEY to call the free endpoint]")
else:
    counted = client.messages.count_tokens(
        model=MODEL,
        system=SYSTEM,
        messages=MSGS,
    )
    print("count_tokens ->", counted.input_tokens)

# For the whole organisation's spend rather than one request, the Usage and Cost
# Admin API aggregates by workspace, API key and model. It needs an Admin key
# (sk-ant-admin…) and is not exposed in the SDKs.
print("\n'which request is expensive?'      -> response.usage")
print("'what did each team spend?'        -> Usage and Cost Admin API (admin key)")

# %% [markdown]
# ### Feature 3 — The cost model
#
# ```
# ONE REQUEST
# cost = input_tokens   × in_price
#      + cache_creation × in_price × 1.25      <- 2.0 for ttl "1h"
#      + cache_read     × in_price × 0.10
#      + output_tokens  × out_price            <- thinking is billed as output
#
# Prices per MTok (Claude API, first-party):
#   claude-opus-5     $5.00 in / $25.00 out    (fast mode: $10 / $50)
#   claude-sonnet-5   $2.00 in / $10.00 out
#   claude-haiku-4-5  $1.00 in / $5.00 out
# Batch API: 50% off both columns. Bedrock / Vertex bill separately.
# ```
#
# Two modelling mistakes the exam punishes. Pricing a chatbot as *one* request
# instead of a growing prefix. And forgetting that **thinking tokens are output
# tokens** — a `max` effort route on Opus 5 bills reasoning at $25/MTok whether
# or not `display` shows it to you.

# %%
# The published list price, per million tokens. One place to update.
PRICE = {"claude-opus-5": (5.00, 25.00),
         "claude-sonnet-5": (2.00, 10.00),
         "claude-haiku-4-5": (1.00, 5.00)}

# The three multipliers applied to the INPUT price. Reads are the whole point.
WRITE_5M, WRITE_1H, READ = 1.25, 2.00, 0.10

# THE ONE FUNCTION IN THIS LESSON, and it is here because it *is* the objective:
# "cost modeling" is the skill, and this is the model you paste into your own
# project and plug your own token counts into. Every other cell is straight-line.
def request_cost(model, u, ttl="5m", batch=False):
    """Price one request from its usage — the model you plug your numbers into.

    Args:
        model (str): a key of PRICE.
        u (Usage): the usage for this request.
        ttl (str): '5m' or '1h' — which write premium the cache entry paid.
        batch (bool): True if submitted through the Batch API (50% off both).
    Returns:
        float: dollars for this one request.
    """
    pin, pout = PRICE[model]
    write = WRITE_1H if ttl == "1h" else WRITE_5M
    dollars = (u.input_tokens * pin
               + u.cache_creation_input_tokens * pin * write
               + u.cache_read_input_tokens * pin * READ
               + u.output_tokens * pout) / 1_000_000
    return dollars * 0.5 if batch else dollars

# Same 50,000-token prompt, three ways. Only the cache columns move.
cold  = Usage(input_tokens=50_000, output_tokens=900)                       # no caching
write = Usage(input_tokens=500, cache_creation_input_tokens=49_500, output_tokens=900)
read  = Usage(input_tokens=500, cache_read_input_tokens=49_500, output_tokens=900)

for label, u in (("uncached", cold), ("first call (write)", write), ("every call after (read)", read)):
    print(f"{label:24s} ${request_cost('claude-opus-5', u):.4f}")

print(f"\nbreak-even: a 5m entry pays from the SECOND request "
      f"({WRITE_5M} + {READ} = {WRITE_5M + READ} < 2 × 1.0)")
print(f"            a 1h entry needs a THIRD "
      f"({WRITE_1H} + 2 × {READ} = {WRITE_1H + 2 * READ} < 3 × 1.0)")

# %% [markdown]
# ### Feature 4 — `cache_control`: where the marker goes, and when it does nothing
#
# ```
# RENDER ORDER AND WHAT A CHANGE COSTS
#
#  [ tools ] [ system ] [ messages … ]
#     ▲          ▲            ▲
#     │          │            └─ change here: nothing upstream is lost
#     │          └─ change here: system + messages caches rebuild
#     └─ change here (add / remove / reorder a tool, or switch model): FULL rebuild
# ```
#
# - **Max 4 breakpoints** per request. `cache_control` goes on any content block —
#   system text, a tool definition, `text` / `image` / `tool_use` / `tool_result` /
#   `document`.
# - The **minimum cacheable prefix is model-dependent and not monotonic**. Below
#   it the marker is accepted and silently does nothing.
# - **A read refreshes the timer for free.** Requests under 5 minutes apart keep
#   the default entry alive indefinitely, so `ttl: "1h"` only earns its 2× write
#   on **gaps of 5–60 minutes**.
# - Caches are scoped per workspace and **per model**. A model switch is a full
#   rebuild with no escape hatch.
# - On Opus 5 / Opus 4.8 an operator instruction can be sent as a
#   `{"role": "system", …}` message *inside* `messages[]` — same instruction,
#   cached history intact, instead of editing top-level `system`.

# %%
# The real request shape. Volatile text goes AFTER the last breakpoint, or you
# pay the write premium on bytes nobody ever reads back.
CACHED_REQUEST = {
    "model": "claude-opus-5",
    "max_tokens": 4096,
    "tools": [],                                    # renders FIRST — reorder it and everything rebuilds
    "system": [{"type": "text", "text": "<BIG_STABLE_PROMPT>",
                "cache_control": {"type": "ephemeral"}}],          # caches tools + system together
    "messages": [{"role": "user", "content": [
        {"type": "text", "text": "<SHARED_DOCS>",
         "cache_control": {"type": "ephemeral", "ttl": "1h"}},     # 2× write, survives a 30-min gap
        {"type": "text", "text": "<todays_question>"},             # volatile — after the last breakpoint
    ]}],
}
print(json.dumps(CACHED_REQUEST, indent=2))

# %%
# Minimum cacheable prefix, per model. Not monotonic — you cannot reason it out,
# you have to look it up.
MIN_CACHEABLE = {"claude-opus-5": 512, "claude-opus-4-8": 1024, "claude-sonnet-5": 1024,
                 "claude-sonnet-4-6": 1024, "claude-opus-4-7": 2048,
                 "claude-opus-4-6": 4096, "claude-haiku-4-5": 4096}

# The nightly-pipeline trap: a 3,000-token shared prefix caches on Opus 5 and
# Sonnet 5, and quietly does not on Haiku 4.5. Below the floor the marker is
# ACCEPTED and does nothing — no error, no warning, just a bigger bill.
PREFIX_TOKENS = 3_000
for model_id in ("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"):
    floor = MIN_CACHEABLE[model_id]
    if PREFIX_TOKENS >= floor:
        verdict = f"caches ({PREFIX_TOKENS:,} >= {floor:,} minimum)"
    else:
        verdict = f"SILENTLY DOES NOTHING ({PREFIX_TOKENS:,} < {floor:,} minimum)"
    print(f"{model_id:20s} {PREFIX_TOKENS:,}-token prefix -> {verdict}")

# The other silent killer: anything that changes the prefix bytes. Freeze the
# prefix, sort tool lists deterministically, move volatile text to the tail.
INVALIDATORS = ["datetime.now() in the system prompt", "a UUID or request id",
                "a user name interpolated into system", "tools built from a set (unsorted)",
                "a conditionally-appended section", "changing thinking or effort mid-session"]
print("\nsilent cache invalidators to grep for:")
for item in INVALIDATORS:
    print("  -", item)

# %% [markdown]
# ### Feature 5 — Check-pointing a growing conversation
#
# ```
# AN AGENT LOOP IS QUADRATIC, NOT LINEAR
# The API is stateless — turn k re-sends turns 1..k-1.
#
# turn 1: [S][u1]                          input = S + u1
# turn 2: [S][u1][a1][t1][u2]              input = S + u1 + a1 + t1 + u2
# turn 3: [S][u1][a1][t1][u2][a2][t2][u3]  input = … and so on
# ```
#
# **Check-pointing** moves the breakpoint forward as the conversation grows. Each
# request then *reads* everything before it and *writes* only the newest turn:
#
# ```python
# messages.append({"role": "user", "content": [{"type": "text", "text": user_text}]})
# messages[-1]["content"][-1]["cache_control"] = {"type": "ephemeral"}   # the check-point
# r = client.messages.create(
#     model="claude-opus-5", max_tokens=8000,
#     system=[{"type": "text", "text": SYSTEM,
#              "cache_control": {"type": "ephemeral"}}],                 # anchor that never moves
#     messages=messages)
# messages.append({"role": "assistant", "content": r.content})
# ```
#
# ```
# THE HEALTHY LOOP SIGNATURE (check every deploy, not just at setup)
#   cache_read_input_tokens      ▁▂▃▄▅▆▇█   grows turn over turn
#   cache_creation_input_tokens  ▂▂▂▂▂▂▂▂   small and flat — the newest delta
#   input_tokens                 ▁▁▁▁▁▁▁▁   only the tail after the last breakpoint
# ```
#
# Three sharp edges: a breakpoint walks back at most **20 content blocks**, so an
# agentic turn appending 30 `tool_use`/`tool_result` pairs silently misses — add
# an intermediate breakpoint about every 15 blocks. An entry is readable only
# once the first response **starts streaming**, so N parallel identical-prefix
# requests all pay full price. And `max_tokens: 0` pre-warms a cache, but is
# rejected with `stream: true`, `output_config.format`, forced `tool_choice`, or
# inside a batch.

# %%
# Total a whole 20-turn session both ways, side by side. The `prefix` line is the
# quadratic term: turn k re-sends everything turns 1..k-1 already sent.
TURNS, SYSTEM_TOKENS, TURN_TOKENS, OUT_TOKENS = 20, 3_000, 2_000, 800

plain = 0.0
cached = 0.0
for k in range(TURNS):
    prefix = SYSTEM_TOKENS + k * TURN_TOKENS          # everything re-sent this turn

    # Stateless and uncached: the whole conversation at full price, again.
    plain += request_cost("claude-opus-5",
                          Usage(input_tokens=prefix + TURN_TOKENS, output_tokens=OUT_TOKENS))

    # Check-pointed: the prior prefix is read at 0.1×, only this turn's delta is written.
    cached += request_cost("claude-opus-5",
                           Usage(cache_read_input_tokens=prefix,
                                 cache_creation_input_tokens=TURN_TOKENS,
                                 output_tokens=OUT_TOKENS))

print(f"{TURNS}-turn Opus 5 session, uncached      ${plain:.2f}")
print(f"{TURNS}-turn Opus 5 session, check-pointed ${cached:.2f}")
print(f"saved {1 - cached / plain:.0%} — and nothing about the prompt changed")

# %% [markdown]
# ### Feature 6 — The levers that do cost something, in order
#
# | Lever | What it costs you | Typical effect | Reach for it |
# |---|---|---|---|
# | **Prompt caching** | nothing in quality; 1.25×/2× on writes | up to ~90% off the repeated prefix | **first** |
# | **Input hygiene** (prune, dedupe) | engineering time | shrinks the number multiplied by every turn | second (lesson 16) |
# | **Output hygiene** (`max_tokens`, "answer only") | nothing, if the format is genuinely shorter | output is 5× input price everywhere | second |
# | **Batch API** | up to 24 h latency | **50%** off input *and* output | any workload with no human waiting |
# | **Effort** (`output_config: {effort: …}`) | thoroughness on hard tasks | large on agentic routes, small on classification | third, per route |
# | **Cheaper model** | capability | $5/$25 → $2/$10 → $1/$5 per MTok | last, and per route |
#
# **Decision rule: take every lever that costs no quality before any lever that
# does.** And judge **cost per completed task** — a cheap call that needs three
# retries is not cheap.
#
# | Guardrail | What it is | What it is **not** |
# |---|---|---|
# | `max_tokens` | hard per-response ceiling; the model is unaware and gets cut off | a budget it paces against |
# | `task_budget` (beta, in `output_config`) | advisory ceiling so an agentic loop paces itself; minimum 20,000 | enforced — it will not stop a runaway loop |
# | Effort | thinking depth and verbosity within one model | a price list — measure before raising a default |
# | Batch | 50% on both columns, ≤24 h | compatible with fast mode or streaming |

# %%
# Not latency-sensitive -> the Batch API. Results come back in ANY order, so key
# by custom_id, never by position.
docs = ["<doc 1>", "<doc 2>", "<doc 3>"]
BATCH_REQUESTS = [
    {"custom_id": f"doc-{i}",
     "params": {"model": "claude-haiku-4-5", "max_tokens": 512,
                "messages": [{"role": "user", "content": doc}]}}
    for i, doc in enumerate(docs)
]
print(json.dumps(BATCH_REQUESTS[0], indent=2))
print("\n# client.messages.batches.create(requests=BATCH_REQUESTS)")
print("# poll batches.retrieve(id).processing_status == 'ended', then key results by custom_id")

# %%
# Costing the nightly pipeline: 40,000 documents, one of six labels each.
DOCS_PER_NIGHT = 40_000
per_doc = Usage(input_tokens=1_500 + 3_000,   # the document + the shared instruction/few-shot block
                output_tokens=5)              # one word

realtime = request_cost("claude-haiku-4-5", per_doc)
batched = request_cost("claude-haiku-4-5", per_doc, batch=True)
print(f"per document, realtime  ${realtime:.5f}")
print(f"per document, batch     ${batched:.5f}")
print(f"per night ({DOCS_PER_NIGHT:,} docs) realtime ${realtime * DOCS_PER_NIGHT:,.2f}"
      f"   batch ${batched * DOCS_PER_NIGHT:,.2f}")

# And the trap in this exact scenario: the 3,000-token shared prefix is BELOW
# Haiku 4.5's minimum, so a cache_control marker on it does nothing at all.
haiku_floor = MIN_CACHEABLE["claude-haiku-4-5"]
print(f"\nshared prefix on Haiku 4.5 -> "
      f"{'caches' if 3_000 >= haiku_floor else 'SILENTLY DOES NOTHING'} "
      f"(3,000 vs a {haiku_floor:,}-token minimum)")

# %% [markdown]
# **Practical use:** caching first, because it is the only lever that costs no
# quality — but it is a **prefix match**, so any byte that changes early kills it
# silently. Freeze the prefix, put volatile text after the last breakpoint,
# check-point the growing turns, and verify with `cache_read_input_tokens`. Log
# `usage` on every call with the route name attached, and assert in CI that a
# second identical request comes back with `cache_read_input_tokens > 0` — a
# caching regression never raises, it just arrives on next month's invoice.
#
# **🏋️ Practice:** finance asks how big the prompt on a long agent turn really
# was. The sum below already adds all three input fields (the last one is marked
# `# <-`), so the cell passes as shipped.
#
# To feel it: delete `+ AGENT_TURN.cache_read_input_tokens`, re-run, and watch the
# assert fail reporting 1,600 tokens for a 49,600-token prompt — the exact
# under-report that makes a cached agent look free. Put it back. Then price the
# turn with `request_cost()` and compare it against the same turn with no
# caching at all.

# %%
# Your turn — see the `# <-` marker.
AGENT_TURN = Usage(input_tokens=400, cache_creation_input_tokens=1_200,
                   cache_read_input_tokens=48_000, output_tokens=900)

# Total tokens the model read this request — all three input fields, added.
prompt_tokens = (AGENT_TURN.input_tokens
                 + AGENT_TURN.cache_creation_input_tokens
                 + AGENT_TURN.cache_read_input_tokens)   # <- the read is the biggest one on a cached turn

print("input_tokens alone reports :", f"{AGENT_TURN.input_tokens:,}")
print("the real prompt size is    :", f"{prompt_tokens:,}")
print("this turn cost             :", f"${request_cost('claude-opus-5', AGENT_TURN):.4f}")

assert prompt_tokens == 49_600, (
    f"expected 49,600 tokens, got {prompt_tokens:,} — a field is missing")
print("\nall three input fields counted")
