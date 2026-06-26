"""DOM-level test for the semantic surface-overlay layers.

Run:  uv run --with playwright --with typing_extensions python viewer/test/overlay_layers_dom.py

Mirrors semantic_refs_dom.py: DB-free. Loads the viewer page (which defines the
global render functions at parse time), seeds the module-level overlay state
with a small in-memory `lawvm_surface_overlays` fixture (one row per overlay
kind, all on the same rendered span/segment), and exercises the PURE
rendering/placement machinery the viewer uses for overlays:

  - each overlay kind renders a distinct, kind-keyed marker (ov-<kind> class)
  - the layer-toggle set (enabledOverlayKinds) hides/shows a kind
  - a frame overlay's hovercard surfaces its typed payload + co-located links
  - the EXISTING inline-reference (interlink) layer still renders unchanged

Self-contained: serves viewer/ on an ephemeral port and drives headless
Chromium. Not wired into ci.sh (needs Playwright); run manually after viewer
changes touching the overlay layers.
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


# A single segment of body text; every overlay row places onto a [start,end)
# slice of it. The fixture exercises one row per overlay kind plus one interlink
# row (the existing layer), all on the same rendered_address / segment 0.
TEXT = ("Tassa laissa tarkoitetaan paatoksella viranomaisen ratkaisua, johon "
        "sovelletaan 5 paivan kuluessa annettua maaraystae, ellei toisin saadeta.")

# char spans into TEXT (start,len) — chosen to be non-overlapping.
#   'paatoksella' ......... defined_term
#   'viranomaisen' ........ actor_modal / delegation share? keep distinct slices
SPANS = {
    "defined_term": (TEXT.index("paatoksella"), len("paatoksella")),
    "term_use": (TEXT.index("ratkaisua"), len("ratkaisua")),
    "temporal": (TEXT.index("5 paivan"), len("5 paivan")),
    "delegation": (TEXT.index("viranomaisen"), len("viranomaisen")),
    "sanction": (TEXT.index("maaraystae"), len("maaraystae")),
    "exception_condition": (TEXT.index("ellei toisin saadeta"), len("ellei toisin saadeta")),
    "actor_modal": (TEXT.index("sovelletaan"), len("sovelletaan")),
}
# interlink (existing reference layer) — '5 paivan' would collide with temporal;
# use 'annettua' for the citation surface.
REF_SPAN = (TEXT.index("annettua"), len("annettua"))

FIXTURE = """(() => {
  const TEXT = %TEXT%;
  const ADDR = 'chapter:1/section:1';
  applyLocale('fi', 'fi');
  changeDates = ['2020-01-01'];
  curDateIdx = 0;

  // Build the overlay rows (one per kind) with the same rendered_* span columns
  // interlink rows carry.
  const spans = %SPANS%;
  surfaceOverlays = [];
  let oid = 0;
  const mk = (kind, start, len, extra) => Object.assign({
    overlay_id: 'ov' + (++oid), statute_id: '1/2020', kind: kind,
    node_id: ADDR, label: kind + ' label',
    rendered_address: ADDR, rendered_segment_index: 0,
    rendered_char_start: start, rendered_char_end: start + len,
  }, extra || {});
  surfaceOverlays.push(mk('defined_term', spans.defined_term[0], spans.defined_term[1], {
    label: 'paatos', payload_json: JSON.stringify({ definition: 'viranomaisen ratkaisu', use_count: 3 }),
    links_json: JSON.stringify([]),
  }));
  surfaceOverlays.push(mk('term_use', spans.term_use[0], spans.term_use[1], {
    overlay_status: 'resolved', payload_json: JSON.stringify({ term: 'paatos' }),
    links_json: JSON.stringify([{ rel: 'defines', target_overlay_id: 'ov1' }]),
  }));
  surfaceOverlays.push(mk('temporal', spans.temporal[0], spans.temporal[1], {
    payload_json: JSON.stringify({ marker_kind: 'deadline', date: '5 paivaa' }),
  }));
  surfaceOverlays.push(mk('delegation', spans.delegation[0], spans.delegation[1], {
    payload_json: JSON.stringify({ instrument_kind: 'asetus', binding_strength: 'binding', actor: 'viranomainen' }),
    links_json: JSON.stringify([]),
  }));
  surfaceOverlays.push(mk('sanction', spans.sanction[0], spans.sanction[1], {
    payload_json: JSON.stringify({ sanction_kind: 'maarays' }),
    links_json: JSON.stringify([{ rel: 'cites', target_node_id: 'chapter:1/section:5' }]),
  }));
  surfaceOverlays.push(mk('exception_condition', spans.exception_condition[0], spans.exception_condition[1], {
    payload_json: JSON.stringify({ condition: 'ellei toisin saadeta' }),
  }));
  surfaceOverlays.push(mk('actor_modal', spans.actor_modal[0], spans.actor_modal[1], {
    payload_json: JSON.stringify({ actor: 'viranomainen', modal: 'must' }),
  }));
  indexSurfaceOverlays();

  // The existing inline-reference layer (interlink row) on the same segment.
  const refSpan = %REF_SPAN%;
  interlinks = [{
    interlink_id: 'il1', surface_text: TEXT.slice(refSpan[0], refSpan[0] + refSpan[1]),
    resolution_status: 'resolved', confidence: 'exact',
    target_work_id: 'fi:normative_act:5/2020', target_local_id: '5/2020',
    target_locator: 'section:1', role: 'cites',
    rendered_address: ADDR, rendered_segment_index: 0,
    rendered_char_start: refSpan[0], rendered_char_end: refSpan[0] + refSpan[1],
  }];
  indexInterlinks();

  // Default enabled set = reference only.
  enabledOverlayKinds = new Set(['reference']);

  const out = {};
  // 1) References only: interlink renders, no overlay markers.
  out.refOnly = renderTextWithInterlinks(ADDR, 0, TEXT);
  // 2) Enable every overlay kind.
  for (const k of ['defined_term','term_use','temporal','delegation','sanction','exception_condition','actor_modal']) {
    enabledOverlayKinds.add(k);
  }
  out.allOn = renderTextWithInterlinks(ADDR, 0, TEXT);
  // 3) Toggle delegation OFF, confirm it disappears.
  toggleOverlayKind('delegation');
  out.delegationOff = renderTextWithInterlinks(ADDR, 0, TEXT);
  toggleOverlayKind('delegation'); // back on
  // 4) Turn the reference layer OFF, confirm the interlink anchor vanishes.
  toggleOverlayKind('reference');
  out.refOff = renderTextWithInterlinks(ADDR, 0, TEXT);
  toggleOverlayKind('reference'); // back on
  // 5) Hovercards for a frame (sanction) and the defined_term (use count).
  out.sanctionHover = overlayHovercardHtml(overlaysById.get('ov5')) || '';
  out.definedHover = overlayHovercardHtml(overlaysById.get('ov1')) || '';
  out.layerBar = overlayLayerBarHtml();
  return out;
})()"""

FIXTURE = (FIXTURE
           .replace("%TEXT%", repr(TEXT).replace("'", '"'))
           .replace("%SPANS%", "{" + ",".join(
               f"{k}:[{v[0]},{v[1]}]" for k, v in SPANS.items()) + "}")
           .replace("%REF_SPAN%", f"[{REF_SPAN[0]},{REF_SPAN[1]}]"))


with sync_playwright() as p:
    browser = launch_browser(p)
    page = browser.new_page()
    page.goto(BASE)
    page.wait_for_function(
        "typeof renderTextWithInterlinks === 'function' && typeof indexSurfaceOverlays === 'function'"
        " && typeof overlayHovercardHtml === 'function' && typeof toggleOverlayKind === 'function'",
        timeout=15000)

    out = page.evaluate(FIXTURE)

    # 1) References-only default: the interlink anchor renders, no overlay markers.
    check("ref layer renders by default", "ref-sem-resolved" in out["refOnly"], out["refOnly"][:120])
    check("no overlay markers when only references on",
          "ref-overlay" not in out["refOnly"], out["refOnly"][:120])

    # 2) Every overlay kind renders a distinct kind-keyed marker AND the
    #    reference layer survives alongside them.
    KINDS = ["defined_term", "term_use", "temporal", "delegation",
             "sanction", "exception_condition", "actor_modal"]
    for k in KINDS:
        check(f"{k} layer renders", f"ov-{k}" in out["allOn"], "")
    check("reference layer still renders with overlays on",
          "ref-sem-resolved" in out["allOn"], "")

    # 3) Toggling a kind off removes only that marker; others remain.
    check("delegation hidden after toggle off",
          "ov-delegation" not in out["delegationOff"], "")
    check("other overlays survive delegation toggle",
          "ov-sanction" in out["delegationOff"] and "ov-temporal" in out["delegationOff"], "")

    # 4) Turning the reference layer off removes the interlink anchor.
    check("reference anchor gone when layer off",
          "ref-sem-resolved" not in out["refOff"], out["refOff"][:120])
    check("overlays survive reference layer off",
          "ov-defined_term" in out["refOff"], "")

    # 5) Frame hovercard surfaces typed payload + co-located links;
    #    defined_term hovercard shows the use count.
    check("sanction hovercard shows payload fact (sanction kind)",
          "maarays" in out["sanctionHover"], out["sanctionHover"][:160])
    check("sanction hovercard lists co-located link",
          "hc-overlay-links" in out["sanctionHover"]
          and "chapter:1/section:5" in out["sanctionHover"], out["sanctionHover"][:200])
    check("defined_term hovercard shows use count",
          "used 3 times" in out["definedHover"] or "3" in out["definedHover"], out["definedHover"][:160])
    check("defined_term hovercard shows definition payload",
          "viranomaisen ratkaisu" in out["definedHover"], out["definedHover"][:200])

    # 6) Layer-bar UI lists pills for every available kind + reference is on.
    check("layer bar has reference pill active",
          'ov-pill-reference' in out["layerBar"] and 'active' in out["layerBar"], out["layerBar"][:160])
    for k in KINDS:
        check(f"layer bar offers {k} pill", f"ov-pill-{k}" in out["layerBar"], "")

    # 7) Drive the real DOM: inject the all-on render, confirm each marker is a
    #    real element and the reference anchor coexists.
    dom = page.evaluate("""(html) => {
        const d = document.createElement('div');
        d.innerHTML = html;
        document.body.appendChild(d);
        return {
          definedCount: d.querySelectorAll('a.ov-defined_term').length,
          termUseClickable: !!d.querySelector('a.ov-term_use'),
          sanctionBadge: !!d.querySelector('a.ov-sanction'),
          refAnchor: !!d.querySelector('a.ref-sem-resolved'),
          termUseCursor: (() => {
            const a = d.querySelector('a.ov-term_use');
            return a ? getComputedStyle(a).cursor : null;
          })(),
        };
    }""", out["allOn"])
    check("defined_term marker is a real anchor", dom["definedCount"] == 1, str(dom["definedCount"]))
    check("term_use marker is a real anchor", dom["termUseClickable"], "")
    check("sanction frame badge is a real anchor", dom["sanctionBadge"], "")
    check("reference anchor coexists in DOM", dom["refAnchor"], "")
    check("term_use is pointer (navigable to definition)",
          dom["termUseCursor"] == "pointer", str(dom["termUseCursor"]))

    browser.close()

fails = [r for r in results if not r[1]]
print(f"\n{len(results) - len(fails)}/{len(results)} passed")
sys.exit(1 if fails else 0)
