# %% [markdown]
# # Topic 6.2 · MCP Server Development: Resources, Tools, Prompts, stdio vs Sockets
#
# 🎓 *CCDV-F objective 17 of 25 · exam weight 2.1% · Claude Code lesson*
#
# **Why this matters.** An MCP server is a **product boundary**, not a code
# style. You write the capability once behind a standard protocol, and every
# MCP-speaking client gets it with no fresh integration — Claude Code, the Claude
# apps, the Agent SDK, the API connector. The whole skill is two independent
# choices: **which primitive** a thing should be, and **which transport** the
# deployment demands. Most wrong answers collapse those two into one.
#
# **What you'll learn.**
# - Pick **Resource / Tool / Prompt** by *who controls it*, not by convenience.
# - Author a real server file with all three primitives plus a resource template.
# - Read the JSON-RPC 2.0 wire: `initialize`, `tools/call`, and `isError: true`.
# - Choose **stdio** or **Streamable HTTP**, and dodge the stdout-is-the-protocol trap.
# - Register it with `claude mcp add`, and know where each primitive surfaces.
# - Work the ship checklist: logging, Origin, OAuth audience, secrets, tool-set size.
#
# **What you need.** Nothing. Every cell runs offline. You will write a server
# file under `~/ccdv-practice/24` and read it back — it is never started and
# never registered into your real Claude Code config. Install the `claude` CLI
# to see one live read-only command at the end.

# %%
# Load the shared setup: the API client and MODEL.
%run ./00_setup.ipynb

# %% [markdown]
# ### Feature 1 — The three primitives, chosen by *who controls* them
#
# A server offers exactly three kinds of thing, and the exam picks between them
# on one question: **who decides to use it?**
#
# | primitive | controlled by | discovered / used with | side effects? |
# |---|---|---|---|
# | **Resource** | the **application** (the client) | `resources/list`, `resources/read`, `resources/templates/list` | no — read-only context |
# | **Tool** | the **model** | `tools/list`, `tools/call` | yes, that is the point |
# | **Prompt** | the **user** | `prompts/list`, `prompts/get` | no — returns messages |
#
# A hotel room makes it stick. The **binder on the desk** — floor plan, wifi code
# — is a **Resource**: it has an address and it just sits there; the guest decides
# to open it. **Room service** is a **Tool**: it *does* something, and Claude
# decides to call it. The **pre-printed request cards** are **Prompts**: a human
# picks one off the rack and it expands into a well-formed request.
#
# The commonest design error: exposing read-only reference data as a *tool* so
# that "Claude can get it". If it has a URI and no side effects it is a
# **resource** — and it stops costing a tool-list slot in every single request.

# %%
# The whole decision is this one dict: controller in, primitive out.
PRIMITIVE = {"application": "resource", "model": "tool", "user": "prompt"}

# Classify by control, mechanically. Read each requirement and ask only:
# who decides — the app, the model, or the human?
REQUIREMENTS = [
    ("The returns policy document, attachable by the app",        "application"),
    ("Adjust on-hand stock for a SKU",                            "model"),
    ("A saved 'draft this week's reorder review' template",       "user"),
    ("A spec sheet addressed as inventory://sku/AX-19",           "application"),
    ("Query the warehouse for low-stock SKUs mid-conversation",   "model"),
]

for requirement, controller in REQUIREMENTS:
    print(f"{PRIMITIVE[controller]:8s} (controlled by {controller:11s}) <- {requirement}")

# %% [markdown]
# ### Feature 2 — Authoring the server
#
# `FastMCP` in the Python SDK gives one decorator per primitive. That is the whole
# authoring surface the exam asks about.
#
# - `@mcp.tool()` — the **docstring becomes the description Claude reads**, and the
#   type hints become the JSON Schema for the arguments. So write the docstring
#   for the model: what it does, when to use it, when not to, what it returns.
# - `@mcp.resource("inventory://catalog")` — read-only data at a fixed URI.
# - `@mcp.resource("inventory://sku/{sku}")` — a **resource template**: a
#   parameterised URI, listed separately under `resources/templates/list`.
# - `@mcp.prompt()` — a user-picked template; in Claude Code it appears as
#   `/mcp__inventory__reorder_review`.
#
# The next cell writes a real, complete server under a scratch directory,
# `~/ccdv-practice/24`, deliberately outside any real project. Read the logging
# line carefully — it is there before anything else can print.

# %%
%%bash
mkdir -p ~/ccdv-practice/24
cat > ~/ccdv-practice/24/inventory_server.py <<'EOF'
"""A tiny MCP server showing all three primitives over stdio.

Run it by hand with:  python inventory_server.py
Claude Code would launch it itself; see the `claude mcp add` cell in the lesson.
"""
import logging
import sys

from mcp.server.fastmcp import FastMCP

# stdout IS the JSON-RPC channel on stdio. Every log line goes to stderr, and
# this must be configured before any import can print a banner.
logging.basicConfig(stream=sys.stderr, level=logging.INFO)
log = logging.getLogger("inventory")

mcp = FastMCP("inventory")

# A stand-in warehouse so the file is self-contained.
STOCK = {"AX-19": 116, "BX-04": 8}
SHEETS = {"AX-19": "AX-19 — bracket, steel, 40mm. Reorder point 25.",
          "BX-04": "BX-04 — gasket, nitrile, 12mm. Reorder point 30."}


# TOOL — model-controlled, has effects. The docstring is the description Claude
# reads at the moment it chooses, so it says when NOT to use this one too.
@mcp.tool()
def adjust_stock(sku: str, delta: int, reason: str) -> dict:
    """Adjust the on-hand quantity for a SKU in the warehouse.

    Use for goods receipts, shrinkage write-offs and audit corrections.
    Do NOT use this to reserve stock for an order — use reserve_stock.
    delta is signed; negative reduces stock. Returns the new on-hand count.
    """
    if sku not in STOCK:
        # A normal failure: report it as content, not as a protocol error, so
        # the model can read the reason and adapt.
        raise ValueError(f"Unknown SKU {sku!r}. Read inventory://catalog for valid SKUs.")
    STOCK[sku] += delta
    log.info("adjusted %s by %d (%s)", sku, delta, reason)
    return {"sku": sku, "on_hand": STOCK[sku]}


# RESOURCE — application-controlled, read-only, addressed by a fixed URI.
@mcp.resource("inventory://catalog")
def catalog() -> str:
    """The full SKU catalog as CSV."""
    return "sku,on_hand\n" + "\n".join(f"{s},{n}" for s, n in STOCK.items())


# RESOURCE TEMPLATE — a parameterised URI, listed under resources/templates/list.
@mcp.resource("inventory://sku/{sku}")
def sku_sheet(sku: str) -> str:
    """Spec sheet for one SKU."""
    return SHEETS.get(sku, f"No sheet for {sku}.")


# PROMPT — user-controlled. Surfaces in Claude Code as
# /mcp__inventory__reorder_review
@mcp.prompt()
def reorder_review(warehouse_id: str) -> str:
    """Draft this week's reorder review for one warehouse."""
    return (f"Read inventory://catalog, list every SKU in warehouse {warehouse_id} "
            f"below its reorder point, and propose reorder quantities.")


if __name__ == "__main__":
    # stdio: the client spawns this file as a child process and talks over
    # stdin/stdout. Swap to "streamable-http" to serve it remotely instead.
    mcp.run(transport="stdio")
EOF
echo "wrote ~/ccdv-practice/24/inventory_server.py"

# %%
# The four `def`s in that file are the artifact this objective teaches: FastMCP
# turns each decorated function into a primitive. Everything in the NOTEBOOK
# stays straight-line. Here we read the file back and count what we declared —
# this never imports or starts the server, so it works whether or not the `mcp`
# package is installed.
import pathlib

LAB = pathlib.Path.home() / "ccdv-practice" / "24"      # our scratch dir, not MCP
SERVER = LAB / "inventory_server.py"
source = SERVER.read_text(encoding="utf-8")

declared = {
    "tool":              len(re.findall(r"@mcp\.tool\(\)", source)),
    "resource (fixed)":  len(re.findall(r'@mcp\.resource\("[^{"]+"\)', source)),
    "resource template": len(re.findall(r'@mcp\.resource\("[^"]*\{[^"]*"\)', source)),
    "prompt":            len(re.findall(r"@mcp\.prompt\(\)", source)),
}
for kind, n in declared.items():
    print(f"{n} × {kind}")

transport = re.search(r'mcp\.run\(transport="([\w-]+)"\)', source).group(1)
print(f"\ntransport in __main__: {transport}")
print(f"file: {SERVER}  ({len(source.splitlines())} lines)")

# %% [markdown]
# ### Feature 3 — The wire: JSON-RPC 2.0, and how failures come back
#
# MCP is JSON-RPC 2.0 over whichever transport you chose. Every session opens
# with capability negotiation, then the client lists and calls things:
#
# ```
#   CLIENT (Claude Code / Desktop / SDK)          SERVER (your process)
#   ── initialize ─────────────────────────────►  protocolVersion, serverInfo,
#      protocolVersion, clientInfo,               capabilities {tools, resources,
#      capabilities {roots, sampling,             prompts, logging}
#                    elicitation}
#   ◄─────────────────────────── result ───────
#   ── notifications/initialized ──────────────►
#   ── tools/list ─────────────────────────────►  [{name, description, inputSchema}]
#   ── tools/call {name, arguments} ───────────►  run it
#   ◄── {content:[{type:"text",…}], isError:false}
#   ── resources/list · resources/read {uri} ──►
#   ── prompts/list  · prompts/get {name,args} ►
#   ◄── notifications/tools/list_changed ──────   (server pushes to client)
#   ◄── sampling/createMessage ────────────────   (server asks the CLIENT's model)
# ```
#
# **Client vs server, precisely.** The *server* offers tools, resources and
# prompts. The *client* offers three things back: **roots** (which directories
# are in scope), **sampling** (the server may ask the client's LLM for a
# completion, so it needs no API key of its own), and **elicitation** (the server
# may ask the *user* a structured question mid-call). A server must not use a
# client feature the client did not advertise at `initialize`.
#
# **The failure rule.** A tool that fails returns a normal JSON-RPC **result**
# with `isError: true` and the reason in `content`. Reserve JSON-RPC **error**
# objects for protocol faults — unknown method, malformed params. Returning a
# protocol error for "SKU not found" hides the reason from the model, which is
# the same mistake as raising instead of returning a `tool_result` (O23).

# %%
# The four message shapes, written out as dicts you can print and copy. Compare
# the last two: a business failure is a RESULT with isError, never an error object.
call = {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "adjust_stock",
                   "arguments": {"sku": "AX-19", "delta": -4, "reason": "damage"}}}

success = {"jsonrpc": "2.0", "id": 3,
           "result": {"content": [{"type": "text", "text": "AX-19 on hand: 112"}],
                      "isError": False}}

business_failure = {"jsonrpc": "2.0", "id": 3,
                    "result": {"content": [{"type": "text",
                                            "text": "Unknown SKU 'AX-99'. "
                                                    "Read inventory://catalog for valid SKUs."}],
                               "isError": True}}       # the call failed; the protocol did not

protocol_error = {"jsonrpc": "2.0", "id": 4,
                  "error": {"code": -32601, "message": "Method not found: tools/execute"}}

print("call            ", json.dumps(call))
print("success         ", json.dumps(success))
print("business failure", json.dumps(business_failure))
print("protocol fault  ", json.dumps(protocol_error), " <- ONLY for malformed/unknown methods")

# %% [markdown]
# ### Feature 4 — Transports: stdio vs Streamable HTTP
#
# This choice is independent of the primitive choice. It answers one question:
# **where does the server run relative to the client?**
#
# | | **stdio** | **Streamable HTTP** ("sockets") |
# |---|---|---|
# | lifecycle | the client spawns it as a subprocess | runs independently; the client connects to a URL |
# | wire | newline-delimited JSON-RPC on stdin/stdout | HTTP POST to one endpoint, optional `text/event-stream` |
# | who can reach it | only the parent client, same machine | anyone who can reach the URL |
# | sessions | implicit — one process per client | explicit `Mcp-Session-Id` header from `initialize` |
# | auth | the user's local privileges; secrets via `env` | OAuth 2.1 / bearer, **and validate `Origin`** |
# | logging | **stderr only** — stdout is the protocol | normal server logs |
# | pick when | local files, local DB, dev-machine tooling | team or SaaS server, central auth, one deployment |
#
# Two rules that bite people. On **stdio**, stdout *is* the protocol: one stray
# `print()`, banner, progress bar, or library that logs to stdout corrupts the
# stream and the client drops the connection. On **local HTTP**, bind
# `127.0.0.1`, not `0.0.0.0`, and validate the `Origin` header — otherwise a web
# page in the user's browser can DNS-rebind onto the server and drive it.
# Remote servers use OAuth 2.1 with tokens **audience-bound** to this server; a
# token minted for another service must be rejected.
#
# The blueprint says "sockets"; the specification's network transport is
# **Streamable HTTP**, which replaced the older HTTP+SSE pair.

# %%
# Check one: does a stdio server keep stdout clean? Scan both our server and a
# deliberately broken one. Comments and stderr lines are fine; a print() is not.
broken = 'print("inventory server starting…")\nlogging.basicConfig(level=logging.INFO)\n'

for label, src in (("our server", source), ("broken one", broken)):
    offenders = []
    for i, line in enumerate(src.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#") or "stderr" in stripped:
            continue                                   # comments and stderr are fine
        if re.match(r"(sys\.stdout\.write|print)\s*\(", stripped):
            offenders.append((i, stripped))
    print(f"{label:11s}:", offenders or "clean — all logging goes to stderr")

# Check two: the transport itself. Remote or shared means HTTP, full stop —
# stdio only works when the client can spawn the process on the same machine.
print()
for desc, runs_on_user_machine, many_users in (("read my local git repo", True, False),
                                               ("team ticketing behind SSO", False, True),
                                               ("personal notes on this laptop", True, False)):
    transport = "stdio" if runs_on_user_machine and not many_users else "streamable-http"
    print(f"{transport:16s} <- {desc}")

# %% [markdown]
# ### Feature 5 — Integrating with Claude applications
#
# ```bash
# # local stdio server — everything after -- is the launch command
# claude mcp add inventory -- uv run ~/ccdv-practice/24/inventory_server.py
#
# # remote server over Streamable HTTP
# claude mcp add --transport http linear https://mcp.linear.app/mcp
#
# # scope: local (default, this project, just me) | project (.mcp.json, committed)
# #        | user (all my projects)
# claude mcp add --scope project inventory -- uv run /srv/inventory/server.py
#
# claude mcp list      # configured servers + connection status
# claude mcp get inventory
# claude mcp remove inventory
# ```
#
# Once connected, the three primitives surface in **three different places** —
# which is the clearest proof they are not interchangeable:
#
# ```
#   tools      → callable as   mcp__inventory__adjust_stock
#                (allow-list with "mcp__inventory__*" in permissions / allowed_tools)
#   resources  → attachable as @inventory:inventory://sku/AX-19
#   prompts    → typed as      /mcp__inventory__reorder_review
#   status     → /mcp          (connection state, OAuth login for remote servers)
# ```
#
# From the Messages API, remote servers are reached with the **MCP connector**,
# which takes **URL servers only** — there is no stdio in the API, because
# Anthropic's servers cannot spawn a process on your laptop.
#
# **Ship checklist:** primitive chosen on control · tool descriptions written for
# the model · failures as `isError: true` · tool set kept small (every
# `tools/list` entry costs tokens in every request) · stdout clean on stdio ·
# `127.0.0.1` + `Origin` validated on local HTTP · OAuth 2.1 audience-bound
# tokens on remote HTTP · secrets from env, never literals in `.mcp.json` ·
# destructive tools gated by client permissions (annotations are *hints*, not a
# control) · `protocolVersion` negotiated so old clients still initialize.

# %%
# The registration commands, printed rather than run — this lesson never touches
# your real Claude Code config. Copy one into a terminal if you want it for real.
for command in (f"claude mcp add inventory -- uv run {SERVER}",
                "claude mcp add --transport http linear https://mcp.linear.app/mcp",
                f"claude mcp add --scope project inventory -- uv run {SERVER}"):
    print("$", command)

# The project scope is just this file, committed with the repo. Note the env
# value is a REFERENCE, never the secret itself.
project_config = {"mcpServers": {
    "inventory": {"command": "uv", "args": ["run", str(SERVER)],
                  "env": {"INVENTORY_DSN": "${INVENTORY_DSN}"}},
    "linear": {"type": "http", "url": "https://mcp.linear.app/mcp"},
}}
(LAB / ".mcp.json").write_text(json.dumps(project_config, indent=2) + "\n", encoding="utf-8")

# Read it back with line numbers, so we can talk about what actually landed.
print(f"\nwrote {LAB / '.mcp.json'} (the scratch copy — not your project's):")
for i, line in enumerate((LAB / ".mcp.json").read_text(encoding="utf-8").splitlines(), 1):
    print(f"{i:3d} | {line}")

# %%
# One live, read-only command: what is already configured here. Never `add`.
!claude mcp list

# %% [markdown]
# **Practical use:** answer two independent questions and the design falls out.
# **Who controls it?** — the app pulls it in (Resource), the model calls it
# (Tool), the human picks it (Prompt). **Where does it run?** — on the user's
# machine, spawned by the client (stdio), or somewhere many clients reach it
# (Streamable HTTP, OAuth 2.1, audience-bound tokens). Neither choice constrains
# the other. Then keep stdout for the protocol and let failures come back as
# `isError: true` so the model can read them.
#
# **🏋️ Practice:** a server exposes twelve reference documents as twelve tools so
# "Claude can get them". Every request now carries twelve descriptions, and the
# model still fetches the wrong document. Read-only data with an address is a
# **resource**. The fix is marked `# <-` below and is already in place, so the
# cell passes as shipped.
#
# To feel it: change `"application"` back to `"model"` for the policy document,
# re-run, and watch the assert fail — that is the twelve-tools design. Put it
# back. Then add a requirement of your own for a **prompt** and check the router
# still gets it right.

# %%
# Your turn — see the `# <-` marker. PRIMITIVE is the dict from Feature 1.
CASES = [
    ("policy://returns — the returns policy, read-only, has a URI", "application", "resource"),
    #                                                     ^^^^^^^^  <- app-controlled, so a RESOURCE
    ("adjust on-hand stock for a SKU (writes)",                     "model",       "tool"),
    ("the weekly reorder-review template a human picks",            "user",        "prompt"),
]

chosen = []
for desc, controller, expected in CASES:
    got = PRIMITIVE[controller]
    chosen.append(got)
    print(f"{got:8s} (want {expected:8s})  <- {desc}")

assert chosen == [want for _, _, want in CASES], "one capability is the wrong primitive"
assert chosen.count("tool") == 1, \
    "only the side-effecting capability should cost a tools/list slot"
print("\nall three land on the right primitive — one tool, not twelve")
