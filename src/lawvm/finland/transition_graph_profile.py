"""Finnish transition-graph export profile."""
from __future__ import annotations

import re

from lawvm.finland.statute_id import canonical_statute_id, engine_statute_id
from lawvm.tools.transition_graph_profile import TransitionGraphExportProfile

_HE_HREF_RE = re.compile(r"/akn/fi/doc/government-proposal/(\d{4})/(\d{1,4}-\d{1,4}|\d{1,4})")
_HE_TEXT_RE = re.compile(r"\bHE\s{1,4}(\d{1,4}-\d{1,4}|\d{1,4})/(\d{4})\s{0,4}vp", re.IGNORECASE)


def fi_current_statute_url(_canonical_id: str, engine_id: str) -> str:
    year, sep, num = engine_id.partition("/")
    if not sep or not year or not num:
        return ""
    return f"https://www.finlex.fi/fi/laki/ajantasa/{year}/{num}"


def fi_amendment_url(_canonical_id: str, engine_id: str) -> str:
    year, sep, _num = engine_id.partition("/")
    if not sep or not year:
        return ""
    return f"https://www.finlex.fi/fi/laki/alkup/{year}/{engine_id.replace('/', '')}"


def extract_fi_source_reference(corpus: object, engine_source_id: str) -> str:
    """Return the first Finnish HE reference attached to an amendment source."""
    try:
        amendment_xml = corpus.read_amendment(engine_source_id)
    except Exception:
        return ""
    if not amendment_xml:
        return ""
    try:
        text = amendment_xml.decode("utf-8", "ignore")
    except Exception:
        return ""
    match = _HE_HREF_RE.search(text)
    if match:
        return f"HE {match.group(2)}/{match.group(1)} vp"
    text_match = _HE_TEXT_RE.search(text)
    if text_match:
        return f"HE {text_match.group(1)}/{text_match.group(2)} vp"
    return ""


def fi_transition_graph_corpus() -> object:
    from lawvm.finland.corpus import _get_corpus_store

    return _get_corpus_store()


def fi_transition_graph_commencement_date(timelines: object) -> str:
    from lawvm.finland.fixed_term_expiry import _scan_statute_commencements

    commencements = _scan_statute_commencements(timelines)
    if len(commencements) == 1:
        return commencements[0]
    return ""


def finland_transition_graph_export_profile() -> TransitionGraphExportProfile:
    return TransitionGraphExportProfile(
        jurisdiction="fi",
        lang="fi",
        canonical_statute_id=canonical_statute_id,
        engine_statute_id=engine_statute_id,
        statute_url=fi_current_statute_url,
        amendment_url=fi_amendment_url,
        source_reference=extract_fi_source_reference,
        corpus=fi_transition_graph_corpus,
        commencement_date=fi_transition_graph_commencement_date,
    )
