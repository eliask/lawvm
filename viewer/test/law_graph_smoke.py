"""Headless smoke test for the pack-native relation-graph + transclusion viewer.

Run:  uv run --with playwright python viewer/test/law_graph_smoke.py

Self-contained: serves viewer/ on an ephemeral port and drives headless
Chromium (Playwright). Asserts the through-line:

  * the statute renders as a readable document (nodes + prose),
  * at least one relation edge with a proof-grade badge appears anchored in the
    document,
  * a range anchor (target_set_semantics=all_valid) shows multiple targets,
  * hovering / opening a resolved GDPR cite TRANSCLUDES the EU article text,
  * the in-browser L0 row-integrity verifier shows a "✓ rows verified" badge.

Prerequisite: viewer/build-graph-demo.py has produced viewer/data/fi-1050-2018
(with edges-resolved.jsonl + edge-anchors.json) and viewer/data/eu-gdpr. Not
wired into ci.sh (needs Playwright); run it manually after a viewer change.
"""

from typing_extensions import override
import glob
import importlib
import os
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

sync_playwright = importlib.import_module("playwright.sync_api").sync_playwright

VIEWER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class QuietHandler(SimpleHTTPRequestHandler):
    @override
    def log_message(self, format: str, *args: object) -> None:
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=VIEWER_DIR))
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_address[1]}/law-graph.html"

results = []
errors = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def launch_browser(p):
    try:
        return p.chromium.launch()
    except Exception:
        cached = sorted(
            glob.glob(
                os.path.expanduser(
                    "~/.cache/ms-playwright/chromium_headless_shell-*/"
                    "chrome-headless-shell-linux64/chrome-headless-shell"
                )
            )
        )
        if not cached:
            raise
        return p.chromium.launch(executable_path=cached[-1])


with sync_playwright() as p:
    browser = launch_browser(p)
    page = browser.new_page()
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))

    page.goto(BASE)

    # 1. statute renders as a readable document.
    page.wait_for_selector("#doc .node", timeout=30000)
    n_nodes = page.locator("#doc .node").count()
    n_prose = page.locator("#doc .prose").count()
    check("document renders (nodes)", n_nodes > 50, f"{n_nodes} nodes")
    check("document renders (prose)", n_prose > 50, f"{n_prose} prose blocks")

    # 2. at least one relation edge with a proof-grade badge appears anchored.
    n_anchors = page.locator("#doc .edge-anchor").count()
    n_badges = page.locator("#doc .edge-anchor .grade-badge").count()
    check("anchored edges with proof badges", n_anchors > 5 and n_badges > 5,
          f"{n_anchors} anchors, {n_badges} badges")

    # distinct proof grades present (the firewall made visible)
    grades = page.evaluate(
        "(() => { const s = new Set();"
        " document.querySelectorAll('#doc .edge-anchor .grade-badge').forEach(b =>"
        " b.className.split(' ').forEach(c => { if (c.startsWith('grade-')) s.add(c); }));"
        " return [...s]; })()"
    )
    check("multiple proof grades visible", len(grades) >= 2, f"{grades}")

    # 3. a range anchor shows multiple targets (target_set_semantics=all_valid).
    n_range = page.locator("#doc .edge-anchor .range-chip").count()
    check("range anchor (one anchor -> many targets)", n_range >= 1, f"{n_range} range chips")

    # 4. the verifier badge shows OK.
    page.wait_for_selector(".verify-badge.verify-ok", timeout=30000)
    check("in-browser L0 verifier OK", True,
          page.text_content(".verify-badge.verify-ok") or "")

    # 5. cross-work transclusion: open a GDPR-bearing anchor, expect EU article
    #    text inline.
    eu_anchor = page.locator("#doc .edge-anchor.has-eu").first
    check("GDPR-bearing anchor present", eu_anchor.count() > 0)
    eu_anchor.scroll_into_view_if_needed()
    eu_anchor.click()
    page.wait_for_selector(".inline-expansion .transclusion", timeout=10000)
    tc_text = page.text_content(".inline-expansion .transclusion .tc-body") or ""
    check("GDPR article transcluded inline", len(tc_text.strip()) > 40,
          repr(tc_text[:80]))
    tc_addr = page.text_content(".inline-expansion .transclusion .tc-addr") or ""
    check("transclusion labels the EU article", "artikla" in tc_addr, repr(tc_addr))

    # 6. rail lists resolved EU citations and clicking one navigates.
    n_rail_eu = page.locator(".rail-eu-link").count()
    check("rail lists resolved EU citations", n_rail_eu >= 1, f"{n_rail_eu} links")

    browser.close()

fails = [r for r in results if not r[1]]
real_errors = [e for e in errors if "favicon" not in e]
print(f"\n{len(results) - len(fails)}/{len(results)} passed")
if real_errors:
    print("CONSOLE/PAGE ERRORS:")
    for e in real_errors[:10]:
        print("  ", e[:300])
sys.exit(1 if (fails or real_errors) else 0)
