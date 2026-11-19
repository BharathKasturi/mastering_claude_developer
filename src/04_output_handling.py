# %% [markdown]
# # Topic 2.3 · Output Handling: Structured Output, Validation, Defensive Parsing, Skepticism
#
# 🎓 *CCDV-F objective 4 of 25 · exam weight 2.6% · API lesson*
#
# **Why this matters.** Claude's output is text until *your code* turns it into
# data. A schema is a contract the model is asked to honour; validation is the
# proof that it did. Ship the first without the second and a fluent, confident,
# perfectly-shaped wrong answer walks straight into your database — because on
# the wire it looks exactly like a right one.
#
# **What you'll learn.**
# - Use a **tool `input_schema`** as the shape, and force it with **`tool_choice`**.
# - Compare the four shape mechanisms, and know which ones work with **thinking**.
# - **Validate in code** — enums, ranges, required fields, and the semantic rules
#   a JSON Schema cannot express.
# - Parse **defensively**: fences, and `stop_reason == "max_tokens"` truncation
#   you must never try to repair.
# - **Retry with the error text**, bounded, so the next attempt is a correction
#   and not a re-roll.
# - Design for **skepticism**: verify mechanically, or gate it on a human.
#
# **What you need.** Nothing. The responses in the retry cell are simulated, so
# the validation logic runs offline exactly as it would in production. Set
# `ANTHROPIC_API_KEY` and Feature 1 makes a real forced-tool call.

# %%
# Load the shared setup: the API client and MODEL.
%run ./00_setup.ipynb

# %% [markdown]
# ### Feature 1 — A tool schema *is* your output schema
#
# A tool does not have to *do* anything. Its `input_schema` is a JSON Schema, the
# API enforces it on the `tool_use.input` block, and `tool_choice` forces the
# model to emit exactly that block. The result arrives as a **dict** — no
# `json.loads`, no fence stripping, nothing to regex.
#
# Two schema tricks remove the most hallucinated values:
#
# - **`enum`** constrains the vocabulary, so `priority` cannot come back as
#   `"very high"`.
# - **`["string", "null"]`** gives the model a legal way to say "not present",
#   instead of inventing a `customer_id` that looks plausible.

# %%
# The shape, as JSON Schema. additionalProperties: False keeps invented fields out.
TICKET_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": ["billing", "bug", "feature", "other"]},
        "priority": {"type": "string", "enum": ["low", "medium", "high"]},
        "summary": {"type": "string", "maxLength": 200},
        "customer_id": {"type": ["string", "null"]},      # absent is a valid answer
    },
    "required": ["category", "priority", "summary", "customer_id"],
    "additionalProperties": False,
}

# The tool definition. It does nothing at all — the schema IS the point.
RECORD_TICKET = {
    "name": "record_ticket",
    "description": "Record the classification of one support ticket. Always call this exactly once.",
    "input_schema": TICKET_SCHEMA,
}

# The whole call: the tool, and tool_choice forcing THIS tool so the model
# cannot answer in prose instead.
ticket_text = "I was charged twice for order 8811."

if client is None:
    print("[PRETEND RESPONSE — set ANTHROPIC_API_KEY for a real forced-tool call]")
    ticket = {"category": "billing", "priority": "high",
              "summary": "Customer charged twice for one order.",
              "customer_id": "cus_8811"}
else:
    resp = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        tools=[RECORD_TICKET],
        tool_choice={"type": "tool", "name": "record_ticket"},   # force THIS tool
        messages=[{"role": "user",
                   "content": f"Classify this ticket:\n<ticket>{ticket_text}</ticket>"}],
    )
    block = next(b for b in resp.content if b.type == "tool_use")
    ticket = block.input        # already a dict — no json.loads, no fence stripping

print(json.dumps(ticket, indent=2))

# %% [markdown]
# ### Feature 2 — The four shape mechanisms, and thinking
#
# ```
# mechanism            enforces shape?   removes preamble?     works with thinking?
# ───────────────────  ───────────────   ───────────────────   ────────────────────
# "reply in JSON"      no                no                    yes
# prefill "{"          no                yes                   NO (and a 400 on
#                                                              current models)
# tool schema+choice   yes (JSON Schema) yes (a block, not text) forcing: NO / auto: yes
# output_format        yes (strongest)   yes                   version-dependent
# ```
#
# - `tool_choice` values: `{"type": "auto"}` (default), `{"type": "any"}` (must
#   call *some* tool), `{"type": "tool", "name": …}` (must call *this* tool), and
#   `{"type": "none"}`. Forcing turns "may return JSON" into "will return this
#   block".
# - With **extended thinking on, only `auto` and `none` are allowed.** Forcing a
#   tool and thinking are mutually exclusive — pick one feature per call.
# - **Native structured outputs** launched as beta `structured-outputs-2025-11-13`,
#   adding an `output_format` parameter of type `json_schema` and a `strict: true`
#   flag on tool definitions. Availability by model and the supported schema
#   subset are version-dependent; say so rather than assert it is everywhere.
# - **Prefill** — starting the assistant turn with `{` — was the classic
#   preamble-killer. It never enforced a schema, it is unavailable with thinking,
#   and on current models it returns a **400**.

# %%
# Native structured output, with the real field names.
STRUCTURED_REQUEST = {
    "model": "claude-sonnet-5", "max_tokens": 1024,
    "betas": ["structured-outputs-2025-11-13"],       # header name at launch — check current docs
    "output_format": {"type": "json_schema", "schema": TICKET_SCHEMA},
    "messages": [{"role": "user", "content": "<ticket>…</ticket>"}],
}
print(json.dumps({k: STRUCTURED_REQUEST[k] for k in ("model", "betas", "output_format")}, indent=2))

# Choose the mechanism from the constraints of the call, not from habit. The one
# rule that is mutually exclusive: forcing a tool, and thinking.
print()
for machine_consumed, thinking_on in ((False, False), (True, False), (True, True)):
    if not machine_consumed:
        mechanism = "plain prompt + length caps — a human is reading it"
    elif thinking_on:
        # Forcing a tool is rejected with thinking on; auto plus validation is the way.
        mechanism = "output_format (if supported) or tool_choice 'auto' + code validation"
    else:
        mechanism = "tool input_schema + tool_choice {'type':'tool','name':…}"
    print(f"machine_consumed={machine_consumed!s:5s} thinking={thinking_on!s:5s} -> {mechanism}")

# %% [markdown]
# ### Feature 3 — Validation in code
#
# JSON Schema proves **syntax and types**. Your domain rules are semantics, and
# they live in a validator: a `due_date` in the past, a `total` that does not
# equal the sum of `line_items`, a `confidence` of 0.99 on an obviously ambiguous
# input. In production this is Pydantic or zod:
#
# ```python
# from pydantic import BaseModel, Field, field_validator, ValidationError
#
# class Ticket(BaseModel):
#     category: Literal["billing", "bug", "feature", "other"]
#     priority: Literal["low", "medium", "high"]
#     summary: str = Field(max_length=200)
#     customer_id: Optional[str] = None
#
#     @field_validator("customer_id")
#     @classmethod
#     def looks_like_an_id(cls, v):
#         if v is not None and not v.startswith("cus_"):
#             raise ValueError("customer_id must start with 'cus_' or be null")
#         return v
#
# ticket = Ticket.model_validate(block.input)     # raises ValidationError
# ```
#
# The rules the exam checks: **validate the parsed object, not the string** — a
# regex over raw text is not validation. **Fail closed** — an invalid object never
# reaches the database "because it was mostly right". And validation is layered
# **under** the schema mechanism, not instead of it: the tool schema stops 90% of
# shape errors cheaply, the validator stops the semantic 10% a schema cannot
# express.

# %%
# The same checks in the standard library, so this notebook needs no extra
# install. In your own code use Pydantic or zod — the logic is identical.
GOOD = {"category": "billing", "priority": "high",
        "summary": "Charged twice.", "customer_id": "cus_8811"}
BAD = {"category": "billing", "priority": "urgent",      # not in the enum
       "summary": "Charged twice.", "customer_id": "8811"}   # missing the prefix

for label, data in (("good ticket", GOOD), ("bad ticket ", BAD)):
    errors = []

    # Required fields — the schema covers this, and we re-check because a
    # different mechanism (or a future refactor) may not enforce it.
    for field in TICKET_SCHEMA["required"]:
        if field not in data:
            errors.append(f"{field}: required field missing")

    # Enum membership: the vocabulary the schema declared.
    for field in ("category", "priority"):
        allowed = TICKET_SCHEMA["properties"][field].get("enum", [])
        if field in data and data[field] not in allowed:
            errors.append(f"{field}: {data[field]!r} is not one of {allowed}")

    if len(str(data.get("summary", ""))) > 200:
        errors.append("summary: longer than 200 characters")

    # The semantic rule no JSON Schema can express: our ids have a prefix.
    cid = data.get("customer_id")
    if cid is not None and not str(cid).startswith("cus_"):
        errors.append("customer_id: must start with 'cus_' or be null")

    print(f"{label} errors:", errors or "none")

# %% [markdown]
# ### Feature 4 — Defensive parsing and retry with feedback
#
# ```
# stop_reason          meaning                                your move
# ──────────────────   ────────────────────────────────────   ──────────────────────────
# end_turn             model finished naturally               parse + validate
# tool_use             model emitted a tool block             read .input (already JSON)
# max_tokens           CUT OFF — output is incomplete         raise max_tokens / shrink the
#                                                             task; never "fix" half an object
# stop_sequence        hit your custom stop string            expected; parse up to it
# refusal              safety refusal                         surface it; do not retry blindly
# pause_turn           long-running server tool paused        resend to continue
# ```
#
# Two properties of the retry loop matter more than the code. **The error text
# goes back to the model** — a bare "try again" just re-rolls the dice. And
# **retries are bounded**: three failures is a bug report, not a fourth attempt.
#
# A "JSON repair" library that closes unbalanced braces is the wrong answer to
# truncation. It turns a loud failure into a quiet one and silently drops fields.

# %%
# The boring fix for the boring failure: a model that wrapped its object in a
# markdown fence. Only ever needed for TEXT responses — a tool block has none.
raw = '```json\n{"category": "bug"}\n```'
fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.S)
print(fenced.group(1) if fenced else raw.strip())

# %%
# Block/Response are OUR stand-ins for the SDK's response objects — NOT part of
# the Anthropic SDK. Real blocks expose .type / .input / .id the same way, and a
# real response exposes .content and .stop_reason, so the loop below is unchanged
# in production. Scripted: a bad first answer, then a corrected one.
from collections import namedtuple

Block = namedtuple("Block", "type text input id", defaults=("", "", None, None))
Response = namedtuple("Response", "content stop_reason")

SCRIPT = [
    Response(content=[Block(type="tool_use", input=BAD, id="toolu_demo")], stop_reason="tool_use"),
    Response(content=[Block(type="tool_use", input=GOOD, id="toolu_demo")], stop_reason="tool_use"),
]

MAX_ATTEMPTS = 3            # bounded: three failures is a bug report, not a fourth attempt
validated = None

for attempt, resp in enumerate(SCRIPT[:MAX_ATTEMPTS], start=1):
    # Truncation is an error, not data. Nothing downstream can trust half an object.
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("cut off — raise max_tokens or shorten the schema; do NOT repair the JSON")

    block = next(b for b in resp.content if b.type == "tool_use")

    # The same domain checks as Feature 3, written out where they actually run.
    errors = []
    for field in ("category", "priority"):
        allowed = TICKET_SCHEMA["properties"][field].get("enum", [])
        if block.input.get(field) not in allowed:
            errors.append(f"{field}: {block.input.get(field)!r} is not one of {allowed}")
    cid = block.input.get("customer_id")
    if cid is not None and not str(cid).startswith("cus_"):
        errors.append("customer_id: must start with 'cus_' or be null")

    if not errors:
        validated = block.input
        break

    # The retry the model can actually act on: a tool_result flagged is_error,
    # tied to the request by tool_use_id, quoting the exact failure.
    feedback = {"role": "user", "content": [{
        "type": "tool_result", "tool_use_id": block.id, "is_error": True,
        "content": f"Validation failed: {errors}. Call record_ticket again with corrected input.",
    }]}
    print(f"attempt {attempt} rejected -> sending back: {feedback['content'][0]['content']}")

# Bounded means the residue is escalated, never retried forever.
if validated is None:
    raise RuntimeError(f"gave up after {MAX_ATTEMPTS} attempts — this goes to a human queue")

print(json.dumps(validated, indent=2))

# %% [markdown]
# ### Feature 5 — Skepticism toward confident output
#
# Claude's prose does not carry an error bar. A fluent, well-formatted, assertive
# answer looks exactly like a fluent, well-formatted, wrong one. The skill is a
# **design question**: where in the pipeline does a check catch a confident
# mistake?
#
# ```
# confident output ─────► can I verify it mechanically?
#                              │
#            ┌── yes ──────────┴────────── no ──┐
#            ▼                                  ▼
# run the check (recompute the total,    is the action reversible / low-stakes?
# re-fetch the record, execute the            │
# test, diff against source)           ┌── yes ─┴─── no ──┐
#            │                         ▼                  ▼
# mismatch → treat as an error,   log + sample-audit   human approval gate
# NOT as "the model's opinion"    (async review)       before it takes effect
# ```
#
# - **Ask for evidence, then check the evidence** — a quote with a source span, a
#   citation with a document id, a figure with the row it came from. Claude's
#   citations feature returns exact source locations for this reason.
# - **A `confidence` field is itself model output.** Use it to route (low → human),
#   never to skip verification.
# - **Cross-check with a cheaper second pass** — a Haiku call asking "does this
#   summary contain claims not in the source?" is an independent signal, not a
#   re-roll of the same one.
# - **Prefer a legal null over a plausible guess** — `["string", "null"]`, an
#   `"unknown"` enum member, and an explicit "if the document does not state it,
#   return null". The cheapest hallucination reducers there are.

# %%
# The decision tree above, as a code path — so "skepticism" is something that
# runs, not something you intend.
INVOICE = {"total": 240.0, "line_items": [{"amount": 100.0}, {"amount": 130.0}], "confidence": 0.97}

# A mechanical check EXISTS here: recompute the total from the line items. It
# outranks the model's self-reported confidence, always.
line_total = round(sum(li["amount"] for li in INVOICE["line_items"]), 2)
if line_total == INVOICE["total"]:
    print("with a mechanical check : accepted")
else:
    print(f"with a mechanical check : rejected — says {INVOICE['total']}, "
          f"items sum to {line_total}; a mismatch is an ERROR, not an opinion")

# No mechanical check available: now the confidence field ROUTES the work. It
# never excuses you from verifying — it only chooses who verifies.
for confidence in (0.97, 0.60):
    print(f"no check, confidence {confidence:.2f}:",
          "human review" if confidence < 0.9 else "log + sample-audit")

# %% [markdown]
# **Practical use:** for anything a program will consume, get the shape from a
# **schema mechanism** — a tool `input_schema` forced with `tool_choice`, or
# native structured output — then **validate the parsed object in code**, then
# **retry with the validation error as feedback**, bounded, and escalate the
# residue. Prompt wording is polish, not the load-bearing wall, and prefill is
# not an option at all on current models. And
# remember what each layer proves: a schema proves *shape*; only code proves
# *truth*.
#
# **🏋️ Practice:** an invoice extractor returns a well-formed object every time,
# and finance still finds totals that do not add up. The JSON Schema cannot
# express "total equals the sum of the line items" — only code can. The recompute
# below (marked `# <-`) catches it, so the cell passes as shipped.
#
# To feel it: delete the two `# <-` lines, re-run, and watch the assert fail as
# the bad invoice sails through validation. Put them back. Then add a second
# semantic rule of your own — reject a `due_date` earlier than the `issue_date`.

# %%
# Your turn — see the `# <-` markers.
CLEAN = {"vendor": "Acme Ltd", "total": 230.0,
         "line_items": [{"amount": 100.0}, {"amount": 130.0}]}
CONFIDENT_BUT_WRONG = {"vendor": "Acme Ltd", "total": 240.0,
                       "line_items": [{"amount": 100.0}, {"amount": 130.0}]}

found = {}
for label, inv in (("clean invoice", CLEAN), ("wrong invoice", CONFIDENT_BUT_WRONG)):
    errors = []

    # Shape first: a missing field makes every later check meaningless.
    for field in ("vendor", "total", "line_items"):
        if field not in inv:
            errors.append(f"{field}: required field missing")

    if not errors:
        if not isinstance(inv["total"], (int, float)):
            errors.append("total: must be a number")

        computed = round(sum(li["amount"] for li in inv["line_items"]), 2)   # <- recompute, never trust
        if computed != inv["total"]:                                         # <- and compare
            errors.append(f"total: says {inv['total']}, line items sum to {computed}")

    found[label] = errors
    print(f"{label} :", errors or "no errors")

assert not found["clean invoice"], "a correct invoice must pass"
assert found["wrong invoice"], "the arithmetic error slipped through — recompute it"
print("\nthe schema proved the shape; the code proved the arithmetic")
