# Mastering Claude Developer — CCDV-F practice notebooks

One runnable notebook per objective on the **Claude Certified Developer — Foundations
(CCDV-F)** blueprint: 25 lessons, 8 domains, 100.0% of the exam accounted for.

Each notebook is self-contained. Open it, run it top to bottom, and every cell
completes — **no API key and no Claude Code install required**. Where a lesson would
call the real API it prints a clearly-labelled pretend answer instead; where it would
drive the CLI it prints the command it would have run. Set `ANTHROPIC_API_KEY` (and
install Claude Code) to turn those into live calls.

## Start here

```bash
pip install anthropic jupyter
cd /Users/satyak/mastering/mastering_claude_developer
jupyter lab 01_agent_architecture.ipynb
```

`00_setup.ipynb` holds the shared connection setup — every lesson runs it in its
first cell. You never open it directly.

## How a lesson is built

Each one follows the same shape, so you always know where you are:

1. **Header** — why the objective matters, what you'll learn, what you need.
2. **Features** — four to six sections, each an idea plus runnable code.
3. **Practical use** — the one rule to carry into real work.
4. **🏋️ Practice** — a cell that ships passing. Break it deliberately, watch the
   `assert` fail, put it back, then extend it.

## The 25 lessons

**Domain 1 · Claude Code**

| # | Skill | Lesson | Tool | Weight |
|---|---|---|---|---|
| 01 | 1.1 | [Claude Code Operation: Rules, Skills, Commands, Agents, Memory, Headless, Auto-mode](01_claude_code_operation.ipynb) | Claude Code | 3.1% |

**Domain 2 · Prompt and Context Engineering**

| # | Skill | Lesson | Tool | Weight |
|---|---|---|---|---|
| 02 | 2.1 | [Context Engineering: Window Management, Drift & Bloat, Compaction, Isolation](02_context_engineering.ipynb) | API | 3.8% |
| 03 | 2.2 | [Prompt Engineering: Clarity, Few-shot, System vs User, Constraints, Iteration, Sanitization](03_prompt_engineering.ipynb) | API | 4.6% |
| 04 | 2.3 | [Output Handling: Structured Output, Validation, Defensive Parsing, Skepticism](04_output_handling.ipynb) | API | 2.6% |

**Domain 3 · Model Selection and Optimization**

| # | Skill | Lesson | Tool | Weight |
|---|---|---|---|---|
| 05 | 3.1 | [LLM Fundamentals: Tokens, Context, Sampling, Thinking Modes, Effort, Shot Prompting](05_llm_fundamentals.ipynb) | API | 5.2% |
| 06 | 3.2 | [Technical Fundamentals: SDKs over REST, Websockets](06_technical_fundamentals.ipynb) | API | 6.1% |
| 07 | 3.3 | [Model Selection & Tradeoffs: Opus / Sonnet / Haiku, Quality-Latency-Cost, Release Changes](07_model_selection_tradeoffs.ipynb) | API | 2.7% |
| 08 | 3.4 | [Cost & Token Management: Usage Tracking, Cost Modelling, Prompt Caching](08_cost_token_management.ipynb) | API | 2.8% |

**Domain 4 · Applications and Integration**

| # | Skill | Lesson | Tool | Weight |
|---|---|---|---|---|
| 09 | 4.1 | [Understanding Requirements: Functional & Infrastructure](09_understanding_requirements.ipynb) | API | 3.4% |
| 10 | 4.2 | [Systems Life Cycle for Claude Applications](10_systems_life_cycle_for_claude_applications.ipynb) | API | 2.8% |
| 11 | 4.3 | [Claude API Mechanics: Messages, Tools, Streaming, Vision, Thinking, Caching, Batch](11_claude_api_mechanics.ipynb) | API | 6.8% |
| 12 | 4.4 | [Software Engineering Foundations: REST, JSON, Async, Git, SDLC, Refactoring](12_software_engineering_foundations.ipynb) | API | 7.4% |
| 13 | 4.5 | [Claude Application Design: Interfaces, Content Boundaries, Schemas, Session Hygiene, Plugins](13_claude_application_design.ipynb) | API | 8.6% |
| 14 | 4.6 | [Configuration Management: CLAUDE.md, settings.json, Model Pinning, Prompt Versioning](14_configuration_management.ipynb) | Claude Code | 4.1% |

**Domain 5 · Eval, Testing, and Debugging**

| # | Skill | Lesson | Tool | Weight |
|---|---|---|---|---|
| 15 | 5.1 | [Debugging & Error Handling: Error Types, Recovery, Trace Analysis, Origin Isolation](15_debugging_error_handling.ipynb) | API | 2.6% |

**Domain 6 · Tools and MCPs**

| # | Skill | Lesson | Tool | Weight |
|---|---|---|---|---|
| 16 | 6.1 | [Tool Implementation: Schemas, Descriptions, Errors, Dispatch & Approval Patterns](16_tool_implementation.ipynb) | API | 4.4% |
| 17 | 6.2 | [MCP Server Development: Resources, Tools, Prompts, stdio vs Sockets](17_mcp_server_development.ipynb) | Claude Code | 2.1% |
| 18 | 6.3 | [Agentic Customization: Built-in Tools vs Custom Tools vs Skills vs MCPs](18_agentic_customization.ipynb) | Claude Code | 4.1% |

**Domain 7 · Security and Safety**

| # | Skill | Lesson | Tool | Weight |
|---|---|---|---|---|
| 19 | 7.1 | [AI Application Security: Prompt Injection, Jailbreaks, Untrusted Input, PII, Leakage](19_ai_application_security.ipynb) | API | 3.2% |
| 20 | 7.2 | [Guardrails & Safe Deployment: Content Policy, Layering, Least Privilege](20_guardrails_safe_deployment.ipynb) | API + Claude Code | 2.3% |
| 21 | 7.3 | [Claude Hooks as Guardrails Against Destructive Actions](21_claude_hooks_as_guardrails_against_destructive_actions.ipynb) | Claude Code | 1.0% |
| 22 | 7.4 | [Identity, Secrets & Key Management Across Environments](22_identity_secrets_key_management_across_environments.ipynb) | API + Claude Code | 1.6% |

**Domain 8 · Agents and Workflows**

| # | Skill | Lesson | Tool | Weight |
|---|---|---|---|---|
| 23 | 8.1 | [Agent Architecture: Workflow vs Agent, Supervisors, Subagents](23_agent_architecture.ipynb) | API | 4.5% |
| 24 | 8.2 | [Agent Construction: Agent SDK, Custom Loops, Managed Agents, Hooks](24_agent_construction.ipynb) | API + Claude Code | 5.3% |
| 25 | 8.3 | [Agent Patterns & Frameworks: Tool Loops, Memory, Strands / LangGraph / PydanticAI](25_agent_patterns_frameworks.ipynb) | API | 4.9% |

## Editing a lesson

Notebooks are **build output**. The source of truth is `src/<name>.py` in percent
format, which is readable and diffable in a way notebook JSON is not.

```bash
python3 nbbuild.py 07        # rebuild lesson 07 from src/07_*.py
python3 nbbuild.py           # rebuild all of them
python3 nbbuild.py --check   # structural check: header, practice, assert, syntax
python3 verify.py            # the promises: ran clean offline, nothing escapes the
                             # scratch dir, no key printed, model pinned
```

After editing, re-execute the notebook so its stored output matches its code:

```bash
jupyter nbconvert --to notebook --execute --inplace 07_*.ipynb
```

Never hand-edit a `.ipynb` — the next build overwrites it. Edit the source and rebuild.
`AUTHORING.md` is the full spec these lessons are written against.

Lessons that write files put everything under `~/ccdv-practice/`. Deleting that
directory resets every lab; nothing outside it is ever touched.

## Where the content comes from

The lessons are derived from the CCDV-F study material in this repo's sibling
projects — the objective deep dives and the 25 slide decks under
`/Users/satyak/mastering/cca_dev_class/`. The quiz app that tests the same objectives
runs at `http://10.0.0.154:8509`.
