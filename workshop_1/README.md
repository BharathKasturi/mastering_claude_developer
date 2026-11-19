# Claude Code CLI Practice Notebooks

Hands-on lab track for the AmbariCloud FDE Workshop (Day 1), generated from
`claude_code_CLI_Workshop.pptx` — the 27-feature lab section (slides 3–34).

## Prerequisites
- Claude Code installed (`claude --version` — notebook 00 covers install)
- Claude Pro/Max subscription or Anthropic Console account
- Git, Python 3.10+, Jupyter

## Notebooks (do in order)
| # | Notebook | Covers |
|---|----------|--------|
| 00 | `00_setup_install.ipynb` | Install, sign-in, first session, slash commands (Labs 1–4) |
| 01 | `01_project_setup_memory.ipynb` | Features #1–#5: CLAUDE.md, hierarchy, `/init`, `#`, `@` |
| 02 | `02_session_context.ipynb` | Features #10–#15: `/compact`, `/clear`, `/context`, rewind, checkpoints, resume |
| 03 | `03_tools.ipynb` | Features #16–#22: built-in tools, `!`, hooks, MCP, subagents, skills, plugins |
| 04 | `04_automation_headless_ci.ipynb` | Features #23–#27: `claude -p`, permissions, worktrees, CI |
| 05 | `05_checklist_capstone.ipynb` | 27-feature checklist + self-check script + homework |
| 09 | `09_claude_cicd_pipeline.ipynb` | `claude -p` as a CI/CD step: review gates, fix, mutation test, evidence trail |
| 10 | `10_claude_add_new_phone_column.ipynb` | End-to-end QA on a real app (Ranger data catalog): SQLite warehouse, `ALTER TABLE` migration, two seats (DEV/QA), RED→fix→GREEN, security review, PR |
| 11 | `11_claude_qa_pipeline_exercises.ipynb` | Five hands-on QA-pipeline failure modes (weakened plan, untagged column, leaky mask, edited tests) |

Notebooks 09–11 need the system under test at `/Users/satyak/Ranagr_DataCatalog_Iceberg`
and work in a scratch copy at `/tmp/qa_demo`; `qa_pipeline_overview.png` is the diagram
notebook 10 embeds.

## How the lab runs
Everyone creates ONE tiny Python project (`~/workshop-todo`) in notebook 00,
then walks every feature inside that same project. Cells marked
**⌨️ Interactive** must be done in a real terminal (Claude Code is a TUI);
all `claude -p` headless cells run directly in Jupyter.

Start Jupyter:
```bash
cd /Users/satyak/mastering/mastering_claude_developer/workshop
jupyter lab
```
