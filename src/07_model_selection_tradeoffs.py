# %% [markdown]
# # Topic 3.3 · Model Selection & Tradeoffs: Opus / Sonnet / Haiku, Quality-Latency-Cost, Release Changes
#
# 🎓 *CCDV-F objective 7 of 25 · exam weight 2.7% · API lesson*
#
# **Why this matters.** Three tiers, three price points, one API. Every design
# review eventually asks "which model?" and the honest answer is a measurement,
# not a preference. Pick too big and you pay 5× for label lookups; pick too small
# and you pay in retries, escalations and a human fixing it afterwards. Then a
# new release lands and last month's answer quietly stops being true.
#
# **What you'll learn.**
# - Read the **three tiers** — ids, prices, context windows, thinking support.
# - Price a request with the **two-term formula**, then price the thing that
#   actually matters: **cost per completed, correct task**.
# - **Route by difficulty** so one product uses all three tiers, and see when a
#   cascade costs more than it saves.
# - Check **feature parity** before you send: adaptive thinking and `effort` on
#   the 5-family, `budget_tokens` on Haiku 4.5.
# - **Pin** an exact model id, and run the migration ritual that makes the pin mean
#   something.
# - Tell a **retirement** (404, a config change) from a transient (retry).
#
# **What you need.** Nothing. Every cell runs offline. Set `ANTHROPIC_API_KEY`
# and the last cell will ask the live API which models are actually served today.

# %%
# Load the shared setup: the API client and MODEL.
%run ./00_setup.ipynb

# %% [markdown]
# ### Feature 1 — Three tiers, one API
#
# The tiers differ in exactly the ways that matter to a design: what they cost,
# how much they can hold, how fast the first token arrives, and which thinking
# controls they accept. Quality, latency and cost move **together**. There is no
# free lunch, only a right-sized one.
#
# | | Opus 5 | Sonnet 5 | Haiku 4.5 |
# |---|---|---|---|
# | Model id | `claude-opus-5` | `claude-sonnet-5` | `claude-haiku-4-5` |
# | $ per MTok in / out | 5 / 25 | 2 / 10 | 1 / 5 |
# | Context window | 1M | 1M | 200K |
# | Thinking | adaptive, effort `low`–`max` | adaptive, effort `low`–`max` | `enabled` + `budget_tokens`, no effort |
# | Best for | hardest reasoning, long agentic runs, complex code | the default production model | classification, routing, extraction, sub-agents |
#
# Prices are the Anthropic API list at time of writing and they change with
# releases — cite the pricing page in a design doc, never last quarter's number.
# Bedrock and Vertex bill separately.
#
# **Decision rule: start on Sonnet.** Measure on your eval. Move down the routes
# where Haiku passes; move up only the routes where Sonnet fails and Opus passes.

# %%
# The catalogue, as data. Everything later in the lesson reads from this dict,
# so there is exactly one place to update when the pricing page changes.
MODELS = {
    # id                  in    out   context  thinking control          typical job
    "claude-opus-5":     dict(price=(5, 25), context=1_000_000, thinking="adaptive+effort",
                              best_for="hardest reasoning, long agentic runs, complex code"),
    "claude-sonnet-5":   dict(price=(2, 10), context=1_000_000, thinking="adaptive+effort",
                              best_for="the default: coding, RAG, chat, most agents"),
    "claude-haiku-4-5":  dict(price=(1, 5),  context=200_000,   thinking="budget_tokens",
                              best_for="classification, routing, extraction, sub-agents"),
}

# Print it as a table so the shape of the trade-off is visible at a glance.
print(f"{'model':20s} {'$/MTok in/out':>14s} {'context':>10s}  {'thinking':16s} best for")
for model_id, spec in MODELS.items():
    pin, pout = spec["price"]
    print(f"{model_id:20s} {f'{pin}/{pout}':>14s} {spec['context']:>10,}  "
          f"{spec['thinking']:16s} {spec['best_for']}")

# %% [markdown]
# ### Feature 2 — Cost is two terms, and output is the expensive one
#
# ```
# cost = input_tokens × in_price  +  output_tokens × out_price
# ```
#
# Output is 5× the input price on every tier, so a generation task and an
# extraction task have completely different shapes even at the same token count.
#
# Then the subtlety the exam loves. A **cheaper model that needs more retries,
# more turns, or a human correction is not cheaper.** Compare **cost per
# completed, correct task** — the price per request divided by the rate at which
# that model actually passes your eval.

# %%
# `Usage` is OUR tiny stand-in for `response.usage` — NOT part of the Anthropic
# SDK. The two attribute names are exactly the real ones, so the arithmetic below
# works unchanged on a live response. This lets us price requests with no key.
from collections import namedtuple

Usage = namedtuple("Usage", "input_tokens output_tokens")

# A typical RAG-ish answer: 3,000 tokens of context in, 800 tokens of answer out.
u = Usage(input_tokens=3_000, output_tokens=800)

# The two-term formula, written out. Prices are per MILLION tokens, hence 1e6.
for model_id, spec in MODELS.items():
    pin, pout = spec["price"]
    dollars = (u.input_tokens * pin + u.output_tokens * pout) / 1_000_000
    print(f"{model_id:20s} ${dollars:.4f} per request")

# The same three models on a task Haiku is bad at. Pass rates come from YOUR
# eval — these are stand-ins so the arithmetic is visible.
PASS_RATE = {"claude-opus-5": 0.98, "claude-sonnet-5": 0.95, "claude-haiku-4-5": 0.40}

print("\nper *correct* task (price ÷ pass rate) on a route Haiku struggles with:")
for model_id, spec in MODELS.items():
    pin, pout = spec["price"]
    dollars = (u.input_tokens * pin + u.output_tokens * pout) / 1_000_000
    print(f"{model_id:20s} ${dollars / PASS_RATE[model_id]:.4f}")

# %% [markdown]
# ### Feature 3 — Routing by difficulty
#
# One product, three tiers. A **static router** maps a task type to a model and
# its per-route defaults. Most systems need nothing more than this.
#
# ```
# ROUTER SHAPES
# static      task type → model            simplest; most systems need only this
# cascade     Haiku → validate → Sonnet    saves money when most items are easy;
#             → validate → Opus            costs latency on the hard ones
# classifier  one cheap Haiku call decides difficulty, then routes
# sub-agents  orchestrator on Sonnet/Opus, workers on Haiku
# ```
#
# A cascade only pays when **most** items pass at the cheap tier. At a 45%
# escalation rate almost half the items pay for two calls — and prompt caches are
# **model-scoped**, so a cascade that bounces between models re-writes its cache
# on every hop instead of reading it.

# %%
# The route table. Note the per-route defaults are REAL request fields — you can
# splat this straight into client.messages.create(...).
ROUTES = {
    "classify": ("claude-haiku-4-5",  {"max_tokens": 10}),
    "extract":  ("claude-haiku-4-5",  {"max_tokens": 800}),
    "answer":   ("claude-sonnet-5",   {"max_tokens": 2000,
                                       "thinking": {"type": "adaptive"},
                                       "output_config": {"effort": "medium"}}),
    "refactor": ("claude-opus-5",     {"max_tokens": 16000,
                                       "thinking": {"type": "adaptive"},
                                       "output_config": {"effort": "xhigh"}}),
}

for task_type, (model_id, defaults) in ROUTES.items():
    print(f"{task_type:9s} -> {model_id:20s} {defaults}")

# %%
# Is a Haiku -> Sonnet cascade cheaper than going straight to Sonnet? That is
# arithmetic, not instinct. Price one attempt on each tier, then price the hop.
CACHE_WRITE = 1.25          # multiplier on the input price for a cache write (lesson 15)
SHARED_PREFIX = 2_500       # tokens of shared instructions and few-shot block
attempt = Usage(input_tokens=3_000, output_tokens=800)

pin_cheap, pout_cheap = MODELS["claude-haiku-4-5"]["price"]
cheap_call = (attempt.input_tokens * pin_cheap + attempt.output_tokens * pout_cheap) / 1_000_000

pin_dear, pout_dear = MODELS["claude-sonnet-5"]["price"]
dear_call = (attempt.input_tokens * pin_dear + attempt.output_tokens * pout_dear) / 1_000_000

# Prompt caches are model-scoped: nothing Haiku cached is readable by Sonnet, so
# every escalated item ALSO pays to write the shared prefix a second time.
prefix_rewrite = SHARED_PREFIX * pin_dear * CACHE_WRITE / 1_000_000

# Every item pays the cheap attempt; the escalated share pays the expensive one too.
for rate in (0.05, 0.20, 0.45):
    cascade = cheap_call + rate * (dear_call + prefix_rewrite)
    verdict = "cascade wins" if cascade < dear_call else "cascade LOSES — go single-tier"
    print(f"escalation {rate:4.0%}  cascade ${cascade:.4f}  vs single ${dear_call:.4f}   {verdict}")

# %% [markdown]
# ### Feature 4 — Feature parity: check before you send
#
# The tiers do not accept the same parameters, and sending the wrong one is a
# `400`, not a graceful degrade.
#
# - **5-family (Opus 5, Sonnet 5)** — `thinking={"type": "adaptive"}` plus
#   `output_config={"effort": …}` with `low|medium|high|xhigh|max`.
# - **Haiku 4.5** — `thinking={"type": "enabled", "budget_tokens": N}`. No `effort`.
# - **Removed on 4.7+/5** — `temperature`, `top_p`, `top_k`, assistant prefill,
#   and `budget_tokens`. Code that still sends them fails outright.
# - **Fast mode** is Opus 5 / 4.8 only, and bills at $10 / $50 per MTok.
#
# So the thinking fields are chosen from the model id, at the moment you build
# the request — the id is the only thing you reliably know at request time.

# %%
# Which models take adaptive thinking + effort. Keyed on the id, because that is
# the only thing you actually know at request time.
ADAPTIVE_THINKING = ("claude-opus-5", "claude-sonnet-5")

# Build one complete, valid request per tier. Same prompt, different thinking
# fields — and every key here is a real messages.create(...) parameter.
for model_id in MODELS:
    if model_id in ADAPTIVE_THINKING:
        # Adaptive thinking is on by default on these; effort tunes the depth.
        thinking_fields = {"thinking": {"type": "adaptive"},
                           "output_config": {"effort": "medium"}}
    else:
        # Haiku 4.5 and older take a fixed budget instead, and reject `effort`.
        thinking_fields = {"thinking": {"type": "enabled", "budget_tokens": 4_000}}

    request = {"model": model_id,
               "max_tokens": 1024,
               "messages": [{"role": "user", "content": "Summarise this ticket."}],
               **thinking_fields}

    # Print everything except the messages, so the tier differences stand out.
    print(f"{model_id:20s} {json.dumps({k: v for k, v in request.items() if k != 'messages'})}")

# %% [markdown]
# ### Feature 5 — Pin the id, gate the change on an eval
#
# The **id string** is what you pin. The **eval** is what makes the pin mean
# something. A pin with no baseline is just a string you are afraid to change.
#
# ```
# MIGRATION RITUAL
# 1. run the eval suite on the CURRENT pin        -> baseline
# 2. run the same suite on the CANDIDATE id       -> comparison
# 3. read the model's migration guide             -> breaking API changes
# 4. shadow / canary, logging model + prompt_version
# 5. flip the pin; keep the old id in config for a one-line rollback
# ```
#
# What "breaking behaviour change" means in practice:
#
# | Change type | Example | What breaks |
# |---|---|---|
# | Removed parameters | `budget_tokens`, `temperature`, prefill → 400 on 4.7+/5 | requests fail outright |
# | Changed defaults | thinking `display` defaults to `"omitted"`; Opus 5 thinks even with `thinking` omitted | UI pauses, token spend moves |
# | Tokenizer changes | Opus 4.7+ counts ~1–1.35× the tokens of 4.6 | prompts near the limit overflow; cost baseline shifts |
# | Behaviour tuning | terser defaults, different tool cadence | eval scores move, length assertions fail |
# | Feature availability | fast mode is Opus-only; effort varies by tier | code assuming a feature 400s elsewhere |
#
# And the lifecycle, which is where the 3 a.m. pages come from:
#
# ```
# active ──► deprecated (announced, still works) ──► retired (404 not_found_error)
#                  └── months of notice + a named successor
# ```
#
# **A 404 on a model id is a configuration fault, not a transient.** Retired
# models do not come back. It is in the never-retry column.

# %%
# Config, not a literal buried in code. One place to bump, one diff to review.
# The dated snapshot is the pin; the comment carries the retirement date so the
# migration lands in the calendar before the pager does.
TRIAGE_MODEL = os.environ.get("TRIAGE_MODEL", "claude-haiku-4-5-20251001")   # retire: see deprecations page

# Fail at startup, not at 3 a.m.: ask the API which ids it actually serves today.
if client is None:
    served = None
    print("[skipped — set ANTHROPIC_API_KEY to check the pins against client.models.list()]")
else:
    served = {m.id for m in client.models.list()}

for pinned in (TRIAGE_MODEL, MODEL):
    if served is None:
        print(f"{pinned}: not checked (offline)")
    else:
        print(f"{pinned}: {'served' if pinned in served else 'NOT SERVED — retired or misspelled'}")

# How the failure looks when you skip the startup check. A NotFoundError on a
# model id means retired: change the config, never add a retry.
NEVER_RETRY = {404: "not_found_error — model retired or misspelled; fix the config"}
TRANSIENT   = {429: "rate limit — back off and retry",
               500: "server error — back off and retry",
               529: "overloaded — back off and retry"}
print()
for code, note in sorted({**NEVER_RETRY, **TRANSIENT}.items()):
    print(f"  {code}: {'RETRY  ' if code in TRANSIENT else 'NO RETRY'}  {note}")

# %% [markdown]
# **Practical use:** start every route on **Sonnet**, then let the eval move it.
# Down to Haiku where Haiku passes; up to Opus only where Sonnet fails. Compare
# models on **cost per correct completed task**, never on price per token — a
# cheap call that needs two retries and a human is the expensive one. Pin the
# exact dated id you measured, keep the previous id in config for rollback, and
# re-run the eval before any model change. A 404 on that id means it was retired:
# that is a config change, and it is the one error you must never retry.
#
# **🏋️ Practice:** finance asks which model to run the *refactor* route on. Per
# request Haiku is the cheapest — but it only passes 40% of the eval on this
# route, so most items get done twice. The line marked `# <-` below divides the
# request price by the pass rate, so the cell passes as shipped and picks Sonnet.
#
# To feel it: delete `/ PASS_RATE[model_id]`, re-run, and watch the assert fail
# with Haiku "winning" on price alone. Put it back. Then change the pass rates to
# your own eval numbers and see where the winner moves.

# %%
# Your turn — see the `# <-` marker.
REQUEST = Usage(input_tokens=3_000, output_tokens=800)

cheapest, cheapest_cost = None, None
for model_id, spec in MODELS.items():
    pin, pout = spec["price"]
    per_request = (REQUEST.input_tokens * pin + REQUEST.output_tokens * pout) / 1_000_000
    per_correct = per_request / PASS_RATE[model_id]      # <- dividing by the pass rate is the fix

    print(f"{model_id:20s} ${per_request:.4f} per request"
          f"   ${per_correct:.4f} per correct task")

    if cheapest_cost is None or per_correct < cheapest_cost:
        cheapest, cheapest_cost = model_id, per_correct

print("\ncheapest per correct task:", cheapest)

assert cheapest == "claude-sonnet-5", f"expected Sonnet to win on this route, got {cheapest}"
print("Sonnet wins — Haiku's 40% pass rate makes the cheap request the dear one")
