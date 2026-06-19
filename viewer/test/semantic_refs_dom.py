"""DOM-level test for the semantic-reference (inline citation) overlay.

Run:  uv run --with playwright python viewer/test/semantic_refs_dom.py

Unlike viewer_smoke.py this test does NOT need a corpus DB: it loads the
viewer page (which defines the global render functions at parse time) and
exercises the PURE rendering/classification functions over a small fixture of
interlink rows — one per resolution status. It asserts that each status yields
the right surface affordance (class + navigability) and hovercard content:

  resolved      → ref-sem-resolved, deep-link (navigating)
  statute_only  → ref-sem-statute_only, act-level link (navigating)
  ambiguous     → ref-sem-ambiguous, non-navigating, hovercard lists candidates
  open          → ref-sem-open, non-navigating tagged span
  broken        → ref-sem-broken, struck-through + "repealed/renumbered" note

Self-contained: serves viewer/ on an ephemeral port and drives headless
Chromium. Not wired into ci.sh (needs Playwright); run manually after viewer
changes touching the semantic-reference layer.
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


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def launch_browser(p):
    try:
        return p.chromium.launch()
    except Exception:
        cached = sorted(glob.glob(os.path.expanduser(
            "~/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell")))
        if not cached:
            raise
        return p.chromium.launch(executable_path=cached[-1])


# Fixture: one interlink row per status. Field names mirror the
# legal_interlink_to_row projection the viewer consumes (target_local_id,
# target_locator, target_url, candidate_work_ids, resolution_status …).
FIXTURE = """(() => {
  // Make the target acts resolvable in-viewer so resolved/statute_only nav.
  manifest = [
    { statute_id: '301/2004', title: 'Ulkomaalaislaki', lang: 'fi', jurisdiction: 'fi' },
    { statute_id: '423/2003', title: 'Kielilaki', lang: 'fi', jurisdiction: 'fi' },
  ];
  applyLocale('fi', 'fi');
  const rows = {
    resolved: {
      surface_text: '301/2004 5 §', resolution_status: 'resolved', confidence: 'exact',
      target_work_id: 'fi:normative_act:301/2004', target_local_id: '301/2004',
      target_locator: 'section:5', role: 'cites',
    },
    statute_only: {
      surface_text: 'kielilaissa', resolution_status: 'resolved', confidence: 'heuristic',
      target_work_id: 'fi:normative_act:423/2003', target_local_id: '423/2003',
      target_locator: '', role: 'cites',
    },
    ambiguous: {
      surface_text: 'siitä mitä laissa säädetään', resolution_status: 'ambiguous',
      confidence: 'heuristic', target_work_id: '', target_local_id: '',
      candidate_work_ids: 'fi:normative_act:301/2004|fi:normative_act:423/2003', role: 'cites',
    },
    open: {
      surface_text: 'erikseen säädetään', resolution_status: 'unresolved',
      confidence: 'legacy_unknown', target_work_id: '', target_local_id: '', role: 'cites',
    },
    broken: {
      surface_text: '999/1900 12 §', resolution_status: 'broken', confidence: 'heuristic',
      target_work_id: 'fi:normative_act:999/1900', target_local_id: '999/1900',
      target_locator: 'section:12', role: 'cites',
    },
    external_eu: {
      surface_text: 'asetus (EU) 2016/679', resolution_status: 'external_only',
      confidence: 'exact', target_work_id: 'eu:eu_act:eu/2016/679', target_local_id: 'eu/2016/679',
      target_url: 'https://eur-lex.europa.eu/eli/reg/2016/679/oj', role: 'cites',
    },
  };
  const out = {};
  for (const [k, row] of Object.entries(rows)) {
    const surface = row.surface_text;
    out[k] = {
      status: semanticRefStatus(row),
      html: refLink('semantic', { ...row, text: surface }, surface),
      hover: semanticInterlinkHovercardHtml(row) || '',
    };
  }
  return out;
})()"""


with sync_playwright() as p:
    browser = launch_browser(p)
    page = browser.new_page()
    page.goto(BASE)
    page.wait_for_function("typeof refLink === 'function' && typeof semanticRefStatus === 'function'", timeout=15000)

    out = page.evaluate(FIXTURE)

    # Status classification correct for every fixture row.
    check("resolved classified", out["resolved"]["status"] == "resolved", out["resolved"]["status"])
    check("statute_only classified", out["statute_only"]["status"] == "statute_only", out["statute_only"]["status"])
    check("ambiguous classified", out["ambiguous"]["status"] == "ambiguous", out["ambiguous"]["status"])
    check("open classified", out["open"]["status"] == "open", out["open"]["status"])
    check("broken classified", out["broken"]["status"] == "broken", out["broken"]["status"])
    check("external classified", out["external_eu"]["status"] == "external", out["external_eu"]["status"])

    # Each anchor carries the status-keyed class + the surface text.
    check("resolved deep-link class", "ref-sem-resolved" in out["resolved"]["html"]
          and "301/2004 5 §" in out["resolved"]["html"], out["resolved"]["html"][:120])
    check("statute_only act-level class", "ref-sem-statute_only" in out["statute_only"]["html"],
          out["statute_only"]["html"][:120])
    check("ambiguous class", "ref-sem-ambiguous" in out["ambiguous"]["html"], out["ambiguous"]["html"][:120])
    check("open non-clickable class", "ref-sem-open" in out["open"]["html"], out["open"]["html"][:120])

    # Broken: struck-through surface — the row carries the broken class; the
    # CSS line-through is keyed by that class.
    check("broken strikethrough class", "ref-sem-broken" in out["broken"]["html"]
          and "999/1900 12 §" in out["broken"]["html"], out["broken"]["html"][:120])

    # External: navigable + opens-external (carries the EU citation kind in hover).
    check("external class", "ref-sem-external" in out["external_eu"]["html"], out["external_eu"]["html"][:120])

    # Hovercards: status badge present for each; ambiguous lists candidates;
    # broken names the "repealed/renumbered since" affordance.
    check("resolved hovercard has status badge",
          "hc-status-resolved" in out["resolved"]["hover"], out["resolved"]["hover"][:80])
    check("ambiguous hovercard lists candidates",
          "hc-candidates" in out["ambiguous"]["hover"]
          and "301/2004" in out["ambiguous"]["hover"]
          and "423/2003" in out["ambiguous"]["hover"], out["ambiguous"]["hover"][:160])
    check("open hovercard has status badge",
          "hc-status-open" in out["open"]["hover"], out["open"]["hover"][:80])
    check("broken hovercard has repealed/renumbered note",
          "hc-broken-note" in out["broken"]["hover"]
          and "hc-status-broken" in out["broken"]["hover"], out["broken"]["hover"][:160])
    check("EU hovercard shows EU citation kind",
          "EU-viittaus" in out["external_eu"]["hover"], out["external_eu"]["hover"][:160])

    # Now drive the real DOM: inject the resolved + broken anchors into the
    # page, hover the broken one, and confirm the status class is on the element
    # and the rendered link surface is the citation phrase (anchor = surface).
    rendered = page.evaluate("""(html) => {
        const d = document.createElement('div');
        d.innerHTML = html.resolved + html.broken;
        document.body.appendChild(d);
        const res = d.querySelector('a.ref-sem-resolved');
        const brk = d.querySelector('a.ref-sem-broken');
        return {
          resolvedText: res ? res.textContent : null,
          resolvedStruck: res ? getComputedStyle(res).textDecorationLine.includes('line-through') : null,
          brokenStruck: brk ? getComputedStyle(brk).textDecorationLine.includes('line-through') : null,
        };
    }""", {"resolved": out["resolved"]["html"], "broken": out["broken"]["html"]})
    check("rendered resolved anchor = citation surface", rendered["resolvedText"] == "301/2004 5 §",
          str(rendered["resolvedText"]))
    check("broken anchor is struck through (CSS applied)", rendered["brokenStruck"] is True,
          f"broken={rendered['brokenStruck']} resolved={rendered['resolvedStruck']}")
    check("resolved anchor is NOT struck through", rendered["resolvedStruck"] is False,
          str(rendered["resolvedStruck"]))

    browser.close()

fails = [r for r in results if not r[1]]
print(f"\n{len(results) - len(fails)}/{len(results)} passed")
sys.exit(1 if fails else 0)
