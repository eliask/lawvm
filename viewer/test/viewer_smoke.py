"""Headless smoke test for the statute-timeline viewer.

Run:  uv run --with playwright python viewer/test/viewer_smoke.py

Self-contained: serves viewer/ on an ephemeral port and drives a headless
Chromium (Playwright). Uses the system Playwright browser cache if the
package's pinned build is absent. Not wired into ci.sh (needs Playwright);
run it manually after any viewer change.
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
    n_moms = page.locator("#doc .pblock.kind-subsection").count()
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
        "(() => { const el = document.querySelector('#doc [data-addr$=\"section:54a\"]');"
        " return !!(el && el.textContent.includes('[kumottu]')); })()")
    check("54a § ghost tombstone present", found_54a_ghost)
    old_54a_neutral = page.evaluate(
        "(() => { const el = document.querySelector('#doc [data-addr$=\"section:54a\"]');"
        " return !!(el && !el.classList.contains('change-removed')); })()")
    check("old repealed tombstone is visually neutral", old_54a_neutral)
    page.fill("#sec-jump", "54 a")
    page.press("#sec-jump", "Enter")
    page.wait_for_timeout(1000)
    near_54a = page.evaluate(
        "(() => { const el = document.querySelector('#doc [data-addr$=\"section:54a\"]');"
        " if (!el) return 'missing'; const r = el.getBoundingClientRect();"
        " return r.top > -120 && r.top < 700 ? 'visible' : 'offscreen ' + r.top; })()")
    check("§ jump to repealed 54 a shows ghost", near_54a == "visible", near_54a)
    derived_section_tomb = page.evaluate(
        "(() => { const el = document.querySelector('#doc .node[data-addr=\"chapter:4/section:47a\"]');"
        " return !!(el && el.classList.contains('derived-tombstone') && el.textContent.includes('[kumottu]')); })()")
    check("section with only repealed subsections is marked at row level", derived_section_tomb)
    evidence_legend_nowrap = page.evaluate(
        "(() => { const el = document.querySelector('.tree-legend .legend-item .leg-evidence');"
        " if (!el) return true;"
        " const item = el.closest('.legend-item');"
        " return !!(item && getComputedStyle(item).whiteSpace === 'nowrap'); })()")
    check("evidence legend marker stays with label", evidence_legend_nowrap)
    toc_derived_section_tomb = page.evaluate(
        "(() => { const el = document.querySelector('#toc .toc-sec[data-addr=\"chapter:4/section:47a\"]');"
        " return !!(el && el.classList.contains('toc-tombstone') && el.textContent.includes('[kumottu]')); })()")
    check("TOC marks derived repealed section", toc_derived_section_tomb)
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

    # Timeline keyboard browse: arrows work in the main reading view, but not
    # while a real input/control owns focus.
    page.evaluate("document.activeElement && document.activeElement.blur && document.activeElement.blur()")
    key_before = page.text_content("#sel-date")
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(700)
    key_after = page.text_content("#sel-date")
    check("right arrow browses timeline in main view", key_after != key_before, f"{key_before} -> {key_after}")
    page.focus("#sec-jump")
    guarded_before = page.text_content("#sel-date")
    page.keyboard.press("ArrowLeft")
    page.wait_for_timeout(300)
    guarded_after = page.text_content("#sel-date")
    check("timeline arrows ignore focused input", guarded_after == guarded_before, f"{guarded_before} -> {guarded_after}")
    page.evaluate("document.activeElement && document.activeElement.blur && document.activeElement.blur()")

    # Contextual focus keeps the full law available, but collapses unchanged
    # outline branches around selected-date changes.
    page.select_option("#date-jump", label="2011-04-05")
    page.wait_for_timeout(700)
    full_addr_count = page.locator("#doc [data-addr]").count()
    page.click("#focus-changes-context")
    page.wait_for_timeout(900)
    context_addr_count = page.locator("#doc [data-addr]").count()
    context_collapsed = page.locator("#doc .node.collapsed").count()
    check("context focus keeps full document",
          context_addr_count == full_addr_count,
          f"{context_addr_count} vs {full_addr_count} addressed elements")
    check("context focus collapses unchanged branches",
          context_collapsed > 0,
          f"{context_collapsed} collapsed")
    page.click("#expand-all")
    page.wait_for_timeout(300)

    # Changed-only topbar toggle filters the primary law view to selected-date
    # changes and survives date scrubs.
    page.click("#focus-changes-toggle")
    page.wait_for_timeout(400)
    focused_addr_count = page.locator("#doc [data-addr]").count()
    focus_pressed = page.locator("#focus-changes-toggle").get_attribute("aria-pressed") == "true"
    check("focus changed filters unchanged branches",
          0 < focused_addr_count < full_addr_count,
          f"{focused_addr_count} of {full_addr_count} addressed elements")
    check("focus changed toggle is pressed", focus_pressed)
    page.click("#next-date")
    page.wait_for_timeout(700)
    focus_survived = page.locator("#focus-changes-toggle").get_attribute("aria-pressed") == "true"
    check("focus changed persists across date scrub", focus_survived)
    page.click("#focus-changes-toggle")
    page.wait_for_timeout(400)

    # Muutokset mode: localized per-provision changes
    page.click(".mode-btn[data-mode='amendments']")
    page.wait_for_selector(".amend-list", timeout=10000)
    page.locator(".amend-item").nth(5).click()
    page.wait_for_timeout(400)
    n_opch = page.locator(".op-change").count()
    check("amendments localized changes", n_opch >= 1, f"{n_opch} localized entries")
    amend_evidence = page.evaluate("""(() => {
        const entries = typeof evidenceBySource !== 'undefined' ? [...evidenceBySource.entries()] : [];
        const hit = entries.find(([id, rows]) => id && rows && rows.length);
        if (!hit) return { ok: false, reason: 'no source evidence rows' };
        selectedSourceId = hit[0];
        renderAmendments();
        const details = document.querySelector('#amend-detail > details.evidence-list.amend-evidence');
        const summary = details ? (details.querySelector('summary')?.textContent || '') : '';
        return { ok: !!details, open: details ? details.open : null, summary };
    })()""")
    check("amendment LawVM evidence defaults collapsed",
          bool(amend_evidence.get("ok")) and amend_evidence.get("open") is False and "LawVM" in amend_evidence.get("summary", ""),
          str(amend_evidence))

    # Diachronic search: deep-localized hits with highlighted snippets
    page.click(".mode-btn[data-mode='search']")
    page.fill("#search-input", "biometri")
    page.click("#search-form button")
    page.wait_for_timeout(2500)
    hits = page.locator(".search-hit").count()
    check("diachronic search hits", hits >= 1, f"{hits} hits")
    deep = page.evaluate(
        "[...document.querySelectorAll('.search-hit-head a')].filter(a => (a.dataset.addr || '').includes('section:')).length")
    check("search hits pinpoint deep addresses", deep >= 1, f"{deep} section-or-deeper of {hits}")
    n_marks = page.locator(".search-snippet mark").count()
    check("search snippets highlight phrase", n_marks >= 1, f"{n_marks} marks")

    # Clicking a hit lands in the reading view and paints the phrase in the main
    # pane via the CSS Custom Highlight API (the Google "jump to highlight"
    # analogue). Verify the highlight registry holds live ranges + q-permalink.
    page.locator(".search-hit-head a.search-goto").first.click()
    page.wait_for_timeout(900)
    hl_ranges = page.evaluate(
        "(() => { const h = CSS.highlights && CSS.highlights.get('lawvm-search');"
        " return h ? h.size : 0; })()")
    check("search click highlights phrase in main pane", hl_ranges >= 1, f"{hl_ranges} ranges")
    check("highlight carries q permalink param", "q=" in page.url, page.url.split("#")[-1][:80])
    # Manual provision pick clears the search highlight.
    other = page.locator("#doc .hist-btn").first
    other.scroll_into_view_if_needed()
    other.click()
    page.wait_for_timeout(300)
    cleared = page.evaluate(
        "(() => { const h = CSS.highlights && CSS.highlights.get('lawvm-search');"
        " return !h || h.size === 0; })()")
    check("manual pick clears search highlight", cleared)

    # --- Reference-link layer: clickable source ids + dates, hovercards ---
    page.click(".mode-btn[data-mode='search']")
    page.fill("#search-input", "biometri")
    page.click("#search-form button")
    page.wait_for_timeout(2000)
    n_refsrc = page.locator(".search-hit a.ref-link.ref-source").count()
    n_refdate = page.locator(".search-hit a.ref-link.ref-date").count()
    check("ref-links render (source)", n_refsrc >= 1, f"{n_refsrc} source refs")
    check("ref-links render (date)", n_refdate >= 1, f"{n_refdate} date refs")

    # Hovercard appears on source-ref hover, hides on mouse-out.
    src_ref = page.locator(".search-hit a.ref-link.ref-source").first
    src_ref.scroll_into_view_if_needed()
    src_ref.hover()
    page.wait_for_timeout(450)
    hc_visible = page.evaluate(
        "(() => { const h = document.querySelector('.ref-hovercard');"
        " return !!(h && !h.hidden && h.textContent.length > 0); })()")
    check("hovercard shows on source hover", hc_visible)
    page.mouse.move(2, 2)
    page.wait_for_timeout(350)
    hc_hidden = page.evaluate(
        "(() => { const h = document.querySelector('.ref-hovercard'); return !h || h.hidden; })()")
    check("hovercard hides on mouse-out", hc_hidden)

    # Date ref → law-in-force on that date.
    date_ref = page.locator(".search-hit a.ref-link.ref-date").first
    date_text = (date_ref.inner_text() or "").strip()
    date_ref.click()
    page.wait_for_timeout(700)
    mode_now = page.evaluate("(() => { const b = document.querySelector('.mode-btn.active'); return b ? b.dataset.mode : null; })()")
    check("date ref → law-in-force mode", mode_now == "law", f"mode={mode_now}")
    sel_date = page.text_content("#sel-date") or ""
    check("date ref jumps to that date", date_text in sel_date, f"{date_text} vs {sel_date}")

    # Source ref → Amendments mode, that act selected, src= permalink, reload round-trip.
    page.click(".mode-btn[data-mode='search']")
    page.fill("#search-input", "biometri")
    page.click("#search-form button")
    page.wait_for_timeout(2000)
    page.locator(".search-hit a.ref-link.ref-source").first.click()
    page.wait_for_selector(".amend-list", timeout=10000)
    page.wait_for_timeout(300)
    active_src = page.evaluate("(() => { const li = document.querySelector('.amend-item.active'); return li ? li.dataset.src : null; })()")
    check("source ref → amendments mode, act active", active_src is not None, f"active={active_src}")
    check("source ref sets src= permalink", "src=" in page.url, page.url.split("#")[-1][:60])
    murl = page.url
    page.goto("about:blank")
    page.goto(murl)
    page.wait_for_selector(".amend-list", timeout=30000)
    page.wait_for_timeout(400)
    reloaded_src = page.evaluate("(() => { const li = document.querySelector('.amend-item.active'); return li ? li.dataset.src : null; })()")
    check("amendments src permalink round-trip", reloaded_src == active_src, f"{reloaded_src} vs {active_src}")

    # Vertaa
    page.click(".mode-btn[data-mode='compare']")
    page.wait_for_selector("#compare-results", timeout=10000)
    page.select_option("#compare-d1", "10")
    page.select_option("#compare-d2", "12")
    page.click("#compare-form button")
    page.wait_for_timeout(800)
    vrows = page.locator(".compare-row").count()
    check("compare rows", vrows >= 1, f"{vrows} rows")

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
    page.click(".mode-btn[data-mode='law']")
    page.wait_for_timeout(800)

    # Structured prose for inserted units: a wholly inserted section renders
    # per-momentti blocks, not one flat slab (152a § inserted 2007).
    sec152a = page.locator("#doc .node[data-addr='chapter:9/section:152a'] .hist-btn").first
    sec152a.scroll_into_view_if_needed()
    sec152a.click()
    page.wait_for_selector(".inline-history", timeout=10000)
    n_dp = page.locator(".inline-history .dp-block").count()
    check("inserted section renders structured prose", n_dp >= 2, f"{n_dp} dp-blocks")

    # Copy UX (4b): the history head exposes a "copy text" affordance that writes
    # labelled provision text + a provenance footer (statute, address, permalink).
    copied = page.evaluate("""async () => {
        const writes = [];
        const real = navigator.clipboard && navigator.clipboard.writeText;
        if (navigator.clipboard) navigator.clipboard.writeText = (t) => { writes.push(t); return Promise.resolve(); };
        const btn = document.querySelector('.inline-history .copy-text');
        if (!btn) return { ok: false, reason: 'no button' };
        btn.click();
        await new Promise(r => setTimeout(r, 60));
        if (real) navigator.clipboard.writeText = real;
        const t = writes[0] || '';
        return { ok: true, hasAddr: t.includes('152'), hasFooter: t.includes('#') && t.includes('s='), len: t.length };
    }""")
    check("copy-text writes provision + provenance", bool(copied.get("ok")) and copied.get("hasFooter"), str(copied))
    page.locator(".inline-history .hist-close").click()

    # Whole-chapter initial content must keep section/chapter structure in the
    # diff box. A regression here flattened chapter 15 into duplicate bare
    # "1 mom." blocks with no section context.
    page.select_option("#date-jump", "0")
    page.wait_for_timeout(700)
    chap15 = page.locator("#doc .node[data-addr='chapter:15'] .hist-btn").first
    chap15.scroll_into_view_if_needed()
    chap15.click()
    page.wait_for_selector(".inline-history", timeout=10000)
    n_diff_sections = page.locator(".inline-history .diff-box .dp-node.kind-section").count()
    check("inserted chapter history keeps section structure",
          n_diff_sections >= 2,
          f"{n_diff_sections} section rows")
    page.locator(".inline-history .hist-close").click()

    # Copy UX (4a): provision number labels are selectable (no user-select:none),
    # so a drag-copy includes the "X mom." prefix.
    label_selectable = page.evaluate(
        "(() => { const el = document.querySelector('#doc .pblock-num');"
        " return el ? getComputedStyle(el).userSelect !== 'none' : false; })()")
    check("provision labels are selectable", label_selectable)

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
        "(() => { const m = document.querySelector('#doc .pblock.kind-subsection > .hist-btn');"
        " const k = document.querySelector('#doc .pblock.kind-paragraph > .hist-btn');"
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
    check("EN toggle", page.locator(".mode-btn[data-mode='law']").text_content() == "Law in force",
          page.locator(".mode-btn[data-mode='law']").text_content())
    check("EN verify badge re-rendered", page.locator(".verify-badge.verify-ok").count() == 1)
    page.click("#lang-toggle button[data-lang='sv']")
    page.wait_for_timeout(900)
    check("SV toggle", page.locator(".mode-btn[data-mode='law']").text_content() == "Gällande lydelse",
          page.locator(".mode-btn[data-mode='law']").text_content())
    sv_addr = page.evaluate(
        "(() => { const l = document.querySelector('#toc .toc-ch .toc-num'); return l ? l.textContent : ''; })()")
    check("SV address formatting (kap.)", "luku" in sv_addr or "kap" in sv_addr, sv_addr)
    page.click("#lang-toggle button[data-lang='fi']")
    page.wait_for_timeout(900)
    check("FI toggle restores", page.locator(".mode-btn[data-mode='law']").text_content() == "Oikeustila")

    # Switching statute must drop a live search highlight + its q permalink
    # param (they belong to the previous statute).
    page.click(".mode-btn[data-mode='search']")
    page.fill("#search-input", "biometri")
    page.click("#search-form button")
    page.wait_for_timeout(2000)
    page.locator(".search-hit-head a.search-goto").first.click()
    page.wait_for_timeout(700)
    assert page.evaluate(
        "(() => { const h = CSS.highlights && CSS.highlights.get('lawvm-search'); return h ? h.size : 0; })()") >= 1
    page.select_option("#statute-select", "423/2003")
    page.wait_for_selector(".verify-badge.verify-ok", timeout=30000)
    page.wait_for_timeout(400)
    after_switch = page.evaluate(
        "(() => { const h = CSS.highlights && CSS.highlights.get('lawvm-search'); return h ? h.size : 0; })()")
    check("statute switch clears search highlight", after_switch == 0, f"{after_switch} ranges")
    check("statute switch drops q permalink param", "q=" not in page.url, page.url.split("#")[-1][:60])
    page.select_option("#statute-select", "301/2004")
    page.wait_for_selector(".verify-badge.verify-ok", timeout=30000)

    # Other statutes load + verify (manifest now has five).
    for sid in ["423/2003", "1093/1996"]:
        page.select_option("#statute-select", sid)
        page.wait_for_selector(".verify-badge.verify-ok", timeout=30000)
        check(f"statute {sid} loads + verifies", True)

    # 527/2014 has a zero-delta checkpoint at 2028-05-01, then a fixed-term
    # expiry on 2029-01-01. Surface both honestly in the primary law view.
    page.select_option("#statute-select", "527/2014")
    page.wait_for_selector(".verify-badge.verify-ok", timeout=30000)
    page.select_option("#date-jump", label="2028-05-01")
    page.wait_for_timeout(700)
    zero_delta_meta = page.text_content("#date-meta") or ""
    check("zero-delta checkpoint is labelled", "ei näkyviä tekstimuutoksia" in zero_delta_meta,
          zero_delta_meta)
    page.click("#focus-changes-toggle")
    page.wait_for_timeout(400)
    zero_delta_empty = page.text_content("#doc") or ""
    check("zero-delta changed-only view is empty",
          "Ei näkyviä tekstimuutoksia" in zero_delta_empty,
          zero_delta_empty[:120])
    page.click("#focus-changes-toggle")
    page.wait_for_timeout(400)
    page.select_option("#date-jump", label="2029-01-01")
    page.wait_for_timeout(700)
    expired_197a = page.evaluate(
        "(() => { const el = document.querySelector('#doc .tombstone[data-addr$=\"section:197a/subsection:1\"]');"
        " return !!(el && el.textContent.includes('[rauennut]') && el.classList.contains('change-expired')); })()")
    check("fixed-term expiry tombstone is distinct", expired_197a)
    page.select_option("#statute-select", "301/2004")
    page.wait_for_selector(".verify-badge.verify-ok", timeout=30000)

    # Permalink round-trip: law view + address -> reload -> verified + panel
    page.click(".mode-btn[data-mode='law']")
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

    # A q-bearing permalink re-paints the phrase on cold load (text-fragment
    # analogue). Build one from the search path, reload, expect live ranges.
    page.click(".mode-btn[data-mode='search']")
    page.fill("#search-input", "biometri")
    page.click("#search-form button")
    page.wait_for_timeout(2000)
    page.locator(".search-hit-head a.search-goto").first.click()
    page.wait_for_timeout(700)
    q_url = page.url
    page.goto("about:blank")
    page.goto(q_url)
    page.wait_for_selector(".inline-history", timeout=30000)
    page.wait_for_timeout(500)
    q_ranges = page.evaluate(
        "(() => { const h = CSS.highlights && CSS.highlights.get('lawvm-search');"
        " return h ? h.size : 0; })()")
    check("q-permalink re-highlights on reload", q_ranges >= 1, f"{q_ranges} ranges; {q_url.split('#')[-1][:70]}")

    browser.close()

fails = [r for r in results if not r[1]]
real_errors = [e for e in errors if "favicon" not in e]
print(f"\n{len(results) - len(fails)}/{len(results)} passed")
if real_errors:
    print("CONSOLE/PAGE ERRORS:")
    for e in real_errors[:10]:
        print("  ", e[:300])
sys.exit(1 if (fails or real_errors) else 0)
