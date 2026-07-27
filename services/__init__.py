"""One folder per container, one Dockerfile each.

Split so that any one service can be rebuilt and restarted without taking the platform down —
a hard requirement, since updates are frequent (PLATFORM_ARCHITECTURE.md §3).

**Build coupling and deploy coupling are different problems** (CODE_STRUCTURE.md §5). A change in
``libs/corpus`` means every service importing it gets a new image; that is fine, images are cheap
and CI rebuilds all of them. Deploying is still exactly one container::

    git pull
    docker compose build api
    docker compose up -d --no-deps api     # --no-deps: touch nothing else

``--no-deps`` is the flag that makes that true; without it Compose helpfully recreates linked
services and the blast radius is the whole stack again.

Three things make a one-service restart *safe*, and all three are non-negotiable:

1. Versioned contracts — every service serves ``/health`` with a version, callers assert and 503
   rather than guessing.
2. Restart-safe workers — claims are transactional and expire back into the queue.
3. Migrations run as their own one-shot step, never in a service entrypoint.

**Services never import each other**; they talk over HTTP or the queue. Enforced by import-linter
(see ``libs``' docstring and ``pyproject.toml``).
"""
