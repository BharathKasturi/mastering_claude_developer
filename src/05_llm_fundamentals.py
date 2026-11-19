# %% [markdown]
# # Topic 3.1 · LLM Fundamentals: Tokens, Context, Sampling, Thinking Modes, Effort, Shot Prompting
#
# 🎓 *CCDV-F objective 5 of 25 · exam weight 5.2% · API lesson*
#
# **Why this matters.** Everything Claude does is next-token prediction over a
# bounded window, and the next token is **sampled**, not looked up. Almost every
# "why did it do that?" question comes straight off that one sentence — why the
# output changes between identical runs, why a long session forgets its own start,
# why thinking sometimes eats the whole answer. Get the mechanism and the exam's
# behaviour questions stop being trivia.
#
# **What you'll learn.**
# - Count **tokens** instead of estimating them, and see what shares the **context window**.
# - Explain **non-determinism** from sampling, and test properties rather than bytes.
# - Choose between **extended thinking** (`budget_tokens`) and **adaptive thinking** + `effort`.
# - Know what **fast mode** buys, and what it does not.
# - Use **zero-, one- and multi-shot** prompting, and know when examples beat instructions.
#
# **What you need.** Nothing. Every cell runs offline with clearly-labelled pretend
# results. Set `ANTHROPIC_API_KEY` to make the token count and the classifier call
# the real API.

# %%
# Load the shared setup: the API client and MODEL.
%run ./00_setup.ipynb

# %% [markdown]
# ### Feature 1 — Tokens, and what shares the window
#
# A token is roughly ¾ of an English word — until it is code, JSON, a long number,
# or a non-Latin script, where the ratio moves. So **never estimate; count.**
#
# ```
#   ONE REQUEST'S WINDOW
#   ┌────────────────────────────────────────────────────────────────────────┐
#   │ tool schemas │ system │ prior turns + tool results │ thinking │ output │
#   └────────────────────────────────────────────────────────────────────────┘
#    ◄──────────────────── input_tokens ────────────────►  ◄─ output_tokens ─►
#                                                            (capped by max_tokens)
#
#   input + max_tokens must fit in the window, or the request is a 400.
# ```
#
# Two consequences people miss. First, the window is not "the system prompt" — in
# an agent loop the **tool results dominate**, because every appended result stays
# for the rest of the run. Second, the API is **stateless**: every turn re-sends the
# whole history, so a 40-turn session pays for 40 growing prefixes. That is why
# prompt caching and pruning exist.

# %%
# Show where the window actually goes. `count_tokens` is a real endpoint — it
# charges nothing and returns the number the API would bill you for.
SYSTEM = "You are a support triage assistant. Reply with one label only."
TOOLS = [{
    "name": "lookup_account",
    "description": "Look up a customer account by its id.",
    "input_schema": {"type": "object",
                     "properties": {"account_id": {"type": "string"}},
                     "required": ["account_id"]},
}]
HISTORY = [
    {"role": "user", "content": "My card was charged twice."},
    {"role": "assistant", "content": "billing"},
    {"role": "user", "content": "Here is the raw statement export: " + "1234,USD,2026-01-0" * 200},
]
DOT = [{"role": "user", "content": "."}]      # a near-empty message, to isolate one part

# Each row is one request shape: a name, its messages, its system, its tools.
shapes = [
    ("system only",           DOT,     SYSTEM, None),
    ("tool schemas",          DOT,     None,   TOOLS),
    ("history + tool result", HISTORY, None,   None),
    ("everything together",   HISTORY, SYSTEM, TOOLS),
]

counted = {}
for name, messages, system, tools in shapes:
    if client is None:
        # A deliberately crude stand-in so the cell runs offline. The whole point
        # of this Feature is that you should NOT trust an estimate like this.
        counted[name] = len(json.dumps([messages, system, tools])) // 4
    else:
        kwargs = {"model": MODEL, "messages": messages}
        if system:
            kwargs["system"] = system          # the system prompt costs input tokens
        if tools:
            kwargs["tools"] = tools            # so do the tool schemas
        counted[name] = client.messages.count_tokens(**kwargs).input_tokens

    print(f"{name:24s} {counted[name]:7,d} input tokens")

print("\ncounting is", "live" if client else "offline (estimated — do not trust it)")

# %%
# The arithmetic that decides whether a request is legal at all. Input and
# max_tokens share one window; going over is a 400 before anything runs.
WINDOW = 200_000            # 200K on Haiku 4.5 and older; 1M on Opus 5 / Sonnet 5
max_tokens = 8_000
used = counted["everything together"]

print(f"input {used:,} + max_tokens {max_tokens:,} = {used + max_tokens:,} "
      f"of {WINDOW:,} -> {'fits' if used + max_tokens <= WINDOW else '400 too large'}")

# %% [markdown]
# ### Feature 2 — Sampling, and why the answer changes
#
# ```
#   window ──► model ──► P(next token | window) ──► sample ──► append ──► repeat
#                                  │
#                         "the"  0.41
#                         "a"    0.22     ◄── sampling draws from this distribution,
#                         "our"  0.09         not always from the top entry
# ```
#
# What follows from that, and what the exam checks:
#
# - **Same input, different output is normal.** No setting removes it entirely.
#   `temperature`, `top_p` and `top_k` are rejected with a 400 on Opus 4.7+,
#   Sonnet 5 and Opus 5, and even where they are accepted, hardware differences can
#   still flip a token.
# - **The model does not know what it will say next.** It cannot check its own
#   answer unless you give it room to think, or a second call to review.
# - **Position matters.** Instructions near the end of a long window carry more
#   weight than the same text buried in the middle.
#
# So a test that asserts byte-equality against a golden file is measuring luck. Test
# the **properties**: the required facts are present, the JSON parses, the label is
# one of the allowed set.

# %%
# Two ways to test the same classifier. One is a coin flip; one is a real check.
SAMPLES = [                      # three sampled answers to the same prompt
    '{"label": "billing", "confidence": 0.91}',
    '{"label": "billing", "confidence": 0.88}',
    '{"label": "billing",  "confidence": 0.9}',
]
GOLDEN = SAMPLES[0]
ALLOWED = ("billing", "bug", "feature_request", "other")

byte_passes, property_passes = 0, 0
for answer in SAMPLES:
    # The anti-pattern: compare bytes to a golden file.
    bytes_ok = answer == GOLDEN

    # The real check: assert what the caller depends on — valid JSON, a known
    # label, a confidence in range — not the exact bytes one sample produced.
    try:
        parsed = json.loads(answer)
        properties_ok = (parsed.get("label") in ALLOWED
                         and 0.0 <= parsed.get("confidence", -1) <= 1.0)
    except json.JSONDecodeError:
        properties_ok = False

    byte_passes += bytes_ok
    property_passes += properties_ok
    print(f"bytes={str(bytes_ok):5s} properties={str(properties_ok):5s}  {answer}")

print(f"\nbyte test passes {byte_passes}/3 — that is the flake."
      f"\nproperty test passes {property_passes}/3 — that is a test.")

# %% [markdown]
# ### Feature 3 — Thinking: fixed budget, or adaptive plus effort
#
# Thinking spends **output tokens** to reason before answering. There are two APIs
# and the model decides which one you get:
#
# | | Older models (Sonnet 4.5, Haiku 4.5) | Current models (Opus 4.6+, Sonnet 5, Opus 5) |
# |---|---|---|
# | Field | `thinking={"type": "enabled", "budget_tokens": N}` | `thinking={"type": "adaptive"}` |
# | Who decides depth | you, with a fixed allowance | the model, steered by `effort` |
# | Depth control | `budget_tokens`, minimum 1024 | `output_config={"effort": ...}` |
# | Sending `budget_tokens` | required | **400** — the field is gone |
#
# Two arithmetic rules cause most of the failures:
#
# 1. `budget_tokens` must be **strictly less than** `max_tokens`. Equal or greater
#    is a 400 before anything runs.
# 2. `max_tokens` caps **thinking plus visible text**. Budget 6,000 inside a 6,500
#    cap leaves 500 tokens for the answer, and you get `stop_reason: "max_tokens"`
#    with a thinking block and no reply.
#
# `effort` runs `low` → `medium` → `high` (the default) → `xhigh` → `max`. Use `low`
# for routine high-volume work, `xhigh` for coding and agentic runs, `max` when
# correctness outranks cost. With adaptive thinking the model can also think
# **between** tool calls, not only before the first one.

# %%
# Both request shapes, side by side, and the checks that catch the 400s before you
# pay for them. A pinned older model id, used only to contrast the two APIs —
# pinning an exact id is the habit the exam wants; an alias can move underneath you.
OLD_MODEL = "claude-haiku-4-5-20251001"
ADAPTIVE_ONLY = ("claude-opus-5", "claude-sonnet-5", "claude-opus-4-6")

legacy_request = {
    "model": OLD_MODEL, "max_tokens": 8000,
    "thinking": {"type": "enabled", "budget_tokens": 4000},
    "messages": [{"role": "user", "content": "Plan the migration."}],
}
current_request = {
    "model": MODEL, "max_tokens": 16000,
    "thinking": {"type": "adaptive", "display": "summarized"},   # default is "omitted"
    "output_config": {"effort": "high"},                         # low|medium|high|xhigh|max
    "messages": [{"role": "user", "content": "Plan the migration."}],
}
broken_request = {
    "model": MODEL, "max_tokens": 4000,
    "thinking": {"type": "enabled", "budget_tokens": 6000},
    "messages": [],
}

for name, request in [("legacy (valid)", legacy_request),
                      ("current (valid)", current_request),
                      ("broken", broken_request)]:
    problems = []
    budget = request.get("thinking", {}).get("budget_tokens")

    # No budget_tokens at all means adaptive thinking, or none — nothing to check.
    if budget is not None:
        if request["model"] in ADAPTIVE_ONLY:
            problems.append("budget_tokens is rejected on this model — use adaptive + effort")
        if budget >= request["max_tokens"]:
            problems.append("budget_tokens must be strictly less than max_tokens")
        if budget < 1024:
            problems.append("budget_tokens has a minimum of 1024")
        # Not a 400, but the failure people actually hit: no room left for an answer.
        if request["max_tokens"] - budget < 1000:
            problems.append("thinking leaves almost no room for text — expect "
                            "stop_reason 'max_tokens' with no answer")

    print(f"{name:16s} {problems if problems else 'ok'}")

# %% [markdown]
# ### Feature 4 — One lever per axis: fast mode, effort, model
#
# Fast mode is the lever people reach for by mistake, so it is worth being exact:
#
# ```python
# r = client.beta.messages.create(
#     model="claude-opus-5", max_tokens=4096,
#     speed="fast", betas=["fast-mode-2026-02-01"],
#     messages=msgs)
# print(r.usage.speed)      # "fast" if it ran in fast mode
# ```
#
# - Research preview on **Opus 5 and Opus 4.8 only**, Claude API and Managed Agents
#   only — not Batch, not Bedrock, Vertex or Foundry.
# - **Same weights, same answers in expectation.** Up to about 2.5× output tokens
#   per second, at a premium per-token price.
# - Its own rate limit. On a 429, wait or drop `speed`. Switching speed also
#   invalidates the prompt cache.
#
# So fast mode answers *"the response takes too long to stream"*. It does not answer
# *"the answer is wrong"*. Each lever moves exactly one axis, and naming the axis
# first is the whole trick.

# %%
# Name the axis, then pick the lever. Answering a quality complaint with a speed
# lever (or the reverse) is the mistake the exam keeps setting up.
LEVERS = {
    "capacity":    "context window and max_tokens — count first, prune, or stream",
    "reasoning":   "thinking + output_config.effort (xhigh for coding/agentic work)",
    "speed":       "speed='fast' on the beta endpoint, or lower effort, or stream",
    "variability": "you cannot remove it — validate properties, or structured outputs",
    "format":      "few-shot examples, or a schema — not more emphasis",
}

symptoms = [
    "The review misses subtle issues",
    "The streamed review takes too long to appear",
    "The answer is cut off mid-sentence",
    "The test is flaky, the output is different every time",
    "The date format drifts about once in ten calls",
]

for symptom in symptoms:
    words = symptom.lower()
    if "cut off" in words or "truncat" in words or "forgets" in words:
        axis = "capacity"
    elif "misses" in words or "shallow" in words or "wrong reasoning" in words:
        axis = "reasoning"
    elif "slow" in words or "takes too long" in words:
        axis = "speed"
    elif "different every time" in words or "flaky" in words:
        axis = "variability"
    else:
        axis = "format"

    print(f"{axis:12s} {symptom}\n             -> {LEVERS[axis]}\n")

# %% [markdown]
# ### Feature 5 — Zero-, one-, and multi-shot
#
# ```
#   ZERO-SHOT    instruction only              cheap; fine for common tasks, strong models
#   ONE-SHOT     + 1 example                   pins a format or one edge convention
#   MULTI-SHOT   + 3-6 diverse examples        pins format AND boundary cases; best for
#                                              classification, extraction, house style
# ```
#
# Few-shot is a **sampling intervention**: examples reshape the distribution the
# model draws from, which is why they beat adjectives at fixing format drift. Two
# rules come with them. Examples cost tokens on **every** call, so put them in the
# stable prefix where prompt caching can pay for them. And examples that are all the
# easy case teach a bias — cover every label and the awkward edges.
#
# The split the exam wants: *wrong format or inconsistent labels* → more examples.
# *Wrong reasoning* → thinking, or a better decomposition of the task. Examples do
# not make the model smarter; they make it consistent.

# %%
# The same classifier, zero-shot and multi-shot. Only the system prompt differs —
# the request shape is identical, which is the point.
LABELS = "billing | bug | feature_request | other"

ZERO_SHOT = f"""Classify the support message into exactly one label:
{LABELS}. Reply with the label only."""

MULTI_SHOT = ZERO_SHOT + """

<examples>
<example><input>My card was charged twice this month.</input><label>billing</label></example>
<example><input>The export button does nothing on Safari.</input><label>bug</label></example>
<example><input>Would love dark mode.</input><label>feature_request</label></example>
<example><input>Thanks, that fixed it!</input><label>other</label></example>
</examples>"""

MESSAGE = "Invoice shows the wrong VAT rate."

for name, system in [("zero-shot ", ZERO_SHOT), ("multi-shot", MULTI_SHOT)]:
    messages = [{"role": "user", "content": MESSAGE}]

    # What this prompt costs on EVERY call, counted rather than guessed.
    if client is None:
        cost = len(json.dumps([messages, system])) // 4
        label = "[PRETEND: billing — set ANTHROPIC_API_KEY for a real label]"
    else:
        cost = client.messages.count_tokens(
            model=MODEL, system=system, messages=messages).input_tokens
        resp = client.messages.create(
            model=MODEL,
            max_tokens=5,
            system=system,
            messages=messages,
        )
        label = resp.content[0].text.strip()

    print(f"{name} {cost:5,d} input tokens  ->  {label}")

print("\nThe extra tokens are paid on EVERY call — which is what caching is for.")

# %% [markdown]
# **Practical use:** name the axis before you touch a parameter. Capacity is the
# window and `max_tokens`. Reasoning depth is thinking and `effort`. Speed is fast
# mode or lower effort. Variability is sampling — you accept it and validate
# properties instead of bytes. Format and style are examples. Each lever moves
# exactly one axis, and the classic wasted afternoon is answering a format problem
# with `effort: max`, or a quality problem with fast mode.
#
# **🏋️ Practice:** the check below has to catch the request that earns a 400 before
# it is sent. Current models reject `budget_tokens` outright — the list of those
# models is marked `# <-` and is already complete, so the cell passes as shipped.
#
# To feel it: delete `"claude-sonnet-5"` from `ADAPTIVE_MODELS`, re-run, and watch
# a request that would 400 be reported as fine. Put it back. Then add a check that
# `effort` is one of the five allowed values, and a case that exercises it.

# %%
# Your turn — see the `# <-` marker.
ADAPTIVE_MODELS = ("claude-opus-5", "claude-sonnet-5", "claude-opus-4-6")   # <- the fix

cases = [
    (("claude-sonnet-5", 16000, {"type": "adaptive"}),                       False),
    (("claude-sonnet-5", 16000, {"type": "enabled", "budget_tokens": 4000}), True),
    ((OLD_MODEL, 8000, {"type": "enabled", "budget_tokens": 4000}),          False),
    ((OLD_MODEL, 4000, {"type": "enabled", "budget_tokens": 6000}),          True),
    ((OLD_MODEL, 8000, {"type": "enabled", "budget_tokens": 512}),           True),
]

verdicts = []
for (model, cap, thinking), expected in cases:
    budget = thinking.get("budget_tokens")
    if budget is None:
        rejected = False                   # adaptive thinking, or none at all
    elif model in ADAPTIVE_MODELS:
        rejected = True                    # the field is gone on these models
    else:
        rejected = budget >= cap or budget < 1024

    verdicts.append(rejected)
    print(f"{'400 ' if rejected else 'ok  '} (want {'400' if expected else 'ok'})  "
          f"{model} {thinking}")

assert verdicts == [want for _, want in cases], \
    "a request that would 400 is being reported as legal"
print("\nevery illegal thinking request is caught before it costs anything")
