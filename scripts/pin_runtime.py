"""Write `deploy/docker/constraints.txt`: every package the services' images install, at the version this
environment holds, which is the version the gates ran on (CLAUDE.md §7).

    .venv/bin/python scripts/pin_runtime.py

The services' own `requirements.txt` say what they need and from which version up; this says which version
an image gets. Without it each build took whatever was newest that day, into images that hold the key to
every stored credential, and a build could differ from the code the gates passed with nothing saying so.
To move a pin: install the new version here, run the gates, run this.
"""
from __future__ import annotations

import re
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
CONSTRAINTS = ROOT / "deploy" / "docker" / "constraints.txt"
# The sign-in broker pins its own in its `requirements.txt`: an older FastAPI, on purpose (its Dockerfile).
SERVICES = ("api", "docgen", "harvester", "notifier")


def _name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def required() -> dict[str, set[str]]:
    """Each package a service names, with the extras it asks for of it."""
    asked: dict[str, set[str]] = {}
    for service in SERVICES:
        for line in (ROOT / "services" / service / "requirements.txt").read_text().splitlines():
            if text := line.split("#", 1)[0].strip():
                requirement = Requirement(text)
                asked.setdefault(_name(requirement.name), set()).update(requirement.extras)
    return asked


def pins() -> tuple[dict[str, str], list[str]]:
    """`{package: version}` for everything those requirements bring in that is installed here, and the names
    that are not installed here, which stay as their service states them."""
    found: dict[str, str] = {}
    absent: list[str] = []
    waiting = list(required().items())
    while waiting:
        name, extras = waiting.pop()
        if name in found or name in absent:
            continue
        try:
            installed = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            absent.append(name)
            continue
        found[name] = installed.version
        for text in installed.requires or ():
            needs = Requirement(text)
            wanted = needs.marker is None or any(needs.marker.evaluate({"extra": extra})
                                                 for extra in (*extras, ""))
            if wanted:
                waiting.append((_name(needs.name), set(needs.extras)))
    return dict(sorted(found.items())), sorted(absent)


def written() -> str:
    found, absent = pins()
    head = ("# Written by scripts/pin_runtime.py from the environment the gates ran in. Do not edit:\n"
            "# install the version wanted, run the gates, and run it again.\n")
    if absent:
        head += f"# Not installed there, so left as its service states it: {', '.join(absent)}\n"
    return head + "".join(f"{name}=={version}\n" for name, version in found.items())


if __name__ == "__main__":
    CONSTRAINTS.write_text(written(), encoding="utf-8", newline="\n")
    print(f"wrote {CONSTRAINTS.relative_to(ROOT)} ({len(pins()[0])} packages)")
