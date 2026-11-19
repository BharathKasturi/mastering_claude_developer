# %% [markdown]
# # Topic 4.2 · Systems Life Cycle for Claude Applications
#
# 🎓 *CCDV-F objective 10 of 25 · exam weight 2.8% · API lesson*
#
# **Why this matters.** A Claude application has the same life cycle as any other
# IT system — plan, build, evaluate, deploy, operate, maintain — with one twist:
# **the model is a dependency you do not control, and it changes underneath you.**
# Treat it like any third-party service with a published deprecation schedule, and
# every boring practice you already know applies. The exam's failure stories are
# always "we skipped a phase because it's AI".
#
# **What you'll learn.**
# - Name each phase's ordinary duty and the **model-shaped duty** it gains.
# - **Pin the pair** — model *and* prompt — and detect alias drift.
# - Build the **eval gate**: dataset, grader, threshold, non-zero exit.
# - Roll out **staged and reversible**: canary buckets, rollback as a config flip.
# - Operate: the metrics worth alerting on, and the **runbook order**.
# - Maintain: inventory every pin before a **deprecation** date.
#
# **What you need.** Nothing. The eval runs against canned outputs when there is
# no key, and every other cell is local. Set `ANTHROPIC_API_KEY` to score the eval
# against the real model.

# %%
# Load the shared setup: the API client and MODEL.
%run ./00_setup.ipynb

# %% [markdown]
# ### Feature 1 — Six phases, each with a model-shaped duty
#
# | Phase | Ordinary SDLC duty | What changes for a Claude app |
# |---|---|---|
# | Plan | requirements, architecture, budget | eval targets, a **pinned** model id, a token budget per request |
# | Build | code, tests, review | prompts/tools/harness in git; prompt versioning; guardrails as code |
# | Evaluate | unit + integration tests | **eval dataset + graders** on every prompt *or* model change; a CI gate |
# | Deploy | environments, CI/CD, flags | dev → staging → canary % → full; the pair behind a flag; instant rollback |
# | Operate | monitoring, alerting, on-call | latency, cost/token, error and refusal rates, cache hit rate; runbooks |
# | Maintain | patching, dependency upgrades | **model deprecations** tracked like CVEs; migrate on a schedule, behind evals |
#
# **Decision rule.** Every change to prompt, model, tool set or harness takes the
# same path: *evaluate → staged deploy → observe, with rollback ready.* A change
# that skips a phase is the bug the stem is describing.

# %%
# The exam tells. Each story names the phase whose duty was skipped.
PHASE_TELLS = [
    ("this model will be retired in 90 days",            "maintain"),
    ("quality dropped after we changed the model id",    "evaluate"),
    ("how do we release the new prompt safely?",         "deploy"),
    ("production is misbehaving right now",              "operate"),
    ("what does 'good enough' mean for this feature?",   "plan"),
    ("the prompt was edited in a console, not in git",   "build"),
]

for story, expected in PHASE_TELLS:
    words = story.lower()

    if any(w in words for w in ("retired", "deprecat", "end-of-life", "sdk upgrade")):
        phase = "maintain"
    elif any(w in words for w in ("right now", "misbehaving", "alert", "incident", "outage")):
        phase = "operate"
    elif any(w in words for w in ("quality dropped", "regression", "changed the model")):
        phase = "evaluate"
    elif any(w in words for w in ("release", "roll out", "ship", "canary")):
        phase = "deploy"
    elif any(w in words for w in ("git", "version control", "review", "prompt file")):
        phase = "build"
    else:
        phase = "plan"

    print(f"{phase:9s} (want {expected:9s})  <- {story}")

# %% [markdown]
# ### Feature 2 — Pin the pair, and catch alias drift
#
# ```
#    repo/
#    ├── prompts/triage_v7.md      ← prompt text is code: reviewed, diffed, tagged
#    ├── evals/triage_cases.jsonl  ← held-out inputs + expected labels
#    ├── config/models.yaml        ← model ids PINNED per environment
#    └── app/hooks/                ← guardrails as code
# ```
#
# An **alias** that silently resolves to a newer snapshot is an unreviewed change
# to production. Prompt and model move together as a **pair**, because an eval
# result is only valid for the pair it was run on.
#
# Two checks worth having in CI: no aliases in the production pin, and the model
# id actually served matches the id you pinned.

# %%
# CONFIG stands in for the repo's config/models.yaml. One key per environment,
# each pinning a (model, prompt) PAIR. Promotion means changing a pin; rollback
# means changing it back. Nothing is rebuilt.
CONFIG = {
    "dev":        {"model": "claude-sonnet-5",   "prompt": "triage_v8"},
    "staging":    {"model": "claude-sonnet-5",   "prompt": "triage_v8"},
    "production": {"model": "claude-sonnet-4-6", "prompt": "triage_v7"},
}

for env, row in CONFIG.items():
    print(f"{env:11s} {row['model']:20s} + {row['prompt']}")

# `re` came from the setup cell. A dated snapshot ends in a version number; an
# alias like "claude-sonnet-latest" does not. Alias drift is a production change
# nobody reviewed, so audit a clean config and a drifting one side by side.
ALIAS = re.compile(r"latest|^claude-[a-z]+$", re.IGNORECASE)

drifting = dict(CONFIG, production={"model": "claude-sonnet-latest", "prompt": "triage_v7"})

print()
for label, config in [("clean config", CONFIG), ("drifting one", drifting)]:
    problems = []
    for env, row in config.items():
        if ALIAS.search(row["model"]):
            problems.append(f"{env}: {row['model']} is an alias, not a pin")
        if not row.get("prompt"):
            problems.append(f"{env}: model pinned but prompt version missing")
    print(f"{label} : {problems or 'no problems'}")

# The operate dashboard's quietest and most useful row: compare the model id the
# API actually reported on the response against the id production is pinned to.
pinned_model = CONFIG["production"]["model"]

print()
for served in ["claude-sonnet-4-6", "claude-sonnet-5"]:
    if served == pinned_model:
        print(f" ok — pinned {pinned_model}, served {served}")
    else:
        print(f" DRIFT: pinned {pinned_model}, served {served}")

# %% [markdown]
# ### Feature 3 — Evaluate: the gate between build and deploy
#
# The minimum is four things: a **dataset**, a **grader**, a **threshold**, and a
# **non-zero exit** so CI can fail the pull request.
#
# Three properties the exam checks:
#
# - **Held-out** — cases the prompt was not tuned on, or the score is memorisation.
# - **Comparable** — same dataset, same grader, every run. Only then is
#   *v7-on-4.6* versus *v8-on-5* a real comparison.
# - **Gated** — the result blocks the merge. An eval run "sometimes, by hand" is
#   not part of the life cycle.
#
# For open-ended output the exact-match grader becomes a rubric graded by a model,
# calibrated against a small human-graded sample. The *shape* does not change.

# %%
# A held-out dataset. In a repo this is evals/triage_cases.jsonl, one JSON per line.
EVAL_CASES = [
    {"input": "my card was charged twice", "expected": "billing"},
    {"input": "the parcel never arrived", "expected": "shipping"},
    {"input": "how do I change my password", "expected": "account"},
    {"input": "refund for a duplicate payment", "expected": "billing"},
    {"input": "tracking number is wrong", "expected": "shipping"},
]
SYSTEM = "Reply with exactly one label: billing, shipping or account."

# Canned answers so the gate runs offline. The third one is deliberately wrong,
# so you can watch a threshold actually bite.
OFFLINE_ANSWERS = ["billing", "shipping", "billing", "billing", "shipping"]

# Score the PAIR production is pinned to: its model id, and the prompt version.
# Both halves go into the run, and into the result.
prod_model = CONFIG["production"]["model"]
prod_prompt_version = CONFIG["production"]["prompt"]
threshold = 0.92
hits = 0

for i, case in enumerate(EVAL_CASES):
    if client is None:
        answer = OFFLINE_ANSWERS[i]              # clearly-labelled stand-in
    else:
        reply = client.messages.create(
            model=prod_model,
            max_tokens=20,
            system=SYSTEM,
            messages=[{"role": "user", "content": case["input"]}],
        )
        answer = reply.content[0].text
    hits += answer.strip().lower() == case["expected"]      # exact-match grader

accuracy = hits / len(EVAL_CASES)
passed = accuracy >= threshold
label = "live" if client else "offline stand-in answers"

print(f"accuracy={accuracy:.3f}  gate={'PASS' if passed else 'FAIL'}  ({label})")
print("in CI this is: sys.exit(0 if passed else 1) — the PR does not merge on FAIL")

# The point of pinning the PAIR: the score belongs to both halves, never one.
print(f"\nthis score is valid ONLY for {prod_model} + {prod_prompt_version}")

# %% [markdown]
# ### Feature 4 — Deploy: staged, flagged, reversible
#
# ```
#    dev ──► staging ──► canary 5% ──► 25% ──► 100%
#                           │           │
#              evals pass   │  live metrics: p95, cost/req, error %, refusal %,
#              on staging   │  sampled eval score ≥ baseline
#                           │           │
#                           └───────────┴──► any regression ──► ROLLBACK (flip the pin)
# ```
#
# Three rules:
#
# - **Roll out the pair, not the model.** Changing the model under a prompt tuned
#   for the old one is the "quality dropped overnight" story.
# - **Rollback is config, not a redeploy.** If reverting means a 40-minute
#   pipeline, you do not have rollback.
# - **Canary metrics include quality** — a sampled eval, the refusal rate, the
#   `stop_reason` mix, the output-length distribution — not just latency and 5xx.

# %%
# Bucket users into stable or canary. Python's built-in hash() is randomised per
# process, so use a stable digest — you need the SAME user in the SAME bucket
# across every process and every restart.
import hashlib

PAIRS = {"stable": ("claude-sonnet-4-6", "prompts/triage_v7.md"),
         "canary": ("claude-sonnet-5",   "prompts/triage_v8.md")}
CANARY_PCT = 5                                   # from config, so it can be turned up

on_canary = 0
for i in range(1000):
    digest = hashlib.md5(f"user_{i}".encode()).hexdigest()
    if int(digest, 16) % 100 < CANARY_PCT:
        on_canary += 1

print(f"{on_canary}/1000 users on the canary pair at {CANARY_PCT}%  -> {PAIRS['canary']}")

# A canary is only readable if a user stays in one bucket. Hash the same id twice
# and the answer must not move.
first = int(hashlib.md5(b"user_7").hexdigest(), 16) % 100 < CANARY_PCT
again = int(hashlib.md5(b"user_7").hexdigest(), 16) % 100 < CANARY_PCT
print(f"user_7 is stable across restarts: "
      f"{'canary' if first else 'stable'} == {'canary' if again else 'stable'}")

# Rollback: one config change, no build. Widening: the same change, other way.
print("\nrollback  -> CANARY_PCT = 0        (the stable pair is already loaded)")
print("widen     -> CANARY_PCT = 25       (after the canary metrics hold)")

# %%
# Canary metrics must include quality, or you ship a fast, cheap regression.
CANARY_GATES = {"p95_ms": ("<=", 2500), "cost_per_req": ("<=", 0.021),
                "error_pct": ("<=", 1.0), "refusal_pct": ("<=", 2.0),
                "sampled_eval": (">=", 0.92)}

healthy = {"p95_ms": 1800, "cost_per_req": 0.018, "error_pct": 0.3,
           "refusal_pct": 1.1, "sampled_eval": 0.94}
regressed = dict(healthy, sampled_eval=0.88, refusal_pct=4.0)

# "Any regression rolls back" is a decision code can take, not a judgement call.
for name, metrics in [("healthy  ", healthy), ("regressed", regressed)]:
    failures = []
    for gate, (op, limit) in CANARY_GATES.items():
        value = metrics[gate]
        if (op == "<=" and value > limit) or (op == ">=" and value < limit):
            failures.append(f"{gate}={value} breaks {op} {limit}")
    print(f"{name}->", failures if failures else "widen to 25%",
          "-> ROLLBACK" if failures else "")

# %% [markdown]
# ### Feature 5 — Operate: what to watch, and the runbook order
#
# ```
#    DASHBOARD                         ALERT WHEN
#    ───────────────────────────────   ────────────────────────────────────────
#    latency p50 / p95 (TTFT + total)  p95 over the SLO for 5 minutes
#    cost per request, tokens in/out   +30% day-over-day (prompt bloat? cache miss?)
#    cache_read_input_tokens ratio     drops toward 0 (a silent cache invalidator)
#    HTTP 429 / 5xx / timeout rate     sustained; check retry-after and backoff
#    stop_reason distribution          max_tokens ↑ (truncation), refusal ↑
#    sampled eval score on live data   below the deploy baseline
#    model id actually served          ≠ the pinned id (alias drift)
# ```
#
# The runbook, in the order the exam expects:
#
# ```
#    1. DETECT      capture request ids, model id, prompt version
#    2. MITIGATE    flip the pair back to stable; route to the fallback endpoint
#    3. COMMUNICATE status to stakeholders; note what users saw
#    4. ROOT-CAUSE  with the traces: integration bug? model change? bad deploy?
#    5. FIX + EVAL  the fix goes through the same gate as any change
#    6. POST-MORTEM add the missing alert or eval case
# ```
#
# **Mitigate comes before root-cause.** A team debugging for two hours while
# production is degraded is the stem describing the wrong order.

# %%
# The alerts that catch the classic "cost doubled and nobody changed anything".
# The `usage` field names here are the real ones the API returns.
monday = {"cost_per_req": 0.010, "input_tokens": 100_000,
          "cache_read_input_tokens": 82_000, "p95_ms": 1800, "slo_ms": 2500}
tuesday = {"cost_per_req": 0.021, "input_tokens": 100_000,
           "cache_read_input_tokens": 0, "p95_ms": 1900, "slo_ms": 2500}

if tuesday["cost_per_req"] > monday["cost_per_req"] * 1.30:
    print("ALERT COST: cost/req +30% day-over-day")

# The cache read ratio going to zero is the earliest signal of the classic bug:
# something changed above the cache breakpoint, so nothing hits.
today_ratio = tuesday["cache_read_input_tokens"] / max(tuesday["input_tokens"], 1)
yesterday_ratio = monday["cache_read_input_tokens"] / max(monday["input_tokens"], 1)
if today_ratio < 0.10 <= yesterday_ratio:
    print(f"ALERT CACHE: cache_read ratio fell to {today_ratio:.2f}")

if tuesday["p95_ms"] > tuesday["slo_ms"]:
    print(f"ALERT LATENCY: p95 {tuesday['p95_ms']}ms over SLO {tuesday['slo_ms']}ms")

print("\ncause: a deploy put a timestamp ABOVE the cache breakpoint. "
      "'nobody changed anything' is false — a deploy is a change.")

# The runbook, as an ordered list you can rehearse. Order is the exam answer.
RUNBOOK = ["detect", "mitigate", "communicate", "root_cause", "fix_and_eval", "post_mortem"]
print("\nrunbook:", " -> ".join(RUNBOOK))
print(f"step 2 is {RUNBOOK[1]!r}, not {RUNBOOK[3]!r} — restore service, then explain it")

# %% [markdown]
# ### Feature 6 — Maintain: deprecation is a deadline you did not choose
#
# ```
#    deprecation announced ──► retirement date
#           │
#           ├── inventory: which environments pin the retiring id?
#           ├── candidate: run the FULL eval on the successor, current prompts
#           ├── adjust:    prompts often need changes — behaviour shifts per release
#           ├── stage:     dev → staging → canary with the new pair
#           └── cut over:  before the date, with rollback to… nothing.
#                          The old model is gone, so the canary phase is the
#                          only safety net. Do not leave it to the last week.
# ```
#
# The shell one-liner that starts every migration:
#
# ```bash
# grep -rnE "claude-[a-z]+-[0-9]" --include='*.py' --include='*.yaml' --include='*.json' .
# ```
#
# The same discipline covers SDK major versions, tool schema changes and MCP
# server upgrades. *Inventory → eval → stage → cut over.* "It is a dependency" is
# the whole lesson.

# %%
# Step one of a migration, and the step teams skip: inventory every pin in the
# repo. Same regex as the grep above, run over sample file contents.
REPO = {
    "config/models.yaml": "production: {model: claude-sonnet-4-6, prompt: triage_v7}",
    "app/summariser.py":  'MODEL = "claude-sonnet-4-6"',
    "app/classifier.py":  'MODEL = "claude-haiku-4-5"',
    "tests/conftest.py":  'FAKE_MODEL = "claude-sonnet-4-6"',
}
PIN_RE = re.compile(r"claude-[a-z]+-[0-9][0-9a-z-]*")
RETIRING = "claude-sonnet-4-6"

hits = [path for path, body in REPO.items() if RETIRING in PIN_RE.findall(body)]
print(f"{RETIRING} is pinned in {len(hits)} file(s):")
for path in hits:
    print("  ", path)

# Turn the retirement date into a schedule, or a warning. Rough but defensible: a
# day per prompt to eval and fix, plus two weeks of staging and canary that
# cannot be compressed. This is why "the last sprint" does not fit.
prompts_to_eval = 14
needed = prompts_to_eval + 14

print()
for days_left in [90, 21]:
    if days_left < needed:
        plan = f"TOO LATE: needs ~{needed} days, {days_left} left — start now, cut scope"
    else:
        plan = (f"fits: {prompts_to_eval} days of eval+fix, 14 days staging/canary, "
                f"{days_left - needed} days of slack")
    print(f"{days_left} days, {prompts_to_eval} prompts -> {plan}")

# %% [markdown]
# **Practical use:** treat the model as a third-party dependency with a
# deprecation schedule and non-deterministic output, and every ordinary practice
# carries over. **Pin the pair**, evaluate the pair, deploy the pair, roll back
# the pair. Keep rollback to a config flip. When production is degraded, mitigate
# first and root-cause second. And start a model migration on the announcement,
# not in the sprint before the date — once the old model is gone, the canary phase
# is your only safety net.
#
# **🏋️ Practice:** the incident runbook must put **mitigate** before
# **root_cause**, and the checker must catch a runbook that gets it backwards. The
# fix is marked `# <-` below and is already in place, so the cell passes as
# shipped.
#
# To feel it: delete the `mitigate before root_cause` rule from `RULES`, re-run,
# and watch a two-hours-of-debugging-while-production-burns runbook be approved.
# Put it back. Then add a rule of your own — say, that `post_mortem` must be last.

# %%
# Your turn — see the `# <-` marker.
RULES = [
    ("detect", "mitigate"),          # you cannot mitigate what you have not detected
    ("mitigate", "root_cause"),      # <- restore service BEFORE explaining it
    ("root_cause", "fix_and_eval"),  # the fix goes through the same gate
]

GOOD = ["detect", "mitigate", "communicate", "root_cause", "fix_and_eval", "post_mortem"]
BACKWARDS = ["detect", "root_cause", "mitigate", "fix_and_eval", "post_mortem"]
INCOMPLETE = ["detect", "root_cause", "fix_and_eval"]

# Check each runbook's ordering. A step that is missing entirely cannot satisfy
# its rule either, so an incomplete runbook is rejected too.
verdicts = {}
for name, steps in [("good", GOOD), ("backwards", BACKWARDS), ("incomplete", INCOMPLETE)]:
    position = {step: i for i, step in enumerate(steps)}
    ok = all(earlier in position and later in position
             and position[earlier] < position[later]
             for earlier, later in RULES)
    verdicts[name] = ok
    print(f"{name:11s} {'OK' if ok else 'REJECTED'}  {' -> '.join(steps)}")

assert verdicts == {"good": True, "backwards": False, "incomplete": False}, \
    "the runbook checker no longer enforces mitigate-before-root_cause"
print("\nthe checker enforces: restore service first, explain second")
