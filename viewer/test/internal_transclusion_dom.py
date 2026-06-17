"""DOM-level test for INTERNAL-reference live transclusion.

Run:  uv run --with playwright --with typing_extensions python viewer/test/internal_transclusion_dom.py

Mirrors semantic_refs_dom.py / overlay_layers_dom.py: DB-free. Loads the viewer
page (which defines the global render functions at parse time), then drives the
PURE internal-reference machinery over a hand-built in-memory fixture:

  - a "loaded act" (currentStatuteId) whose #doc tree contains a provision X
    with real prose text, injected directly into the DOM;
  - an INTERNAL interlink pointing at X with NO precomputed preview field;
  - an EXTERNAL (cross-statute) interlink that DOES carry a precomputed preview.

It asserts:
  1. the internal interlink is detected as internal (semanticIsInternalRef);
  2. its hovercard transcludes the provision text LIVE from the loaded #doc
     (the prose appears in the card) and is tagged hc-preview-live — there is no
     preview field on the row, so the only possible source is the live DOM;
  3. the internal anchor renders as a live in-page deeplink (ref-sem-internal)
     and clicking it calls goToAddrAtDate (in-page nav, no reload);
  4. when the target provision is NOT in the DOM, the card shows the
     "in this act — not present on this date" note, never a fabricated preview;
  5. the EXTERNAL interlink still shows its PRECOMPUTED preview (unchanged).

Self-contained: serves viewer/ on an ephemeral port and drives headless
Chromium. Not wired into ci.sh (needs Playwright); run manually after viewer
changes touching the internal-reference transclusion path.
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


# The provision prose we transclude live — a distinctive sentinel so we can prove
# the hovercard text came from the DOM, not from any preview field.
PROVISION_TEXT = "Tassa pykalassa saadetaan transkluusion elavasta sisallosta."

# Seed module-level state, inject a loaded #doc with provision X, and return the
# hovercard HTML + detection for an INTERNAL row (no preview) and an EXTERNAL row
# (with a precomputed preview). The internal row's target IS the loaded act.
FIXTURE = """(() => {
  currentStatuteId = '108/2009';
  manifest = [
    { statute_id: '108/2009', title: 'Testilaki', lang: 'fi', jurisdiction: 'fi' },
    { statute_id: '301/2004', title: 'Ulkomaalaislaki', lang: 'fi', jurisdiction: 'fi' },
  ];
  applyLocale('fi', 'fi');

  // Build a minimal loaded document: a single section provision with prose,
  // mirroring the real pblock markup (data-addr + .pblock-body/.pblock-text).
  // #doc is created dynamically by the viewer at render time; in this DB-free
  // harness we create it ourselves so the live-DOM lookup has something to find.
  let doc = document.getElementById('doc');
  if (!doc) { doc = document.createElement('div'); doc.id = 'doc'; document.body.appendChild(doc); }
  doc.innerHTML = ''
    + '<div class="pblock kind-section" data-addr="chapter:10/section:108">'
    + '  <div class="pblock-inner">'
    + '    <span class="pblock-num" title="108 §">108 §</span>'
    + '    <span class="pblock-body"><span class="pblock-text">' + PROSE + ' </span></span>'
    + '  </div>'
    + '  <button class="hist-btn">history</button>'
    + '</div>';

  // INTERNAL ref: target_local_id == loaded act, locator points at the section,
  // and crucially NO preview field anywhere on the row.
  const internalRow = {
    surface_text: '108 §:n 1 momentin nojalla', resolution_status: 'resolved',
    confidence: 'exact', target_work_id: 'fi:normative_act:108/2009',
    target_local_id: '108/2009', target_locator: 'section:108', role: 'cites',
  };

  // INTERNAL ref to a section NOT present in the loaded #doc → absent note.
  const internalAbsentRow = {
    surface_text: '999 §:n nojalla', resolution_status: 'resolved',
    confidence: 'exact', target_work_id: 'fi:normative_act:108/2009',
    target_local_id: '108/2009', target_locator: 'section:999', role: 'cites',
  };

  // EXTERNAL (cross-statute) ref WITH a precomputed preview, threaded through the
  // interlinkTargetsByKey map the way the real projection does.
  interlinkTargetsByKey = new Map();
  interlinkTargetsByKey.set('ext-key', {
    title: 'Ulkomaalaislaki 5 §', target_key: 'ext-key',
    preview_text: 'ULKOINEN ESIKATSELU: precomputed cross-statute preview body.',
    preview_status: 'ok',
  });
  const externalRow = {
    surface_text: '301/2004 5 §', resolution_status: 'resolved', confidence: 'exact',
    target_work_id: 'fi:normative_act:301/2004', target_local_id: '301/2004',
    target_locator: 'section:5', role: 'cites',
    detail_json: JSON.stringify({ target_key: 'ext-key' }),
  };

  return {
    internalDetected: semanticIsInternalRef(internalRow),
    externalDetected: semanticIsInternalRef(externalRow),
    internalSnippet: liveTransclusionSnippet(semanticInternalAddr(internalRow)),
    internalHtml: refLink('semantic', { ...internalRow, text: internalRow.surface_text }, internalRow.surface_text),
    internalHover: semanticInterlinkHovercardHtml(internalRow) || '',
    internalAbsentHover: semanticInterlinkHovercardHtml(internalAbsentRow) || '',
    internalAbsentHtml: refLink('semantic', { ...internalAbsentRow, text: internalAbsentRow.surface_text }, internalAbsentRow.surface_text),
    externalHover: semanticInterlinkHovercardHtml(externalRow) || '',
  };
})()""".replace("PROSE", repr(PROVISION_TEXT))


with sync_playwright() as p:
    browser = launch_browser(p)
    page = browser.new_page()
    page.goto(BASE)
    page.wait_for_function(
        "typeof semanticIsInternalRef === 'function' "
        "&& typeof liveTransclusionSnippet === 'function' "
        "&& typeof semanticInterlinkHovercardHtml === 'function'",
        timeout=15000)

    out = page.evaluate(FIXTURE)

    # 1. Detection: internal vs external.
    check("internal ref detected as internal", out["internalDetected"] is True,
          str(out["internalDetected"]))
    check("external ref NOT detected as internal", out["externalDetected"] is False,
          str(out["externalDetected"]))

    # 2. Live transclusion: the snippet helper pulls the provision prose from the
    # DOM; the hovercard embeds it and tags it as live (no preview field exists).
    check("live snippet pulled from DOM", out["internalSnippet"] == PROVISION_TEXT,
          str(out["internalSnippet"]))
    check("internal hovercard transcludes provision text",
          PROVISION_TEXT in out["internalHover"], out["internalHover"][:200])
    check("internal hovercard tagged live (not precomputed preview)",
          "hc-preview-live" in out["internalHover"], out["internalHover"][:120])
    check("internal hovercard preview is clickable jump link",
          "hc-internal-jump" in out["internalHover"], out["internalHover"][:120])
    check("internal hovercard names live source",
          "hc-internal-source" in out["internalHover"], out["internalHover"][:120])
    check("internal hovercard is internal citation kind",
          "Sisäinen viittaus" in out["internalHover"], out["internalHover"][:200])

    # 3. Surface: internal anchor is a live in-page deeplink.
    check("internal anchor is live deeplink class",
          "ref-sem-internal" in out["internalHtml"]
          and "108 §:n 1 momentin nojalla" in out["internalHtml"], out["internalHtml"][:160])

    # 4. Absent target → clear note, never a fabricated preview.
    check("absent-target hovercard shows not-present note",
          "hc-internal-absent" in out["internalAbsentHover"]
          and "hc-preview-live" not in out["internalAbsentHover"],
          out["internalAbsentHover"][:200])
    check("absent internal anchor appends missing badge",
          "ref-sem-internal-absent" in out["internalAbsentHtml"]
          and "[puuttuu]" in out["internalAbsentHtml"],
          out["internalAbsentHtml"][:200])

    # 4b. Tombstoned target → inactive styling + editorial suffix in anchor.
    tomb = page.evaluate("""() => {
      let doc = document.getElementById('doc');
      doc.innerHTML = ''
        + '<div class="pblock tombstone kind-section" data-addr="chapter:10/section:108/repealed">'
        + '  <div class="pblock-inner">'
        + '    <span class="pblock-num">108 §</span>'
        + '    <span class="pblock-body tomb-label"><em class="repeal">[kumottu]</em></span>'
        + '  </div>'
        + '</div>';
      invalidateRenderedAddrIndex();
      const row = {
        surface_text: '108 §:n kumottu kohta', resolution_status: 'resolved',
        confidence: 'exact', target_work_id: 'fi:normative_act:108/2009',
        target_local_id: '108/2009', target_locator: 'section:108/repealed', role: 'cites',
      };
      return {
        html: refLink('semantic', { ...row, text: row.surface_text }, row.surface_text),
        hover: semanticInterlinkHovercardHtml(row) || '',
      };
    }""")
    check("repealed internal anchor appends kumottu badge",
          "ref-sem-internal-repealed" in tomb["html"]
          and "[kumottu]" in tomb["html"],
          tomb["html"][:220])
    check("repealed internal hovercard names inactive target",
          "hc-internal-inactive" in tomb["hover"]
          and "hc-preview-tombstone" in tomb["hover"],
          tomb["hover"][:220])

    # 4c. Fold-evidenced repeal without a DOM ghost → [kumottu], never [puuttuu].
    fold_tomb = page.evaluate("""() => {
      currentStatuteId = '108/2009';
      curLive = new Map();
      curTombstoned = new Map([['chapter:10/section:108/repealed', { reason: 'repeal', date: '2020-01-01' }]]);
      invalidateRenderedAddrIndex();
      document.getElementById('doc').innerHTML = '';
      const row = {
        surface_text: '108 §:n kumottu kohta', resolution_status: 'resolved',
        confidence: 'exact', target_work_id: 'fi:normative_act:108/2009',
        target_local_id: '108/2009', target_locator: 'section:108/repealed', role: 'cites',
      };
      return refLink('semantic', { ...row, text: row.surface_text }, row.surface_text);
    }""")
    check("fold-evidenced repeal uses kumottu not puuttuu",
          "ref-sem-internal-repealed" in fold_tomb
          and "[kumottu]" in fold_tomb
          and "[puuttuu]" not in fold_tomb,
          fold_tomb[:220])

    # 5. External link still shows its PRECOMPUTED preview (unchanged behaviour).
    check("external hovercard shows precomputed preview",
          "ULKOINEN ESIKATSELU" in out["externalHover"]
          and "hc-preview" in out["externalHover"]
          and "hc-preview-live" not in out["externalHover"], out["externalHover"][:200])

    # 6. Clicking the internal anchor routes through semanticNavToTarget with the
    # in-act locator (strict-mode scripts cannot reassign function declarations).
    nav = page.evaluate("""(html) => {
        const calls = { nav: [], load: [] };
        const origNav = semanticNavToTarget;
        const origLoad = loadStatute;
        semanticNavToTarget = (row, status) => {
          calls.nav.push({ status, addr: semanticInternalAddr(row) });
        };
        loadStatute = (id) => { calls.load.push(id); };
        const d = document.createElement('div');
        d.innerHTML = html;
        document.body.appendChild(d);
        const a = d.querySelector('a.ref-sem-internal');
        if (a) a.click();
        semanticNavToTarget = origNav;
        loadStatute = origLoad;
        d.remove();
        return calls;
    }""", out["internalHtml"])
    check("internal click navigates in-page via semanticNavToTarget",
          len(nav["nav"]) == 1 and nav["nav"][0].get("status") == "resolved"
          and "section:108" in (nav["nav"][0].get("addr") or ""), str(nav["nav"]))
    check("internal click does NOT reload the act", len(nav["load"]) == 0, str(nav["load"]))

    # 7. Deep locators map item→paragraph and suffix-match chapter prefixes.
    deep = page.evaluate("""() => {
      currentStatuteId = '301/2004';
      let doc = document.getElementById('doc');
      if (!doc) { doc = document.createElement('div'); doc.id = 'doc'; document.body.appendChild(doc); }
      invalidateRenderedAddrIndex();
      doc.innerHTML = ''
        + '<div class="node kind-section" data-addr="chapter:6/section:114">'
        + '  <div class="node-body">'
        + '    <div class="pblock kind-subsection" data-addr="chapter:6/section:114/subsection:4">'
        + '      <div class="pblock-inner"><span class="pblock-body"><span class="pblock-text">mom 4 </span></span></div>'
        + '      <div class="pblock-children">'
        + '        <div class="pblock kind-paragraph" data-addr="chapter:6/section:114/subsection:4/paragraph:1">'
        + '          <div class="pblock-inner"><span class="pblock-body"><span class="pblock-text">KOHTA YKSI </span></span></div>'
        + '        </div>'
        + '      </div>'
        + '    </div>'
        + '  </div>'
        + '</div>';
      const locator = 'section:114/subsection:4/item:1';
      const resolved = resolveRenderedAddr(locator);
      const scrollEl = scrollTargetForAddr(locator);
      return {
        resolvedAddr: resolved.addr,
        scrollTag: scrollEl && scrollEl.className,
        scrollAddr: scrollEl && scrollEl.dataset && scrollEl.dataset.addr,
      };
    }""")
    check("item locator resolves to rendered paragraph address",
          deep.get("resolvedAddr") == "chapter:6/section:114/subsection:4/paragraph:1",
          str(deep))
    check("deep internal scroll target is the paragraph block",
          deep.get("scrollAddr") == "chapter:6/section:114/subsection:4/paragraph:1",
          str(deep))

    browser.close()

fails = [r for r in results if not r[1]]
print(f"\n{len(results) - len(fails)}/{len(results)} passed")
sys.exit(1 if fails else 0)
