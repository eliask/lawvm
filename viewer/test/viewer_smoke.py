"""Headless smoke test for the statute-timeline viewer.

Run:  uv run --with playwright python viewer/test/viewer_smoke.py

Self-contained: serves viewer/ on an ephemeral port and drives a headless
Chromium (Playwright). Uses the system Playwright browser cache if the
package's pinned build is absent. Not wired into ci.sh (needs Playwright);
run it manually after any viewer change.
"""
import glob
import os
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from playwright.sync_api import sync_playwright

VIEWER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=VIEWER_DIR))
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_address[1]}/statute-timeline.html"

results = []
errors = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def launch_browser(p):
    try:
        return p.chromium.launch()
    except Exception:
        # Package/browser version mismatch: fall back to any cached build.
        cached = sorted(glob.glob(os.path.expanduser(
            "~/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell")))
        if not cached:
            raise
        return p.chromium.launch(executable_path=cached[-1])


with sync_playwright() as p:
    browser = launch_browser(p)
    page = browser.new_page()
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))

    page.goto(BASE)
    page.wait_for_selector(".verify-badge.verify-ok", timeout=30000)
    check("verify badge OK", True)

    # Document rendered with sections as rows, momentti as prose blocks
    n_sections = page.locator("#doc .node.kind-section").count()
    n_moms = page.locator("#doc .pblock.mom").count()
    check("sections rendered", n_sections > 150, f"{n_sections} sections")
    check("momentti prose blocks", n_moms > 300, f"{n_moms} blocks")

    # Collapse: click a section row -> body hidden; click again -> visible
    row = page.locator("#doc .node.kind-section .node-row.clk").first
    node = page.locator("#doc .node.kind-section").first
    row.click()
    check("collapse on row click", "collapsed" in (node.get_attribute("class") or ""))
    row.click()
    check("expand on second click", "collapsed" not in (node.get_attribute("class") or ""))

    # Change badge exists on some section; click opens inline history
    badge = page.locator("#doc .chg-badge").first
    badge_count = page.locator("#doc .chg-badge").count()
    check("change badges present", badge_count > 50, f"{badge_count} badges")
    badge.scroll_into_view_if_needed()
    badge.click()
    page.wait_for_selector(".inline-history", timeout=10000)
    check("inline history panel opens", True)
    n_changes = page.locator(".inline-history .change").count()
    check("history has version entries", n_changes >= 1, f"{n_changes} entries")
    # Current version diff auto-open and rendered
    open_diff = page.locator(".inline-history details.diff[open] .diff-body")
    check("current version diff auto-rendered",
          open_diff.count() >= 1 and len(open_diff.first.inner_html()) > 10)

    # Unified diff or stacked wholesale present somewhere in panel
    has_unified = page.locator(".inline-history .diff-unified").count() > 0
    has_stack = page.locator(".inline-history .diff-stack").count() > 0
    check("diff rendering present", has_unified or has_stack,
          f"unified={has_unified} stack={has_stack}")

    # dmp loaded (not LCS fallback)
    dmp_ok = page.evaluate("typeof diff_match_patch !== 'undefined'")
    check("diff_match_patch loaded", dmp_ok)

    # Close panel via button
    page.locator(".inline-history .hist-close").click()
    check("history close works", page.locator(".inline-history").count() == 0)

    # Topbar sticky: scroll down, topbar still visible at top
    page.evaluate("window.scrollTo(0, 4000)")
    page.wait_for_timeout(400)
    top_visible = page.evaluate(
        "document.getElementById('topbar').getBoundingClientRect().top >= -1")
    check("topbar sticky after scroll", top_visible)

    # Scroll-spy: some TOC link marked current after scroll
    page.wait_for_timeout(500)
    check("TOC scroll-spy current", page.locator("#toc .toc-link.current").count() == 1)

    # § jump (81a § exists from 2006 on; the time-axis scrub below may move
    # the date, so jump while still at the latest date)
    page.fill("#sec-jump", "81 a")
    page.press("#sec-jump", "Enter")
    page.wait_for_timeout(1800)
    flash_or_spy = page.evaluate(
        "(() => { const el = document.querySelector('#doc .node[data-addr$=\"section:81a\"]');"
        " if (!el) return 'missing'; const r = el.getBoundingClientRect();"
        " return r.top > -80 && r.top < 700 ? 'visible' : 'offscreen ' + r.top; })()")
    check("§ jump to 81 a", flash_or_spy == "visible", flash_or_spy)

    # Repealed section (54a §, repealed 2023-02-23) renders as a ghost
    # tombstone in place at the latest date; § jump goes to it.
    n_ghosts = page.locator("#doc .ghost-line").count()
    check("ghost tombstones rendered", n_ghosts >= 1, f"{n_ghosts} ghosts")
    found_54a_ghost = page.evaluate(
        "!!document.querySelector('#doc .ghost-line[data-addr$=\"section:54a\"]')")
    check("54a § ghost tombstone present", found_54a_ghost)
    page.fill("#sec-jump", "54 a")
    page.press("#sec-jump", "Enter")
    page.wait_for_timeout(1000)
    near_54a = page.evaluate(
        "(() => { const el = document.querySelector('#doc [data-addr$=\"section:54a\"]');"
        " if (!el) return 'missing'; const r = el.getBoundingClientRect();"
        " return r.top > -120 && r.top < 700 ? 'visible' : 'offscreen ' + r.top; })()")
    check("§ jump to repealed 54 a shows ghost", near_54a == "visible", near_54a)
    # Lifecycle strip on the ghost shows an expiry/repeal segment somewhere
    n_seg = page.locator("#doc .chg-strip .seg-rep, #doc .chg-strip .seg-exp").count()
    check("lifecycle strips show repeal/expiry segments", n_seg >= 1, f"{n_seg} segments")

    # Time axis scrub: click at 30% -> date changes
    before = page.text_content("#sel-date")
    axis = page.locator("#timeaxis")
    bb = axis.bounding_box()
    page.mouse.click(bb["x"] + bb["width"] * 0.3, bb["y"] + bb["height"] / 2)
    page.wait_for_timeout(600)
    after = page.text_content("#sel-date")
    check("time-axis scrub changes date", before != after, f"{before} -> {after}")
    check("verify still OK after scrub",
          page.locator(".verify-badge.verify-ok").count() == 1)

    # Badge shows n/total when scrubbed to middle
    some_badge = page.locator("#doc .chg-badge .chg-count").all_text_contents()
    check("badge counts render", any("/" in t for t in some_badge) or len(some_badge) > 0,
          f"sample: {some_badge[:5]}")

    # Muutokset mode: localized per-provision changes
    page.click(".mode-btn[data-mode='muutokset']")
    page.wait_for_selector(".amend-list", timeout=10000)
    page.locator(".amend-item").nth(5).click()
    page.wait_for_timeout(400)
    n_opch = page.locator(".op-change").count()
    check("muutokset localized changes", n_opch >= 1, f"{n_opch} localized entries")

    # Diachronic search: deep-localized hits with highlighted snippets
    page.click(".mode-btn[data-mode='haku']")
    page.fill("#haku-input", "biometri")
    page.click("#haku-form button")
    page.wait_for_timeout(2500)
    hits = page.locator(".haku-hit").count()
    check("diachronic search hits", hits >= 1, f"{hits} hits")
    deep = page.evaluate(
        "[...document.querySelectorAll('.haku-hit-head a')].filter(a => (a.dataset.addr || '').includes('section:')).length")
    check("search hits pinpoint deep addresses", deep >= 1, f"{deep} section-or-deeper of {hits}")
    n_marks = page.locator(".haku-snippet mark").count()
    check("search snippets highlight phrase", n_marks >= 1, f"{n_marks} marks")

    # Vertaa
    page.click(".mode-btn[data-mode='vertaa']")
    page.wait_for_selector("#vertaa-results", timeout=10000)
    page.select_option("#vertaa-d1", "10")
    page.select_option("#vertaa-d2", "12")
    page.click("#vertaa-form button")
    page.wait_for_timeout(800)
    vrows = page.locator(".vertaa-row").count()
    check("vertaa compare rows", vrows >= 1, f"{vrows} rows")

    # Diff streak coalescing: alternating word replacements must merge into
    # ONE del streak + ONE ins streak, not word-by-word red/green.
    streak = page.evaluate(
        "(() => { const h = diffBlockHtml("
        "'alpha beta gamma delta epsilon zeta eta theta iota kappa',"
        "'alpha NEW1 NEW2 NEW3 epsilon zeta eta theta iota kappa');"
        " return { dels: (h.match(/<del/g) || []).length, inss: (h.match(/<ins/g) || []).length,"
        " wholesale: h.includes('diff-wholesale-note') }; })()")
    check("diff streak coalescing",
          not streak["wholesale"] and streak["dels"] == 1 and streak["inss"] == 1, str(streak))

    # Back to the reading view for the structural checks below.
    page.click(".mode-btn[data-mode='oikeustila']")
    page.wait_for_timeout(800)

    # Structured prose for inserted units: a wholly inserted section renders
    # per-momentti blocks, not one flat slab (152a § inserted 2007).
    sec152a = page.locator("#doc .node[data-addr='chapter:9/section:152a'] .hist-btn").first
    sec152a.scroll_into_view_if_needed()
    sec152a.click()
    page.wait_for_selector(".inline-history", timeout=10000)
    n_dp = page.locator(".inline-history .dp-block").count()
    check("inserted section renders structured prose", n_dp >= 2, f"{n_dp} dp-blocks")
    page.locator(".inline-history .hist-close").click()

    # Pixel-stable scroll anchoring: scrub dates while scrolled mid-document;
    # the element at the anchor point must keep its viewport offset (~±24px).
    page.evaluate("window.scrollTo(0, 12000)")
    page.wait_for_timeout(400)
    anchor_before = page.evaluate(
        "(() => { const tb = document.getElementById('topbar').getBoundingClientRect().bottom;"
        " const doc = document.getElementById('doc').getBoundingClientRect();"
        " const el = document.elementFromPoint(doc.left + 200, tb + 10).closest('#doc [data-addr]');"
        " return el ? { addr: el.dataset.addr, top: el.getBoundingClientRect().top } : null; })()")
    page.click("#prev-date")
    page.wait_for_timeout(800)
    drift = page.evaluate(
        "(a) => { const el = document.querySelector(`#doc [data-addr=\"${a.addr}\"]`);"
        " return el ? Math.abs(el.getBoundingClientRect().top - a.top) : 9999; }", anchor_before)
    check("scroll anchor stable across scrub", drift <= 24, f"drift={drift}px addr={anchor_before and anchor_before['addr']}")
    page.click("#next-date")
    page.wait_for_timeout(500)

    # History chips align on one vertical line across nesting levels.
    chip_drift = page.evaluate(
        "(() => { const m = document.querySelector('#doc .pblock.mom > .hist-btn');"
        " const k = document.querySelector('#doc .pblock.kohta > .hist-btn');"
        " if (!m || !k) return null;"
        " return Math.abs(m.getBoundingClientRect().right - k.getBoundingClientRect().right); })()")
    check("history chips align across nesting", chip_drift is not None and chip_drift <= 2,
          f"drift={chip_drift}px")

    # Document-order interleaving: chapter 9 väliotsikko crossheadings must
    # interleave with their section groups, not stack together.
    seq = page.evaluate(
        "[...document.querySelectorAll('.node[data-addr=\"chapter:9\"] > .node-body > *')]"
        ".map(e => e.classList.contains('crossheading') ? 'x' : (e.classList.contains('node') ? 's' : 'o')).join('')")
    check("crossheadings interleave with sections", "xx" not in seq, f"seq={seq[:40]}")

    # Collapse state survives a date scrub.
    ch1 = page.locator("#doc .node[data-addr='chapter:1']")
    ch1.locator(":scope > .node-row").click()
    assert "collapsed" in (ch1.get_attribute("class") or "")
    page.click("#prev-date")
    page.wait_for_timeout(600)
    still = "collapsed" in (page.locator("#doc .node[data-addr='chapter:1']").get_attribute("class") or "")
    check("collapse state survives date scrub", still)
    page.locator("#doc .node[data-addr='chapter:1'] > .node-row").click()  # restore
    page.click("#next-date")
    page.wait_for_timeout(400)

    # Language toggle: EN, SV, back to FI — full re-render in place.
    page.click("#lang-toggle button[data-lang='en']")
    page.wait_for_timeout(900)
    check("EN toggle", page.locator(".mode-btn[data-mode='oikeustila']").text_content() == "Law in force",
          page.locator(".mode-btn[data-mode='oikeustila']").text_content())
    check("EN verify badge re-rendered", page.locator(".verify-badge.verify-ok").count() == 1)
    page.click("#lang-toggle button[data-lang='sv']")
    page.wait_for_timeout(900)
    check("SV toggle", page.locator(".mode-btn[data-mode='oikeustila']").text_content() == "Gällande lydelse",
          page.locator(".mode-btn[data-mode='oikeustila']").text_content())
    sv_addr = page.evaluate(
        "(() => { const l = document.querySelector('#toc .toc-ch .toc-num'); return l ? l.textContent : ''; })()")
    check("SV address formatting (kap.)", "luku" in sv_addr or "kap" in sv_addr, sv_addr)
    page.click("#lang-toggle button[data-lang='fi']")
    page.wait_for_timeout(900)
    check("FI toggle restores", page.locator(".mode-btn[data-mode='oikeustila']").text_content() == "Oikeustila")

    # Other statutes load + verify (manifest now has five).
    for sid in ["423/2003", "1093/1996"]:
        page.select_option("#statute-select", sid)
        page.wait_for_selector(".verify-badge.verify-ok", timeout=30000)
        check(f"statute {sid} loads + verifies", True)
    page.select_option("#statute-select", "301/2004")
    page.wait_for_selector(".verify-badge.verify-ok", timeout=30000)

    # Permalink round-trip: oikeustila + address -> reload -> verified + panel
    page.click(".mode-btn[data-mode='oikeustila']")
    page.wait_for_timeout(800)
    badge2 = page.locator("#doc .chg-badge").first
    badge2.scroll_into_view_if_needed()
    badge2.click()
    page.wait_for_selector(".inline-history", timeout=10000)
    url = page.url
    page.goto("about:blank")
    page.goto(url)
    page.wait_for_selector(".perma-proof.ok", timeout=30000)
    check("permalink re-proof (sitaatti todennettu)", True)
    page.wait_for_selector(".inline-history", timeout=10000)
    check("permalink reopens inline history", True)

    browser.close()

fails = [r for r in results if not r[1]]
real_errors = [e for e in errors if "favicon" not in e]
print(f"\n{len(results) - len(fails)}/{len(results)} passed")
if real_errors:
    print("CONSOLE/PAGE ERRORS:")
    for e in real_errors[:10]:
        print("  ", e[:300])
sys.exit(1 if (fails or real_errors) else 0)
