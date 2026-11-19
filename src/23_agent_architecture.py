# %% [markdown]
# # Topic 8.1 · Agent Architecture: Workflow vs Agent, Supervisors, Subagents
#
# 🎓 *CCDV-F objective 23 of 25 · exam weight 4.5% · API lesson*
#
# **Why this matters.** Every "build an agent" ticket is really one question:
# **is the path through this task known in advance?** Answer it wrong and you
# either hand-code a rigid pipeline for a job that needs judgement, or you turn a
# three-step process loose on a model and pay for it in latency, cost and
# surprises. The exam asks this question over and over, in different costumes.
#
# **What you'll learn.**
# - Decide **workflow or agent** from one property of the task, not from taste.
# - Build a **workflow** — your code picks the next step.
# - Build an **agent** — the model picks the next step, and `stop_reason` ends the loop.
# - Add a **supervisor** that routes work to specialist **subagents**.
# - See why a subagent's **isolated context** is the point, not a side effect.
# - Read the **cost** of each choice, so "just use an agent" stops being free.
#
# **What you need.** Nothing. Every cell runs offline with clearly-labelled
# pretend answers. Set `ANTHROPIC_API_KEY` to make the calls real.

# %%
# Load the shared setup: the API client and MODEL.
%run ./00_setup.ipynb

# %% [markdown]
# ### Feature 1 — The one question: is the path known in advance?
#
# This is the whole decision, and it has nothing to do with how clever the task is.
#
# - **Known path** → a **workflow**. *Your code* decides what happens next.
#   Predictable, cheap, easy to test, easy to debug.
# - **Unknown path** → an **agent**. *The model* decides what happens next.
#   Flexible, and you pay in latency, tokens and unpredictability.
#
# "Unknown" means you cannot write the `if` statements in advance, because the
# next step depends on what the previous step found.

# %%
# Read each task and ask only: "could I write the steps down before running it?"
# True means yes -> workflow. False means no -> agent.
tasks = [
    ("Summarise every PDF in a folder",             True),
    ("Translate a fixed field into five languages", True),
    ("Debug why this test fails",                   False),
    ("Extract the invoice total, then email it",    True),
    ("Research a topic until you can answer well",  False),
]

for task, path_known in tasks:
    print(f"{'workflow' if path_known else 'agent':9s} <- {task}")

# %% [markdown]
# ### Feature 2 — A workflow: your code decides the next step
#
# A workflow is ordinary software that happens to call a model. The control flow
# lives in Python. You can read it, test it, and put a breakpoint in it.
#
# Below is the **prompt chaining** pattern: three fixed steps, each feeding the
# next. The `for` loop *is* the control flow — nothing about the order depends on
# what the model says.

# %%
# Three fixed steps. Each prompt gets the previous step's answer.
document = "The roof leaked into the server room at 3am."

steps = [
    "List the key facts, one per line:\n\n{}",
    "Write one sentence summarising these facts:\n\n{}",
    "Reply with exactly one word, urgent or routine:\n\n{}",
]

text = document
for number, template in enumerate(steps, start=1):
    prompt = template.format(text)

    # This is the whole Messages API call: which model, how long an answer, and
    # the conversation so far as a list of {role, content} turns.
    if client is None:
        text = f"[PRETEND ANSWER for step {number} — set ANTHROPIC_API_KEY for a real reply]"
    else:
        reply = client.messages.create(
            model=MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        text = reply.content[0].text

    print(f"--- step {number}\n{text}\n")

# %% [markdown]
# ### Feature 3 — An agent: the model decides, `stop_reason` stops the loop
#
# An agent is a **loop**. You give the model tools, it asks to use one, your code
# runs it and hands the result back, and round it goes. The signal that controls
# the loop is `stop_reason` on the response:
#
# - `stop_reason == "tool_use"` → the model wants a tool → run it, loop again.
# - anything else (usually `"end_turn"`) → the model is finished → stop.
#
# **Never decide you are done by reading the model's words.** Read `stop_reason`.
#
# The loop below uses scripted replies so it runs with no API key. The shape is
# exactly the shape of the real thing.

# %%
# What the "model" sends back on each turn: first a tool request, then an answer.
scripted_replies = [
    {"stop_reason": "tool_use", "order_id": "A-1001"},
    {"stop_reason": "end_turn", "text": "Order A-1001 shipped via UPS."},
]

# A pretend order database, standing in for a real tool.
orders = {"A-1001": {"status": "shipped", "carrier": "UPS"}}

# A cap so a confused loop cannot run forever. Real agents need this too — it is
# the difference between a bug and a bill.
max_steps = 6
final = "[stopped: ran out of replies]"

for step, reply in enumerate(scripted_replies[:max_steps], start=1):
    # THE line that matters. Everything else is plumbing.
    if reply["stop_reason"] != "tool_use":
        final = reply["text"]
        break

    # The model asked for a tool. Run it, and in a real loop send the result
    # back as a tool_result block before looping again.
    result = orders[reply["order_id"]]
    print(f"  step {step}: model asked for a tool -> {result}")

print("FINAL:", final)

# %% [markdown]
# ### Feature 4 — Supervisor and subagents
#
# One agent with twenty tools makes worse choices than five agents with four
# tools each. So you put a **supervisor** in front: it does no domain work, it
# only decides **who** should do the work, and it merges what comes back.
#
# Two things make a subagent a subagent, and both are on purpose:
#
# - **A narrow tool set.** Fewer choices means better choices, and an
#   out-of-role action becomes *impossible*, not merely discouraged.
# - **An isolated context.** The subagent sees the prompt you hand it and
#   nothing else. Its intermediate reasoning never lands in the main window.
#
# That second one is why supervisors save context, not just attention: a
# subagent can read forty files and return four lines.

# %%
# Each subagent is a role: the tools it may touch, and its instructions.
subagents = {
    "researcher": {"tools": ["web_search", "read_file"],
                   "system": "Find sources. Report findings. Never edit files."},
    "coder":      {"tools": ["read_file", "write_file", "run_tests"],
                   "system": "Change code to satisfy the described behaviour."},
    "reviewer":   {"tools": ["read_file"],
                   "system": "Report problems. You may not change anything."},
}

# The supervisor's ONLY job is routing. It decides "who", never "how".
# A real supervisor asks the model to route; the keywords here keep it offline.
research_words = ("find", "research", "look up")
coder_words = ("fix", "implement", "refactor")

for task in ["find the retry policy docs", "fix the failing auth test",
             "check this diff for problems"]:
    if any(word in task for word in research_words):
        name = "researcher"
    elif any(word in task for word in coder_words):
        name = "coder"
    else:
        name = "reviewer"

    # The subagent's context starts EMPTY except for this prompt. It cannot see
    # the supervisor's conversation, and the supervisor sees only what it reports.
    print(f"{name:11s} tools={subagents[name]['tools']}  <- {task}")

# %% [markdown]
# ### Feature 5 — What each choice costs
#
# "Just use an agent" sounds safe and is not. Each step up buys flexibility and
# spends something real. Know the trade before you pick.
#
# | Architecture | Who picks the next step | Latency | Cost | Predictable? |
# |---|---|---|---|---|
# | Single call | nobody — there is one step | lowest | lowest | fully |
# | Workflow | your code | low, fixed | fixed and countable | fully |
# | Agent | the model | unbounded until it stops | grows with every turn | no |
# | Supervisor + subagents | a routing model, then each subagent | highest | highest | no, but contained |
#
# The rule the exam wants: **use the simplest architecture that can do the job.**
# Try one call. Then a workflow. Reach for an agent last.

# %%
# Turn "agents are expensive" into a number you can defend in review.
turns = 4          # loop turns an agent takes before it stops
helpers = 3        # subagents the supervisor calls

model_calls = {
    "single":     1,                    # one step, one call
    "workflow":   3,                    # one per fixed step
    "agent":      turns,                # one per loop turn, unbounded
    "supervisor": 1 + helpers * turns,  # the routing call, plus each subagent's loop
}

for architecture, calls in model_calls.items():
    print(f"{architecture:11s} ~{calls:3d} model calls")

# %% [markdown]
# ### Feature 6 — The same agent loop, for real
#
# Feature 3 scripted the model's replies so it runs offline. Here is the live
# twin, with `stop_reason` doing the same job. The extra plumbing a real loop
# needs:
#
# - **describe the tool** — `name`, `description`, and a JSON `input_schema`,
# - **append the model's turn** (the `tool_use` block) back into `messages`,
# - **send a `tool_result`** tied to the request by `tool_use_id`,
# - **cap the steps** so a misbehaving loop cannot run forever.
#
# Set `ANTHROPIC_API_KEY` to watch it run; offline it prints a skip note.

# %%
# Describe the tool to the model: what it is called, what it does, what it takes.
order_tool = {
    "name": "look_up_order",
    "description": "Look up the shipping status of a customer order by its id.",
    "input_schema": {
        "type": "object",
        "properties": {"order_id": {"type": "string", "description": "e.g. A-1001"}},
        "required": ["order_id"],
    },
}

if client is None:
    print("[skipped — set ANTHROPIC_API_KEY to run the live loop]")
else:
    messages = [{"role": "user", "content": "What is the status of order A-1001?"}]
    answer = "[stopped: hit the step cap — a real agent needs this]"

    for step in range(5):
        reply = client.messages.create(model=MODEL, max_tokens=500,
                                       tools=[order_tool], messages=messages)

        # Not tool_use -> the model is done. Read stop_reason, not the words.
        if reply.stop_reason != "tool_use":
            answer = "".join(b.text for b in reply.content if b.type == "text")
            break

        # Put the model's own turn back into the history, unchanged. Skipping
        # this is the most common bug in a hand-written loop.
        messages.append({"role": "assistant", "content": reply.content})

        # Answer every tool the model asked for, each tied to its request id.
        results = []
        for block in reply.content:
            if block.type == "tool_use":
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,          # ties this result to that request
                    "content": json.dumps(orders[block.input["order_id"]]),
                })
        messages.append({"role": "user", "content": results})

    print("FINAL:", answer)

# %% [markdown]
# **Practical use:** answer one question before you write any code — **is the
# path through this task known in advance?** Known path is a workflow and your
# code owns the control flow. Unknown path is an agent and `stop_reason` owns it.
# Reach for a supervisor only when one agent's tool list has grown past what it
# can choose from well. And always use the simplest architecture that can do the
# job: every step up buys flexibility with latency, money and predictability.
#
# **🏋️ Practice:** a ticket saying *"refactor the billing module"* must reach the
# **coder**. The word that makes that work is marked `# <-` below, and it is
# already in place, so the cell passes as shipped.
#
# To feel it: delete `"refactor"`, re-run, and watch the assert fail and the
# ticket land on the reviewer. Then put it back, and try adding a ticket of your
# own that should reach the researcher.

# %%
# Your turn — see the `# <-` marker.
coder_words = ("fix", "implement", "refactor")     # <- "refactor" is the fix

tickets = [
    ("look up the retry policy", "researcher"),
    ("refactor the billing module", "coder"),
    ("check this diff for problems", "reviewer"),
]

routed = []
for ticket, expected in tickets:
    if any(word in ticket for word in research_words):
        name = "researcher"
    elif any(word in ticket for word in coder_words):
        name = "coder"
    else:
        name = "reviewer"
    routed.append(name)
    print(f"{name:11s} (want {expected:11s})  <- {ticket}")

assert routed == [want for _, want in tickets], "one ticket still routes wrong"
print("\nall three route correctly")
