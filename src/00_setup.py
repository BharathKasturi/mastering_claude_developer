# %% [markdown]
# # Shared Setup — Claude Developer (CCDV-F) practice notebooks
#
# **Purpose of this notebook:** hold the one-time connection setup so every
# lesson notebook stays short. Each lesson's first code cell is:
#
# ```python
# %run ./00_setup.ipynb
# ```
#
# That line *runs this notebook*, so afterwards the lesson has `client` and
# `MODEL` ready to use.
#
# **This file sets up the API connection only.** Anything a lesson needs beyond
# that is defined **in the lesson itself**, so it is always obvious it is *our*
# teaching code and not part of the Anthropic SDK.
#
# **You do not need an API key to learn.** With no key set, each lesson prints a
# clearly-labelled **pretend** answer, so every cell still runs top to bottom.
# Set `ANTHROPIC_API_KEY` later to get real answers.
#
# **Two tools, two kinds of lesson.**
#
# | Lesson kind | Uses | Needs |
# |---|---|---|
# | API lessons | the `anthropic` Python SDK | `pip install anthropic`, optional `ANTHROPIC_API_KEY` |
# | Claude Code lessons | the `claude` command line, run straight from a cell | the Claude Code CLI on your `PATH` |
#
# Claude Code lessons call the CLI the same way you would in a terminal — `!claude …`
# for one line, or a `%%bash` cell for several. There is no Python wrapper to learn.
# If the CLI is not installed the cell simply prints `command not found`, which is
# a readable result, not a crash.

# %%
# ============================================================
# SHARED SETUP  —  run once per lesson via  %run ./00_setup.ipynb
# ============================================================
# Sets up ONLY the API connection: imports, the key, the `client`, the `MODEL`.

# 1) Standard-library imports the lessons use. All ship with Python.
import json          # turn Python dicts into text and back
import os            # read environment variables (like the API key)
import re            # simple text pattern matching
import shutil        # find programs on PATH (we use it to look for `claude`)
import textwrap      # tidy long strings in a few demos
import time          # pauses and timing in a couple of demos

# 2) The API key, read from the environment. NEVER hard-code a key in a
#    notebook — anything you type here can end up in git or in a screenshot.
API_KEY = os.environ.get("ANTHROPIC_API_KEY")

# 3) The model every lesson calls unless it says otherwise. Pinning an exact
#    model id is the habit the exam wants: "latest" changes underneath you.
MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")

# 4) Build the SDK client only if the library is installed AND we have a key.
#    `client` stays None otherwise, and the lessons check for that.
client = None
try:
    from anthropic import Anthropic
    if API_KEY:
        client = Anthropic(api_key=API_KEY)
except ImportError:
    Anthropic = None      # the lessons print an install hint when they need it

print("Anthropic SDK  :", "installed" if Anthropic else "NOT installed  (pip install anthropic)")
print("API key        :", "found" if API_KEY else "not set        (lessons print pretend answers)")
print("Model          :", MODEL)
print("Claude Code CLI:", shutil.which("claude") or "not found      (the !claude cells will say so)")
