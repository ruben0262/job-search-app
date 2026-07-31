"""The draft platform director, measured against the one it would replace.

**"Better" has to be checkable or it is an opinion.** A conversational prompt cannot be fully judged
without running it on somebody's credential, and that is a separate, paid exercise. What *can* be
checked mechanically is the class of defect that made the rewrite necessary in the first place: a
director whose completion gates name things that do not exist on this platform, and whose steps
point at commands nothing can start.

So every assertion here is a property the current `setup-all.md` measurably fails and the draft
measurably passes. The current file is asserted to fail on purpose — if somebody fixes it in place,
this test fails and tells them the draft is redundant, which is the right outcome.
"""
from __future__ import annotations

import re

import pytest

from libs.agents import STARTABLE, command, system_prompt
from libs.db.models.setup import SETUP_AGENTS
from libs.profiles import setup_files

DRAFT = "setup-director"
CURRENT = "setup-all"

# Things that exist only in a local checkout of the reference instance. A gate that names one of
# these cannot be verified from inside the platform, where there is no filesystem and no shell.
LOCAL_ONLY = (
    "docker compose", "docker ", ".env", "pipeline/", "scorer/", ".joblib",
    "git clone", "venv", "docs/GETTING_STARTED.md", "docs/VPS_SETUP.md",
    ".claude/commands", "cv/base_cv.html", "CLAUDE.md",
)


def _text(name: str) -> str:
    return command(name)


def _prose(name: str) -> str:
    """The text with wrapping collapsed. A phrase assertion must not depend on where markdown
    happened to break the line — that tests the formatter, not the instruction."""
    return " ".join(command(name).split()).lower()


# Both drafts are reviewed and approved together: the director hands off to `/setup-context`, and
# approving one without the other leaves the gap that motivated them.
DRAFT_COMMANDS = {"setup-director", "setup-context"}


# ── the defect that motivated the rewrite ─────────────────────────────────────────────────────

def test_the_draft_names_nothing_that_does_not_exist_on_this_platform():
    """The gates must be checkable from inside the chat, with the tools the agent actually has."""
    text = _text(DRAFT)
    found = [marker for marker in LOCAL_ONLY if marker in text]

    assert not found, f"the draft still names local-checkout things: {found}"


def test_the_current_director_is_the_thing_being_fixed():
    """Documents *why* the draft exists, and fails if somebody fixes the original in place."""
    found = [marker for marker in LOCAL_ONLY if marker in _text(CURRENT)]

    assert found, (
        "setup-all.md no longer names local-checkout paths — if it has been rewritten for the "
        "platform, this draft is redundant and should be deleted rather than kept in parallel")


# ── completeness: the gate must cover exactly what the trainer requires ────────────────────────

def test_the_draft_accounts_for_every_file_the_trainer_gates_on():
    """**The director's gate and `train_user`'s gate must be the same eight files.**

    If they drift, a user is told setup is complete and then refused a model with no explanation —
    the worst shape this failure can take, because both components are behaving correctly.
    """
    text = _text(DRAFT)
    missing = [name for name in setup_files() if name not in text]

    assert not missing, f"the draft never mentions {missing}, so nothing would produce them"


def test_the_current_director_does_not_cover_them():
    """The concrete gap: three of the eight files are absent from the original's stages."""
    text = _text(CURRENT)
    absent = [name for name in setup_files() if name not in text]

    assert absent, "setup-all.md now covers every required file; the draft may be redundant"


# ── reachability: every step it names must be startable ───────────────────────────────────────

def test_every_command_the_draft_tells_a_user_to_run_is_one_that_exists():
    """A director that hands off to a command nothing can start strands the user mid-setup.

    This is exactly what shipped: `context.md`, `08-role-angles.md` and `label.md` belong to steps
    the chat never offered, so a new account could not finish setup and could never train.
    """
    referenced = set(re.findall(r"/([a-z][a-z-]+)", _text(DRAFT)))
    # Words that look like commands but are prose or paths, not handoffs.
    referenced -= DRAFT_COMMANDS
    startable = set(STARTABLE) | set(SETUP_AGENTS)

    unknown = {name for name in referenced if name.startswith("setup") and name not in startable}
    assert not unknown, f"the draft points at steps nothing can start: {sorted(unknown)}"


# ── termination: the reason a director loops forever ──────────────────────────────────────────

def test_the_draft_says_when_to_stop():
    """**An agent with no stop condition optimises until the budget runs out.**

    The owner's requirement, and the failure mode a director is most prone to: it can always find
    something else to improve about a profile, and every pass spends the user's own credential.
    """
    text = _text(DRAFT).lower()

    assert "stop" in text, "no stop instruction at all"
    assert any(phrase in text for phrase in ("do not re-read", "do not offer another pass",
                                             "there isn't one", "there is nothing else")), \
        "nothing tells the director that a finished profile is finished"


def test_the_draft_asks_for_the_experience_a_cv_leaves_out():
    """Owner's requirement: internships, side projects and volunteering are often what separates
    two otherwise similar postings, and none of it reliably survives onto a CV."""
    text = _text(DRAFT).lower()

    for topic in ("internship", "side project", "volunteer"):
        assert topic in text, f"the draft never asks about {topic}s"


# ── the same hygiene every shipped prompt is held to ──────────────────────────────────────────

def test_the_draft_carries_no_ones_personal_details():
    """A scaffold that names a real person is a claim on every future user's profile."""
    text = _text(DRAFT)

    for leak in ("Syntagma", "FlowSolutions", "Sheffield", "Bloomberg", "ByBit", "Ruben", "Lekli"):
        assert leak not in text, f"{leak} is in the draft director"


def test_the_draft_is_marked_as_a_draft_and_cannot_be_run_yet():
    """It ships alongside the original rather than replacing it, and stays inert until reviewed."""
    assert DRAFT not in SETUP_AGENTS, "the draft is startable — it was not meant to be, yet"
    assert "DRAFT" in _text(DRAFT).split("\n")[0].upper()


def test_the_draft_gets_the_environment_note_like_every_other_command():
    """`system_prompt` is what the model actually receives; a command that only reads well on its
    own is not the thing being tested."""
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


def test_the_context_command_is_also_a_draft():
    """It owns `context.md`, which nothing currently writes. Inert until reviewed, like the
    director — approving one without the other leaves the same gap."""
    assert CONTEXT not in SETUP_AGENTS
    assert "DRAFT" in _text(CONTEXT).split("\n")[0].upper()


def test_the_context_command_names_nothing_local_only():
    found = [marker for marker in LOCAL_ONLY if marker in _text(CONTEXT)]

    assert not found, f"the context draft names local-checkout things: {found}"


def test_the_context_command_asks_for_what_a_cv_drops():
    """The reason this file exists rather than being derived from a CV."""
    text = _prose(CONTEXT)

    for topic in ("internship", "volunteer", "contract", "another field"):
        assert topic in text, f"the context draft never asks about {topic}"


def test_the_context_command_refuses_to_guess_between_conflicting_documents():
    """Two CVs and a LinkedIn export disagree on titles and dates — observed on a real account.
    A guess here becomes a fabricated claim on an application."""
    text = _text(CONTEXT).lower()

    assert "conflict" in text
    assert "never pick for them" in text or "never invent" in text


def test_the_context_command_forbids_placeholders():
    """`is_filled` treats a bracket as unfinished, so a stub would leave the account unable to
    train — the failure would surface as a refusal nobody could explain."""
    assert "placeholder" in _text(CONTEXT).lower()


def test_the_director_runs_context_first():
    """`setup-role-angles` states that `context.md` must already be written, so an order that runs
    it later produces files grounded on nothing."""
    text = _text(DRAFT)
    assert text.index("/setup-context") < text.index("/setup-role-angles")
    assert "The order is not a preference" in text


# ── the context file, reverse-engineered from a real one ──────────────────────────────────────

def test_the_context_draft_covers_what_a_real_context_file_contains():
    """**Modelled on the owner's own file rather than on what seemed sensible.**

    Reading its section headings found six things the first draft would have silently dropped —
    certifications, publications, methodologies, research methods, and the per-person *rendering*
    conventions that every CV must follow. The last group is the dangerous one: a missing header
    location rule or spelling convention does not fail, it produces a confidently wrong document.
    """
    text = _prose(CONTEXT)

    for topic in ("certification", "publication", "methodolog", "research method",
                  "spelling", "header", "education is rendered", "application language"):
        assert topic in text, f"a real context file has {topic} and the draft never asks"


def test_the_context_draft_excludes_the_platforms_own_rules():
    """A real context file mixes the person with the workflow, because in a checkout they live in
    one document. Here the workflow half is the platform's and identical for everyone — writing it
    per user would mean every account carrying its own copy of rules it cannot change."""
    text = _prose(CONTEXT)

    assert "not the place for how the platform works" in text


# ── a scenario walked end to end ──────────────────────────────────────────────────────────────

def test_the_directors_four_steps_produce_exactly_the_required_files():
    """**Model the run before paying for one.** Every file the four steps claim to write, taken
    together, must be exactly the set the trainer gates on — no more, no fewer.

    A missing one strands the account at a gate it cannot pass; a spare one is a step nobody needs
    that still costs a conversation.
    """
    claimed: set[str] = set()
    for name in ("setup-context", "setup", "setup-role-angles", "setup-scorer"):
        text = _text(name)
        claimed |= {f for f in setup_files() if f in text}

    assert claimed == set(setup_files()), (
        f"steps claim {sorted(claimed)}; the trainer wants {sorted(setup_files())}")


def test_the_director_never_promises_a_step_that_writes_nothing():
    """Each step in the map must own at least one required file, or it is a conversation that
    cannot move the account closer to being trainable."""
    director = _text(DRAFT)

    for name in ("setup-context", "setup", "setup-role-angles", "setup-scorer"):
        assert f"/{name}" in director, f"{name} is not in the director's map"
        owned = [f for f in setup_files() if f in _text(name)]
        assert owned, f"/{name} is in the map but writes none of the required files"
