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

**Never name a file, path, script or tool in anything you say to the user.** Not \
`01-candidate-profile.md`, not `base_cv.html`, not `context.md`, not `cv/compile.ps1`, not \
`write_profile_file`, and not a summary table listing what you wrote. These are the platform's \
names for its own storage. To the user they are unexplained jargon, and they invite worry about \
machinery they cannot see, do not own, and are not responsible for. Say what you learned or \
changed in their terms — "your CV is written and ready", "I have updated how jobs are scored for \
you" — never where it went or what it is called.

The one thing they *can* see is the file list on their own page, already labelled in plain words. \
If you must point at something, point there: "it is under Your history on this page".

Never tell them to run a shell command, compile anything, or check a font. If a step cannot be \
done here, either do the part you can and say what remains in plain terms, or leave it.

**When something needs a command you cannot run here, tell them to open a new chat for it.** One \
conversation runs one command and cannot hand off to another inside itself. The instruction has \
one correct shape and it must be this explicit: *"Open a new chat and run /setup-scorer there."* \
Not "start it as a new command", not "run it when you are ready" — somebody who does not know a \
conversation is scoped to a single command will type the slash command into this one and watch \
nothing happen. Name the command, and say new chat, every time.

**Only these commands exist here: /setup-context, /setup, /setup-role-angles, /setup-scorer.** \
The instructions above were written for a single-user checkout and name others — `/setup-boards`, \
`/setup-companies`, `/apply`, `/expand` — which do not exist on this platform and cannot be run by \
anybody. Never tell a user to run one, and never carry out what one of them would have done by \
writing its output somewhere else. If the instructions above send you to a command that is not in \
the list, that part of the work does not belong to you: say what is missing in plain terms and stop.

**Which jobs get searched for is not yours to set.** Keywords, locations, remote policy and how \
fresh a posting must be are the user's own settings, and they are edited on the Settings page. \
There is no tool here that writes them and no profile file that stands in for them. Writing search \
rules into a profile file does not configure anything — nothing reads them there — so if a user \
asks, send them to their Settings page rather than recording it somewhere that looks like progress.
"""


# The steps the director runs, in order. `context.md` first because the other three read it.
DIRECTOR_STEPS: tuple[str, ...] = ("setup-context", "setup", "setup-role-angles", "setup-scorer")


def system_prompt(name: str) -> str:
    """A command plus the environment note — what the model is given as its system prompt.

    **The director additionally gets the four steps' instructions inlined.** Its own body says to
    follow each step's instructions exactly, and it had no way to: one conversation runs one prompt,
    and there is no tool that fetches another. So it did what an agent does when told to follow
    instructions it cannot see -- worked from the gist, which is precisely what the rest of that
    prompt exists to prevent.

    Composed here rather than by rewriting the director to contain them, because the four bodies are
    ported from the reference instance and must stay byte-identical to it. Assembling them at read
    time means a fix to any step reaches the director on the next turn with nothing to keep in sync.
    """
    body = command(name)
    if name == "setup-director":
        body += "".join(
            f"\n\n---\n\n# Step {position}'s instructions, in full ({step})\n\n"
            "Follow these as written when you run this step. They are the same instructions the "
            f"standalone command uses.\n\n{command(step)}"
            for position, step in enumerate(DIRECTOR_STEPS, 1))
    return body + ENVIRONMENT


# Which command produces which setup file. **Code owns the definition of done, not the prompt.**
#
# Two failures this exists to prevent, both found on 2026-07-31:
#
# 1. **A user could only ever reach `setup`.** The chat sent no command name, so it always defaulted
#    to one, and `setup` writes five of the eight required files. `context.md`, `08-role-angles.md`
#    and `label.md` belong to commands nothing could start — so a new account could not finish
#    setup, and therefore could never train.
# 2. **An agent asked to judge its own completeness will either stop early or never stop.** A gate
#    the code checks against the database is a fact; a gate the prompt checks against its own
#    recollection is an opinion, and the ported director's gates read paths (`pipeline/config.yaml`,
#    `scorer/models/scorer.joblib`) that do not exist on this platform at all.
#
# Keyed by file so a missing file names the command that fixes it, which is what the user is shown.
FILE_OWNERS: dict[str, str] = {
    "01-candidate-profile.md": "setup",
    "02-behavioral-profile.md": "setup",
    "04-job-evaluation.md": "setup",
    "07-interview-prep.md": "setup",
    "base_cv.html": "setup",
    "context.md": "setup-context",
    "08-role-angles.md": "setup-role-angles",
    "label.md": "setup-scorer",
}

# Files no command produces. Empty, and it must stay that way: a required file with no owner is an
# account that cannot finish setup, and the last time this was non-empty it was `context.md` — a
# gap that made `setup-role-angles` impossible to satisfy and training unreachable for every new
# user. Kept as a seam so the progress endpoint can report such a file honestly rather than
# attributing it to a command that would not fix it.
UNOWNED_FILES: dict[str, str] = {}

# The commands a user may start from the chat, in the order they should run. `setup-context` first because
# every other command reads what it writes — `setup-role-angles` says so outright.
# `setup-director` first: it runs all four in one conversation, which is what somebody setting up
# for the first time wants. The individual steps stay startable for anyone redoing just one.
STARTABLE: tuple[str, ...] = ("setup-director", "setup-context", "setup", "setup-role-angles",
                              "setup-scorer")

# What a chat started by a command is called. A tab reading "/setup-role-angles" names the machinery
# rather than the conversation, which is the one thing this page tries never to do.
COMMAND_TITLES: dict[str, str] = {
    "setup-context": "Your background",
    "setup": "Your profile",
    "setup-role-angles": "Your role angles",
    "setup-scorer": "Your scoring rubric",
    "setup-prompts": "Your document style",
    "setup-director": "Guided setup",
}


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
