# %% [markdown]
# # Topic 4.1 · Understanding Requirements: Functional & Infrastructure
#
# 🎓 *CCDV-F objective 9 of 25 · exam weight 3.4% · API lesson*
#
# **Why this matters.** A business requirement — "cut ticket handling time in
# half" — never names an API. The skill is translation: business need →
# **functional requirements** (what the system must do) → **infrastructure
# requirements** (latency, throughput, availability, residency, budget). Only then
# do you get to name a model or an API mode. In review, an architecture choice you
# cannot trace back to a requirement row is a guess.
#
# **What you'll learn.**
# - Turn a business brief into **functional** rows and **infrastructure** rows.
# - Read a stem for the **five levers**: latency, throughput, availability, residency, budget.
# - Choose **sync**, **sync + streaming**, or the **Batch API** from *who is waiting*.
# - Turn an availability requirement into code: retries, timeout, fallback route.
# - Treat **residency**, **PII** and **human approval** as design constraints, not afterthoughts.
# - Let the requirement pick the **model tier** — or route between two.
#
# **What you need.** Nothing. Every request payload is built and printed locally;
# the live calls are guarded. Set `ANTHROPIC_API_KEY` to send them for real.

# %%
# Load the shared setup: the API client and MODEL.
%run ./00_setup.ipynb

# %% [markdown]
# ### Feature 1 — Business → functional → infrastructure
#
# This is the exam's chain. Practise turning each sentence of a stem into a row.
#
# ```
#    BUSINESS      "Reduce the support backlog; customers see a first reply within
#                   a minute; we operate in the EU and Brazil; agents must approve
#                   any refund."
#         │
#         ▼
#    FUNCTIONAL    F1  classify incoming tickets by intent and urgency
#    (what it does) F2  draft a first reply in the customer's language
#                   F3  propose (not execute) refunds for agent approval
#                   F4  summarise the thread for the human agent
#         │
#         ▼
#    INFRA          I1  latency: first reply < 60 s      → sync, streaming to the UI
#    (how / limits) I2  throughput: 40k/day, peak 20/s   → rate-limit plan, caching
#                   I3  residency: EU + BR stay in-region → region-pinned endpoints
#                   I4  HITL: refunds gated on approval   → approval state in workflow
#                   I5  availability: 99.9%               → retries, fallback model
#                   I6  budget: ≤ $0.02/ticket            → Sonnet drafts, Haiku classify
# ```
#
# **The functional list has no model names in it, and it should not.** Model,
# endpoint and API mode belong in the infrastructure rows, each justified by a
# constraint. When an answer option names a model, ask *which infra row justifies
# it*. The correct option is the one with a justification.

# %%
# The brief, split into two lists. Each row is a plain dict: an id you can cite in
# review, the requirement in the business's own words, the lever it pulls, and the
# platform choices it selects. Notice that nothing in FUNCTIONAL could be swapped
# for a different vendor's product — that is how you know it is functional.
FUNCTIONAL = [
    {"rid": "F1", "text": "classify incoming tickets by intent and urgency"},
    {"rid": "F2", "text": "draft a first reply in the customer's language"},
    {"rid": "F3", "text": "propose (not execute) refunds for agent approval"},
    {"rid": "F4", "text": "summarise the thread for the human agent"},
]

INFRA = [
    {"rid": "I1", "text": "first reply within 60 s", "lever": "latency",
     "implies": ["sync Messages API", "streaming to the agent UI"]},
    {"rid": "I2", "text": "40k tickets/day, peak 20/s", "lever": "throughput",
     "implies": ["rate-limit tier", "prompt caching on the policy prefix"]},
    {"rid": "I3", "text": "EU and BR data stays in-region", "lever": "residency",
     "implies": ["region-pinned Bedrock/Vertex endpoint"]},
    {"rid": "I4", "text": "an agent approves every refund", "lever": "hitl",
     "implies": ["approval state in the workflow", "no bypassPermissions"]},
    {"rid": "I5", "text": "99.9% in business hours", "lever": "availability",
     "implies": ["SDK retries + timeout", "fallback route"]},
    {"rid": "I6", "text": "at most $0.02 per ticket", "lever": "budget",
     "implies": ["Haiku to classify", "Sonnet to draft", "cache the 3k-token prefix"]},
]

print("FUNCTIONAL — what the system must do")
for r in FUNCTIONAL:
    print(f"  {r['rid']:3s} {r['text']}")

print("\nINFRASTRUCTURE — the constraints on how")
for r in INFRA:
    print(f"  {r['rid']:3s} {r['text']}  [{r['lever']}] -> {', '.join(r['implies'])}")

# %%
# The trace that makes a design reviewable: for every platform choice, find the
# row that justifies it. A choice with no row is a guess, and saying so out loud
# is the whole discipline.
print("why this choice? ->  the requirement row that justifies it\n")
for choice in ["streaming", "Haiku", "Opus"]:
    justification = "UNJUSTIFIED — this is a guess"
    for r in INFRA:
        if any(choice.lower() in implication.lower() for implication in r["implies"]):
            justification = f"{r['rid']} ({r['lever']}): {r['text']}"
            break
    print(f"{choice:10s} -> {justification}")

# %% [markdown]
# ### Feature 2 — The five levers, and what each one selects
#
# ```
#    LEVER          STEM SIGNAL                      SELECTS
#    ────────────   ──────────────────────────────   ────────────────────────────────
#    latency        "under N seconds", "interactive" sync API; streaming; smaller model;
#                                                    prompt caching; low effort
#    throughput     "N per day/second", "peak"       rate-limit tier; batch when nothing
#                                                    waits; caching; Haiku
#    availability   "99.9%", "SLA", "no downtime"    SDK retries + timeouts; fallback
#                                                    model or route; idempotency
#    residency /    "EU only", "our VPC", "HIPAA",   region-pinned Bedrock/Vertex/Foundry;
#    compliance     "FedRAMP", "no training"         inference_geo; PII redaction; ZDR
#    budget         "cost primary", "$ per item"     batch (~50%); caching; model tier;
#                                                    max_tokens; effort level
# ```
#
# The classic error is reading **volume as speed**. "Process 10,000 documents" is
# throughput, not latency. Ask *is anyone waiting for each one?* A fast model does
# not help when you need twelve of them.

# %%
# Classify a requirement sentence into the lever it pulls. Five levers, no more.
# A sentence that names no limit at all is functional, not infrastructure.
LEVER_SIGNALS = {
    "latency":      ("under", "seconds", "interactive", "typing", "p95", "real time"),
    "throughput":   ("per day", "per second", "peak", "millions", "volume", "10,000"),
    "availability": ("99.", "sla", "downtime", "outage", "always up"),
    "residency":    ("eu", "region", "vpc", "hipaa", "fedramp", "residency", "training"),
    "budget":       ("cost", "cheapest", "$", "budget", "per item"),
}

SENTENCES = [
    "agents answer in under 2 seconds while the user is typing",
    "10,000 documents by tomorrow morning, cheapest possible",
    "99.9% available with no single vendor dependency",
    "data must not leave the EU",
    "the system drafts a reply to each ticket",
]

for sentence in SENTENCES:
    words = sentence.lower()
    lever = "functional"
    for name, signals in LEVER_SIGNALS.items():
        if any(signal in words for signal in signals):
            lever = name
            break
    print(f"{lever:13s} <- {sentence}")

# %% [markdown]
# ### Feature 3 — Sync, streaming or batch: *who is waiting?*
#
# | Requirement says | Mode | Why |
# |---|---|---|
# | a person or request is blocked on the answer | **sync** | seconds, not hours |
# | …and the answer is long enough to notice | **sync + streaming** | perceived latency is time-to-**first** token |
# | nothing is blocked, results due within a day, cost matters | **batch** | ~50% cheaper, higher throughput, no latency SLA (24 h bound) |
# | interactive today **and** a backfill of history | **both** | same prompt, two transports |
#
# The Batch API takes up to 100k requests (256 MB) per batch, each with a
# `custom_id` and a `params` block that is an ordinary Messages request. Results
# come back keyed by `custom_id`, within 24 hours.

# %%
# The decision, made from the requirement rather than from habit. Batch is only
# reachable when NOTHING waits and the 24 h bound fits.
TRANSPORT_CASES = [
    ("nobody", 11, True, False, "25k archived articles tagged overnight"),
    ("person", 0.05, False, True, "support agent watches a 300-word draft appear"),
    ("system", 0.8, False, False, "intent label before the message is routed"),
]

for who_waits, deadline_hours, cost_primary, answer_is_long, story in TRANSPORT_CASES:
    if who_waits == "nobody" and deadline_hours >= 1 and cost_primary:
        mode = "batch"
    elif who_waits == "person" and answer_is_long:
        mode = "sync + streaming"
    else:
        mode = "sync"
    print(f"{mode:17s} <- {story}")

# %%
# The streaming shape: a system prefix marked for caching, then text as it lands.
# Perceived latency is time-to-FIRST-token, which is what streaming buys you.
POLICY = "You are a support assistant. Follow the refund policy below.\n" + ("…" * 20)

if client is None:
    print("[skipped the live stream — set ANTHROPIC_API_KEY]")
    print("with client.messages.stream(model=MODEL, max_tokens=2000,")
    print("        system=[{'type': 'text', 'text': POLICY,")
    print("                 'cache_control': {'type': 'ephemeral'}}],")
    print("        messages=[{'role': 'user', 'content': ticket}]) as stream:")
    print("    for text in stream.text_stream: ui.append(text)   # time-to-FIRST-token")
else:
    # A cache breakpoint needs a long enough prefix to be worth caching, so a
    # toy POLICY may be rejected. Report that rather than kill the notebook.
    try:
        with client.messages.stream(
                model=MODEL, max_tokens=200,
                system=[{"type": "text", "text": POLICY,
                         "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": "My order never arrived."}]) as stream:
            for text in stream.text_stream:
                print(text, end="")
        print()
    except Exception as exc:
        print(f"[live stream refused: {exc}]")

# %%
# The batch shape. Build the payload offline so you can read the real fields:
# a list of requests, each a `custom_id` plus a `params` Messages request.
overnight_tickets = [{"id": f"t-{i}", "body": f"ticket body {i}"} for i in range(3)]

batch_requests = [
    {"custom_id": t["id"],
     "params": {"model": MODEL, "max_tokens": 200, "system": POLICY,
                "messages": [{"role": "user", "content": t["body"]}]}}
    for t in overnight_tickets
]
print(json.dumps(batch_requests[0], indent=2)[:400], "…\n")

if client is None:
    print("[skipped the live batch — set ANTHROPIC_API_KEY]")
    print("batch = client.messages.batches.create(requests=batch_requests)")
    print("# poll batch.processing_status until 'ended'; results keyed by custom_id")
    print("# limits: up to 100k requests / 256 MB per batch, ≤ 24 h, ~50% cost")
else:
    try:
        batch = client.messages.batches.create(requests=batch_requests)
        print("submitted:", batch.id, batch.processing_status)
    except Exception as exc:
        print(f"[live batch refused: {exc}]")

# %% [markdown]
# ### Feature 4 — Availability, residency and HITL become code
#
# The availability row is the one most often left as a wish. It becomes retries, a
# timeout, and a fallback route you can name. Note what the fallback costs: a
# second provider is a second cache namespace and possibly a different feature
# set. Write the trade down — the exam asks which requirement you sacrificed.
#
# ```
#    REQUIREMENT                     DESIGN CONSEQUENCE
#    ─────────────────────────────   ────────────────────────────────────────────
#    "PHI / PII in the tickets"      redact BEFORE the call; log nothing raw; ZDR terms
#    "must stay in AWS eu-central"   Bedrock/Vertex/Foundry in that region — and verify
#                                    the feature (batch, caching, tools) exists there
#    "auditable decisions"           workflow over agent; log request id + model version;
#                                    pin the model
#    "a human approves refunds"      the model PROPOSES via a tool call; execution waits
#                                    on an approval state
#    "no vendor lock-in"             same Messages shape behind an SDK abstraction
# ```
#
# Residency changes the **constructor**, not the call. Encrypting the payload does
# not satisfy a rule about *where inference runs*.

# %%
# Availability, as code. `max_retries` and `timeout` are real client constructor
# parameters; the fallback route is yours to write.
ticket = "my card was charged twice"
request = {"max_tokens": 20,
           "system": "Reply with one label: billing, shipping, other.",
           "messages": [{"role": "user", "content": ticket}]}

fallback = None       # a second client — another region, or another provider

if client is None:
    print("[skipped — set ANTHROPIC_API_KEY]  primary = Anthropic(max_retries=4, "
          "timeout=30.0); on APIStatusError/APIConnectionError, retry on `fallback`")
else:
    try:
        label = client.messages.create(model=MODEL, **request).content[0].text
    except Exception as exc:               # APIStatusError / APIConnectionError
        if fallback is None:
            label = f"[primary failed and no fallback configured: {exc}]"
        else:
            label = fallback.messages.create(model=MODEL, **request).content[0].text
    print("label:", label)

# Residency: the requirement picks the constructor. Same messages.create() after.
RESIDENCY = {
    "EU, on AWS":      'AnthropicBedrockMantle(aws_region="eu-central-1")',
    "EU, on GCP":      'AnthropicVertex(project_id="acme-eu", region="europe-west4")',
    "first-party":     'Anthropic()  # plus inference_geo, if the terms satisfy legal',
}
print()
for need, constructor in RESIDENCY.items():
    print(f"{need:14s} -> {constructor}")

# %%
# HITL: the model proposes, a human gate executes. This is a state machine, not a
# prompt, and `bypassPermissions` in place of it is a requirement violation.
proposal = {"tool": "issue_refund", "input": {"amount": 42.50}}   # the model's request

print()
for approved_by in [None, "agent_17"]:
    if approved_by is None:
        print(f"PENDING approval — proposed {proposal}")
    else:
        print(f"EXECUTED {proposal} approved_by={approved_by}")

# %% [markdown]
# ### Feature 5 — The requirements sheet, and the model tier it picks
#
# ```
#    REQUIREMENT SHEET — <feature>
#    ───────────────────────────────────────────────────────────
#    who waits?           none | a system | a person   → batch | sync | sync+stream
#    volume / peak        ___ per day, ___ per s       → tier, caching, model
#    deadline             ___ (headroom vs 24 h)       → batch viable? y/n
#    availability target  ___ %                        → retries, fallback
#    region / residency   ___                          → endpoint, inference_geo
#    data classes         PII | PHI | none             → redaction, ZDR, logging policy
#    human gate           where: ___                   → approval state
#    budget               $ ___ per item               → model tier, max_tokens, batch
#    quality bar          ___ (eval metric)            → model tier, thinking/effort
# ```
#
# ```
#    quality-critical, complex reasoning, low volume  ──► Opus (thinking, effort high)
#    balanced production workload                     ──► Sonnet
#    high volume, simple, latency- or cost-bound      ──► Haiku
#    mixed                                            ──► ROUTE: Haiku classifies,
#                                                        Sonnet/Opus takes the hard bucket
# ```
#
# A stem that says "cost is primary" **and** "accuracy on hard cases matters" is
# asking for **routing**, not for one model.

# %%
# Fill the sheet, and let the sheet make the decisions. Every line below reads a
# field off the sheet — nothing here is invented.
sheet = {
    "who_waits": "person", "per_day": 40_000, "peak_per_s": 20,
    "deadline_hours": 0.02, "availability": 99.9, "region": "eu-central-1",
    "data_classes": ["PII"], "human_gate": "refunds", "budget_per_item": 0.02,
    "quality_bar": "92% label accuracy",
}

decisions = {}

if sheet["who_waits"] == "person":
    decisions["transport"] = "sync + streaming"
elif sheet["who_waits"] == "nobody":
    decisions["transport"] = "batch"
else:
    decisions["transport"] = "sync"

decisions["endpoint"] = (f"region-pinned endpoint in {sheet['region']}"
                         if sheet["region"] else "first-party, any region")
decisions["reliability"] = f"retries + timeout + fallback for {sheet['availability']}%"
decisions["privacy"] = ("redact before the call, log nothing raw"
                        if sheet["data_classes"] else "standard logging")
decisions["hitl"] = f"approval state on: {sheet['human_gate']}"

# Budget and quality together are a ROUTING signal, not a single-model one.
decisions["model"] = ("route: Haiku classifies, Sonnet handles the hard bucket"
                      if sheet["budget_per_item"] < 0.05 else "Sonnet for everything")

for choice, why in decisions.items():
    print(f"{choice:12s} -> {why}")

# %% [markdown]
# **Practical use:** before naming a model or an API mode, answer five questions —
# **who is waiting, how many, where must it run, who must approve, what may it
# cost?** Sync, streaming, batch, model tier and endpoint all fall out of those
# answers. Then keep the trace: every choice in your design should cite the
# requirement row that justifies it, and any choice that cannot is a guess a
# reviewer will find.
#
# **🏋️ Practice:** a stem says *"25,000 archived articles, tagged by 7 am, cost is
# the primary driver"* — nobody is waiting, so it must select **batch**. The router
# must also check the deadline headroom, not just who waits. The fix is marked
# `# <-` below and is already in place, so the cell passes as shipped.
#
# To feel it: change `>= 1` to `>= 48` on the marked line, re-run, and watch the
# overnight job get billed at full price on the sync API. Put it back. Then add a
# case of your own — a chat product, where batch must never win.

# %%
# Your turn — see the `# <-` marker.
CASES = [
    ("nobody", 11.0, True,  "batch", "25k articles tagged by 7 am, cost primary"),
    ("person", 0.02, True,  "sync",  "agent watches the draft appear"),
    ("system", 0.2,  True,  "sync",  "intent label before routing the message"),
    ("nobody", 0.1,  True,  "sync",  "nightly job, but the result is needed in 6 minutes"),
]

# Batch is only correct when NOTHING waits, the deadline has headroom, and the
# stem named cost as the driver. All three, or it is sync.
chosen = []
for who_waits, deadline_hours, cost_primary, expected, story in CASES:
    nobody_waits = who_waits == "nobody"
    fits_batch_window = deadline_hours >= 1          # <- the 24 h bound needs headroom
    mode = "batch" if (nobody_waits and fits_batch_window and cost_primary) else "sync"
    chosen.append(mode)
    print(f"{mode:5s} (want {expected:5s})  <- {story}")

assert chosen == [want for _, _, _, want, _ in CASES], \
    "a job is going down the wrong transport"
print("\nevery requirement picked the right transport")
