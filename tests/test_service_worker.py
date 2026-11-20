"""The site's service worker (`web/public/sw.js`), run as it is shipped with a stand-in for the browser around
it: what a tap on a notification opens. Node runs it, which the site's own build needs too."""
from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

WORKER = pathlib.Path(__file__).resolve().parents[1] / "web" / "public" / "sw.js"
NODE = shutil.which("node")

# The worker's file, with `self` a recording of what it does, tapped on a notification naming `argv[3]`.
TAP = """
import { readFileSync } from "node:fs";
import vm from "node:vm";

const listeners = {};
const opened = [];
const self = {
  location: new URL("https://app.example/sw.js"),
  addEventListener: (name, listener) => { listeners[name] = listener; },
  skipWaiting: () => undefined,
  registration: {},
  clients: { claim: () => undefined, matchAll: async () => [],
             openWindow: async (url) => { opened.push(url); } },
};
vm.runInNewContext(readFileSync(process.argv[2], "utf8"), { self, URL });
let done = Promise.resolve();
listeners.notificationclick({
  notification: { close: () => undefined, data: { url: process.argv[3] } },
  waitUntil: (promise) => { done = promise; },
});
await done;
console.log(opened[0]);
"""


@pytest.mark.skipif(NODE is None, reason="needs node, as the site's build does")
@pytest.mark.parametrize(("named", "opens"), [
    ("/#/feed?job=7", "https://app.example/#/feed?job=7"),
    ("/#/alerts", "https://app.example/#/alerts"),
    ("https://elsewhere.example/sign-in", "https://app.example/"),
    ("//elsewhere.example/sign-in", "https://app.example/"),
])
def test_a_tap_on_a_notification_opens_a_page_of_this_site_and_no_other(named, opens, tmp_path):
    """The address a notification opens comes in the message, and the worker opened whatever it said: in the
    installed app, under the site's own name. The server writes every address today, so nothing reached it,
    and nothing should be able to: an address anywhere else opens the site's first page instead."""
    script = tmp_path / "tap.mjs"
    script.write_text(TAP)

    ran = subprocess.run([NODE, str(script), str(WORKER), named], capture_output=True, text=True,  # noqa: S603
                         timeout=30, check=True)

    assert ran.stdout.strip() == opens
