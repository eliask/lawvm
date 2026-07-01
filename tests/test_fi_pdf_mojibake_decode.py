"""Tests for the Regime-B budget-PDF mojibake decoder (task #147).

The 2020s säädöskokoelma budget / lisätalousarvio PDFs (e.g. ``2025/358``)
embed subset TrueType fonts with no ToUnicode CMap. pdfplumber then reports
each glyph as ``(cid:N)``. For those fonts the glyph ids sit at a constant
per-font offset below WinAnsi/ASCII; adding a single *solved* delta (0x1D for
the observed fonts, never hardcoded) recovers the printable band, with a small
shared exception table for ä/ö/Ä/Ö, ``§`` and the en-dash.

These tests pin, using synthetic per-char fixtures (no pdfplumber import
required — the decoder operates on plain ``{"text", "fontname"}`` char dicts):

  1. :func:`test_ciphered_font_fixture_decodes` — a ciphered-font glyph run
     solves the delta from an anchor and decodes to correct Finnish.
  2. :func:`test_clean_font_fixture_untouched` — a clean-font run (real
     unicode, no ``(cid:N)``) is returned unchanged, byte-identical.
  3. :func:`test_mixed_run_font_scoped` — in one page mixing a ciphered and a
     clean font, only the ciphered font's chars are rewritten.
  4. :func:`test_no_deltas_is_noop` — with no ciphered font (Regime-A clean
     PDF shape) no delta is solved and apply is a no-op.
  5. :func:`test_glyph_exception_table` — the non-ASCII glyph exceptions map to
     the correct Finnish characters.
  6. :func:`test_symbol_font_not_ciphered` — a no-ToUnicode Symbol-like font
     whose glyphs don't align to any anchor is *not* treated as ciphered.
  7. :func:`test_corpus_2025_358_decode` — end-to-end on the real archived
     ``2025/358`` fin PDF: anchors decode AND clean-font text is unchanged.
     Skips if the corpus / pdfplumber is unavailable.
"""
from __future__ import annotations

import os

import pytest

from lawvm.finland.pdf_layout import (
    _MOJIBAKE_GLYPH_EXCEPTIONS,
    _apply_mojibake_decode,
    _mojibake_decode_glyph,
    _solve_mojibake_delta,
)


_CIPHER_FONT = "FCPBGG+TimesNewRoman"
_CLEAN_FONT = "IKXCZN+TimesNewRoman"

# The proven offset for the observed budget fonts. The decoder *solves* this
# from anchor alignment; the tests use it only to *build* the ciphered fixture
# (the inverse map), never to assert a hardcoded decode.
_DELTA = 0x1D
# Inverse of _MOJIBAKE_GLYPH_EXCEPTIONS: unicode char -> glyph id.
_UNICODE_TO_GLYPH = {v: k for k, v in _MOJIBAKE_GLYPH_EXCEPTIONS.items()}


def _encipher(text: str) -> list[int]:
    """Turn a unicode string into the glyph-id stream a ciphered subset font
    would emit (the inverse of the decoder): ASCII band -> ``ord(c) - delta``,
    exceptions -> their glyph id."""
    cids: list[int] = []
    for ch in text:
        if ch in _UNICODE_TO_GLYPH:
            cids.append(_UNICODE_TO_GLYPH[ch])
        else:
            cids.append(ord(ch) - _DELTA)
    return cids


def _cipher_chars(text: str, fontname: str = _CIPHER_FONT) -> list[dict]:
    """Build pdfplumber-shaped char dicts for a ciphered run: each char's
    ``text`` is the ``(cid:N)`` token pdfplumber emits for a no-ToUnicode
    glyph."""
    return [
        {"text": f"(cid:{cid})", "fontname": fontname}
        for cid in _encipher(text)
    ]


def _clean_chars(text: str, fontname: str = _CLEAN_FONT) -> list[dict]:
    """Build char dicts for a clean run: real unicode text, no ``(cid:N)``."""
    return [{"text": ch, "fontname": fontname} for ch in text]


def _joined(chars: list[dict]) -> str:
    return "".join(c["text"] for c in chars)


def test_ciphered_font_fixture_decodes() -> None:
    """A ciphered-font glyph run solves the delta from an anchor and decodes
    to correct Finnish."""
    # A run containing the anchor ``VEROT JA VERONLUONTEISET TULOT`` plus
    # accented Finnish words so the exception glyphs are exercised too.
    source = "VEROT JA VERONLUONTEISET TULOT Osasto euroa MÄÄRÄRAHAT lisäystä"
    chars = _cipher_chars(source)
    cids = [int(c["text"][5:-1]) for c in chars]

    delta = _solve_mojibake_delta(cids)
    assert delta == _DELTA, f"expected solved delta 0x1D, got {delta!r}"

    decoded = _joined(_apply_mojibake_decode(chars, {_CIPHER_FONT: delta}))
    assert decoded == source
    for anchor in ("VEROT JA VERONLUONTEISET TULOT", "Osasto", "euroa",
                   "MÄÄRÄRAHAT", "lisäystä"):
        assert anchor in decoded


def test_clean_font_fixture_untouched() -> None:
    """A clean-font run (real unicode, never ``(cid:N)``) is returned
    byte-identical, even when a decode delta is active for a *different*
    font."""
    clean = _clean_chars("HE 58/2025 vp Vuoden 2025 II lisätalousarvio 358/2025")
    original = _joined(clean)

    # A delta is present for the ciphered font, but the clean font isn't keyed.
    out = _apply_mojibake_decode(clean, {_CIPHER_FONT: _DELTA})
    assert _joined(out) == original
    # Same object identity — clean chars are not even copied.
    assert all(a is b for a, b in zip(out, clean, strict=True))


def test_mixed_run_font_scoped() -> None:
    """In a page mixing a ciphered and a clean font, only the ciphered font's
    chars are rewritten; clean-font chars stay identical."""
    ciph = _cipher_chars("euroa Osasto")
    clean = _clean_chars("HE 58/2025")
    page_chars = clean + ciph + clean  # interleave to be adversarial

    out = _apply_mojibake_decode(page_chars, {_CIPHER_FONT: _DELTA})

    # Clean segments unchanged (and same objects).
    assert _joined(out[: len(clean)]) == _joined(clean)
    assert all(out[i] is clean[i] for i in range(len(clean)))
    # Ciphered segment decoded.
    ciph_out = out[len(clean): len(clean) + len(ciph)]
    assert _joined(ciph_out) == "euroa Osasto"


def test_no_deltas_is_noop() -> None:
    """With no ciphered font (Regime-A clean PDF shape), no delta is solved and
    the apply step is an identity no-op returning the same list object."""
    clean = _clean_chars("1 § Tämä laki tulee voimaan.")
    out = _apply_mojibake_decode(clean, {})
    assert out is clean  # exact no-op fast path


def test_glyph_exception_table() -> None:
    """The non-ASCII glyph exceptions map to the correct Finnish characters."""
    expected = {98: "Ä", 103: "Ö", 108: "ä", 124: "ö", 134: "§", 178: "–"}
    for cid, ch in expected.items():
        assert _MOJIBAKE_GLYPH_EXCEPTIONS[cid] == ch
        assert _mojibake_decode_glyph(cid, _DELTA) == ch
    # And 'y' (cid 92) is a normal ASCII-band glyph, NOT an exception —
    # regression guard against the earlier 92->Ä mislabel.
    assert 92 not in _MOJIBAKE_GLYPH_EXCEPTIONS
    assert _mojibake_decode_glyph(92, _DELTA) == "y"


def test_symbol_font_not_ciphered() -> None:
    """A no-ToUnicode font whose glyph run does not align to any anchor is not
    treated as ciphered (no delta solved)."""
    # Glyph ids that, under no delta, spell any anchor.
    assert _solve_mojibake_delta([3, 3, 3]) is None
    # A short run of random glyphs with no anchor.
    assert _solve_mojibake_delta([200, 201, 202, 5, 9]) is None
    # Empty stream.
    assert _solve_mojibake_delta([]) is None


# --- End-to-end on the real archived budget PDF (corpus-gated) ------------

_PDF_KEY = "finlex://sd/2025/358/fin/media/8306.pdf"


def _load_corpus_pdf() -> bytes | None:
    if not os.environ.get("LAWVM_CANONICAL_DATA_ROOT"):
        return None
    try:
        import importlib

        importlib.import_module("pdfplumber")
    except ModuleNotFoundError:
        return None
    try:
        from lawvm.corpus_store import get_corpus_store

        store = get_corpus_store(readonly=True)
        return store.read_locator(_PDF_KEY)
    except Exception:  # pragma: no cover - corpus not extracted in this env
        return None


def test_corpus_2025_358_decode(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end on the real ``2025/358`` fin PDF: ciphered table
    headers/labels decode to correct Finnish, AND clean-font text (page
    numbers / cover title) is byte-identical with the decoder on vs off."""
    pdf_bytes = _load_corpus_pdf()
    if pdf_bytes is None:
        pytest.skip("corpus PDF / pdfplumber unavailable")

    import lawvm.finland.pdf_layout as pl

    layout_on = pl.extract_pdf_layout(pdf_bytes)
    assert layout_on is not None
    alltext = "\n".join(b.text for b in layout_on.body_blocks)

    # Ciphered content decoded — no residual (cid:N) tokens.
    assert "(cid:" not in alltext
    for anchor in (
        "VEROT JA VERONLUONTEISET TULOT",
        "Osasto",
        "euroa",
        "TULOARVIOT",
        "MÄÄRÄRAHAT",
        "Tulon ja varallisuuden",
        "lisäystä",
        "pääomatulo",
        "Yhteisövero",
    ):
        assert anchor in alltext, f"missing decoded anchor: {anchor!r}"

    # Clean-font text untouched: rerun with the decoder disabled and require
    # every pure-clean-font block (no (cid:N) in the disabled run) identical.
    def _no_deltas(pages: list) -> dict[str, int]:
        return {}

    monkeypatch.setattr(pl, "_solve_mojibake_deltas", _no_deltas)
    layout_off = pl.extract_pdf_layout(pdf_bytes)
    monkeypatch.undo()
    assert layout_off is not None

    off_by_key = {
        (b.page_num, round(b.y_position)): b.text for b in layout_off.body_blocks
    }
    on_by_key = {
        (b.page_num, round(b.y_position)): b.text for b in layout_on.body_blocks
    }
    clean_keys = [k for k, t in off_by_key.items() if "(cid:" not in t]
    assert clean_keys, "expected some pure-clean-font blocks"
    changed = [k for k in clean_keys if off_by_key[k] != on_by_key.get(k)]
    assert not changed, f"decoder corrupted clean-font blocks: {changed[:5]}"
