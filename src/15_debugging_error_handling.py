# %% [markdown]
# # Topic 5.1 · Debugging & Error Handling: Error Types, Recovery, Trace Analysis, Origin Isolation
#
# 🎓 *CCDV-F objective 15 of 25 · exam weight 2.6% · API lesson*
#
# **Why this matters.** A Claude application fails in three different places — the
# HTTP layer, the model's output, or your own code around both — and each place has
# a different fix. The expensive bug is always the same shape: a `try/except` that
# retries everything, so a 400 gets sent ten more times and a truncated answer gets
# treated as a network blip. Naming the layer first is the whole skill.
#
# **What you'll learn.**
# - Name the **three layers** a Claude app fails in, and the one question that separates them.
# - Map **status code → error type**, and know which are worth retrying.
# - Write **backoff with jitter**, honour `retry-after`, and open a **circuit breaker**.
# - Handle the 200s that still failed: `stop_reason` of `max_tokens` or `refusal`,
#   and bad tool input answered with a **feedback `tool_result`**.
# - Read a **trace** for the *first* anomaly instead of the loudest one.
# - **Isolate the origin** — integration layer or model output — by replay and a small eval.
#
# **What you need.** Nothing. Every cell runs offline against scripted failures.
# Set `ANTHROPIC_API_KEY` to make the last section replay a request against the
# real API instead of a recorded one.

# %%
# Load the shared setup: the API client and MODEL.
%run ./00_setup.ipynb

# %% [markdown]
# ### Feature 1 — Three layers, one question
#
# > **Retry what is transient, fix what is invalid, re-prompt what is wrong — and
# > never confuse the three.**
#
# The question that sorts them: **did the HTTP call succeed?**
#
# - **No, and the status is transient** (429, 500, 529, timeout) → back off and retry.
#   Nothing is wrong with your request.
# - **No, and the status is not transient** (400, 401, 403, 404, 413) → fix the
#   request. Sending the same bytes again fails the same way.
# - **Yes — HTTP 200 — but the answer is wrong.** The transport worked. This is a
#   `stop_reason`, schema, or prompt problem, and **retrying a 200 is never the fix**.

# %%
# The SDK exception classes, by status. The import is guarded so this lesson still
# runs with the SDK uninstalled; when it IS installed we print the real class
# names, because those are what you catch in production code.
try:
    import anthropic
    sdk_classes = {
        400: anthropic.BadRequestError,
        401: anthropic.AuthenticationError,
        403: anthropic.PermissionDeniedError,
        404: anthropic.NotFoundError,
        429: anthropic.RateLimitError,
        500: anthropic.InternalServerError,
        529: anthropic.InternalServerError,
    }
except ImportError:
    anthropic, sdk_classes = None, {}

# The taxonomy as data: status -> (error type, layer, retry?). These are the
# eight pairings the exam draws from.
ERROR_TABLE = {
    400: ("invalid_request_error", "your request",    False),
    401: ("authentication_error",  "credentials",     False),
    403: ("permission_error",      "credentials",     False),
    404: ("not_found_error",       "config",          False),  # usually a wrong model ID
    413: ("request_too_large",     "your request",    False),
    429: ("rate_limit_error",      "transport",       True),
    500: ("api_error",             "transport",       True),
    529: ("overloaded_error",      "transport",       True),
}

# Read each status once: what it is called, which layer owns it, whether a retry
# could ever help, and the exception class your `except` clause should name.
for status in sorted(ERROR_TABLE):
    error_type, layer, retry = ERROR_TABLE[status]
    if status in sdk_classes:
        catch = f"anthropic.{sdk_classes[status].__name__}"
    elif anthropic:
        catch = "anthropic.APIStatusError"
    else:
        catch = "(anthropic SDK not installed)"

    print(f"{status}  {'retry' if retry else 'FIX '}  "
          f"{error_type:22s} {layer:14s} {catch}")

print("\nEvery error body carries a request_id. Log it — support asks for it.")

# %% [markdown]
# ### Feature 2 — Backoff with jitter, `retry-after`, and a circuit breaker
#
# Three rules, and each one exists because the naive version made an incident worse:
#
# - **Exponential backoff** — double the wait each attempt, with a cap.
# - **Jitter** — multiply by a random factor. Without it every client in your fleet
#   retries at the same instant and recreates the spike that caused the 429.
# - **Honour `retry-after`** — when the header is present it is the server telling
#   you the safe delay. Guessing your own is strictly worse.
#
# When failures keep coming, a **circuit breaker** stops you hammering a jammed
# backend: after N consecutive transient failures it opens, and traffic goes to a
# fallback model or a degraded response until the cooldown passes.
#
# The SDK already retries 408, 409, 429 and 5xx twice by default
# (`anthropic.Anthropic(max_retries=2)`). The code below is what you add *around*
# that for a sustained outage.

# %%
# random is not in the shared setup, so import it here.
import random

random.seed(7)                 # so the printed waits repeat between runs

base, cap = 1.0, 60.0          # first wait before jitter, and the ceiling
threshold, cooldown = 3, 30    # consecutive failures that open the breaker, and for how long
failures, open_until = 0, 0.0  # the breaker's whole state — two numbers

# Walk a scripted incident: three 529s, then the breaker opens.
for attempt, status in enumerate([529, 529, 529, 529]):
    # The breaker. Once it is open, nothing goes to the primary model at all.
    if time.time() < open_until:
        print(f"attempt {attempt}: breaker OPEN -> send to the fallback model")
        break

    error_type, layer, retry = ERROR_TABLE[status]

    failures += 1                        # a successful call would reset this to 0
    if failures >= threshold:
        open_until = time.time() + cooldown

    # The server's own advice beats your formula whenever it sends one. This
    # scripted 529 carried no retry-after header, so we compute the wait.
    retry_after = None
    if retry_after is not None:
        wait = retry_after
    else:
        # Exponential growth, a ceiling, and jitter — the three parts that stop a
        # retry policy amplifying the incident it is reacting to.
        wait = min(cap, base * 2 ** attempt) * random.uniform(0.5, 1.5)

    print(f"attempt {attempt}: {status} {error_type} -> sleep {wait:5.2f}s")

# %% [markdown]
# ### Feature 3 — The calls that succeeded and still failed
#
# HTTP 200 means the bytes arrived. It says nothing about whether the answer is
# usable. Three model-output failures, and the fix for each:
#
# | Failure | How you detect it | Fix |
# |---|---|---|
# | Truncation | `stop_reason == "max_tokens"` | Raise `max_tokens`, or stream. Never parse a partial as complete. |
# | Refusal | `stop_reason == "refusal"`, with `stop_details` | Reword or route. Looping the same prompt just burns spend. |
# | Bad tool input | your schema validation fails | Hand the error back as a `tool_result` with `is_error: true`. |
#
# That last one is the **feedback re-prompt**, and it is the pattern worth
# memorising. You append the model's own turn, then a `tool_result` carrying the
# validation message and `is_error: true`, and the model tries again with the
# error in front of it. You do **not** re-send the original request.

# %%
# Route a 200 by its stop_reason. The request was fine; only the content is not.
recovery_by_stop_reason = {
    "end_turn":   "use the answer",
    "max_tokens": "raise max_tokens or stream — the answer was cut off, not lost",
    "refusal":    "read stop_details, then reword or route — do NOT loop",
    "tool_use":   "validate block.input, then run the tool or send feedback",
}

for reason in ("end_turn", "max_tokens", "refusal", "tool_use"):
    action = recovery_by_stop_reason.get(reason, "unknown stop_reason — log it and stop")
    print(f"{reason:11s} -> {action}")

# %%
# The feedback re-prompt, built exactly as it goes on the wire. These are the two
# messages you APPEND — the model sees its own bad attempt and why it was rejected.
bad_turn = [{"type": "tool_use", "id": "toolu_01A",
             "name": "get_weather", "input": {"city": "Paris", "units": "kelvin"}}]
validation_error = "units must be 'c' or 'f'"

feedback_messages = [
    # 1. the model's own turn, appended unchanged — skip this and the ids dangle
    {"role": "assistant", "content": bad_turn},
    # 2. the failure, handed back as a tool_result rather than a new request
    {"role": "user", "content": [{
        "type": "tool_result",
        "tool_use_id": "toolu_01A",            # ties the result to the request
        "is_error": True,                      # this is a failure, not an answer
        "content": f"Invalid input: {validation_error}. "
                   "Fix the arguments and call the tool again.",
    }]},
]

for message in feedback_messages:
    print(json.dumps(message, indent=2, default=str))

# %% [markdown]
# ### Feature 4 — Trace analysis: find the *first* anomaly
#
# A trace is the ordered record of every request, tool call and token count in a
# run. When an agent run goes wrong, everything after the first anomaly is
# downstream damage — so fixing the last line in the trace fixes nothing.
#
# ```
#   turn  action                       in_tok   out_tok  stop_reason   note
#   ────  ───────────────────────────  ───────  ───────  ────────────  ──────────────────────
#    1    user → assistant              3,210      412   tool_use      search("Q3 revenue")
#    2    tool_result (search)         11,880        —   —             8,600 tokens of HTML  ◄── first anomaly
#    3    assistant                    12,300      388   tool_use      search("Q3 revenue")  ◄── same call again
#    4    tool_result (search)         20,900        —   —
#    5    assistant                    21,400      401   tool_use      search("Q3 revenue")  ◄── loop
#    6    assistant                    30,100    1,024   max_tokens    ◄── truncated JSON
#    7    integration: json.loads → JSONDecodeError → retried turn 6 three times  ◄── wrong layer
# ```
#
# Four symptoms, one root cause: an unpruned tool result at turn 2. The model never
# found what it needed inside 8,600 tokens of HTML, so it called again; the window
# grew until the answer was truncated; and then the integration layer retried a 200
# as if it were a 5xx. Prune tool output and cap identical calls, and all four go away.

# %%
# Scan the trace for the FIRST anomaly instead of the loudest one.
TRACE = [
    {"turn": 1, "in": 3210,  "out": 412,  "stop": "tool_use",   "tool": "search"},
    {"turn": 2, "in": 11880, "out": 0,    "stop": None,         "tool": "search_result"},
    {"turn": 3, "in": 12300, "out": 388,  "stop": "tool_use",   "tool": "search"},
    {"turn": 4, "in": 20900, "out": 0,    "stop": None,         "tool": "search_result"},
    {"turn": 5, "in": 21400, "out": 401,  "stop": "tool_use",   "tool": "search"},
    {"turn": 6, "in": 30100, "out": 1024, "stop": "max_tokens", "tool": None},
]

growth_limit = 5000            # an input jump this big is unpruned tool output
anomalies, seen_tools, previous_in = [], [], 0

for step in TRACE:
    if step["in"] - previous_in > growth_limit:
        anomalies.append((step["turn"],
                          f"input jumped {step['in'] - previous_in:,} tokens "
                          "— unpruned tool result"))
    if step["tool"] == "search" and step["tool"] in seen_tools:
        anomalies.append((step["turn"], "identical tool call repeated — model loop"))
    if step["stop"] == "max_tokens":
        anomalies.append((step["turn"], "output truncated — raise max_tokens or stream"))

    seen_tools.append(step["tool"])
    previous_in = step["in"]

for turn, description in anomalies:
    print(f"turn {turn}: {description}")

print(f"\nFIX THIS ONE FIRST -> turn {anomalies[0][0]}: {anomalies[0][1]}")
print("Turns 3-6 are consequences. A retry around the whole agent replays all of it.")

# %% [markdown]
# ### Feature 5 — Isolating the origin: your integration, or the model?
#
# Five questions, and each answer points at one layer:
#
# ```
#   QUESTION                                     IF YES → LAYER
#   ───────────────────────────────────────────  ──────────────────────────────
#   Does the raw API response look right?        integration (parsing, glue, retries)
#   Did the same prompt, replayed, fail again?   model output / prompt
#   Did it fail only under load, or at a time?   transport / capacity
#   Did the failure start with a deploy?         your code or config
#   Did it start with a model or prompt bump?    model behaviour
# ```
#
# The move that settles it is **replaying the exact request** from the trace, alone:
# same model id, same messages. If the isolated call is fine, the bug is yours —
# parsing, state, or concurrency. If it reproduces, it is prompt or model territory,
# and the fix is a prompt change *verified by an eval*.
#
# And when a deploy changed three things at once, change one at a time against a
# small labelled set. Thirty cases beats zero, and it runs in CI in a minute.

# %%
# The isolation decision, written out. Order matters: load explains everything
# else, so it is tested first.
signals = [
    #  raw response ok, replay fails, only under load, started with a deploy
    (False, False, True,  False),
    (True,  False, False, False),
    (False, True,  False, False),
    (False, False, False, True),
]

for raw_response_ok, replay_fails, only_under_load, started_with_deploy in signals:
    if only_under_load:
        layer = "transport / capacity — rate limits, 529s, timeouts"
    elif raw_response_ok:
        layer = "integration — your parsing, state or retry code"
    elif replay_fails:
        layer = "model output / prompt — fix the prompt, gate it with an eval"
    elif started_with_deploy:
        layer = "your code or config — diff the release, bisect, check the model pin"
    else:
        layer = "not enough signal — instrument the call and collect a trace first"

    print(layer)

# %%
# The replay itself: ONE exact request, sent alone. This is the single most
# useful debugging move there is — does the failure reproduce in isolation?
prompt = "Reply with the single word: ok"

if client is None:
    answer = ("[RECORDED REPLAY — set ANTHROPIC_API_KEY for a live one] "
              "the isolated call succeeded, so the bug is in the integration layer")
else:
    resp = client.messages.create(
        model=MODEL,
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    # Log what a trace needs: the stop reason and the usage numbers.
    print(f"[replay] stop_reason={resp.stop_reason} "
          f"in={resp.usage.input_tokens} out={resp.usage.output_tokens}")
    answer = "".join(b.text for b in resp.content if b.type == "text")

print("\n" + answer)

# %% [markdown]
# **Practical use:** ask *did the HTTP call succeed?* before you write any recovery
# code. A transient status (429, 500, 529, timeout) gets backoff with jitter and
# `retry-after`, and a circuit breaker if it keeps coming. Any other 4xx gets fixed,
# never retried. And a 200 with a bad answer is a `stop_reason`, schema or prompt
# problem — raise `max_tokens`, send a feedback `tool_result` with `is_error: true`,
# or change the prompt and gate it with an eval. When a run goes wrong, read the
# trace for the **first** anomaly; everything after it is consequence.
#
# **🏋️ Practice:** the recovery router below must keep `max_tokens` truncation out
# of the retry path, which is the classic mistake — the request succeeded, so
# retrying it costs money and changes nothing. The fix is marked `# <-` and is
# already in place, so the cell passes as shipped.
#
# To feel it: delete the marked branch, re-run, and watch a truncated answer get
# used as if it were complete. Put it back. Then add a `refusal` case and a `413`
# case of your own to `cases`, and make the branches handle them.

# %%
# Your turn — see the `# <-` marker.
TRANSIENT = {429, 500, 502, 503, 529}

cases = [
    ((429, None),         "retry"),
    ((529, None),         "retry"),
    ((400, None),         "fix_request"),
    ((404, None),         "fix_request"),
    ((200, "max_tokens"), "raise_max_tokens"),
    ((200, "end_turn"),   "use_answer"),
]

routed = []
for (status, stop_reason), expected in cases:
    if status != 200:
        action = "retry" if status in TRANSIENT else "fix_request"
    elif stop_reason == "max_tokens":
        action = "raise_max_tokens"         # <- a 200 is never a retry
    else:
        action = "use_answer"

    routed.append(action)
    print(f"{status} {str(stop_reason):11s} -> {action:17s} (want {expected})")

assert routed == [want for _, want in cases], \
    "a 200 is being retried, or a 4xx is not being fixed"
print("\nevery outcome routes to the layer that can actually fix it")
