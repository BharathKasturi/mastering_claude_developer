# %% [markdown]
# # Topic 4.3 · Claude API Mechanics: Messages, Tools, Streaming, Vision, Thinking, Caching, Batch
#
# 🎓 *CCDV-F objective 11 of 25 · exam weight 6.8% · API lesson*
#
# **Why this matters.** Almost everything you build with Claude is one call to
# `POST /v1/messages`. Blocks go in, blocks come back, and the server remembers
# nothing. Tools, images, PDFs, thinking and caching are **not separate APIs** —
# they are content-block types and parameters on that one request. The only real
# fork in the road is Batch, and picking it wrong is either a slow user or a
# doubled bill.
#
# **What you'll learn.**
# - Read a Messages response properly: `stop_reason` first, then typed blocks, then `usage`.
# - Get bytes *in* — inline text, images, PDFs, the Files API, citations.
# - Run the `tool_use` → `tool_result` handshake, including parallel calls and errors.
# - Reassemble a **stream** from its events, and know why `input_json_delta` must be buffered.
# - Turn the two token dials: adaptive **thinking** and prefix **prompt caching**.
# - Choose realtime, streaming or **Batch**, and call Claude through a third-party vendor.
#
# **What you need.** Nothing. Every cell runs offline with clearly-labelled
# pretend results. Set `ANTHROPIC_API_KEY` to make the live-guarded cells call
# the real API instead.

# %%
# Load the shared setup: the API client and MODEL.
%run ./00_setup.ipynb

# %% [markdown]
# ### Feature 1 — One stateless endpoint: blocks in, blocks out
#
# Four facts the exam tests over and over:
#
# 1. **Stateless.** There is no `conversation_id` and no server-side history.
#    Every turn re-sends `system` plus every prior message. That resend is your
#    token bill, and the reason prompt caching exists.
# 2. **Blocks.** Request *and* response `content` are lists of typed blocks —
#    `text`, `image`, `document`, `tool_use`, `tool_result`, `thinking`.
#    `resp.content[0].text` only works if you checked `.type` first.
# 3. **Roles.** Messages alternate `user`/`assistant` and the first must be
#    `user`. `system` is a **top-level parameter**, not a message.
# 4. **`stop_reason`.** One of `end_turn`, `max_tokens`, `stop_sequence`,
#    `tool_use`, `pause_turn`, `refusal`. `stop_details` is populated only for
#    `refusal`. All of these come back as **HTTP 200** — a 200 is not a promise
#    that the answer is complete.

# %%
# Build a real request. `max_tokens` is a HARD cap on the reply, not a target.
request = {
    "model": MODEL,
    "max_tokens": 300,
    "system": "You are a contracts analyst.",          # separate field, NOT a message
    "messages": [
        {"role": "user", "content": "Summarise clause 7."},
        {"role": "assistant", "content": "Clause 7 covers indemnity."},  # prior turn — YOU resend it
        {"role": "user", "content": "And clause 8?"},
    ],
}

# Send it, then read the reply in the safe order: stop_reason, blocks, usage, text.
if client is None:
    stop_reason = "end_turn"
    block_types = ["text"]
    usage = {"input_tokens": 0, "output_tokens": 0}
    text = "[PRETEND ANSWER — set ANTHROPIC_API_KEY for a real reply]"
else:
    resp = client.messages.create(**request)
    stop_reason = resp.stop_reason                        # ALWAYS check this first
    block_types = [b.type for b in resp.content]          # content is a LIST of typed blocks
    usage = {"input_tokens": resp.usage.input_tokens,
             "output_tokens": resp.usage.output_tokens}
    text = "".join(b.text for b in resp.content if b.type == "text")

print("stop_reason :", stop_reason)
print("block types :", block_types)
print("usage       :", usage)
print("text        :", text[:120])

# %%
# What each stop_reason means for YOUR code. This table is the difference
# between "we got a 200" and "we got a complete answer".
STOP_REASONS = {
    "end_turn":      "finished normally — safe to use the text",
    "max_tokens":    "TRUNCATED — a success that is missing its ending",
    "stop_sequence": "hit one of your stop_sequences",
    "tool_use":      "wants a tool — run it and loop again",
    "pause_turn":    "long-running server tool paused — send the turn back to continue",
    "refusal":       "policy stop — read stop_details, do not retry blindly",
}

# Only these two mean the model finished on its own terms. Treating any other
# 200 as done is the most common silent bug in a Claude application.
COMPLETE = ("end_turn", "stop_sequence")

for reason, meaning in STOP_REASONS.items():
    complete = reason in COMPLETE
    print(f"{reason:14s} complete={str(complete):5s}  {meaning}")

# %% [markdown]
# ### Feature 2 — Data access patterns: getting bytes *in*
#
# There is one way in — a typed content block in a user message. Which block you
# use depends on where the bytes live and how often you send them.
#
# | Getting data in | Block shape |
# |---|---|
# | Inline text | `{"type": "text", "text": "..."}` |
# | Inline image | `{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "..."}}` |
# | Hosted image | `{"type": "image", "source": {"type": "url", "url": "..."}}` |
# | PDF document | `{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "..."}}` |
# | Files API | upload once → `{"type": "document", "source": {"type": "file", "file_id": "..."}}` |
# | Citations | add `"citations": {"enabled": True}` to a document block |
#
# Gotchas the exam likes: the base64 string carries **no newlines**; the
# image/document block goes **before** the text that asks about it; the block
# type must match the file's MIME type; and citations are incompatible with
# `output_config.format` (400).

# %%
import base64

# A real 1×1 PNG, so the cell needs no image file and no network.
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

# standard_b64encode gives ONE line. Never use encodebytes() here: it wraps at
# 76 characters and the newlines are rejected.
png_b64 = base64.standard_b64encode(PNG_BYTES).decode()

# "Vision" is a block TYPE, not a different endpoint. media_type must match the
# real bytes. Order matters: the picture first, then the question about it.
vision_message = {"role": "user", "content": [
    {"type": "image", "source": {"type": "base64",
                                 "media_type": "image/png", "data": png_b64}},
    {"type": "text", "text": "What colour is this pixel?"},
]}

print("blocks in order :", [b["type"] for b in vision_message["content"]])
print("newlines in b64 :", "\n" in png_b64)

# The same file sent many times should be uploaded once and referenced by id:
#   f = client.files.upload(file=("contract.pdf", open("contract.pdf","rb"), "application/pdf"))
reusable_doc = {"type": "document",
                "source": {"type": "file", "file_id": "file_abc123"},
                "citations": {"enabled": True}}   # answers gain a .citations array
print("reusable doc    :", json.dumps(reusable_doc))

# Price an expensive call BEFORE sending it. count_tokens runs the real
# tokenizer — never estimate Claude's tokens with a tokenizer built for
# another model family.
if client is None:
    print("[skipped — set ANTHROPIC_API_KEY to count tokens with the real tokenizer]")
else:
    count = client.messages.count_tokens(
        model=MODEL,
        system="You are a contracts analyst.",
        messages=[{"role": "user", "content": "Summarise clause 7."}],
    )
    print("input_tokens:", count.input_tokens)

# %% [markdown]
# ### Feature 3 — Tools: the `tool_use` → `tool_result` handshake
#
# A tool is a JSON Schema you send with the request. **Claude never executes
# anything** — it asks. You execute and send the answer back.
#
# ```
# user      "where is ORD-10422?"
# assistant [ text "Let me check." ] [ tool_use id=toolu_01 ]   stop_reason=tool_use
# user      [ tool_result tool_use_id=toolu_01 content="..." ]  <- role is USER
# assistant [ text "It shipped Tuesday." ]                      stop_reason=end_turn
# ```
#
# Three rules that are pure exam material:
#
# - Append the model's **whole** assistant turn (`resp.content`) back into `messages`.
# - One assistant turn's results go back in **exactly one** user message, even
#   when the model asked for three tools. Splitting them teaches the model to
#   stop parallelising.
# - A failure is returned with `"is_error": True`, never swallowed. Dropping it
#   makes the model repeat the same call forever.

# %%
# The tool definition. `strict: True` sits on the TOOL — it needs both
# `required` and `additionalProperties: False` to guarantee the input validates.
ORDER_TOOL = {
    "name": "get_order_status",
    "description": ("Look up the current status of a customer order by its ID. "
                    "Returns status, carrier and ETA. Use when the user names an order."),
    "input_schema": {
        "type": "object",
        "properties": {"order_id": {"type": "string", "description": "e.g. ORD-10422"}},
        "required": ["order_id"],
        "additionalProperties": False,
    },
    "strict": True,
}

# Controls that change WHETHER a tool is called, all on the request:
TOOL_CHOICE = {
    "auto":     {"type": "auto"},                       # default — model decides
    "any":      {"type": "any"},                        # must call SOME tool
    "specific": {"type": "tool", "name": "get_order_status"},   # must call THAT tool
    "serial":   {"type": "auto", "disable_parallel_tool_use": True},  # at most one per turn
}
for name, choice in TOOL_CHOICE.items():
    print(f"{name:9s} {json.dumps(choice)}")

# %%
# Our pretend backend, standing in for a real service call.
ORDERS = {"ORD-10422": {"status": "shipped", "carrier": "UPS", "eta": "Thursday"}}

# A scripted assistant turn asking for two tools at once — the parallel case.
assistant_turn = [
    {"type": "text", "text": "Let me check both."},
    {"type": "tool_use", "id": "toolu_01", "name": "get_order_status",
     "input": {"order_id": "ORD-10422"}},
    {"type": "tool_use", "id": "toolu_02", "name": "get_order_status",
     "input": {"order_id": "BAD-1"}},        # this one will fail on purpose
]

# Execute every tool_use block and collect the results. `input` is already a
# dict — never string-match a serialized tool input.
results = []
for block in assistant_turn:
    if block["type"] != "tool_use":
        continue                             # text and thinking blocks are not our job
    order_id = block["input"]["order_id"]
    if order_id in ORDERS:
        output = dict(ORDERS[order_id], order_id=order_id)
        results.append({"type": "tool_result", "tool_use_id": block["id"],
                        "content": json.dumps(output)})
    else:
        # Return the failure so the model can change course. An error message
        # that says HOW to fix it gets a corrected retry.
        results.append({"type": "tool_result", "tool_use_id": block["id"],
                        "content": "order_id must match ORD-<digits>", "is_error": True})

# ALL of one turn's results go back in ONE user message.
tool_reply = {"role": "user", "content": results}

print("role           :", tool_reply["role"], "(tool_result ALWAYS goes in a user message)")
print("result blocks  :", len(tool_reply["content"]), "in one message")
for block in tool_reply["content"]:
    print("  ", block["tool_use_id"], "is_error =", block.get("is_error", False),
          "->", block["content"][:60])

# %% [markdown]
# ### Feature 4 — Streaming: the same response, arriving in pieces
#
# Streaming is server-sent events over the same endpoint. It changes **delivery
# only** — not the content, not the price.
#
# ```
# message_start        {message: {id, model, usage: {input_tokens}}}
# content_block_start  index 0  {type: "text"}
# content_block_delta  index 0  delta.type = "text_delta"        -> append .text
# content_block_stop   index 0
# content_block_start  index 1  {type: "tool_use", id, name}
# content_block_delta  index 1  delta.type = "input_json_delta"  -> concat .partial_json
# content_block_stop   index 1        <- only NOW is the JSON parseable
# message_delta        {delta: {stop_reason}, usage: {output_tokens}}
# message_stop
# ```
#
# Three rules:
#
# - **Output tokens are on `message_delta`, not `message_start`.** Cost logging
#   that reads only `message_start` under-reports every single request.
# - **`input_json_delta` fragments are not JSON.** Concatenate `partial_json`,
#   parse once at `content_block_stop`.
# - Blocks arrive **interleaved by index** — buffer per index, never by arrival order.
# - Large `max_tokens` effectively requires streaming: a long non-streaming
#   request will hit the SDK's HTTP timeout.

# %%
# A scripted event list, exactly the shape the SSE stream delivers. Replaying it
# lets us build the reassembler with no key and no network.
EVENTS = [
    {"type": "message_start", "message": {"id": "msg_01", "usage": {"input_tokens": 412}}},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Checking "}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "that order."}},
    {"type": "content_block_stop", "index": 0},
    {"type": "content_block_start", "index": 1,
     "content_block": {"type": "tool_use", "id": "toolu_09", "name": "get_order_status"}},
    {"type": "content_block_delta", "index": 1,
     "delta": {"type": "input_json_delta", "partial_json": '{"order_'}},
    {"type": "content_block_delta", "index": 1,
     "delta": {"type": "input_json_delta", "partial_json": 'id": "ORD-10422"}'}},
    {"type": "content_block_stop", "index": 1},
    {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 57}},
    {"type": "message_stop"},
]

# Rebuild the Message from its events. Buffer PER INDEX, and parse only at stop.
blocks, usage, stop_reason = {}, {}, None
for ev in EVENTS:
    kind = ev["type"]
    if kind == "message_start":
        usage.update(ev["message"]["usage"])              # input tokens known up front
    elif kind == "content_block_start":
        blocks[ev["index"]] = dict(ev["content_block"], buffer="")
    elif kind == "content_block_delta":
        # Both delta types append to the SAME per-index buffer. Never parse yet.
        delta = ev["delta"]
        blocks[ev["index"]]["buffer"] += delta.get("text", delta.get("partial_json", ""))
    elif kind == "content_block_stop":
        block = blocks[ev["index"]]
        if block["type"] == "tool_use":
            block["input"] = json.loads(block.pop("buffer"))   # complete only now
        else:
            block["text"] = block.pop("buffer")
    elif kind == "message_delta":
        stop_reason = ev["delta"]["stop_reason"]
        usage.update(ev["usage"])                         # OUTPUT tokens arrive here

for index in sorted(blocks):
    print(blocks[index])
print("stop_reason:", stop_reason, " usage:", usage)

# %%
# The live twin. `stream.get_final_message()` hands back the assembled Message,
# so you still get usage, stop_reason and non-text blocks.
if client is None:
    print("[skipped — set ANTHROPIC_API_KEY to watch a real stream]")
    print("with client.messages.stream(model=MODEL, max_tokens=64000, messages=msgs) as s:")
    print("    for chunk in s.text_stream: print(chunk, end='')")
    print("    final = s.get_final_message()   # usage, stop_reason, every block")
else:
    with client.messages.stream(model=MODEL, max_tokens=200,
                                messages=[{"role": "user",
                                           "content": "Name three UK cities."}]) as stream:
        for chunk in stream.text_stream:
            print(chunk, end="", flush=True)
        final = stream.get_final_message()
    print("\n--- stop_reason:", final.stop_reason, "usage:", final.usage.output_tokens)

# %% [markdown]
# ### Feature 5 — The two token dials: thinking and prompt caching
#
# **Thinking** spends *output* tokens to reason before answering. On current
# models it is adaptive — the model decides how much, and you steer depth with
# `effort`, not a byte budget. The older `{"type": "enabled", "budget_tokens": N}`
# form is **rejected with a 400** by the 5-family. Thinking blocks are billed as
# output tokens whether or not you display them.
#
# **Prompt caching** attacks the statelessness bill. It is a **prefix** cache.
# The request renders as `tools` → `system` → `messages`, and a `cache_control`
# breakpoint says "everything up to here is reusable".
#
# ```
# stable first    frozen system prompt, deterministic tool ORDER
# volatile last   timestamps, request ids, the user's question
# breakpoints     up to 4 per request
# silent killers  datetime.now() in system, a UUID, unsorted json.dumps(),
#                 a tool list built from a set, switching model mid-conversation
# proof of life   usage.cache_read_input_tokens > 0 on the SECOND request
# ```
#
# A cache miss is never an error. It is a silently larger bill, and the only
# diagnosis is the `usage` fields — which is why production logs all three.

# %%
POLICY_MANUAL = "REFUND POLICY\n" + ("Section text. " * 40)   # stands in for 80 pages

# The two dials, on one request. Note where each piece goes.
thinking_request = {
    "model": MODEL,
    "max_tokens": 4000,
    "thinking": {"type": "adaptive", "display": "summarized"},   # default display is "omitted"
    "output_config": {"effort": "high"},                         # low|medium|high|xhigh|max
    "system": [{"type": "text", "text": POLICY_MANUAL,           # identical every call
                "cache_control": {"type": "ephemeral"}}],        # optional "ttl": "1h"
    "messages": [{"role": "user", "content": "Is a 40-day-old order refundable?"}],
}
print("thinking     :", json.dumps(thinking_request["thinking"]))
print("effort       :", json.dumps(thinking_request["output_config"]))
print("breakpoint on:", thinking_request["system"][0]["cache_control"])
print("volatile part: the user question, AFTER the breakpoint  ✅")

# %%
# Two candidate prefixes, checked for the things that silently stop a prefix
# from ever matching. This turns "the cache is on but the bill didn't move"
# into a checklist.
CANDIDATES = [
    ("BAD ", f"Today is 2026-09-05. {POLICY_MANUAL}", [{"name": "z"}, {"name": "a"}]),
    ("GOOD", POLICY_MANUAL,                           [{"name": "a"}, {"name": "z"}]),
]

for label, system_text, tools in CANDIDATES:
    problems = []
    # A timestamp or an id in the prefix changes the bytes on every request.
    if re.search(r"\d{4}-\d{2}-\d{2}|\d{2}:\d{2}:\d{2}", system_text):
        problems.append("a date/time in the system prompt — move it into the user message")
    if re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", system_text):
        problems.append("a UUID in the system prompt — volatile content must come last")
    # Tools render BEFORE system, so unstable tool order invalidates everything after it.
    names = [t["name"] for t in tools]
    if names != sorted(names):
        problems.append("tool list is not in a deterministic order — sort it")
    print(label, problems or "prefix is stable")

# After any real call, these three numbers are the only proof:
#   usage.cache_creation_input_tokens  written to cache (~1.25x input price)
#   usage.cache_read_input_tokens      served from cache (~0.1x input price)
#   usage.input_tokens                 uncached, full price
print("\nlog all three usage fields on every call — a miss raises no error")

# %% [markdown]
# ### Feature 6 — Realtime, Batch, and Claude through third-party vendors
#
# | | Standard (sync) | Streaming | Batch |
# |---|---|---|---|
# | Call | `messages.create(...)` | `messages.stream(...)` | `messages.batches.create(requests=[...])` |
# | Latency | seconds, blocking | first token ~1s | asynchronous, **within 24 h** |
# | Cost | list price | same as standard | ~**50%** of standard |
# | Results | one `Message` | SSE events you reassemble | keyed by `custom_id`, in **any order** |
#
# **Decision rule:** *is a human or a caller blocked on this right now?* No →
# **Batch**. Yes and the output is long → **stream**. Yes and it is short and
# machine-consumed → plain sync. Firing 10,000 parallel sync calls "to go
# faster" is the anti-pattern: same price, plus 429s, plus retries.
#
# **Third-party vendors.** The model is the same; the client class, the
# credentials and the **model-ID format** are not.
#
# | | Claude API | Amazon Bedrock | Google Vertex AI | Microsoft Foundry |
# |---|---|---|---|---|
# | Client | `Anthropic()` | `AnthropicBedrockMantle(aws_region=...)` | `AnthropicVertex(project_id=..., region=...)` | `AnthropicFoundry(api_key=..., resource=...)` |
# | Auth | Anthropic key | AWS IAM | GCP ADC | Azure resource |
# | Model ID | `claude-opus-5` | `anthropic.claude-opus-5` | `claude-opus-4-5@20251101` | first-party id |
#
# Pick whichever cloud already holds the data and the contract. Choose
# first-party when the stem names a feature that ships there alone.

# %%
# The realtime-vs-batch tradeoff, made mechanical. Volume never enters it:
# 10,000 requests someone is waiting on are still realtime, and one request
# nobody is waiting on is still Batch.
JOBS = [
    ("40,000 tickets tagged by Friday",   False, False, "batch"),
    ("Chat UI drafting a 20-page report", True,  True,  "stream"),
    ("Classify one incoming webhook",     True,  False, "sync"),
]

for label, anyone_waiting, output_is_long, expected in JOBS:
    if not anyone_waiting:
        delivery = "batch"          # half price, up to 24 h — never for interactive work
    elif output_is_long:
        delivery = "stream"         # long generations do not fit one blocking response
    else:
        delivery = "sync"           # short, machine-consumed, one call
    print(f"{delivery:7s} (want {expected:7s})  <- {label}")

# %%
# A batch request body. Each `params` is an ordinary Messages request — tools,
# images, caching and system all work inside it. `custom_id` is YOUR key.
documents = [{"id": 101, "text": "card declined"}, {"id": 102, "text": "where is my parcel"}]

batch_requests = [
    {"custom_id": f"doc-{d['id']}",
     "params": {"model": MODEL, "max_tokens": 64,
                "messages": [{"role": "user", "content": f"Classify:\n{d['text']}"}]}}
    for d in documents
]
print("first request:", json.dumps(batch_requests[0])[:110], "...")

# Results come back UNORDERED. Keying by position is the classic bug.
fake_results = [
    {"custom_id": "doc-102", "result": {"type": "succeeded", "text": "shipping"}},
    {"custom_id": "doc-101", "result": {"type": "expired"}},      # requeue, do not drop
]
store, retry_queue = {}, []
for entry in fake_results:
    if entry["result"]["type"] == "succeeded":
        store[entry["custom_id"]] = entry["result"]["text"]       # key by custom_id, NEVER by index
    else:
        retry_queue.append(entry["custom_id"])                    # errored|canceled|expired
print("stored :", store)
print("requeue:", retry_queue)

# %% [markdown]
# **Practical use:** there is one endpoint and it forgets you between calls.
# Read `stop_reason` before you read any content, and treat `content` as a list
# of typed blocks. Send data in as blocks, send tool results back as **one user
# message** with matching `tool_use_id`s and `is_error` on the failures. Stream
# when the output is long, cache the stable prefix and prove it with
# `usage.cache_read_input_tokens`, and send the work to Batch whenever nobody is
# waiting.
#
# **🏋️ Practice:** the caching bug from the exam. A service caches a policy
# manual but the bill never moves, because the tool list is built from a `set`
# and so renders in a different order in each process. Tools render *before*
# `system`, so that invalidates the whole prefix.
#
# The fix is marked `# <-` below and is already in place, so the cell passes as
# shipped. To feel it: delete the `sorted(...)` call, re-run, and watch the
# assert fail. Put it back. Then break it a second way — move `stamp` into the
# system text instead of the volatile part — and see the same failure.

# %%
# Your turn — see the `# <-` marker.
TOOLS_A = [{"name": "get_order_status"}, {"name": "create_refund"}]
TOOLS_B = list(reversed(TOOLS_A))          # the same set, a different iteration order

# Render the cacheable prefix exactly as the API does: tools, then system.
# A stable prefix is a property of the BYTES, not of the cache_control flag.
prefixes = []
for tools, stamp in [(TOOLS_A, "2026-09-05"), (TOOLS_B, "2026-09-06")]:
    ordered = sorted(tools, key=lambda t: t["name"])      # <- deterministic tool ORDER
    prefix = json.dumps(ordered, sort_keys=True) + json.dumps(
        [{"type": "text", "text": POLICY_MANUAL,
          "cache_control": {"type": "ephemeral"}}], sort_keys=True)
    volatile = f"Today is {stamp}. What is the refund window?"   # AFTER the breakpoint
    prefixes.append(prefix)

prefix_a, prefix_b = prefixes
print("prefix A == prefix B :", prefix_a == prefix_b)
print("prefix length        :", len(prefix_a), "bytes")

assert prefix_a == prefix_b, "the prefix changed — every request would miss the cache"
print("\nstable prefix: the second request can read from cache")
