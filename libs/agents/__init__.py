"""The setup agents, and the loop that runs them."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_HERE = Path(__file__).parent

# Opus 5 for setup, and this is the one place the platform pays for a frontier model.
SETUP_MODEL = "claude-opus-5"

# Generous: the agent writes whole files.
MAX_TOKENS = 32_000

# How many tool calls one turn may make before the loop gives up.
MAX_TOOL_ITERATIONS = 40


@lru_cache(maxsize=16)
def command(name: str) -> str:
    """One agent command, byte-identical to the reference instance's."""
    return (_HERE / f"{name}.md").read_text(encoding="utf-8").replace("\r\n", "\n")


# Appended to every command.
HANDOFF = """\
**When something needs a command you cannot run here, tell them to open a new chat for it.** One \
conversation runs one command and cannot hand off to another inside itself. The instruction has \
one correct shape and it must be this explicit: *"Open a new chat and run /setup-scorer there."* \
Not "start it as a new command", not "run it when you are ready" — somebody who does not know a \
conversation is scoped to a single command will type the slash command into this one and watch \
nothing happen. Name the command, and say new chat, every time.
"""

# The director runs all four steps itself, so the handoff above would strand the user.
DIRECTOR_HANDOFF = """\
**There is no other chat, and nothing to hand off.** Every step is yours, and their instructions \
are included below. Never tell the user to open a new chat, never tell them to run a command, and \
never name one to them. Those are how somebody runs a single step on its own; you are running all \
four here, in this conversation.

If you catch yourself about to write "open a new chat", the step you are thinking of is one you \
are meant to do next. Do it instead.
"""

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

{handoff}

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
    """A command plus the environment note — what the model is given as its system prompt."""
    environment = ENVIRONMENT.format(handoff=HANDOFF)
    body = command(name)
    if name == "setup-director":
        environment = ENVIRONMENT.format(handoff=DIRECTOR_HANDOFF)
        body += "".join(
            f"\n\n---\n\n# Step {position}'s instructions, in full ({step})\n\n"
            "Follow these as written when you run this step. They are the same instructions the "
            f"standalone command uses.\n\n{command(step)}"
            for position, step in enumerate(DIRECTOR_STEPS, 1))
    return body + environment


# Which command produces which setup file.
FILE_OWNERS: dict[str, str] = {
    "01-candidate-profile.md": "setup",
    "02-behavioral-profile.md": "setup",
    "04-job-evaluation.md": "setup",
    "07-interview-prep.md": "setup",
    "base_cv.html": "setup",
    "context.md": "setup-context",
    "08-role-angles.md": "setup-role-angles",
    "label.md": "setup-scorer",
    # The two documents the platform writes FOR a person, about a specific job. They ship as
    # scaffolds carrying `<<NAME>>`, `<<CONTACT>>` and three more, and nothing owned them — so
    # nothing filled them. One account's were filled only because they were imported from the
    # single-user reference instance; every account onboarded normally had five placeholders in
    # both prompts and passed every completeness check.
    "tailor_cv.md": "setup-prompts",
    "tailor_cover.md": "setup-prompts",
}

# Files no command produces.
UNOWNED_FILES: dict[str, str] = {}

# The commands a user may start from the chat, in the order they should run.
# `setup-prompts` sits after the profile it reads and before the rubric, because it personalises
# the writing prompts from `01-candidate-profile.md` and `base_cv.html`. It was absent from this
# tuple while being present in `SETUP_AGENTS`: a real command, with real instructions, that no user
# could start and no file depended on.
STARTABLE: tuple[str, ...] = ("setup-director", "setup-context", "setup", "setup-prompts",
                              "setup-role-angles", "setup-scorer")

# What a chat started by a command is called.
COMMAND_TITLES: dict[str, str] = {
    "setup-context": "Your background",
    "setup": "Your profile",
    "setup-role-angles": "Your role angles",
    "setup-scorer": "Your scoring rubric",
    "setup-prompts": "Your document style",
    "setup-director": "Guided setup",
}


# The tools, in the shape the API takes them.
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
