# %% [markdown]
# # Topic 4.4 · Software Engineering Foundations: REST, JSON, Async, Git, SDLC, Refactoring
#
# 🎓 *CCDV-F objective 12 of 25 · exam weight 7.4% · API lesson*
#
# **Why this matters.** A Claude application is ordinary software with one
# unusual dependency: a slow, fallible, non-idempotent network call that returns
# text you must not trust as data. Nothing about a model excuses you from the
# engineering. Two things get harder — a retry costs money and may return a
# *different* answer, and "the tests pass" is a weaker signal when a model wrote
# both the code and the tests.
#
# **What you'll learn.**
# - Classify an HTTP failure and decide **retry or fix**, and why `POST /v1/messages` needs your own dedupe key.
# - Tell the three JSONs apart — wire format, tool schema, model output — and validate at the boundary once.
# - Run many calls concurrently with a **bounded semaphore** instead of a 429 storm.
# - Put prompts, schemas and evals under **version control**, and gate them in CI.
# - Review an **AI-written diff** for the things a model gets plausibly wrong.
# - Refactor at both scales: one commit, or a strangler fig behind a flag.
#
# **What you need.** Nothing. The async section fakes the network so it runs
# with no key. Set `ANTHROPIC_API_KEY` to make the one live-guarded cell talk to
# the real API.

# %%
# Load the shared setup: the API client and MODEL.
%run ./00_setup.ipynb

# %% [markdown]
# ### Feature 1 — REST semantics: verbs, status codes, retries, idempotency
#
# Every SDK is a wrapper over HTTPS calls to resource URLs. Knowing the contract
# underneath is what lets you debug when the wrapper is not in the way.
#
# ```
# /v1/messages                 POST   create a completion (NOT idempotent)
# /v1/messages/count_tokens    POST   pure computation, no side effect
# /v1/messages/batches         POST   create a batch job
# /v1/messages/batches/{id}    GET    poll status (safe, idempotent)
# /v1/models                   GET    discover ids and capabilities
# /v1/files                    POST   upload once, reference by id many times
# ```
#
# `GET` is safe and idempotent. `POST /v1/messages` is neither: a retried POST
# **bills a second time** and may return different text. A client timeout does
# not mean it did not happen — the work may have completed and been billed.
# Dedupe is your job.

# %%
# The retry decision, as a table. The distinction between "retry" and "fix the
# request" is the only thing an error handler exists to make.
STATUS_RULES = {
    400: ("invalid_request", "malformed body or an unsupported parameter — fix it"),
    401: ("authentication",  "bad key — a credential problem, not a code problem"),
    403: ("permission",      "the key lacks permission for this resource"),
    404: ("not_found",       "unknown model id — verify with client.models.list()"),
    413: ("request_too_large", "shrink the payload; use the Files API"),
    429: ("rate_limit",      "honour retry-after, lower concurrency"),
    500: ("api_error",       "server side — exponential backoff with jitter"),
    529: ("overloaded",      "server side — back off, do not hammer"),
}

# 408 (timeout), 409 (conflict) and 429 are retryable, and so is every 5xx.
# Every other 4xx is deterministic: retrying it just reproduces the bug, billed.
for code, (name, note) in STATUS_RULES.items():
    retryable = code in (408, 409, 429) or code >= 500
    print(f"{code}  {name:18s} retry={str(retryable):5s}  {note}")

# %%
# Catch a CHAIN, most specific first. One broad `except APIStatusError` erases
# the retryable/non-retryable distinction the handler is for.
#
#   client = anthropic.Anthropic(max_retries=3, timeout=60.0)   # Python: SECONDS
#   try:
#       resp = client.messages.create(model=MODEL, max_tokens=1000, messages=msgs)
#   except anthropic.BadRequestError as e:      # 400 — never retry, fix the request
#       log.error("bad request: %s", e.message); raise
#   except anthropic.RateLimitError as e:       # 429 — SDK already retried max_retries times
#       schedule_retry(after=int(e.response.headers.get("retry-after", "60")))
#   except anthropic.APIStatusError as e:       # everything else that has a status
#       schedule_retry(...) if e.status_code >= 500 else raise
#   except anthropic.APIConnectionError:        # no status at all — network
#       schedule_retry(after=backoff())
#
# The SDK ALREADY retries 408/409/429/5xx with backoff (default 2 retries), so a
# hand-rolled loop multiplies attempts and wall-clock becomes
# timeout x (max_retries + 1).

# POST /v1/messages is not idempotent, so YOU supply the dedupe key. Store the
# result under that key BEFORE acting on it, and a doubled send is free.
LEDGER, BILLED = {}, []

for attempt in range(1, 4):                   # the same request, retried twice
    if "refund-7781" not in LEDGER:
        BILLED.append("charged")              # the thing that costs money
        LEDGER["refund-7781"] = "refund issued"
    print(f"attempt {attempt}:", LEDGER["refund-7781"])

print("times billed:", len(BILLED), "(a naive retry would have billed three times)")

# %% [markdown]
# ### Feature 2 — JSON is a contract, in both directions
#
# JSON appears three times in a Claude application, and confusing them is a
# classic wrong answer:
#
# ```
# 1. THE WIRE FORMAT   the request/response body — the SDK handles it
# 2. THE TOOL SCHEMA   JSON Schema you author; the model's input is validated
#                      against it (strict: true) before it reaches your code
# 3. THE MODEL OUTPUT  text that is SUPPOSED to be JSON — the only one that can
#                      lie to you, and the only one you must validate
# ```
#
# Rules that show up as exam stems:
#
# - **Never string-match a serialized tool input.** Escaping varies. `json.loads()` it.
# - **Deterministic serialization matters for caching.** Unsorted keys, or a dict
#   built from a `set`, change the bytes between processes and silently kill the cache.
# - **A JSON Schema is documentation the model reads.** `description`, `enum`,
#   `required` and `additionalProperties: false` beat pleading in the prompt.
# - **Validate at the trust boundary, once.** Then pass typed objects around.
#   Parsing "defensively" in five places is five places to get it wrong.

# %%
# The boundary validator, written out. Real projects use Pydantic (or
# messages.parse() with output_format=, which constrains AND validates); the
# checks are the same ones.
TRIAGE_SCHEMA = {
    "category": {"type": str, "enum": {"billing", "shipping", "technical"}},
    "severity": {"type": int, "min": 1, "max": 5},
    "needs_human": {"type": bool},
}

SAMPLES = [
    '{"category": "billing", "severity": 3, "needs_human": false}',
    '{"category": "refunds", "severity": 9, "needs_human": false}',
    'Sure! Here is the JSON: {"category": "billing"}',
]

for raw in SAMPLES:
    errors, data = [], None
    try:
        data = json.loads(raw)               # never a regex, never string matching
    except json.JSONDecodeError as err:
        errors.append(f"not JSON: {err.msg}")
    if data is not None and not isinstance(data, dict):
        errors.append("top level is not an object")
        data = None

    if data is not None:
        for field, rule in TRIAGE_SCHEMA.items():
            if field not in data:
                errors.append(f"missing field {field!r}")
                continue
            value = data[field]
            # bool is a subclass of int in Python, so check it before the int rule.
            if rule["type"] is int and isinstance(value, bool):
                errors.append(f"{field} must be an integer, got a bool")
            elif not isinstance(value, rule["type"]):
                errors.append(f"{field} must be {rule['type'].__name__}")
            elif "enum" in rule and value not in rule["enum"]:
                errors.append(f"{field}={value!r} is not one of {sorted(rule['enum'])}")
            elif "min" in rule and not (rule["min"] <= value <= rule["max"]):
                errors.append(f"{field}={value} is outside {rule['min']}-{rule['max']}")

    print(("OK  " if not errors else "BAD "), raw[:52])
    for err in errors:
        print("      -", err)

# %%
# Deterministic serialization is not a style preference — it decides whether a
# cached prefix matches. Any byte change invalidates it, silently.
tool = {"name": "create_refund", "description": "Issue a refund.", "strict": True}

as_written = json.dumps(tool)                      # insertion order — whatever the code did
as_canonical = json.dumps(tool, sort_keys=True)    # one byte string, in every process
print("as written  :", as_written)
print("as canonical:", as_canonical)
print("same bytes? :", as_written == as_canonical, " <- a cached prefix would miss")

# A tool LIST built from a set has no order at all. Sort it before sending.
tool_names = {"search_kb", "get_order_status", "create_refund"}
print("\nfrom a set  :", list(tool_names), "<- not guaranteed between processes")
print("sorted      :", sorted(tool_names), "<- identical in every process")

# The preferred shape when your code must consume the answer — the API enforces
# the schema instead of you hoping for it:
#   resp = client.messages.parse(model=MODEL, max_tokens=1024,
#                                output_format=Triage, messages=msgs)
#   triage = resp.parsed_output          # already a validated Triage instance
if client is None:
    print("\n[skipped — set ANTHROPIC_API_KEY to try messages.parse() for real]")
else:
    print("\nclient is live — try messages.parse() with a Pydantic output_format")

# %% [markdown]
# ### Feature 3 — Asynchronous programming without a 429 storm
#
# Model calls are I/O-bound and slow — seconds, not milliseconds. Concurrency is
# the difference between a job finishing in a minute and in an hour. The failure
# mode is always the same: unbounded fan-out.
#
# ```
# gather(*10_000 coroutines)  -> 10,000 sockets at once
#                             -> 429s on nearly all of them
#                             -> the SDK retries all of them at once
#                             -> thundering herd, plus memory
# with Semaphore(N)           -> N in flight, steady state, retries spread out;
#                                N tuned from the anthropic-ratelimit-* headers
# ```
#
# Two more rules: `return_exceptions=True`, or one bad item cancels the whole
# gather; and a dead-letter list, so failures are visible instead of lost.
#
# | | Async (`asyncio`) | Threads | Multiprocessing |
# |---|---|---|---|
# | Good for | I/O-bound: API calls | I/O-bound in sync codebases | CPU-bound work |
# | Cost per unit | a coroutine — cheap | an OS thread — heavier | a process — heaviest |
# | For Claude calls | the default choice | fine, simpler to retrofit | wrong tool |
#
# Async does not make one call faster — it makes many calls **overlap**. For a
# chain of *dependent* calls it buys nothing; the lever there is a better prompt,
# a smaller model, or fewer turns. And async is not Batch: async is realtime
# concurrency you manage, Batch is an offline job Anthropic manages at half price.

# %%
import asyncio
import concurrent.futures

# `await` exists only inside a coroutine, so these two `async def`s stay: the
# coroutine IS the thing being taught. Everything else is written out flat.
IN_FLIGHT = {"now": 0, "peak": 0}          # peak is the number the semaphore controls

async def call_claude(text, sem):
    """One request, standing in for `await client.messages.create(...)`."""
    async with sem:                        # nothing runs outside the gate
        IN_FLIGHT["now"] += 1
        IN_FLIGHT["peak"] = max(IN_FLIGHT["peak"], IN_FLIGHT["now"])
        try:
            await asyncio.sleep(0.01)      # stands in for network latency
            if "boom" in text:
                raise RuntimeError(f"429 rate_limit on {text!r}")
            return text.upper()
        finally:
            IN_FLIGHT["now"] -= 1

async def fan_out(items, limit):
    """Bounded fan-out: size `limit` from your rate limit, never from len(items)."""
    sem = asyncio.Semaphore(limit)                     # the gate
    return await asyncio.gather(*(call_claude(t, sem) for t in items),
                                return_exceptions=True)   # one failure != lose the batch

WORK = [f"ticket-{i}" for i in range(20)] + ["boom-1"]

# A notebook kernel is ALREADY running an event loop, so asyncio.run() would
# raise here. Run it on a private loop in a worker thread instead.
with concurrent.futures.ThreadPoolExecutor(1) as pool:
    settled = pool.submit(asyncio.run, fan_out(WORK, 5)).result()

ok = [r for r in settled if not isinstance(r, Exception)]
dead = [str(r) for r in settled if isinstance(r, Exception)]   # somewhere for failures to go
print(f"succeeded : {len(ok)}")
print(f"dead-letter: {dead}")
print(f"peak in flight: {IN_FLIGHT['peak']}  (semaphore limit was 5, "
      f"work list was {len(WORK)})")

# %% [markdown]
# ### Feature 4 — Version control, SDLC integration, reviewing AI-written code
#
# An LLM application's "source" is larger than its `.py` files. Anything that
# changes behaviour belongs in the repo and in the diff.
#
# ```
# COMMITTED (behaviour lives here)     NEVER COMMITTED
# prompt files, versioned              .env, API keys, tokens
# tool definitions / JSON schemas      CLAUDE.local.md, settings.local.json
# eval sets and expected outputs       local state, caches, __pycache__
# CLAUDE.md, .claude/settings.json     raw customer data in fixtures
# the pinned model id                  resolved secret values
# lockfile pinning the SDK version
# ```
#
# **Unit tests mock the API** — they prove your parsing, retry and dispatch
# logic. **Evals call the real model** — they prove the prompt still works,
# pinned to an exact model id so a score change means a *prompt* change. A
# pipeline with only the first is blind to prompt regressions; one with only the
# second is slow, expensive and flaky.
#
# ```yaml
# # .github/workflows/ci.yml
# - run: ruff check . && mypy .        # static: cheap, deterministic, first
# - run: pytest -q                     # unit: mock the API, test YOUR code
# - run: pytest tests/contract -q      # schema tests against recorded responses
# - run: python evals/run.py --suite regression --min-score 0.92   # the LLM gate
# ```

# %%
# Does this file belong in git? Prompts and eval sets change behaviour, so they
# are source. Secrets and personal overrides never enter a shared diff.
NEVER_COMMIT = (".env", "settings.local.json", "CLAUDE.local.md", ".pem")

for path in ["prompts/triage_v4.md", "tools/refund_schema.json", "evals/regression.json",
             ".claude/settings.json", ".claude/settings.local.json", ".env",
             "__pycache__/app.pyc"]:
    if any(path.endswith(marker) or marker in path for marker in NEVER_COMMIT):
        verdict = "ignore  (secret or personal — never in a shared diff)"
    elif path.endswith((".md", ".json", ".yaml", ".py")):
        verdict = "commit  (it changes behaviour, so it is source)"
    else:
        verdict = "ignore  (build output or local state)"
    print(f"{path:34s} {verdict}")

# %%
# Reviewing a diff a model wrote. The standard is the same; the weighting moves
# towards the things a model gets *plausibly* wrong.
AGENT_DIFF = '''
def fetch(ids):
    try:
        return client.messages.create(model="claude-sonnet-5", max_tokens=100)
    except Exception:
        return []
'''
lines_changed = 700
tests_by_same_author = True

PATTERN_CHECKS = [
    (r"except\s+Exception\s*:\s*(?:pass|return\s+\[\]|return\s+None)",
     "swallowed exception — failures become silent empty results"),
    (r"except\s+Exception\b",
     "over-broad except — retryable and permanent errors are now the same"),
    (r"model\s*=\s*[\"'][^\"']+[\"']",
     "hard-coded model id in code — it belongs in config, pinned once"),
]

findings = [message for pattern, message in PATTERN_CHECKS
            if re.search(pattern, AGENT_DIFF)]
if lines_changed > 400:
    findings.append(f"{lines_changed}-line diff — split it; small diffs are a review technique")
if tests_by_same_author:
    findings.append("tests written by the author of the code are not independent evidence "
                    "— read what each test ASSERTS")

for finding in findings:
    print("-", finding)

# %% [markdown]
# ### Feature 5 — Refactoring: small steps and large migrations
#
# Refactoring means changing structure **without changing behaviour**. That
# definition is the whole test: if behaviour changes, it is a rewrite and needs a
# different plan.
#
# ```
# SMALL-SCALE                       LARGE-SCALE
# rename, extract function,         extract a service, replace a data model,
# inline a variable, dedupe         migrate a framework, split a monolith
#
# safety net: existing tests        safety net: characterization tests written
# scope: one file, one commit       FIRST, against current behaviour, bugs included
# rollback: revert the commit       strategy: strangler fig — new path beside old,
#                                   traffic moved a slice at a time
#                                   rollback: a feature flag, not a revert
# ```
#
# Two adjacent traps worth naming. Refactoring **and** adding a feature in one
# commit — now a revert costs you the feature. And refactoring code with no tests
# at all: that is not refactoring, that is editing and hoping.

# %%
# The strangler fig, made concrete: both implementations live, and the FLAG —
# not a deploy — decides who sees the new one.
import hashlib

DOC = "quarterly revenue report"
users = [f"user-{i}" for i in range(100)]

for rollout_pct in (0, 10, 50, 100):
    on_v2, divergences = 0, 0
    for user_id in users:
        # A stable 0-99 slice. Python's built-in hash() is salted per process, so
        # it would move people between arms and make the comparison meaningless.
        slice_of_100 = int(hashlib.md5(user_id.encode()).hexdigest(), 16) % 100

        v1 = f"v1: {DOC[:20]}"          # the incumbent, pinned by characterization tests
        v2 = f"v2: {DOC[:20]}"          # the replacement: new prompt, same contract

        if slice_of_100 < rollout_pct:
            served = v2
            on_v2 += 1
        else:
            # Everyone still on v1 shadow-runs v2: log the divergence, serve v1.
            served = v1
            if v2 != v1:
                divergences += 1

    print(f"rollout {rollout_pct:3d}%  -> {on_v2:3d}/100 users on v2, "
          f"{divergences:3d} divergences logged from the shadow run")

print("\nrollback is a flag flip, not a revert — v1 never left")

# %% [markdown]
# **Practical use:** the model does not change the engineering, it changes the
# weighting. Retry only `429`, `5xx` and connection failures — a `400` retried
# five times just reproduces itself. Keep your own dedupe key, because
# `POST /v1/messages` is not idempotent and a timeout may still have billed.
# Validate model output once at the boundary with a schema, never a regex. Bound
# every fan-out with a semaphore. Commit the prompts and the evals, because they
# change behaviour. And when a refactor is large, characterize first and move
# traffic behind a flag.
#
# **🏋️ Practice:** the retry loop that made it worse. A pipeline wraps every call
# in `for attempt in range(5)` and retries on *any* exception, so a deterministic
# `400` is billed five times and a `429` retries in lockstep with every other
# worker.
#
# The fix is marked `# <-` below and is already in place, so the cell passes as
# shipped. To feel it: change that line to `retryable = True`, re-run, and watch
# the assert fail as the 400 gets retried. Put it back. Then add jitter to the
# backoff and check that two workers no longer wait the same amount.

# %%
# Your turn — see the `# <-` marker.
CASES = [
    ("permanent 400", [400, 400, 400, 400, 400], 1),   # fix the request, do not retry
    ("429 then ok",   [429, 429, 200],           3),   # transient — retry pays off
    ("500 then ok",   [500, 200],                2),
]
MAX_ATTEMPTS = 5
ATTEMPTS = {}

for label, status_sequence, expected in CASES:
    attempts, waited, status = 0, 0, None
    for status in status_sequence[:MAX_ATTEMPTS]:
        attempts += 1
        if status == 200:
            break
        retryable = status in (408, 409, 429) or status >= 500   # <- permanent errors stop here
        if not retryable:
            break
        # Exponential backoff. Real code adds jitter to this number, or every
        # worker in the fleet retries in lockstep and the herd re-arrives together.
        waited += 2 ** attempts

    ATTEMPTS[label] = attempts
    print(f"{label:14s} attempts={attempts} (want {expected})  final={status}  waited={waited}s")

assert ATTEMPTS["permanent 400"] == 1, "a 400 was retried — that bills to reproduce a bug"
assert ATTEMPTS["429 then ok"] == 3, "a 429 should be retried until it succeeds"
print("\nretryable and permanent errors are handled differently")
