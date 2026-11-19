# %% [markdown]
# # Topic 7.4 · Identity, Secrets & Key Management Across Environments
#
# 🎓 *CCDV-F objective 22 of 25 · exam weight 1.6% · API lesson*
#
# **Why this matters.** A Claude application holds two identities at once. One
# credential proves *your application* may call the model. The other proves *the
# person on the other end* may make this request. Every question on this skill is
# a variation of one thing: where does the key live, and who can reach it. The
# wrong answers all put it somewhere convenient — a committed `.env`, a shared
# `settings.json`, a browser bundle, the prompt.
#
# **What you'll learn.**
# - Rank **where a credential lives** by lifetime, reach and blast radius.
# - Get the key **into the process** at runtime, and check for it without ever revealing it.
# - Keep the **user's identity, the app's identity and a tool's identity** apart.
# - Run the three gates: **authentication, authorization, and human approval**.
# - **Monitor and rotate**: usage per key, revoke by status, overlap for zero downtime.
# - Do the same in **Claude Code** with `permissions.deny` and `apiKeyHelper`.
#
# **What you need.** Nothing. Every cell runs offline, and **no cell ever prints a
# key or any part of one** — that is the lesson, not a limitation. The Claude Code
# section writes a sample `settings.json` under `~/ccdv-practice/22`, a scratch
# directory, never your own `~/.claude`.

# %%
# Load the shared setup: the API client and MODEL.
%run ./00_setup.ipynb

# %%
# Extra standard-library imports this lesson uses on top of the setup cell.
import pathlib

# Everything this lesson writes goes here. A scratch directory, never a real one.
PRACTICE_DIR = pathlib.Path.home() / "ccdv-practice" / "22"
PRACTICE_DIR.mkdir(parents=True, exist_ok=True)

# The ONLY question your code may ask about a secret: is it configured?
# `bool(...)` is the whole point — never the value, never a prefix, never a
# length, never a hash. The moment a diagnostic prints characters, it leaks.
for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_ADMIN_KEY", "AWS_REGION"):
    print({"variable": var, "configured": bool(os.environ.get(var))})

# %% [markdown]
# ### Feature 1 — Where the credential lives decides everything else
#
# | Where it lives | Environment | Lifetime | Who can read it | Blast radius | Revoke by |
# |---|---|---|---|---|---|
# | **Committed file / hard-coded** | never correct | forever | anyone with repo *history* access | full org spend and data | rotating everywhere, painfully |
# | **Local `.env`, git-ignored** | a developer laptop | until rotated | that developer, and any process on the box | one dev key on a low limit | disable that key |
# | **CI secret store** | build and test | until rotated | jobs in that repo, and anyone who can edit a workflow | whatever it is scoped to | rotate the secret, disable the key |
# | **Cloud secret manager** | staging, production | until rotated, fetched per boot | only workloads whose IAM role allows the read | scoped, and every read is logged | rotate in place, no redeploy |
# | **Cloud IAM role** (Bedrock, Vertex) | production on that cloud | minutes — short-lived | the running workload only | expires on its own | detach the role |
# | **User OAuth / OIDC token** | any — per person | minutes to hours | that user's session | that user's own permissions | revoke the grant |
#
# **The decision rule:** keep the credential's lifetime and scope as small as the
# job allows. Prefer an identity the *platform issues* — an IAM role, OIDC, OAuth
# — over a long-lived string you have to store. When you must store a string, put
# it in a manager that logs reads, and give every environment its own.
#
# The exam contrasts two shapes over and over. A **static key in a store**: you
# rotate it on a schedule and revoke it by disabling it. A **short-lived
# credential from an identity provider**: there is nothing to rotate, it expires.
# Where the platform can issue the second, that is the stronger answer.

# %%
# Score each home by the two properties that actually matter on the exam.
STORES = {
    #                        environment         lifetime      reach if leaked
    "committed_file":       ("none — never",    "forever",    "the whole organisation"),
    "local_dotenv":         ("laptop",          "until rotated", "one low-limit dev key"),
    "ci_secret_store":      ("build",           "until rotated", "that repo's scope"),
    "cloud_secret_manager": ("staging, prod",   "until rotated", "one scoped key, reads logged"),
    "iam_role":             ("prod on cloud",   "minutes",    "expires on its own"),
    "user_oauth_token":     ("per person",      "minutes",    "that one user's permissions"),
}

# The same table as the yes/no a code review needs. Two rules reject; the rest pass.
for store in STORES:
    for env in ("laptop", "production"):
        if store == "committed_file":
            ok, reason = False, "public to everyone with repo history, forever"
        elif env == "production" and store in ("local_dotenv", "ci_secret_store"):
            ok, reason = False, "production reads a secret manager or an IAM role, not a file"
        elif env == "laptop" and store == "iam_role":
            ok, reason = True, "fine if the platform issues it; else a low-limit dev key"
        else:
            ok, reason = True, "scoped and revocable"
        print(f"{'OK  ' if ok else 'NO  '} {store:22s} in {env:11s} — {reason}")

# %% [markdown]
# ### Feature 2 — Getting the key into the process, and never back out again
#
# The SDK reads `ANTHROPIC_API_KEY` from the environment by default. That is the
# whole interface. Everything else is about *who puts it there*.
#
# ```python
# # ✅ default: the constructor picks up ANTHROPIC_API_KEY from the environment
# client = anthropic.Anthropic()
#
# # ✅ production: fetch from the secret manager at start-up, never from a file in the image
# secret = json.loads(
#     boto3.client("secretsmanager").get_secret_value(SecretId="prod/claude/api-key")["SecretString"]
# )
# client = anthropic.Anthropic(api_key=secret["ANTHROPIC_API_KEY"])
#
# # ✅ no Anthropic key at all — the cloud's own identity signs the request
# from anthropic import AnthropicBedrock, AnthropicVertex
# client = AnthropicBedrock(aws_region="us-east-1")                    # STS creds from the task role
# client = AnthropicVertex(project_id="my-proj", region="us-east5")    # workload identity
#
# # ❌ never
# client = anthropic.Anthropic(api_key="sk-ant-…")   # in source, in a notebook, in a Dockerfile ENV
# ```
#
# **And now the rule that this whole notebook obeys.** A redacted print is still
# a leak. `print(key[:8])` narrows an attacker's search space, and it lands in
# notebook output, in a screenshot, in a pasted bug report, in git. So does a
# length. So does a hash, which turns the secret into something checkable offline.
# The only safe diagnostic is a **boolean**: is it configured or not.
#
# The corollary is bigger than printing. A secret in the *context window* is a
# secret in your transcripts, your evals and your logs. Tools take a handle; the
# harness attaches the credential at dispatch.

# %%
# Anything that goes to a log passes through here first. The scanner looks for
# the SHAPES of credentials, so a mistake is caught even when nobody expected it.
SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]+"),          # an Anthropic API key
    re.compile(r"AKIA[0-9A-Z]{16}"),                # an AWS access key id
    re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),   # a JWT
]

# A clearly fake credential, so this cell demonstrates the scanner without ever
# touching the real environment. Redacting is just sub() once per pattern.
FAKE_LINE = "startup failed: Authorization: Bearer sk-ant-EXAMPLE-NOT-A-REAL-KEY at boot"

cleaned = FAKE_LINE
for pattern in SECRET_PATTERNS:
    cleaned = pattern.sub("[REDACTED]", cleaned)

print("what a naive logger would write:  <the raw line, with the credential in it>")
print("what the redacting logger writes:", cleaned)

# Everything in a tool input is transcript content. A tool should receive
# {"account_id": "acct_991"} and let the harness attach the token itself.
BANNED_KEYS = ("api_key", "token", "password", "secret", "credential")

for tool_input in ({"account_id": "acct_991"},
                   {"account_id": "acct_991", "api_token": "…"}):
    offending = [k for k in tool_input if any(b in k.lower() for b in BANNED_KEYS)]
    print(f"{'clean ' if not offending else 'REFUSE'} {tool_input}"
          + (f"  <- {offending} belongs to the harness, not the model" if offending else ""))

# %% [markdown]
# ### Feature 3 — Three identities, and stems blur them on purpose
#
# ```
#    ┌─ the USER's identity ─────────────────────────────────────────────────────┐
#    │  browser / client ──► OIDC login ──► your API verifies the JWT signature,  │
#    │  never holds a key                   issuer, audience, expiry ──► session  │
#    └───────────────────────────────────────────────────────────────────────────┘
#                                        │  session.user_id, session.roles
#                                        ▼
#    ┌─ the APP's identity ──────────────────────────────────────────────────────┐
#    │  your backend ──► ANTHROPIC_API_KEY (or an IAM role) ──► the Claude API    │
#    │  ONE key for the service; the model never sees it, users never see it      │
#    └───────────────────────────────────────────────────────────────────────────┘
#                                        │
#                                        ▼
#    ┌─ the TOOL's identity ─────────────────────────────────────────────────────┐
#    │  the harness ──► a per-tool credential, resolved from session.user at      │
#    │  dispatch (a DB role, a scoped OAuth token for a remote MCP server)        │
#    └───────────────────────────────────────────────────────────────────────────┘
# ```
#
# The most-tested anti-pattern lives in the first box. Ship the app's key to the
# browser and every visitor has it. The TypeScript SDK refuses to run in a
# browser unless you opt in by writing `dangerouslyAllowBrowser: true` — the flag
# is named that way on purpose. Anything a user's browser can hold, a user can read.
#
# For **remote MCP servers** the same separation is OAuth. The server is a
# resource server; the client gets a token bound to *that* server. A server must
# reject a token minted for something else. Accepting one anyway is **token
# passthrough**, and the spec calls it an anti-pattern: it destroys the audience
# check that made the identity mean anything.

# %%
# Verifying the user's token. The claims you MUST check are the ones an attacker
# would otherwise choose for you: who issued it, who it is for, and when it dies.
# A token that is merely well-formed proves nothing. Three claims decide it:
# who issued it, who it is FOR, and when it dies. Checking `aud` is what makes
# token passthrough impossible — a token minted for another API is refused here.
ISSUER, AUDIENCE, NOW = "https://idp.acme.test", "https://api.acme.test", 1000

TOKENS = [
    ("valid",              {"iss": ISSUER, "aud": AUDIENCE, "exp": 2000, "sub": "dana", "roles": ["agent"]}),
    ("expired",            {"iss": ISSUER, "aud": AUDIENCE, "exp": 500,  "sub": "dana", "roles": ["agent"]}),
    ("wrong audience",     {"iss": ISSUER, "aud": "https://other.test", "exp": 2000, "sub": "dana", "roles": ["admin"]}),
]
for label, claims in TOKENS:
    if claims.get("iss") != ISSUER:
        session, reason = None, "issued by someone else"
    elif claims.get("aud") != AUDIENCE:
        session, reason = None, "audience mismatch — minted for another API"
    elif claims.get("exp", 0) <= NOW:
        session, reason = None, "expired"
    else:
        session = {"user": claims["sub"], "roles": claims.get("roles", [])}
        reason = "ok"
    print(f"{'ACCEPT' if session else 'REJECT':6s} {label:16s} {reason}")

# %% [markdown]
# ### Feature 4 — Three gates, and the one everybody skips
#
# ```
#    gate 1 · does this identity EXIST and is it valid?      → authentication
#    gate 2 · is this identity ALLOWED this action, on       → authorization
#             this object?
#    gate 3 · should a HUMAN approve this specific call?     → approval
# ```
#
# Gate 3 is the skipped one. It is not "ask the model to be careful". It is a step
# in the harness that stops the loop, records who approved, and resumes.
#
# Access **level** verification on the Anthropic side is the **workspace**. Keys
# belong to a workspace, and a workspace carries its own spend limit and member
# list. So "dev cannot spend production's budget" becomes configuration rather
# than trust, and "the contractor's key only reaches the sandbox workspace" is
# something you can point at. Provisioning and de-provisioning of people belong
# to SSO and SCIM where the plan supports it: removing someone from the directory
# removes their console access with it.

# %%
# The three gates in order. Each one is code, and each one can say no.
ROLE_TOOLS = {"viewer": ["read_ticket"],
              "agent":  ["read_ticket", "update_ticket"],
              "admin":  ["read_ticket", "update_ticket", "read_audit_log", "delete_user"]}
NEEDS_APPROVAL = {"delete_user"}          # irreversible: a human signs for it

agent_claims = {"iss": ISSUER, "aud": AUDIENCE, "exp": 2000, "sub": "dana", "roles": ["agent"]}
admin_claims = {**agent_claims, "sub": "raj", "roles": ["admin"]}

# Three calls through the three gates. The chat may say "I'm on the security
# team" all it likes — the gates read the TOKEN, never the conversation.
calls = [
    (agent_claims, "read_audit_log", {},              None),
    (admin_claims, "delete_user",    {"id": "8812"},  None),
    (admin_claims, "delete_user",    {"id": "8812"},  "on-call@acme.test"),
]

for claims, tool_name, tool_input, approver in calls:
    # gate 1 — AuthN: is the token ours, for us, and still alive?
    expired = claims.get("exp", 0) <= NOW
    if claims.get("iss") != ISSUER or claims.get("aud") != AUDIENCE or expired:
        print("gate 1 AuthN: rejected"); continue

    user, role = claims["sub"], claims["roles"][0]

    # gate 2 — AuthZ: does that role carry this tool?
    if tool_name not in ROLE_TOOLS.get(role, []):
        print(f"gate 2 AuthZ: {user} may not call {tool_name}"); continue

    # gate 3 — approval: irreversible actions need a named human.
    if tool_name in NEEDS_APPROVAL and approver is None:
        print(f"gate 3 approval: {tool_name} is irreversible and needs a named approver"); continue

    print(f"ran {tool_name}{tool_input} for {user}, approved by {approver}")

# %% [markdown]
# ### Feature 5 — Monitoring, rotation, revocation
#
# You cannot detect misuse of a credential you are not measuring. Two layers:
# what Anthropic can tell you about the key, and what only your application knows.
#
# ```python
# # Organisation-level, via the Admin API. An Admin key is SEPARATE from the app's
# # key, is org-scoped, and never sits in an application's environment.
# H = {"x-api-key": os.environ["ANTHROPIC_ADMIN_KEY"], "anthropic-version": "2023-06-01"}
#
# usage = httpx.get("https://api.anthropic.com/v1/organizations/usage_report/messages",
#                   params={"starting_at": "2026-09-01T00:00:00Z", "bucket_width": "1d",
#                           "group_by[]": "api_key_id"}, headers=H).json()
#
# keys  = httpx.get("https://api.anthropic.com/v1/organizations/api_keys", headers=H).json()
#
# # Kill a leaked key by STATUS, not by deleting it — you want the history.
# httpx.post(f"https://api.anthropic.com/v1/organizations/api_keys/{leaked_id}",
#            json={"status": "inactive"}, headers=H)
# ```
#
# ```
#    signal                             where it comes from          what it catches
#    ────────────────────────────────   ──────────────────────────   ────────────────────────
#    spend and tokens per key per day   Usage & Cost Admin API       a leaked key being farmed
#    requests from unfamiliar regions   your gateway or proxy logs   a key used off-network
#    tool calls per user per object     your own audit table         a user reaching past a role
#    approvals granted, and by whom     your approval workflow       a rubber-stamped side effect
#    secret reads                       secret-manager access logs   a workload reading prod in dev
# ```
#
# **A rotation you have never rehearsed is not a control.** The runbook has
# overlapping validity in the middle, and that overlap is the whole reason there
# is no downtime window.

# %%
# The rotation runbook as a state machine, so the ORDER is the thing you learn.
ROTATION = [
    ("mint",     "create the new key in the manager; both keys are now valid"),
    ("pick_up",  "workloads fetch on next boot or next refresh — no code change"),
    ("confirm",  "check the usage report shows traffic on the NEW key"),
    ("disable",  "set the OLD key status=inactive"),
    ("verify",   "check the usage report shows ZERO traffic on the old key"),
]

for i, (name, description) in enumerate(ROTATION, 1):
    print(f"{i}. {name:9s} {description}")

# Why 'disable' comes AFTER 'confirm'. Walk each order and watch whether there
# is ever a moment with no valid key — that moment is an outage, not a rotation.
disable_first = [ROTATION[3]] + ROTATION[:3] + ROTATION[4:]

for label, steps in [("the runbook order", ROTATION), ("disable first", disable_first)]:
    old_valid, new_valid, safe = True, False, True
    for name, _ in steps:
        if name == "mint":
            new_valid = True
        if name == "disable":
            old_valid = False
        if not (old_valid or new_valid):     # nobody can call the API right now
            safe = False
    print(f"\n{label:18s} zero-downtime: {safe}"
          + ("" if safe else "   <- a window with no valid key"))

# %% [markdown]
# ### Feature 6 — The same discipline in Claude Code: `permissions.deny` and `apiKeyHelper`
#
# Claude Code gives you two settings that do most of this work.
#
# **`permissions.deny`** is the cheapest control on the page. It stops the agent
# *reading* the secret in the first place. A secret that never enters the context
# cannot be echoed into a transcript, a log, or a bug report you paste into a
# ticket. `Read(./.env)` and `Bash(env:*)` are the two everybody forgets.
#
# **`apiKeyHelper`** points at a script that prints a fresh credential. So the
# session mints a short-lived token instead of carrying a permanent key in the
# environment. The refresh interval is a separate, version-dependent setting —
# check the settings reference rather than memorising a number.
#
# Our helper script below is a stub that prints a placeholder. A real one calls
# your secret manager or your STS endpoint. It writes into `~/ccdv-practice/22`,
# never your own `~/.claude`.

# %%
# The settings file. Note what is NOT in it: any credential.
settings = {
    "permissions": {
        "deny": [
            "Read(./.env)",            # the agent must not read the secret
            "Read(./secrets/**)",
            "Read(./**/*.pem)",
            "Bash(env:*)",             # printing the environment prints the keys
        ],
        "ask": [
            "Bash(terraform apply:*)", # legitimate, and a human should sign for it
            "Bash(gh secret set:*)",
        ],
        "allow": [
            "Read(./src/**)",
            "Bash(npm test:*)",
        ],
    },
    "apiKeyHelper": str(PRACTICE_DIR / "fetch-token.sh"),
}

settings_dir = PRACTICE_DIR / ".claude"
settings_dir.mkdir(parents=True, exist_ok=True)
(settings_dir / "settings.json").write_text(json.dumps(settings, indent=2) + "\n")

# The helper script. In production this line is a call to your secret manager.
helper = PRACTICE_DIR / "fetch-token.sh"
helper.write_text("#!/usr/bin/env bash\n"
                  "# Prints a short-lived credential on stdout. A real one would call\n"
                  "# e.g. `aws secretsmanager get-secret-value` or an STS endpoint.\n"
                  "echo \"placeholder-token-from-your-secret-manager\"\n")
helper.chmod(0o755)

print("wrote", settings_dir / "settings.json", "\n")
print((settings_dir / "settings.json").read_text())
# Run the CLI the way you would in a terminal. `command not found` here just
# means Claude Code is not installed — the config above is still the answer.
!claude --version

# %% [markdown]
# **Practical use:** inject the credential at runtime from a store that knows who
# is asking, scope it to the smallest thing that works, and make revoking it one
# boring action. Never type it into a file, a prompt, or a client a user can
# open. Keep the user's identity, the app's identity and each tool's identity
# separate, and resolve all three in the harness. And treat every diagnostic as
# a publication: a redacted print is still a leak.
#
# **🏋️ Practice:** the log line below must never carry a credential, whatever
# shape it arrives in. The JWT pattern that catches a bearer token is marked
# `# <-` and is already in place, so the cell passes as shipped.
#
# To feel it: delete the JWT pattern, re-run, and watch a whole session token
# land in the log while the assert fails. Put it back. Then extend it: add a
# pattern for a Slack or GitHub token shape, and a test line to prove it works.

# %%
# Your turn — see the `# <-` marker. Every string here is obviously fake.
PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]+"),                                    # Anthropic key
    re.compile(r"AKIA[0-9A-Z]{16}"),                                          # AWS access key id
    re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),    # <- bearer JWT
]

LINES = [
    "auth failed for header Authorization: Bearer eyJhbGciOi.eyJzdWIiOiJk.SIGNATURE",
    "boot error: ANTHROPIC_API_KEY=sk-ant-EXAMPLE-NOT-REAL rejected",
    "s3 client refused key AKIAEXAMPLE1234ABCDE",
    "request 91af completed in 240ms",          # nothing to redact — must survive intact
]

# Redacting is one sub() per pattern. Collect the cleaned lines, then check them.
cleaned_lines = []
for line in LINES:
    for pattern in PATTERNS:
        line = pattern.sub("[REDACTED]", line)
    cleaned_lines.append(line)
    print(line)

# No credential shape may survive the redactor…
assert not any(p.search(l) for l in cleaned_lines for p in PATTERNS), "a credential reached the log"
# …and an innocent line must come through untouched.
assert cleaned_lines[-1] == LINES[-1], "the redactor is eating ordinary log lines"
print("\nevery credential shape redacted, ordinary lines untouched")
