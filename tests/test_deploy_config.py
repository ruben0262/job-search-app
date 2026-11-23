"""The deploy files agree with each other: every setting compose reads is one the server's template
names, every compose command reads the server's settings, and the timers name no one box's layout.

Each of these was a defect before it was a test (2026-11-03): `TUNNEL_TOKEN` was read by compose and
named nowhere an operator would look, and the units ran one person's `/home/...`, which is one server's
layout and not the next one's."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"

# Set by `deploy/compose.sh` itself, from the checkout's owner, rather than written by an operator.
SET_BY_THE_WRAPPER = {"APP_UID"}


def test_every_setting_compose_reads_is_in_the_server_template():
    read = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", (DEPLOY / "compose.yml").read_text(encoding="utf-8")))
    template = (DEPLOY / "server.env.example").read_text(encoding="utf-8")
    named = set(re.findall(r"^([A-Z_][A-Z0-9_]*)=", template, re.MULTILINE))
    assert len(read) > 15, "compose.yml reads hardly anything: the pattern above has stopped matching"
    missing = sorted(read - SET_BY_THE_WRAPPER - named)
    assert missing == [], f"read by compose, named nowhere: {missing}"


def test_every_key_a_board_runs_on_reaches_the_harvester():
    """A board runs only with its key (`source_config.KEY_GATED`), and the harvester's container is given each
    setting by name: a key put in the server's settings and not named here never arrives, and its board is
    left out of every run with nothing to say which (Reed's, 2026-11-23)."""
    import yaml

    from services.harvester.source_config import KEY_GATED

    services = yaml.safe_load((DEPLOY / "compose.yml").read_text(encoding="utf-8"))["services"]
    given = set(services["harvester"]["environment"])
    assert sorted(set(KEY_GATED.values()) - given) == []


def test_every_compose_command_goes_through_the_wrapper():
    """A bare `docker compose` reads no settings: every `${VAR:?}` fails and nothing starts."""
    for path in (DEPLOY / "deploy.sh", *DEPLOY.glob("*.service")):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"^[^#]*docker compose", text, re.MULTILINE), f"{path.name} runs compose itself"
    assert 'COMPOSE="deploy/compose.sh"' in (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
    assert "--env-file \"$ENV_FILE\"" in (DEPLOY / "compose.sh").read_text(encoding="utf-8")


def test_the_timers_name_no_one_servers_layout():
    """Each unit's placeholders are the ones the installer fills in, and nothing in them is a fixed path."""
    rendered = set(re.findall(r"s#(@[A-Z]+@)#", (DEPLOY / "install-units.sh").read_text(encoding="utf-8")))
    for unit in DEPLOY.glob("*.service"):
        text = unit.read_text(encoding="utf-8")
        assert set(re.findall(r"@[A-Z]+@", text)) <= rendered, unit.name
        assert "/home/" not in text and "deploy/.env" not in text, unit.name


def test_no_running_code_or_config_reads_settings_from_deploy():
    """They live in certificates/ (owner, 2026-11-03). The README's steps for the old VPS name its
    `deploy/.env` on purpose: that box still has one."""
    code = [path for base in ("libs", "services", "deploy", "scripts", "web/scripts")
            for path in (ROOT / base).rglob("*")
            if path.suffix in {".py", ".sh", ".yml", ".service", ".timer"}
            and "__pycache__" not in path.parts]
    assert len(code) > 100, "hardly any files: the folders above have moved"
    offenders = [path.relative_to(ROOT).as_posix() for path in [*code, ROOT / "pyproject.toml"]
                 if "deploy/.env" in path.read_text(encoding="utf-8")]
    assert offenders == []


def test_every_image_runs_the_python_the_code_is_written_for():
    """The sign-in broker's image was `node:22-slim` with Debian's own python3, which is 3.11. The code
    is written for 3.12 (`def insert[D: Document]`), so on the first deploy to the new server it
    crashed on import and restarted forever (2026-11-03). Every image ends on the one Python."""
    target = re.search(r'target-version = "py3(\d+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    python = f"python:3.{target.group(1)}-slim"
    dockerfiles = sorted((DEPLOY / "docker").glob("Dockerfile.*"))
    assert len(dockerfiles) >= 6, [path.name for path in dockerfiles]
    for dockerfile in dockerfiles:
        stages = re.findall(r"^FROM\s+(\S+)", dockerfile.read_text(encoding="utf-8"), re.MULTILINE)
        assert stages[-1] == python, f"{dockerfile.name} runs on {stages[-1]}, not {python}"


def _site(health: dict | None):
    """A web server on a free local port answering `/health` with `health` (`None`: its own address)."""
    import http.server
    import json
    import threading

    class Health(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            url = f"http://127.0.0.1:{self.server.server_port}"
            body = json.dumps(health or {"status": "ok", "public_url": url}, separators=(",", ":"))
            self.send_response(200 if self.path == "/health" else 404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _reach(url: str):
    import os
    import subprocess

    return subprocess.run(["bash", str(DEPLOY / "reach.sh"), url], capture_output=True, text=True,  # noqa: S603, S607 - our own script
                          env={**os.environ, "REACH_TRIES": "1"}, timeout=30, check=False)


def test_the_deploy_passes_when_the_emails_address_reaches_this_site():
    server = _site(None)
    try:
        result = _reach(f"http://127.0.0.1:{server.server_port}/")
    finally:
        server.shutdown()
    assert result.returncode == 0, result.stdout
    assert "the links in emails work" in result.stdout


def test_the_deploy_fails_when_the_emails_address_reaches_another_server():
    """The old VPS still answering at the old name: up, healthy, and not where the accounts are."""
    server = _site({"status": "ok"})
    try:
        result = _reach(f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown()
    assert result.returncode == 1
    assert "does not reach this site" in result.stdout and "PUBLIC_URL" in result.stdout


def test_the_deploy_fails_when_the_emails_address_reaches_nothing():
    """`https://mahoraga-app…` once the old tunnel let it go: "This site can't be reached"."""
    server = _site(None)
    port = server.server_port
    server.shutdown()
    server.server_close()
    result = _reach(f"http://127.0.0.1:{port}")
    assert result.returncode == 1
    assert "does not reach this site" in result.stdout


def test_every_deploy_checks_the_emails_address():
    """A check nobody runs is no check: `deploy.sh` runs it with the address the running site uses."""
    text = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
    assert re.search(r"^deploy/reach\.sh .*site_url", text, re.MULTILINE)


# ── the nightly backup (2026-11-20) ───────────────────────────────────────────────────────────

def _backup():
    import importlib.util

    spec = importlib.util.spec_from_file_location("backup", DEPLOY / "scripts" / "backup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_backup_is_started_with_the_databases_address_and_nothing_else_of_the_servers_settings():
    """Its unit says the key to every stored credential is absent from its environment, and gave it all of
    `server.env`, the key with it: a dump written by a process that holds the key to what is in it."""
    unit = (DEPLOY / "backup.service").read_text()
    [started] = re.findall(r"^ExecStart=(.+)$", unit, flags=re.M)
    command = started.split()

    assert command[:2] == ["/usr/bin/env", "-i"], "started with an empty environment"
    given = {word.split("=", 1)[0] for word in command[2:command.index("@REPO@/.venv/bin/python")]}
    assert given == {"PATH", "MONGODB_URI", "MONGODB_DB"}


def test_old_copies_of_the_archive_go_with_the_backups_and_the_newest_never_does(tmp_path):
    """A whole copy of the corpus archive was written beside each night's backup and none was ever removed.
    Each copy holds everything the ones before it do, so the old ones go; the newest stays whatever its
    age, as the only copy of a posting retention has deleted."""
    import os
    import time

    month_ago, today = time.time() - 40 * 86400, time.time()
    for name, written in (("mahoraga-old.jsonl.gz", month_ago), ("mahoraga-new.jsonl.gz", today),
                          ("corpus-archive-1.tar.gz", month_ago), ("corpus-archive-2.tar.gz", month_ago)):
        (tmp_path / name).write_bytes(b"x")
        os.utime(tmp_path / name, (written, written))

    assert _backup().prune(tmp_path, 30) == 2

    left = sorted(path.name for path in tmp_path.iterdir())
    assert left == ["corpus-archive-2.tar.gz", "mahoraga-new.jsonl.gz"]


# ── what the images run as, and what they are built from (2026-11-20) ─────────────────────────

DOCKERFILES = sorted((DEPLOY / "docker").glob("Dockerfile.*"))


def test_no_image_runs_as_root():
    """Every service but the sign-in broker ran as root, the one that drives a browser without its sandbox
    and three AI tools among them: a fault in any of it was root, beside the key to every stored credential.
    Each runs as an account of its own, the last word its Dockerfile says about who."""
    for dockerfile in DOCKERFILES:
        users = re.findall(r"^USER\s+(\S+)", dockerfile.read_text(), flags=re.M)
        assert users and users[-1] not in ("root", "0"), f"{dockerfile.name} runs as root"


def test_every_image_is_built_for_the_account_that_owns_the_servers_folders():
    """The services write to `logs/` and `archive/`, which the server lends them: so each image's account
    has the id of the checkout's owner, read once by the wrapper and handed to every build."""
    import yaml

    compose = yaml.safe_load((DEPLOY / "compose.yml").read_text())
    built = {name: service["build"] for name, service in compose["services"].items() if "build" in service}
    assert len(built) >= 7, "the premise: the services built here"
    for name, build in built.items():
        assert build.get("args", {}).get("APP_UID") == "${APP_UID:-1001}", name
        assert "ARG APP_UID" in (ROOT / build["dockerfile"]).read_text(), name
    assert 'export APP_UID="${APP_UID:-$(stat -c %u "$ROOT")}"' in (DEPLOY / "compose.sh").read_text()


def test_a_deploy_lets_the_document_being_written_finish():
    """**A relationship, not two numbers.** Stopped, a container has Docker's ten seconds unless it is given
    more, and a call writing a CV may take `libs.llm.client.TIMEOUT_SEC`: a deploy threw away a draft the
    person's own AI plan had paid for, and the retry paid again. The worker stops after the job in hand
    (`libs.queue.worker`), so what it is given to stop in is longer than the longest call."""
    import yaml

    from libs.llm.client import TIMEOUT_SEC

    grace = yaml.safe_load((DEPLOY / "compose.yml").read_text())["services"]["docgen"]["stop_grace_period"]
    amount, unit = re.fullmatch(r"(\d+)([sm])", grace).groups()

    assert int(amount) * {"s": 1, "m": 60}[unit] > TIMEOUT_SEC


def test_the_deploy_hands_the_folders_over_before_anything_writes_to_them():
    """What the services wrote while they ran as root is root's, and the account they run as now cannot add
    to it: a log that cannot be opened, an archive that cannot be written. Handed over before the schema
    step, the first thing a deploy runs in a container."""
    script = (DEPLOY / "deploy.sh").read_text()
    handed = script.index("--user root --entrypoint chown harvester")
    assert "/var/log/jobsearch /var/lib/jobsearch/archive" in script[handed:handed + 200]
    assert handed < script.index('say "Schema"') < script.index('say "Recreating"')


def test_the_images_install_the_versions_the_gates_ran_on():
    """A build took whatever was newest that day, so an image could differ from the code the gates passed
    with nothing saying so. Every service's install names the one list of versions
    (`scripts/pin_runtime.py`), everything a service asks for that is installed here is on it, and what is
    on it is the version installed here: move a version here without the list, and this fails."""
    import importlib.util
    from importlib import metadata

    spec = importlib.util.spec_from_file_location("pin_runtime", ROOT / "scripts" / "pin_runtime.py")
    pin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pin)

    pinned = dict(line.strip().split("==") for line in pin.CONSTRAINTS.read_text().splitlines()
                  if "==" in line and not line.startswith("#"))
    assert len(pinned) > 20, "the premise: the list is read"
    for name, version in pinned.items():
        try:
            here = metadata.version(name)
            assert here == version, f"{name} is {here} here and {version} in the list"
        except metadata.PackageNotFoundError:
            continue  # not on this machine (another system's package): nothing here to disagree with it
    asked = [name for name in pin.required() if name not in pin.pins()[1]]
    assert asked and not [name for name in asked if name not in pinned]

    for service in pin.SERVICES:
        dockerfile = (DEPLOY / "docker" / f"Dockerfile.{service}").read_text()
        installs = re.findall(r"^RUN pip install .*$", dockerfile, flags=re.M)
        assert installs and all("-c /tmp/constraints.txt" in line for line in installs), service


def test_the_tunnel_is_a_version_somebody_chose():
    """It holds the tunnel's token, and `latest` is whatever had been published the day the server pulled."""
    [image] = re.findall(r"image:\s*(cloudflare/cloudflared\S*)", (DEPLOY / "compose.yml").read_text())
    assert re.fullmatch(r"cloudflare/cloudflared:\d{4}\.\d+\.\d+", image), image
