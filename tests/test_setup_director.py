"""The setup director and the context command, held to what made them necessary."""
from __future__ import annotations

import re

import pytest

from libs.agents import STARTABLE, command, system_prompt
from libs.db.models.setup import SETUP_AGENTS
from libs.profiles import setup_files

DRAFT = "setup-director"

# Things that exist only in a local checkout of the reference instance.
LOCAL_ONLY = (
    "docker compose", "docker ", ".env", "pipeline/", "scorer/", ".joblib",
    "git clone", "venv", "docs/GETTING_STARTED.md", "docs/VPS_SETUP.md",
    ".claude/commands", "cv/base_cv.html", "CLAUDE.md",
)


def _text(name: str) -> str:
    return command(name)


def _prose(name: str) -> str:
    """The text with wrapping collapsed."""
    return " ".join(command(name).split()).lower()


# Both drafts are reviewed and approved together: the director hands off to `/setup-context`.
DRAFT_COMMANDS = {"setup-director", "setup-context"}


# ── the defect that motivated the rewrite ─────────────────────────────────────────────────────

def test_the_draft_names_nothing_that_does_not_exist_on_this_platform():
    """The gates must be checkable from inside the chat, with the tools the agent actually has."""
    text = _text(DRAFT)
    found = [marker for marker in LOCAL_ONLY if marker in text]

    assert not found, f"the draft still names local-checkout things: {found}"


# ── completeness: the gate must cover exactly what the trainer requires ────────────────────────

def test_the_draft_accounts_for_every_file_the_trainer_gates_on():
    """The director's gate and `train_user`'s gate must be the same eight files."""
    text = _text(DRAFT)
    missing = [name for name in setup_files() if name not in text]

    assert not missing, f"the draft never mentions {missing}, so nothing would produce them"


# ── reachability: every step it names must be startable ───────────────────────────────────────

def test_every_command_the_draft_tells_a_user_to_run_is_one_that_exists():
    """A director that hands off to a command nothing can start strands the user mid-setup."""
    referenced = set(re.findall(r"/([a-z][a-z-]+)", _text(DRAFT)))
    # Words that look like commands but are prose or paths, not handoffs.
    referenced -= DRAFT_COMMANDS
    startable = set(STARTABLE) | set(SETUP_AGENTS)

    unknown = {name for name in referenced if name.startswith("setup") and name not in startable}
    assert not unknown, f"the draft points at steps nothing can start: {sorted(unknown)}"


# ── termination: the reason a director loops forever ──────────────────────────────────────────

def test_the_draft_says_when_to_stop():
    """An agent with no stop condition optimises until the budget runs out."""
    text = _text(DRAFT).lower()

    assert "stop" in text, "no stop instruction at all"
    assert any(phrase in text for phrase in ("do not re-read", "do not offer another pass",
                                             "there isn't one", "there is nothing else")), \
        "nothing tells the director that a finished profile is finished"


def test_the_draft_asks_for_the_experience_a_cv_leaves_out():
    """Owner's requirement: internships, side projects and volunteering are often what separates two."""
    text = _text(DRAFT).lower()

    for topic in ("internship", "side project", "volunteer"):
        assert topic in text, f"the draft never asks about {topic}s"


# ── the same hygiene every shipped prompt is held to ──────────────────────────────────────────

def test_the_draft_carries_no_ones_personal_details():
    """A scaffold that names a real person is a claim on every future user's profile."""
    text = _text(DRAFT)

    for leak in ("Syntagma", "FlowSolutions", "Sheffield", "Bloomberg", "ByBit", "Ruben", "Lekli"):
        assert leak not in text, f"{leak} is in the draft director"


def test_both_commands_are_startable():
    """Live as of 2026-07-31. A director a user cannot start is the gap they were written to close."""
    assert DRAFT in SETUP_AGENTS
    assert CONTEXT in SETUP_AGENTS
    for name in (DRAFT, CONTEXT):
        assert "DRAFT" not in _text(name).split("\n")[0].upper(), f"{name} still says draft"


def test_the_draft_gets_the_environment_note_like_every_other_command():
    """`system_prompt` is what the model actually receives."""
    prompt = system_prompt(DRAFT)

    assert "There is no checkout and no filesystem" in prompt
    assert prompt.startswith("# /setup-director")


@pytest.mark.parametrize("name", [DRAFT])
def test_the_draft_is_not_obviously_truncated(name):
    text = _text(name)

    assert text.rstrip().endswith("."), "ends mid-sentence"
    assert len(text) > 2_000, "too short to be a director"


# ── the command that fills the hole the audit found ───────────────────────────────────────────

CONTEXT = "setup-context"


def test_the_context_command_owns_the_file_nothing_used_to_write():
    """The gap that made setup impossible to finish."""
    from libs.agents import FILE_OWNERS, UNOWNED_FILES

    assert FILE_OWNERS["context.md"] == CONTEXT
    assert UNOWNED_FILES == {}, "a required file with no owner is an account that cannot finish"


def test_the_context_command_names_nothing_local_only():
    found = [marker for marker in LOCAL_ONLY if marker in _text(CONTEXT)]

    assert not found, f"the context draft names local-checkout things: {found}"


def test_the_context_command_asks_for_what_a_cv_drops():
    """The reason this file exists rather than being derived from a CV."""
    text = _prose(CONTEXT)

    for topic in ("internship", "volunteer", "contract", "another field"):
        assert topic in text, f"the context draft never asks about {topic}"


def test_the_context_command_refuses_to_guess_between_conflicting_documents():
    """Two CVs and a LinkedIn export disagree on titles and dates — observed on a real account."""
    text = _text(CONTEXT).lower()

    assert "conflict" in text
    assert "never pick for them" in text or "never invent" in text


def test_the_context_command_forbids_placeholders():
    """`is_filled` treats a bracket as unfinished, so a stub would leave the account unable to train."""
    assert "placeholder" in _text(CONTEXT).lower()


def test_the_director_runs_context_first():
    """`setup-role-angles` states that `context.md` must already be written."""
    # Asserted on the order the steps are carried in, not on slash names in the prose.
    from libs.agents import DIRECTOR_STEPS

    assert DIRECTOR_STEPS.index("setup-context") < DIRECTOR_STEPS.index("setup-role-angles")
    assert "The order is not a preference" in _text(DRAFT)


# ── the context file, reverse-engineered from a real one ──────────────────────────────────────

def test_the_context_draft_covers_what_a_real_context_file_contains():
    """Modelled on the owner's own file rather than on what seemed sensible."""
    text = _prose(CONTEXT)

    for topic in ("certification", "publication", "methodolog", "research method",
                  "spelling", "header", "education is rendered", "application language"):
        assert topic in text, f"a real context file has {topic} and the draft never asks"


def test_the_context_draft_excludes_the_platforms_own_rules():
    """A real context file mixes the person with the workflow."""
    text = _prose(CONTEXT)

    assert "not the place for how the platform works" in text


# ── a scenario walked end to end ──────────────────────────────────────────────────────────────

def test_the_directors_steps_produce_exactly_the_required_files():
    """Model the run before paying for one.

    `setup-prompts` joined this list on 2026-08-09. It was a real command with real instructions
    that no user could start and no file depended on, so `tailor_cv.md` and `tailor_cover.md` were
    seeded carrying five placeholders each and never written — and every completeness check passed,
    because both files were declared "usable as shipped".
    """
    claimed: set[str] = set()
    for name in ("setup-context", "setup", "setup-prompts", "setup-role-angles", "setup-scorer"):
        text = _text(name)
        claimed |= {f for f in setup_files() if f in text}

    assert claimed == set(setup_files()), (
        f"steps claim {sorted(claimed)}; the trainer wants {sorted(setup_files())}")


def test_the_director_never_promises_a_step_that_writes_nothing():
    """Each step in the map must own at least one required file."""
    director = _text(DRAFT)

    from libs.agents import DIRECTOR_STEPS

    assert director  # the map itself still has to exist
    for name in DIRECTOR_STEPS:
        owned = [f for f in setup_files() if f in _text(name)]
        assert owned, f"{name} is a step that writes none of the required files"


def test_the_director_carries_every_step_it_has_to_run():
    """It used to be told to follow instructions it could not see."""
    from libs.agents import DIRECTOR_STEPS, command, system_prompt

    prompt = system_prompt("setup-director")
    for step in DIRECTOR_STEPS:
        body = command(step)
        # A distinctive run of the real body, not just its name in a heading.
        assert body[200:600] in prompt, f"{step} is not carried in full"


def test_the_director_is_startable_and_runs_first():
    """It is what somebody setting up for the first time wants: one chat, four steps."""
    from libs.agents import DIRECTOR_STEPS, STARTABLE

    assert STARTABLE[0] == "setup-director"
    for step in DIRECTOR_STEPS:
        assert step in STARTABLE


def test_the_director_does_not_send_anybody_to_another_chat():
    """The environment block tells every command to say "open a new chat" when it cannot do something."""
    from libs.agents import command

    body = command("setup-director")
    assert "You are not handing off" in body
    assert "no other chat to open" in body


def test_the_director_runs_every_command_that_owns_a_file():
    """**A command that owns a file and is not in the director's steps never runs for anybody.**

    `setup-prompts` owns `tailor_cv.md` and `tailor_cover.md` — the two prompts that ship as
    scaffolds carrying `<<NAME>>`, `<<CONTACT>>` and three more placeholders. It was in `STARTABLE`,
    it was in `FILE_OWNERS`, it had a full command file, and it was absent from `DIRECTOR_STEPS`. So
    guided setup ran four steps out of five and every account onboarded through it kept five
    placeholders in both writing prompts — while passing every completeness check, because
    `SHIPS_USABLE` treats those two as complete on arrival.

    Measured on the live platform 2026-08-12: the second account had all five placeholders in both
    files; the founder's were filled only because they were imported from the single-user reference
    instance, not because anything on this platform filled them.
    """
    from libs.agents import DIRECTOR_STEPS, FILE_OWNERS

    owners = set(FILE_OWNERS.values())
    missing = sorted(owners - set(DIRECTOR_STEPS))

    assert not missing, (
        f"these commands own a setup file and guided setup never runs them: {missing}. A file whose "
        "owner is not a director step is a file nothing fills.")
