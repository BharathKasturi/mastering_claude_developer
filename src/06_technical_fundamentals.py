# %% [markdown]
# # Topic 3.2 · Technical Fundamentals: SDKs over REST, Websockets
#
# 🎓 *CCDV-F objective 6 of 25 · exam weight 6.1% · API lesson*
#
# **Why this matters.** Under every SDK call is one `POST /v1/messages` over HTTPS,
# and under every live-looking chat UI is a stream of server-sent events. Teams that
# do not know that write a "lightweight" HTTP wrapper, get the auth header wrong,
# ship no timeout, retry 400s five times — and then reach for websockets because
# "streaming means websockets". Knowing what the SDK already owns, and what
# transport the API actually speaks, saves all four mistakes.
#
# **What you'll learn.**
# - See the **REST shape** the SDK wraps: `x-api-key`, `anthropic-version`, and a stateless API.
# - Name the **five things the SDK owns** — auth, retries, timeouts, typing, streaming.
# - Choose **sync or async**, and bound concurrency so `gather` does not become a 429 storm.
# - Read the **SSE event order**, and accumulate `input_json_delta` instead of parsing fragments.
# - Pick **SSE, websocket or polling** for getting a stream to a browser.
#
# **What you need.** Nothing. Every cell runs offline: the streaming and async
# sections replay recorded events. Set `ANTHROPIC_API_KEY` to make the async
# section issue real concurrent calls instead.

# %%
# Load the shared setup: the API client and MODEL.
%run ./00_setup.ipynb

# %% [markdown]
# ### Feature 1 — The REST shape the SDK wraps
#
# ```bash
# curl https://api.anthropic.com/v1/messages \
#   -H "x-api-key: $ANTHROPIC_API_KEY" \
#   -H "anthropic-version: 2023-06-01" \
#   -H "content-type: application/json" \
#   -d '{"model": "claude-sonnet-5", "max_tokens": 1024,
#        "messages": [{"role": "user", "content": "Hello"}]}'
# ```
#
# Three facts here become exam distractors:
#
# 1. The API key goes in **`x-api-key`**, not `Authorization: Bearer`. Bearer is for
#    OAuth tokens, which need `anthropic-beta: oauth-2025-04-20`.
# 2. **`anthropic-version`** is a required header.
# 3. The API is **stateless** — the entire conversation travels in `messages` on
#    every call. Nothing is remembered server-side between requests.
#
# A 200 comes back with `id`, `model`, `content` blocks, `stop_reason`, `stop_details`
# and `usage`; an error comes back as `{"type": "error", "error": {...}}` with a
# `request-id` header, and `retry-after` on a 429.

# %%
# The headers and body the SDK assembles for you, written out. The key is read
# from the environment in real code and NEVER printed — this stays a placeholder.
headers = {
    "x-api-key": "$ANTHROPIC_API_KEY",     # placeholder — never print a real key
    "anthropic-version": "2023-06-01",     # required
    "content-type": "application/json",
}
body = {"model": MODEL, "max_tokens": 1024,
        "messages": [{"role": "user", "content": "Hello"}]}

print("POST https://api.anthropic.com/v1/messages")
for key, value in headers.items():
    print(f"  {key}: {value}")
print("  body:", json.dumps(body))

# %%
# The stateless part, made concrete: turn 3 re-sends turns 1 and 2 as well.
# Nothing is kept server-side, so the request grows with every exchange.
conversation = []
for turn, (question, answer) in enumerate(
        [("Hi", "Hello."), ("What is 2+2?", "4."), ("And times 3?", "12.")], start=1):
    conversation.append({"role": "user", "content": question})
    print(f"turn {turn}: sending {len(conversation)} message(s) — the whole history")
    conversation.append({"role": "assistant", "content": answer})

# %% [markdown]
# ### Feature 2 — What the SDK owns, and what it costs you to re-own
#
# | Concern | Raw `requests` / `fetch` | Official SDK |
# |---|---|---|
# | Auth header | you send `x-api-key` + `anthropic-version` yourself | read from env or profile, headers automatic |
# | Retries | none unless you write them | 408, 409, 429, 5xx and connection errors, with backoff; `max_retries` default 2 |
# | Timeouts | library default, often none | 10 minutes by default; `with_options(timeout=…)` per request |
# | Typing | dicts and hope | typed `Message`, `TextBlock`, `ToolUseBlock`, typed exceptions |
# | Streaming | parse SSE frames yourself | `messages.stream()`, `text_stream`, `get_final_message()` |
# | Other providers | separate signing code | `AnthropicBedrockMantle`, `AnthropicVertex`, `AnthropicFoundry` |
#
# One cross-SDK trap: **Python timeouts are seconds, TypeScript timeouts are
# milliseconds.** `timeout: 30` in TypeScript is thirty milliseconds, and every call
# fails.
#
# And one number to work out before you promise an SLA: the worst-case wall clock is
# **`timeout × (max_retries + 1)`**. A ten-minute default with two retries is half an
# hour of a worker's life.

# %%
# The wall-clock arithmetic that decides whether your settings fit an SLA.
# Backoff sleeps are ignored here, so the real number is a little worse.
for timeout, max_retries in [(600.0, 2), (30.0, 5), (10.0, 2)]:
    worst_case = timeout * (max_retries + 1)
    print(f"timeout={timeout:6.1f}s retries={max_retries} -> worst case {worst_case:7.1f}s")

# This is how you tighten ONE call without changing the client everyone shares.
if client is None:
    print("\n[offline] the call would be:")
    print("  client.with_options(timeout=30.0, max_retries=5)"
          ".messages.create(model=MODEL, max_tokens=64, messages=[...])")
else:
    tight = client.with_options(timeout=30.0, max_retries=5)
    resp = tight.messages.create(
        model=MODEL,
        max_tokens=64,
        messages=[{"role": "user", "content": "Reply: ok"}],
    )
    # request_id is what Anthropic support asks for — log it on every call.
    print("\nrequest_id:", resp._request_id, "| stop_reason:", resp.stop_reason)

# %% [markdown]
# ### Feature 3 — Sync, async, and the semaphore
#
# ```python
# aclient = anthropic.AsyncAnthropic()
#
# async def classify(text):
#     r = await aclient.messages.create(
#         model="claude-haiku-4-5", max_tokens=5,
#         system="Reply with one label: billing | bug | other.",
#         messages=[{"role": "user", "content": text}])
#     return r.content[0].text
# ```
#
# - The **sync client** blocks the thread until the response arrives. Right for
#   scripts, CLIs, and one-request-per-worker servers.
# - The **async client** is awaitable, so one process holds many requests in flight.
#   Right for FastAPI or Node handlers, and for fan-out over independent items.
# - **Neither is faster per request.** Async raises throughput and responsiveness;
#   the model's latency is unchanged.
# - Always **bound the concurrency**. An unbounded `asyncio.gather` over 5,000 items
#   is not clever, it is a 429 storm. A `Semaphore` is the whole fix.
#
# And when nobody is waiting on any single item, the Batch API beats both on cost.

# %%
# The async client, built only when the SDK is installed AND a key is set.
import asyncio

try:
    from anthropic import AsyncAnthropic
    aclient = AsyncAnthropic(api_key=API_KEY) if API_KEY else None
except ImportError:
    aclient = None

print("async client:", "live" if aclient else "offline (calls are simulated)")

TICKETS = ["Card charged twice", "Export button broken", "Add dark mode",
           "Thanks, fixed", "Invoice VAT wrong", "App crashes on launch"]


# KEPT DEF 1 of 2 — `async def` IS the thing this Feature teaches. An awaitable
# call cannot be written as straight-line code; that is the whole point of it.
# The semaphore is a parameter so it is created inside the loop that awaits it.
async def classify(text, gate):
    async with gate:                             # never more than `limit` in flight
        if aclient is None:
            await asyncio.sleep(0.2)             # stand in for network latency
            return "[pretend label]"
        resp = await aclient.messages.create(
            model=MODEL,
            max_tokens=5,
            system="Reply with one label: billing | bug | other.",
            messages=[{"role": "user", "content": text}],
        )
        return resp.content[0].text.strip()


# KEPT DEF 2 of 2 — asyncio.run() needs exactly one coroutine to drive, and this
# is the fan-out itself: a semaphore, then gather. limit=1 gives the sequential
# shape a sync client would give you; limit=3 is the bounded concurrent one.
async def fan_out(tickets, limit):
    gate = asyncio.Semaphore(limit)
    return await asyncio.gather(*(classify(t, gate) for t in tickets))

# %%
# A notebook kernel already owns a running event loop, so asyncio.run() raises if
# you call it straight from a cell. Running it on its own thread sidesteps that.
# In a real script this is just `asyncio.run(fan_out(TICKETS, 3))`.
import threading

box = {}

start = time.time()
worker = threading.Thread(target=lambda: box.update(seq=asyncio.run(fan_out(TICKETS, 1))))
worker.start()
worker.join()
sequential = time.time() - start

start = time.time()
worker = threading.Thread(target=lambda: box.update(labels=asyncio.run(fan_out(TICKETS, 3))))
worker.start()
worker.join()
concurrent = time.time() - start

print(f"sequential : {sequential:.2f}s for {len(TICKETS)} tickets")
print(f"concurrent : {concurrent:.2f}s with at most 3 in flight")
print("labels     :", box["labels"])
print("\nEach call took the same time. Async changed throughput, not latency.")

# %% [markdown]
# ### Feature 4 — Streaming: SSE, and the partial JSON trap
#
# ```python
# with client.messages.stream(model=MODEL, max_tokens=8000,
#                             messages=[{"role": "user", "content": "Write the notes"}]) as stream:
#     for text in stream.text_stream:      # text deltas only
#         print(text, end="", flush=True)
#     final = stream.get_final_message()   # usage, stop_reason, all blocks
# ```
#
# The events arrive in one HTTP response of type `text/event-stream`, in this order:
#
# ```
#   message_start                       usage.input_tokens known here
#   content_block_start   index 0       {type:"text"} or {type:"tool_use", name, id}
#   content_block_delta   index 0  ×N   text_delta        → append .text
#                                       input_json_delta  → append .partial_json  ◄── NOT valid JSON yet
#                                       thinking_delta    → append .thinking
#   content_block_stop    index 0
#   … more blocks …
#   message_delta                       stop_reason, usage.output_tokens
#   message_stop
# ```
#
# **The trap.** A `tool_use` block's `input` arrives as string fragments in
# `input_json_delta.partial_json`. Calling `json.loads` on a fragment usually throws
# — and the dangerous case is the fragment that *succeeds*, because `{"path": "a"}`
# can parse before `, "recursive": true}` has arrived, and then you run the tool with
# half its arguments. Accumulate per block index; parse after `content_block_stop`.
# Or just let `get_final_message()` do the assembly.
#
# One more: Anthropic's Messages API does **not** offer a websocket endpoint. The
# stream is SSE. And streaming is the recommendation for any request with a large
# `max_tokens`, because it keeps the connection alive.

# %%
# A recorded stream, replayed. Watch the tool input arrive in pieces that are not
# valid JSON until the block stops.
EVENTS = [
    {"type": "message_start",       "usage": {"input_tokens": 412}},
    {"type": "content_block_start", "index": 0, "block": {"type": "text"}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Listing "}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "the repo."}},
    {"type": "content_block_stop",  "index": 0},
    {"type": "content_block_start", "index": 1,
     "block": {"type": "tool_use", "id": "toolu_01B", "name": "list_files"}},
    {"type": "content_block_delta", "index": 1,
     "delta": {"type": "input_json_delta", "partial_json": '{"path": "src"'}},
    {"type": "content_block_delta", "index": 1,
     "delta": {"type": "input_json_delta", "partial_json": ', "recursive": true}'}},
    {"type": "content_block_stop",  "index": 1},
    {"type": "message_delta",       "stop_reason": "tool_use", "usage": {"output_tokens": 57}},
    {"type": "message_stop"},
]

# Assemble the message the safe way: accumulate deltas per block index, and parse
# tool input ONLY after content_block_stop. That is the rule that stops a tool
# being called with half its arguments.
blocks, text, tool_calls, stop_reason, usage = {}, "", [], None, {}

for event in EVENTS:
    kind = event["type"]

    if kind == "message_start":
        usage.update(event["usage"])                 # input_tokens is known here

    elif kind == "content_block_start":
        blocks[event["index"]] = {**event["block"], "buffer": ""}

    elif kind == "content_block_delta":
        delta = event["delta"]
        # Every delta type is just an append. The difference is what you do with
        # the buffer once the block stops.
        piece = delta.get("text") or delta.get("partial_json") or delta.get("thinking", "")
        blocks[event["index"]]["buffer"] += piece

        if delta["type"] == "input_json_delta":
            partial = blocks[event["index"]]["buffer"]
            try:
                json.loads(partial)
                # Parseable is NOT the same as complete. Only the block's stop
                # event tells you no more fragments are coming.
                verdict = "parses — but you cannot yet know it is COMPLETE"
            except json.JSONDecodeError:
                verdict = "not valid JSON yet"
            print(f"  fragment {partial!r:45s} {verdict}")

    elif kind == "content_block_stop":
        block = blocks[event["index"]]
        if block["type"] == "text":
            text += block["buffer"]
        elif block["type"] == "tool_use":
            # Safe to parse: the block is complete.
            tool_calls.append({"id": block["id"], "name": block["name"],
                               "input": json.loads(block["buffer"])})

    elif kind == "message_delta":
        stop_reason = event["stop_reason"]           # and output_tokens arrives here
        usage.update(event["usage"])

print("\ntext       :", text)
print("tool call  :", tool_calls[0]["name"], tool_calls[0]["input"])
print("stop_reason:", stop_reason, "| usage:", usage)

# %% [markdown]
# ### Feature 5 — Getting the stream to a browser
#
# The API speaks SSE, so the cheapest correct relay is SSE all the way through:
#
# ```python
# @app.get("/chat")
# async def chat(q: str):
#     async def gen():
#         async with aclient.messages.stream(model=MODEL, max_tokens=4000,
#                                            messages=[{"role": "user", "content": q}]) as s:
#             async for text in s.text_stream:
#                 yield f"data: {json.dumps({'t': text})}\n\n"
#         yield "data: [DONE]\n\n"
#     return StreamingResponse(gen(), media_type="text/event-stream",
#                              headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
# ```
#
# The browser needs no library at all — `EventSource` is built in, and it reconnects
# on its own.
#
# | Transport | Direction | Fits | Does not fit |
# |---|---|---|---|
# | **SSE** | server → client | token streaming, progress; plain HTTP, proxy- and CDN-friendly | the client sending mid-stream |
# | **WebSocket** | bidirectional | interrupt or cancel mid-generation, voice, shared multi-user sessions | one-way text — it adds a connection lifecycle, auth and scaling problem |
# | **Polling** | client pulls | batch status, job results, anything finishing in minutes | token-by-token display |
#
# The reflex the exam rewards you for resisting: **websockets "because it's
# real-time"**. SSE *is* real-time in the server→client direction, and it is what the
# API itself speaks. Pick a websocket only when a concrete client→server message must
# arrive *during* the stream — and even then, a `POST /cancel` beside an SSE stream
# often does the job with less machinery.

# %%
# Choose the transport from the requirements, not from the vocabulary. The first
# question is the only one that can force a websocket.
scenarios = [
    #  label                                                     stream  bidi   job
    ("Show tokens as they arrive; cancel with a separate POST",   True,  False, False),
    ("Voice assistant: the user interrupts mid-sentence",         True,  True,  False),
    ("Check whether a Batch API job has finished",                False, False, True),
    ("One short classification call",                             False, False, False),
]

for label, needs_token_stream, client_sends_mid_stream, finishes_in_minutes in scenarios:
    if client_sends_mid_stream:
        transport = "websocket — a real bidirectional requirement, not a preference"
    elif needs_token_stream:
        transport = "SSE — one-way, real-time, and what the API itself speaks"
    elif finishes_in_minutes:
        transport = "polling — right for batch and job status, wrong for tokens"
    else:
        transport = "a plain request/response — no streaming transport needed"

    print(f"{transport}\n    <- {label}\n")

# %% [markdown]
# **Practical use:** if an official SDK exists for your language, use it. The REST
# shape is identical, and auth, retries, timeouts, typing and streaming are already
# done by the people who run the API — a hand-rolled wrapper re-owns all five and
# usually gets two of them wrong. When you fan out, use the async client **with a
# semaphore**, and move genuinely unattended bulk work to the Batch API. When you
# stream, accumulate `input_json_delta` per block index and parse after
# `content_block_stop`. And relay to the browser over SSE unless the client has a
# concrete message it must send *while the stream is running*.
#
# **🏋️ Practice:** the transport router below has to send the voice assistant — the
# only case where the client speaks mid-stream — to a websocket. The check is marked
# `# <-` and is already in place, so the cell passes as shipped.
#
# To feel it: delete the marked branch, re-run, and watch the voice case fall through
# to SSE, which cannot carry the interrupt. Put it back. Then add a case for a
# collaborative editor where several viewers share one session, and check it routes
# the way you expect.

# %%
# Your turn — see the `# <-` marker.
cases = [
    #  needs_stream, client_sends_mid_stream, is_background_job
    ((True,  False, False), "sse"),
    ((True,  True,  False), "websocket"),
    ((False, False, True),  "polling"),
    ((False, False, False), "request-response"),
]

routed = []
for (needs_stream, client_sends_mid_stream, is_background_job), expected in cases:
    if client_sends_mid_stream:
        picked = "websocket"        # <- the only thing SSE genuinely cannot do
    elif needs_stream:
        picked = "sse"
    elif is_background_job:
        picked = "polling"
    else:
        picked = "request-response"

    routed.append(picked)
    print(f"{picked:16s} (want {expected:16s})  stream={needs_stream} "
          f"bidi={client_sends_mid_stream} job={is_background_job}")

assert routed == [want for _, want in cases], \
    "a bidirectional requirement is being routed to a one-way transport"
print("\nevery requirement reaches a transport that can actually carry it")
