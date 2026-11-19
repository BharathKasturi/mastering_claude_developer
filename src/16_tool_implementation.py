# %% [markdown]
# # Topic 6.1 · Tool Implementation: Schemas, Descriptions, Errors, Dispatch & Approval Patterns
#
# 🎓 *CCDV-F objective 16 of 25 · exam weight 4.4% · API lesson*
#
# **Why this matters.** A tool is not a function you expose. It is a **contract
# you write for a reader who cannot ask a follow-up question.** The model picks a
# tool by reading its name, description and schema, and it recovers from a
# failure by reading whatever you put in the `tool_result`. Both of those are
# yours to write, so a tool that misfires is almost always a documentation bug —
# never a reason for a longer system prompt.
#
# **What you'll learn.**
# - Write the three fields that decide everything: `name`, `description`, `input_schema`.
# - Answer a `tool_use` block correctly — `tool_use_id`, verbatim echo, one user message.
# - Return errors instead of raising them, with `is_error` and an actionable message.
# - Dispatch through a harness: registry → validate → authorize → approval gate → audit.
# - Tell a **client-side** tool from a **server-side** one by who produces the result.
# - Build a tool *set*: consolidate, scope, and cache it with `cache_control`.
#
# **What you need.** Nothing. Every cell runs offline; the wire shapes are built
# as plain dicts you can print. Set `ANTHROPIC_API_KEY` to make Feature 2's last
# cell call the real Messages API instead of printing a skip note.

# %%
# Load the shared setup: the API client and MODEL.
%run ./00_setup.ipynb

# %% [markdown]
# ### Feature 1 — The contract: `name`, `description`, `input_schema`
#
# Every tool you define is three fields. They are the highest-leverage prose in
# the whole application, because they are what the model reads at the moment it
# chooses.
#
# - **`name`** — name for *discrimination*, not brevity. Overlapping names are
#   the commonest cause of wrong-tool selection. Prefix by service:
#   `crm_search_contacts`, `github_create_issue`.
# - **`description`** — three or four sentences: what it does, **when to use it**,
#   **when NOT to** (naming the sibling tool), what it returns, and its limits.
# - **`input_schema`** — a JSON Schema. Constrain *here*, not in prose. `enum`,
#   `required`, types and formats are enforced structurally. A sentence asking
#   for ISO dates is only a suggestion.
#
# Two salads on a menu with the same one-line blurb go to the wrong station all
# night. Distinct names plus "use the other one when …" fixes more mistakes than
# any amount of system prompt.

# %%
# The same capability written twice. Read them side by side — the difference is
# the whole skill.
weak_tool = {
    "name": "search",                        # collides with every other search
    "description": "Searches the database.", # says nothing about WHEN
    "input_schema": {
        "type": "object",
        "properties": {"q": {"type": "string"}, "status": {"type": "string"}},
        # nothing required, nothing constrained -> the model guesses
    },
}

strong_tool = {
    "name": "crm_search_contacts",           # namespaced, unambiguous
    "description": (
        "Search CRM contacts by free-text name, email or company. "
        "Use this when you do NOT already have a contact ID. "
        "Do NOT use it to fetch a known ID — use crm_get_contact for that. "
        "Returns at most 50 rows: {id, name, email, company, last_seen}."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string",
                      "description": "Free-text name or email. Not an ID — "
                                     "use crm_get_contact for a known ID."},
            "status": {"type": "string", "enum": ["open", "closed", "pending"]},
            "start_date": {"type": "string",
                           "description": "ISO-8601 date, e.g. 2026-09-03"},
            "limit": {"type": "integer", "description": "1-50, default 20"},
        },
        "required": ["query"],               # say what is mandatory
    },
}

# Score both definitions against the rules the exam keeps asking about. The six
# checks are written out in place: they ARE the checklist, so read them here
# rather than scrolling to a helper.
for label, tool in (("weak", weak_tool), ("strong", strong_tool)):
    props = tool["input_schema"].get("properties", {})
    problems = []

    if "_" not in tool["name"]:
        problems.append("name is not namespaced — collides with sibling tools")
    if len(tool["description"].split(".")) < 4:
        problems.append("description is too thin — say when to use, when not to, what it returns")
    if "not use" not in tool["description"].lower():
        problems.append("description never names the sibling tool to prefer instead")
    if not tool["input_schema"].get("required"):
        problems.append("no `required` list — the model may omit key arguments")
    if not any("description" in p for p in props.values()):
        problems.append("no per-property descriptions — arguments will be guessed")
    if not any("enum" in p for p in props.values()):
        problems.append("no `enum` anywhere — free-text where a fixed set belongs")

    print(f"--- {label}: {tool['name']}  ({len(problems)} problems)")
    for p in problems:
        print("     -", p)
    if not problems:
        print("     (clean)")

# %% [markdown]
# ### Feature 2 — The loop: answering a `tool_use` block
#
# The wire shape is small and the exam tests three facts about it.
#
# 1. **Echo the assistant turn back verbatim.** Append `resp.content` unchanged.
#    Summarising or rebuilding it breaks the `tool_use` ids.
# 2. **Answer every block, each by its own `tool_use_id`, in ONE user message.**
#    A turn can contain several `tool_use` blocks — that is parallel tool use.
#    Answering only the first one is a classic bug.
# 3. **`tool_choice` controls the model's freedom.** `{"type": "auto"}` is the
#    default when tools are present; `{"type": "any"}` forces *some* tool;
#    `{"type": "tool", "name": …}` forces one; `{"type": "none"}` blocks them.
#    Add `"disable_parallel_tool_use": True` inside it to get one call per turn.
#
# The loop ends on `stop_reason` — read the field, never the model's words.

# %%
# Build the exact messages list a real loop would build, offline, so you can read
# the shapes. `assistant_turn` is what came back from the API. With a real
# response these blocks are SDK objects and you read `block.type`, `block.id`,
# `block.name`, `block.input` — the same four fields, dotted instead of keyed.
assistant_turn = [                                    # what resp.content looks like
    {"type": "text", "text": "Let me look both of those up."},
    {"type": "tool_use", "id": "toolu_01A", "name": "crm_search_contacts",
     "input": {"query": "ada@acme.io"}},
    {"type": "tool_use", "id": "toolu_01B", "name": "crm_search_contacts",
     "input": {"query": "grace@acme.io"}},            # parallel: TWO blocks, one turn
]

# Rule 2 in code: walk every block, answer each tool_use by its own tool_use_id,
# and collect them all so they can go back in a SINGLE user message.
results = []
for block in assistant_turn:
    if block["type"] != "tool_use":                   # text blocks need no answer
        continue
    # Pretend lookup. The point is the envelope, not the payload.
    payload = {"id": "c_77", "email": block["input"]["query"], "company": "Acme"}
    results.append({
        "type": "tool_result",
        "tool_use_id": block["id"],                   # ties this result to that request
        "content": json.dumps(payload),
    })

messages = [{"role": "user", "content": "Find ada@acme.io and grace@acme.io."}]
messages.append({"role": "assistant", "content": assistant_turn})   # VERBATIM echo
messages.append({"role": "user", "content": results})               # ONE message

print(json.dumps(messages[-1], indent=2)[:520])
print(f"\n{len([b for b in assistant_turn if b['type'] == 'tool_use'])} tool_use blocks "
      f"-> {len(messages[-1]['content'])} tool_result blocks in 1 user message")

# %%
# The same loop against the real API. Offline it prints a skip note; with a key it
# runs for real. Note tools=, tool_choice=, the verbatim echo, and the cap on turns.
max_steps = 4                                  # a confused loop must not run forever

if client is None:
    print("[skipped — set ANTHROPIC_API_KEY to run the live loop]")
else:
    convo = [{"role": "user", "content": "Who is ada@acme.io in our CRM?"}]
    answer = "[stopped: hit max_steps — a real loop needs this cap]"

    for _ in range(max_steps):
        resp = client.messages.create(
            model=MODEL,
            max_tokens=600,
            tools=[strong_tool],
            tool_choice={"type": "auto"},      # the default; shown here to be explicit
            messages=convo,
        )
        if resp.stop_reason != "tool_use":     # read the field, not the words
            answer = "".join(b.text for b in resp.content if b.type == "text")
            break

        convo.append({"role": "assistant", "content": resp.content})   # verbatim
        convo.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": b.id,
             "content": json.dumps({"id": "c_77", "name": "Ada L.", "company": "Acme"})}
            for b in resp.content if b.type == "tool_use"
        ]})

    print(answer)

# %% [markdown]
# ### Feature 3 — Errors: return them, don't raise them
#
# An exception in your harness is **invisible to the model.** The model only
# learns what you put in the `tool_result`. So a failure becomes a normal result
# carrying `"is_error": True` and a message specific enough to act on.
#
# Two failure modes to name:
#
# - **Swallowing the error and returning `""`.** Emptiness reads as success. The
#   model proceeds on nothing and then tells the user the booking worked.
# - **Returning a raw traceback.** It leaks paths and connection strings, and
#   burns tokens. Log the detail on *your* side; send a clean sentence.
#
# | error class | what the `tool_result` should say | what the model does next |
# |---|---|---|
# | bad arguments | the field, the expectation, an example | re-calls with a fixed input |
# | not found | plus the tool that finds the right ID | calls the lookup tool first |
# | permission denied | "do not retry" + what the user must do | stops and tells the user |
# | transient / rate limit | "retry after N" or "try a smaller page" | backs off, retries once |
# | internal bug | generic, no stack, no secrets | reports it instead of looping |
#
# Cap the size too. An unbounded result is the fastest way to blow the context
# window — truncate with a marker and offer a page cursor.

# %%
# One dispatch, written straight through: every path ends in a tool_result and
# nothing escapes without a tool_use_id or a size cap.
REGISTRY = ["book_room", "crm_search_contacts"]   # the tools this harness can run
ROOMS = {"Blue": "seats 6", "Green": "seats 12"}  # a pretend booking system
MAX_CHARS = 4000                                  # cap: never blow the context window

requests = [
    {"type": "tool_use", "id": "toolu_01C", "name": "book_room",
     "input": {"date": "03/09/26", "room": "Blue"}},          # bad arguments
    {"type": "tool_use", "id": "toolu_01D", "name": "book_room",
     "input": {"date": "2026-09-03", "room": "Mauve"}},       # not found
    {"type": "tool_use", "id": "toolu_01E", "name": "delete_everything",
     "input": {}},                                            # unknown tool
    {"type": "tool_use", "id": "toolu_01G", "name": "book_room",
     "input": {"date": "2026-09-03", "room": "Blue"}},        # the happy path
]

for req in requests:
    args = req["input"]

    if req["name"] not in REGISTRY:
        # Unknown tool: say what IS available so the model can pick again.
        content = f"Unknown tool {req['name']!r}. Available: {sorted(REGISTRY)}"
        is_error = True
    elif not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.get("date", "")):
        # Bad arguments: name the field, say what is acceptable, give an example.
        content = ("Invalid input: date — expected ISO-8601 date, e.g. 2026-09-03. "
                   f"Received {args.get('date')!r}.")
        is_error = True
    elif args["room"] not in ROOMS:
        # Not found: point at the tool that finds the right identifier.
        content = (f"No room named {args['room']!r}. "
                   "Call list_rooms first to get a valid room name.")
        is_error = True
    else:
        try:
            content = json.dumps({"booked": True, "date": args["date"],
                                  "room": args["room"], "size": ROOMS[args["room"]]})
            is_error = False
        except Exception:
            # Last line of defence: an internal bug is OURS, and it must still
            # leave as a tool_result. Full detail to YOUR logs; a generic
            # sentence to the model. No stack trace, no paths, no secrets.
            content = f"Internal error in {req['name']}. Try again or report it."
            is_error = True

    if len(content) > MAX_CHARS:
        content = content[:MAX_CHARS] + f"\n…[truncated at {MAX_CHARS} chars — narrow the query]"

    result = {"type": "tool_result", "tool_use_id": req["id"], "content": content}
    if is_error:
        result["is_error"] = True             # the field that says "this attempt FAILED"
    print(f"{req['name']:18s} {'ERROR' if is_error else 'ok   '}  {result['content'][:88]}")

# The anti-pattern, for contrast: the same failure swallowed into an empty string.
swallowed = {"type": "tool_result", "tool_use_id": "toolu_01C", "content": ""}
print("\nSWALLOWED:", swallowed, " <- reads as success; the model books nothing and says it did")

# %% [markdown]
# ### Feature 4 — Harness dispatch and approval gates
#
# The dispatch point is the only place you can actually enforce anything, because
# it is the only code the model does not write. Six steps, in order:
#
# 1. look the name up in the registry — unknown becomes an error result,
# 2. validate the input against the schema,
# 3. **authorize against the authenticated identity**, not a claim in the input,
# 4. classify: read-only runs unattended, side-effecting stops at an approval gate,
# 5. apply a timeout and a size cap,
# 6. write an audit row: who, tool, object, approver.
#
# Approval patterns, weakest to strongest:
#
# | strength | control | inspects |
# |---|---|---|
# | weakest | a sentence in the system prompt | nothing — it is not a control |
# | | `tool_choice` / never offering the tool | the tool set |
# | | a permission rule on the tool name | the name |
# | | a `PreToolUse` hook or `can_use_tool` callback | the **arguments** |
# | strongest | human confirmation plus an audit row | arguments, identity, money |
#
# A denial must return a **message**, not silence. Told why it was refused, the
# model proposes a legitimate alternative instead of retrying variations.
#
# And the trap: a policy limit does **not** belong in the schema.
# `{"max_amount": 100}` is an input the model supplies — it is not a control.

# %%
# The gate, in line. Note that the amount limit is arithmetic in OUR code, checked
# against the authenticated caller — never a sentence in the prompt or a schema field.
READ_ONLY = {"crm_search_contacts", "book_room"}
SIDE_EFFECTING = {"issue_refund", "send_email", "delete_record"}
LIMITS = {"agent": 100.0, "supervisor": 5000.0}     # dollars per role
AUDIT = []                                          # your audit log, one row per call

for actor_role, amount in (("agent", 40.0), ("agent", 4000.0), ("supervisor", 4000.0)):
    req = {"type": "tool_use", "id": "toolu_01H", "name": "issue_refund",
           "input": {"amount": amount, "order": "A-1001"}}
    ceiling = LIMITS.get(actor_role, 0.0)           # from the IDENTITY, not the input

    if req["name"] in READ_ONLY:
        behavior, message = "allow", "read-only, runs unattended"
    elif req["name"] not in SIDE_EFFECTING:
        behavior = "deny"
        message = (f"{req['name']} is not in this agent's tool set. Choose one of: "
                   + ", ".join(sorted(READ_ONLY | SIDE_EFFECTING)))
    elif float(req["input"].get("amount", 0)) > ceiling:
        # Refuse with a REASON so the model can propose something legitimate.
        behavior = "ask"
        message = (f"${amount:,.2f} exceeds the ${ceiling:,.0f} limit for role "
                   f"{actor_role!r}. Routed to a human approver.")
    else:
        behavior, message = "allow", f"within the ${ceiling:,.0f} limit"

    # Step 6: one audit row per call — who, which tool, what object, what verdict.
    AUDIT.append({"actor": actor_role, "tool": req["name"], "object": req["input"],
                  "verdict": behavior, "approver": None})

    # Allowed calls run and return their payload; everything else returns the
    # refusal as a tool_result, marked is_error, so the model can read it.
    content = json.dumps({"ok": True}) if behavior == "allow" else message
    print(f"{actor_role:11s} ${amount:>8,.2f} -> {behavior:5s} {content}")

print(f"\naudit rows written: {len(AUDIT)}")

# %% [markdown]
# ### Feature 5 — Client-side vs server-side tools
#
# One question separates them: **who is responsible for producing the
# `tool_result`?**
#
# | dispatch model | who executes | what you send back |
# |---|---|---|
# | **client-side tool** (your function) | your harness | a `tool_result` with the same `tool_use_id` |
# | **server-side tool** (web search, code execution) | Anthropic's infrastructure | nothing — the result arrives in the same response |
# | **MCP tool via a client** (Claude Code) | the MCP server process | the MCP client handles it |
# | **MCP tool via the API connector** | the remote MCP server | the connector handles it |
# | **text editor / bash / computer use** | your harness | a `tool_result` you produce |
#
# The rule: if the work touches **your** systems or must be **gated**, it is a
# client-side tool — you dispatch it and you can refuse it. If it is generic
# capability Anthropic already runs safely, prefer the server-side tool: fewer
# round trips, no key to manage, no result-formatting code.
#
# Note the trap in the last row. `bash`, `text_editor` and `computer` are
# built-in **schemas** that *you* still execute. "Built-in" does not mean
# "Anthropic runs it".

# %%
# The two shapes side by side. A server-side tool is a dated `type` plus its fixed
# `name`; a client-side tool is your own name/description/input_schema.
server_side = {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}

# The hosted set is small and closed. Everything else — your own tools AND the
# client-side built-ins — comes back to your harness for a tool_result.
HOSTED = {"web_search", "web_fetch", "code_execution"}

for tool in (server_side,
             {"type": "bash_20250124", "name": "bash"},
             {"type": "text_editor_20250728", "name": "str_replace_based_edit_tool"},
             strong_tool):
    who = "Anthropic (server-side)" if tool["name"] in HOSTED else "you (client-side)"
    print(f"{tool['name']:32s} -> {who}")

# %% [markdown]
# ### Feature 6 — Building the tool *set*
#
# Tool definitions sit at the front of every request and are re-billed on every
# turn. So the set is a design object, not a pile.
#
# | symptom | cause | fix |
# |---|---|---|
# | picks the wrong one of two similar tools | overlapping name/description | rename + "use X instead" |
# | calls `list_users` then filters in text | tools mirror your REST endpoints 1:1 | one tool that does the *task* |
# | burns turns paging results | no `limit` / filter / cursor | add them, return only useful fields |
# | confused with 30 tools loaded | everything registered at once | scope the set per task or subagent |
# | huge tool block re-billed each turn | no cache breakpoint | `cache_control` on the **last** tool |
# | hallucinates an argument | under-specified schema | `enum` + `required` + examples |
#
# Build around **the workflow, not the API surface**. One `schedule_meeting` that
# looks up availability *and* books beats four thin wrappers the model must
# chain: every hop is a round trip and a chance to lose the thread.

# %%
# Two habits in one cell: scope the set per role, then cache it.
ALL_TOOLS = {
    "crm_search_contacts": strong_tool,
    "crm_get_contact":     {"name": "crm_get_contact", "description": "Fetch one contact by ID.",
                            "input_schema": {"type": "object",
                                             "properties": {"id": {"type": "string"}},
                                             "required": ["id"]}},
    "issue_refund":        {"name": "issue_refund", "description": "Refund an order.",
                            "input_schema": {"type": "object",
                                             "properties": {"order": {"type": "string"},
                                                            "amount": {"type": "number"}},
                                             "required": ["order", "amount"]}},
    "send_email":          {"name": "send_email", "description": "Send an email.",
                            "input_schema": {"type": "object",
                                             "properties": {"to": {"type": "string"}},
                                             "required": ["to"]}},
}
ROLES = {"researcher": ["crm_search_contacts", "crm_get_contact"],
         "support":    ["crm_get_contact", "issue_refund", "send_email"]}

# Assemble each role's `tools` list, then put ONE cache breakpoint on the last
# tool so the whole stable block in front of it is reused instead of re-billed.
for role, names in ROLES.items():
    picked = [dict(ALL_TOOLS[n]) for n in names]     # copy: never mutate the source
    picked[-1]["cache_control"] = {"type": "ephemeral"}
    marked = [t["name"] for t in picked if "cache_control" in t]
    print(f"{role:11s} {len(picked)} tools {[t['name'] for t in picked]}  cached-through: {marked}")

print(f"\nregistering all {len(ALL_TOOLS)} for every request would ship "
      f"{len(ALL_TOOLS) - min(len(v) for v in ROLES.values())} extra definitions to the smaller role")

# %% [markdown]
# **Practical use:** treat the tool definition and the tool result as the two
# pieces of prose the model actually reads. Name for discrimination, describe
# when *not* to use it, constrain in the schema. Then never raise: every failure
# comes back as a `tool_result` with `is_error: true` and a sentence specific
# enough to retry on. Enforce policy at dispatch against the authenticated
# identity — never in the prompt, never in the schema.
#
# **🏋️ Practice:** a booking tool rejects `"03/09/26"`. The harness must hand the
# model an error it can act on: marked as an error, naming the field, and showing
# an acceptable example. The fix is marked `# <-` below and is already in place,
# so the cell passes as shipped.
#
# To feel it: delete the `EXAMPLE` from `HINT`, re-run, and watch the assert fail —
# that is the generic-error case, where the model has nothing to correct with.
# Put it back. Then add a second failure class of your own (say a rate limit) and
# assert that its message tells the model how long to back off.

# %%
# Your turn — see the `# <-` marker.
EXAMPLE = "2026-09-03"                                    # <- the concrete example is the fix
HINT = f"expected ISO-8601, e.g. {EXAMPLE}"

# The request as the model sent it, and the tool_result you hand back.
req = {"type": "tool_use", "id": "toolu_01F", "name": "book_room",
       "input": {"date": "03/09/26", "room": "Blue"}}

err = {
    "type": "tool_result",
    "tool_use_id": req["id"],                             # always tie it to the request
    "content": f"Invalid input: date — {HINT}. Received {req['input']['date']!r}.",
    "is_error": True,                                     # says THIS ATTEMPT FAILED
}
print(json.dumps(err, indent=2))

assert err["is_error"] is True, "the model cannot tell this failed"
assert err["tool_use_id"] == req["id"], "result is not tied to the request"
assert "date" in err["content"], "the message does not name the offending field"
assert EXAMPLE in err["content"], "no acceptable example — the model cannot retry correctly"
print("\nactionable error: marked, tied to the request, names the field, shows the format")
