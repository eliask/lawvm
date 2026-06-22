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

AND the TIME lens (driven from the same pack, on the heavily-amended
Ulkomaalaislaki 301/2004):

  * a real time axis with many change-date ticks renders,
  * scrubbing to two different dates yields DIFFERENT reconstructed text for an
    amended provision (point-in-time reconstruction from the pack intervals),
  * per-provision inline history opens under a clicked unit,
  * a lifecycle strip (change badge + micro time-strip) renders, and a repealed
    provision renders a GHOST tombstone,
  * § quick-jump scrolls to a section,
  * the verify badge recomputes the checkpoint tree_hash for the scrubbed date.

Prerequisite: viewer/build-graph-demo.py has produced viewer/data/fi-1050-2018
(with edges-resolved.jsonl + edge-anchors.json), viewer/data/eu-gdpr, and
viewer/data/fi-301-2004 (the time-lens pack). Not wired into ci.sh (needs
Playwright); run it manually after a viewer change.
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

    # 7. TIME lens (on the same viewer, default fi-1050 pack): a real time axis
    #    with change-date ticks renders and a checkpoint verify line appears.
    n_ticks_1050 = page.locator("#timeaxis .ta-tick").count()
    check("time axis renders ticks (fi-1050)", n_ticks_1050 >= 2, f"{n_ticks_1050} ticks")

    # Switch to the heavily-amended Ulkomaalaislaki for the full time lens.
    page.select_option("#statute-select", label="Ulkomaalaislaki (301/2004)")
    page.wait_for_function(
        "() => document.querySelectorAll('#timeaxis .ta-tick').length > 20",
        timeout=30000,
    )
    n_ticks = page.locator("#timeaxis .ta-tick").count()
    check("dense time axis (fi-301 ~93 dates)", n_ticks > 20, f"{n_ticks} ticks")

    # The list of change dates available in the scrubber.
    date_opts = page.evaluate(
        "() => Array.from(document.querySelectorAll('#date-select option'))"
        ".map(o => o.value).filter(v => v)"
    )
    check("scrubber lists change dates", len(date_opts) > 20, f"{len(date_opts)} dates")

    # Point-in-time reconstruction: find a provision whose text DIFFERS between
    # an early and a late date (an amended provision). Scrub to two dates and
    # compare the rendered text of the same address.
    def texts_at(date):
        page.select_option("#date-select", date)
        page.wait_for_timeout(120)
        return page.evaluate(
            "() => { const m = {};"
            " document.querySelectorAll('#doc .prose[data-addr]').forEach(p =>"
            " { m[p.getAttribute('data-addr')] = p.textContent; }); return m; }"
        )

    early = texts_at(date_opts[1])
    late = texts_at(date_opts[-1])
    diff_addr = None
    for addr, t0 in early.items():
        if addr in late and late[addr] and late[addr] != t0:
            diff_addr = addr
            break
    check(
        "point-in-time reconstruction differs across dates",
        diff_addr is not None,
        f"changed addr: {diff_addr}",
    )

    # Per-provision inline history opens under a clicked unit.
    hist_btn = page.locator("#doc .hist-btn").first
    check("history affordance present", hist_btn.count() > 0)
    hist_btn.scroll_into_view_if_needed()
    hist_btn.click()
    page.wait_for_selector("#doc .prov-history .ph-list", timeout=10000)
    n_hist_items = page.locator("#doc .prov-history .ph-item").count()
    check("per-provision history opens inline", n_hist_items >= 1, f"{n_hist_items} items")

    # A lifecycle strip (change badge + micro time-strip) renders.
    n_strips = page.locator("#doc .chg-badge .chg-strip").count()
    check("lifecycle strips render", n_strips >= 1, f"{n_strips} strips")

    # A GHOST tombstone renders for a repealed provision at the latest date.
    n_ghosts = page.locator("#doc .node.tombstone[data-ghost='1']").count()
    check("ghost tombstone renders (repealed provision)", n_ghosts >= 1, f"{n_ghosts} ghosts")

    # § quick-jump scrolls to a section.
    sec_label = page.evaluate(
        "() => { for (const a of document.querySelectorAll('#doc .node.kind-section')) {"
        " const m = /section:([^/]+)$/.exec(a.getAttribute('data-addr') || '');"
        " if (m) return m[1]; } return null; }"
    )
    check("a section address exists for § jump", sec_label is not None, f"{sec_label}")
    if sec_label:
        page.fill("#sec-jump", sec_label)
        page.press("#sec-jump", "Enter")
        page.wait_for_timeout(200)
        flashed = page.locator("#doc .node-row.flash, #doc .node-row").count()
        not_found = page.evaluate(
            "() => document.getElementById('sec-jump').classList.contains('nf')"
        )
        check("§ quick-jump resolves a section", not not_found, f"§{sec_label}")

    # The verify badge stays OK and recomputes the checkpoint for the scrubbed
    # date (an exact change date => committed checkpoint reproduced).
    page.select_option("#date-select", date_opts[-1])
    page.wait_for_timeout(300)
    vbadge = page.text_content(".verify-badge") or ""
    vok = page.locator(".verify-badge.verify-ok").count() > 0
    check("checkpoint self-verify at scrubbed date", vok and "tarkistuspiste" in vbadge, repr(vbadge[:90]))

    browser.close()

fails = [r for r in results if not r[1]]
real_errors = [e for e in errors if "favicon" not in e]
print(f"\n{len(results) - len(fails)}/{len(results)} passed")
if real_errors:
    print("CONSOLE/PAGE ERRORS:")
    for e in real_errors[:10]:
        print("  ", e[:300])
sys.exit(1 if (fails or real_errors) else 0)
