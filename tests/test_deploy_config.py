"""The deploy files agree with each other: every setting compose reads is one the server's template
names, every compose command reads the server's settings, and the timers name no one box's layout.

Each of these was a defect before it was a test (2026-11-03): `TUNNEL_TOKEN` was read by compose and
named nowhere an operator would look, and the units ran `/home/ruben/...`, which is one server's
layout and not the next one's."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"

# Set by `deploy/compose.sh` itself, from the checkout's owner, rather than written by an operator.
SET_BY_THE_WRAPPER = {"BROKER_UID"}


def test_every_setting_compose_reads_is_in_the_server_template():
    read = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", (DEPLOY / "compose.yml").read_text(encoding="utf-8")))
    template = (DEPLOY / "server.env.example").read_text(encoding="utf-8")
    named = set(re.findall(r"^([A-Z_][A-Z0-9_]*)=", template, re.MULTILINE))
    assert len(read) > 15, "compose.yml reads hardly anything: the pattern above has stopped matching"
    missing = sorted(read - SET_BY_THE_WRAPPER - named)
    assert missing == [], f"read by compose, named nowhere: {missing}"


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
