"""provenance_span — neutral home for the provenance-span boundary helper.

This module owns ``_skip_prov_span`` (and its strict private helper
``_has_citation_before_hard_boundary``), the token-span boundary computation
shared between the legacy ``surface_parse`` parser, the ``scan`` annotation
layer, and the construction-grammar ``grammar.parser`` driver.

It lives here — depending only on the low-level ``lexicon`` token type and
``source_verb`` — so the grammar package can import the helper WITHOUT taking a
dependency on legacy ``surface_parse``.  The function bodies are an exact
relocation; behavior is unchanged.
"""

from __future__ import annotations

from lawvm.finland.johtolause.lexicon import Token
from lawvm.finland.source_verb import SourceVerb


def _has_citation_before_hard_boundary(tokens: list[Token], start: int, n: int) -> bool:
    """Return True if a CITATION_SPAN appears before the next hard boundary.

    Scans from ``start`` until a VERB / END / UUSI token (the boundaries that
    terminate a provenance span).  Used to distinguish a provenance phrase's
    closing citation (no further citation ahead) from an interior citation in a
    multi-arm "ovat ... laissa X, ... laissa Y" enumeration (more citations
    ahead).
    """
    j = start
    while j < n:
        cat = tokens[j].cat
        if cat in ("VERB", "END", "UUSI"):
            return False
        if cat == "CITATION_SPAN":
            return True
        j += 1
    return False


def _skip_prov_span(tokens: list[Token], start: int, n: int) -> int:
    """Skip a provenance span starting at `start`, return index after it."""
    _PROV_CONTINUATION = frozenset(
        {
            "mainitulla",
            "mainittu",
            "mainitun",
            "mainituilla",
            "annetulla",
            "annettu",
            "annetuilla",
            "annetun",
            "viimeksi",
            "osittain",
        }
    )
    # Track whether we've seen a non-legislative verb (ovat/olla) — if so,
    # structural tokens after it are inside the provenance, not real targets.
    # "sellaisina kuin niistä ovat 4 ja 6 §:n" — "ovat" signals that
    # "4 ja 6 §:n" enumerates WHAT was changed, not targets for this amendment.
    _PROV_INTERNAL_VERBS = frozenset({"ovat", "on", "olla"})
    seen_internal_verb = False
    # Distinct from seen_internal_verb: True only when a SURVIVING ovat/on token
    # was seen (not when the internal verb was merely inferred from a leading
    # CITATION_SPAN).  In the surviving-verb shape the appositive is a single
    # "ovat <provenance section-refs> laissa (NNN/YY)" phrase whose closing
    # citation truly ends the provenance; in the inferred shape the phrase is a
    # repeating "<section> laissa <cite>" enumeration where each citation is
    # internal, so its closure must NOT resume the target list.
    seen_surviving_internal_verb = False
    # True once a date phrase ("N päivänä <month> NNNN", recognised as a NUM
    # immediately followed by the word "päivänä") is seen while the internal-verb
    # flag is active.  A date between the internal verb and the closing comma is
    # the positive hallmark of an ANAPHORIC provenance ("se on [edellä
    # mainitussa] N päivänä <month> NNNN annetussa asetuksessa,"): it names a
    # specific dated statute and then resumes the real target list.  The
    # surviving-verb enumerations this skip must NOT break out of — whether
    # per-arm-citation ("ovat, 2 §:n ... laissa X ja 10 §:n ... laissa Y") or
    # bare-number coordination ("niistä ovat, 4, 11, 12 ja 16 §, 18 §:n ...") —
    # never carry a date in that span.  Requiring the date keeps the anaphoric
    # exit from firing inside any surviving-verb enumeration.
    seen_date_after_internal_verb = False

    # When citation stripping runs before provenance detection (the normal
    # pipeline), the "kuin ne/se ovat/on ... laissa NNN/YYYY" words between
    # the PROV trigger and the first provenance section reference get consumed
    # into a CITATION_SPAN sentinel.  This hides the internal verb (ovat/on)
    # that _skip_prov_span relies on to know that subsequent structural tokens
    # (NUM + PYKALA patterns) are provenance enumerations, not real targets.
    # Infer the internal verb from the presence of CITATION_SPAN immediately
    # after the PROV token.
    if start + 1 < n and tokens[start + 1].cat == "CITATION_SPAN":
        seen_internal_verb = True

    def _is_relative_move_tail_after_structural_list(start_idx: int) -> bool:
        """Return True when a structural list is followed by a relative move tail.

        This covers old shapes like:
          ``..., 30 ja 31 §, jotka samalla siirretään I osaan``

        The structural section list after the provenance phrase is real target
        syntax, not part of the provenance enumeration.
        """
        saw_structural = False
        j = start_idx
        while j < n:
            t = tokens[j]
            if t.cat in ("NUM", "LETTER", "DASH", "CONJ"):
                j += 1
                continue
            if t.cat == "PYKALA":
                saw_structural = True
                j += 1
                continue
            break
        if not saw_structural or j >= n or tokens[j].cat != "COMMA":
            return False
        j += 1
        while j < n and tokens[j].cat == "WORD":
            if tokens[j].text.lower() in {"joka", "jotka", "joista"}:
                j += 1
                while j < n and tokens[j].cat == "WORD":
                    j += 1
                return j < n and tokens[j].cat == "VERB" and tokens[j].verb_code == SourceVerb.SIIRTAA
            j += 1
        return False

    def _numeric_run_reaches_structural_unit(start_idx: int) -> bool:
        """Return True for bounded ``NUM[, NUM ja NUM] §`` target runs."""
        for k in range(start_idx, min(start_idx + 8, n)):
            if tokens[k].cat in ("PYKALA", "LUKU", "LIITE", "NIMIKE"):
                return True
            if tokens[k].cat not in ("NUM", "LETTER", "DASH", "COMMA", "CONJ"):
                break
        return False

    i = start + 1
    while i < n:
        t = tokens[i]
        if t.cat in ("VERB", "END", "UUSI"):
            break
        if t.text.lower() in _PROV_INTERNAL_VERBS:
            seen_internal_verb = True
            seen_surviving_internal_verb = True
        if (
            t.cat == "NUM"
            and seen_internal_verb
            and i + 1 < n
            and tokens[i + 1].text.lower() == "päivänä"
        ):
            seen_date_after_internal_verb = True
        # The appositive's closing citation ("... laissa (NNN/YY)") collapses
        # into a CITATION_SPAN sentinel.  Once we pass it, an "ovat ... laissa"
        # provenance phrase whose internal verb survived as a real token can be
        # complete: a subsequent separator + structural reference is then a REAL
        # target resuming the list, not a continuation of the provenance
        # enumeration.  Clearing the internal-verb flag re-enables the
        # COMMA/CONJ → structural exits below so the resuming target is
        # preserved instead of silently swallowed.
        #
        # Two guards keep this from misfiring on legitimate provenance:
        #   1. seen_surviving_internal_verb — only the surviving-verb shape.
        #      In the inferred shape (leading CITATION_SPAN absorbed the verb)
        #      the phrase is a repeating "<section> laissa <cite>" enumeration
        #      where each citation is internal.
        #   2. No further CITATION_SPAN before the hard boundary (VERB/END/UUSI).
        #      A multi-arm provenance ("ovat ... laissa X, ... laissa Y") still
        #      has citations ahead, so this one is NOT the closing citation and
        #      the enumeration after it is still provenance.
        if (
            t.cat == "CITATION_SPAN"
            and seen_surviving_internal_verb
            and not _has_citation_before_hard_boundary(tokens, i + 1, n)
        ):
            seen_internal_verb = False
            seen_surviving_internal_verb = False
        # Comma followed by UUSI or structural = end of provenance
        # BUT: after an internal verb (ovat/on), structural tokens are part
        # of the provenance enumeration, not real targets.
        if t.cat == "COMMA" and i + 1 < n:
            nxt = tokens[i + 1]
            if nxt.cat in ("UUSI", "VERB", "END"):
                i += 1  # consume comma
                break
            # COMMA + BACKREF: check what follows
            if nxt.cat == "BACKREF" and i + 2 < n:
                nxt2 = tokens[i + 2]
                if nxt2.cat == "PYKALA":
                    i += 1  # consume comma
                    return i  # ", mainitun pykälän" = structural, exit
                # ", mainitun lain" = still provenance
                i += 1
                continue
            if nxt.cat == "NUM" and not seen_internal_verb:
                if _numeric_run_reaches_structural_unit(i + 2):
                    i += 1  # consume comma
                    return i
            # Anaphoric provenance ("..., sellaisena kuin se on [edellä
            # mainitussa] N päivänä <month> NNNN annetussa asetuksessa, 34 §:n
            # ...") carries a real internal verb (on/ovat) but its appositive
            # names a specific dated statute and enumerates no sections of its
            # own, so it closes with a statute noun and no closing citation.  The
            # surviving-verb clearing logic above only fires on a real closing
            # CITATION_SPAN, so this anaphoric span keeps seen_internal_verb set
            # and swallows the genuine targets that follow.  Exit at the comma
            # into the next structural target — but ONLY for the anaphoric shape,
            # identified by the date phrase ("N päivänä ... annettu") between the
            # internal verb and this comma.  Surviving-verb enumerations carry no
            # such date, whether per-arm-citation ("ovat, 2 §:n ... laissa X ja
            # 10 §:n ... laissa Y") or bare-number coordination ("niistä ovat, 4,
            # 11, 12 ja 16 §, 18 §:n ..."), so requiring the date keeps this exit
            # from firing inside them.
            if (
                nxt.cat == "NUM"
                and seen_internal_verb
                and seen_date_after_internal_verb
            ):
                if _numeric_run_reaches_structural_unit(i + 2):
                    i += 1  # consume comma
                    return i
            if nxt.cat == "NUM" and seen_internal_verb and _is_relative_move_tail_after_structural_list(i + 1):
                i += 1  # consume comma
                return i
            # Comma followed by provenance continuation word = keep skipping
            if nxt.text.lower() in _PROV_CONTINUATION:
                i += 1
                continue
        # CONJ followed by structural = end — preserve the CONJ as separator
        # Skip this exit when we're inside a provenance enumeration (after ovat/on)
        if t.cat == "CONJ" and i + 1 < n:
            nxt = tokens[i + 1]
            if nxt.cat in ("UUSI", "VERB", "DOC"):
                break  # preserve CONJ
            # CONJ + BACKREF: check what follows the backref
            if nxt.cat == "BACKREF" and i + 2 < n:
                nxt2 = tokens[i + 2]
                if nxt2.cat == "PYKALA":
                    return i  # "ja mainitun pykälän" = structural, exit provenance
                # "ja mainitun lain" = still provenance, keep skipping
                i += 1
                continue
            if nxt.cat == "NUM" and not seen_internal_verb:
                if _numeric_run_reaches_structural_unit(i + 2):
                    return i  # preserve CONJ
            # CONJ + continuation word = keep skipping
            if nxt.text.lower() in _PROV_CONTINUATION:
                i += 1
                continue
        i += 1
    return i
