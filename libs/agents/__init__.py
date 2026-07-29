"""The setup agents, and the loop that runs them.

`libs/agents/*.md` are the reference instance's slash commands, copied byte-identical from
`mahoraga-jobs/.claude/commands/`. They are **not** cleaned and never will be: they are the
machinery that fills a profile, not part of one, so there is no identity in them to remove. Their
nine files are identical between mahoraga-jobs and andras-job-search, which is how we know nobody
improved them in the field.

## What the platform has to supply

The commands were written for a terminal with a checkout: they say "read `documents/`", "write
`CLAUDE.md`", "check `05-cv-templates.md` for placeholders". None of that exists here — the
documents are rows a user uploaded and the files are rows the agent writes.

So the environment note below tells the agent where it actually is, and the four tools give it the
same reach `/setup` actually uses (Read, Write, Glob, Edit — no web access, no bash). Nothing else
about the command is touched.

## Why a manual loop

`libs/llm` is a deliberately narrow adapter so the tests can drive it with a fake rather than the
network. The loop here is the same shape the SDK's tool runner implements, kept explicit for two
reasons: every tool call is a write to somebody's profile and belongs in an audit line, and the
runner is a beta surface this platform does not otherwise depend on.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_HERE = Path(__file__).parent

# Opus 5 for setup, and this is the one place the platform pays for a frontier model. It runs once
# per user, and every document the account ever generates is built from what this conversation
# produces — a thin profile costs far more than the call (`REBUILD_PLAN.md`).
SETUP_MODEL = "claude-opus-5"

# Generous: the agent writes whole files, and a truncated `context.md` is a profile with the end
# missing rather than an error.
MAX_TOKENS = 32_000

# How many tool calls one turn may make before the loop gives up. `/setup` Path A reads a document
# set and writes seven files, so the ceiling has to clear that with room; it exists to stop a
# malfunctioning turn spending a frontier model's budget in a circle, not to bound normal work.
MAX_TOOL_ITERATIONS = 40


@lru_cache(maxsize=16)
def command(name: str) -> str:
    """One agent command, byte-identical to the reference instance's.

    Newlines normalised on read for the reason `libs.prompts.load` gives: a checkout on Windows must
    not produce different bytes from one on Linux.
    """
    return (_HERE / f"{name}.md").read_text(encoding="utf-8").replace("\r\n", "\n")


# Appended to every command. It says what changed about the environment and nothing about the
# person — the command still owns every question it asks and every rule it applies.
ENVIRONMENT = """\

---

# Where you are running

You are running inside the job-search platform, talking to the user in a chat window. This changes
*how* you read and write, and nothing about what this command asks you to do.

- **There is no checkout and no filesystem.** Where the instructions above name a path — \
`documents/`, `CLAUDE.md`, `.claude/skills/job-application-assistant/01-candidate-profile.md`, \
`cv/base_cv.html` — use the tools instead. Every profile file is addressed by its bare name: \
`context.md` (this is `CLAUDE.md`), `01-candidate-profile.md`, `02-behavioral-profile.md`, \
`04-job-evaluation.md`, `05-cv-templates.md`, `07-interview-prep.md`, `label.md`, `base_cv.html`.
- **The user's documents are already uploaded.** `list_documents` shows what they gave you at \
signup and `read_document` returns the extracted text. Path A of this command — reading a documents \
folder — is the path you are on.
- **You cannot run commands, compile a PDF, or browse the web.** Where the instructions ask you to \
verify by compiling or fetching, say what you would have checked and move on.
- **Write files as you go, rather than at the end.** Each `write_profile_file` is saved \
immediately, so a conversation that gets interrupted keeps everything already written.

# How to talk to the user

They do not know what these files are and do not need to. They are answering questions about \
themselves; that the answers become markdown is your business, not theirs. Never ask them to \
approve a filename, paste a placeholder, or read a diff.

Ask one thing at a time. Keep questions short. When you have enough to fill a section, fill it and \
move on rather than confirming each field back.
"""


def system_prompt(name: str) -> str:
    """A command plus the environment note — what the model is given as its system prompt."""
    return command(name) + ENVIRONMENT


# The tools, in the shape the API takes them. This is `/setup`'s own tool list — Read, Write, Glob,
# Edit — mapped onto rows: documents are read-only inputs, profile files are read-write outputs.
TOOLS: list[dict] = [
    {
        "name": "list_documents",
        "description": ("List the documents the user uploaded at signup — CV, cover letters, "
                        "references, any long-form history they wrote. Start here."),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "read_document",
        "description": "Read one uploaded document's extracted text, by the name `list_documents` gave.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "The document's name"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_profile_files",
        "description": ("List the user's profile files, showing which are still unfilled "
                        "scaffolds. These are the files this command populates."),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "read_profile_file",
        "description": ("Read one profile file — the scaffold to fill, or what a previous run "
                        "wrote. Read before writing so you keep the structure."),
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "e.g. context.md"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "write_profile_file",
        "description": ("Write a profile file, replacing it entirely. Keep the scaffold's "
                        "structure and headings; replace the placeholder content. Saved "
                        "immediately."),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "e.g. context.md"},
                "content": {"type": "string", "description": "The file's full new content"},
            },
            "required": ["name", "content"],
            "additionalProperties": False,
        },
    },
]
