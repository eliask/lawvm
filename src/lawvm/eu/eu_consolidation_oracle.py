"""eu_consolidation_oracle.py — acquire a sector-0 consolidation at a PIT + diff.

Increment 1 goal 2 (the real consolidation-PIT oracle). The native replay
(``fmx4_amendment_grammar`` → ``eu_ordering`` → ``apply_eu_ops_conserved``)
reconstructs a point-in-time body; the EUR-Lex SECTOR-0 consolidation is the
Office's editorial rendering of the same PIT. This module acquires that
consolidation and feeds it to the existing non-repairing comparator
(``eu_oracle_divergence.compare_replay_to_consolidation``).

Consolidated CELEX form (design §2.2): ``0YYYY<LETTER><NNNN>-YYYYMMDD`` — the
basic act's number with sector ``0`` and the date-of-application of the last
incorporated amending act as a suffix (e.g. ``02016R0044-20160401``). These texts
have "no legal value … no guarantee [of] the latest state" (EUR-Lex), so the
comparator NEVER repairs the replay toward them — divergence is a first-class
finding (the ``authoritative oracle ≠ correct`` regime already first-class in
LawVM; cf. EE oracle_suspect).

Two-lane honesty (design §4 / observed live in Increment 0+1): the SPARQL lane
that ENUMERATES the consolidation series is up while the REST byte lane that
FETCHES the consolidated FMX4 intermittently returns HTTP 5xx
(``502 Bad Gateway`` / ``Unable to acquire JDBC Connection``). A REST failure is
recorded as a typed :class:`ConsolidationAcquisitionFailure` — NEVER a silent
empty oracle masquerading as "the consolidation matches the replay".
"""

from __future__ import annotations

import re
from typing import Callable, Optional

from lawvm.core.ir import IRStatute
from lawvm.eu.eu_oracle_divergence import (
    OracleComparison,
    compare_replay_to_consolidation,
)

__all__ = [
    "ConsolidationAcquisitionFailure",
    "consolidated_celex",
    "parse_consolidation_date",
    "build_consolidation_oracle",
]

#: A consolidated CELEX: sector 0, year, descriptor letter, number, ``-`` date.
_CONSOLIDATED_RE = re.compile(r"^0\d{4}[A-Z]\d+-(?P<date>\d{8})$")


class ConsolidationAcquisitionFailure(RuntimeError):
    """Raised when the consolidated manifestation could not be acquired/parsed.

    Carries the typed reason (REST 5xx, non-XML body, parse failure) so the
    caller records a recorded gap, never a fabricated agreement.
    """


def consolidated_celex(base_celex: str, as_of: str) -> str:
    """Build the sector-0 consolidated CELEX for ``base_celex`` at ``as_of``.

    ``base_celex`` is a well-formed act CELEX (``32016R0044``); ``as_of`` is a
    PIT date ``YYYY-MM-DD`` or ``YYYYMMDD``. Returns ``02016R0044-20160401``.
    """
    digits = as_of.replace("-", "")
    if not re.fullmatch(r"\d{8}", digits):
        raise ValueError(
            f"as_of must be YYYY-MM-DD or YYYYMMDD, got {as_of!r}"
        )
    if not re.fullmatch(r"[1-9]\d{4}[A-Z]\d+", base_celex):
        raise ValueError(
            f"base_celex must be a well-formed act CELEX, got {base_celex!r}"
        )
    # Sector 0 = consolidated; replace the leading sector digit with 0.
    return f"0{base_celex[1:]}-{digits}"


def parse_consolidation_date(consolidated: str) -> str:
    """Extract the ``YYYYMMDD`` date suffix of a consolidated CELEX, or raise."""
    m = _CONSOLIDATED_RE.match(consolidated)
    if not m:
        raise ValueError(
            f"not a consolidated CELEX (0YYYY<L><N>-YYYYMMDD): {consolidated!r}"
        )
    return m.group("date")


def build_consolidation_oracle(
    replayed: IRStatute,
    *,
    base_celex: str,
    as_of: str,
    fetch_consolidation: Callable[[str], bytes],
    parse_fmx4_bytes: Optional[Callable[[bytes, str], IRStatute]] = None,
) -> OracleComparison:
    """Acquire the sector-0 consolidation at ``as_of`` and diff it vs ``replayed``.

    Parameters
    ----------
    replayed:
        The native-replay PIT body (the LawVM-native reconstruction).
    base_celex / as_of:
        Identify the consolidated manifestation (``consolidated_celex``).
    fetch_consolidation:
        ``fetch_consolidation(consolidated_celex) -> fmx4_bytes``. The byte lane.
        Raise (any exception) on a REST failure; it is wrapped into a typed
        :class:`ConsolidationAcquisitionFailure` (never a silent empty oracle).
    parse_fmx4_bytes:
        ``parse_fmx4_bytes(bytes, celex) -> IRStatute``. Defaults to the grafter
        (``parse_eu_regulation_ir`` over a temp file). A parse failure is also a
        typed acquisition failure.

    Returns
    -------
    An :class:`OracleComparison` — the per-article evidence ledger. NEVER repairs
    the replay toward the consolidation.
    """
    cons_celex = consolidated_celex(base_celex, as_of)
    try:
        raw = fetch_consolidation(cons_celex)
    except Exception as exc:  # noqa: BLE001 — any byte-lane failure is a typed gap
        raise ConsolidationAcquisitionFailure(
            f"could not acquire consolidation {cons_celex}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if not raw:
        raise ConsolidationAcquisitionFailure(
            f"consolidation {cons_celex} returned empty bytes (not an empty "
            "oracle — a recorded acquisition gap)"
        )

    parser = parse_fmx4_bytes or _default_parse_fmx4_bytes
    try:
        consolidated = parser(raw, cons_celex)
    except Exception as exc:  # noqa: BLE001
        raise ConsolidationAcquisitionFailure(
            f"could not parse consolidation {cons_celex}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    return compare_replay_to_consolidation(
        replayed, consolidated, as_of=as_of, base_celex=base_celex
    )


def _default_parse_fmx4_bytes(raw: bytes, celex: str) -> IRStatute:
    """Parse consolidated FMX4 bytes into an IRStatute via the grafter.

    The grafter parses from a path, so the bytes are written to a NamedTemporary
    file (consolidated bytes are NOT persisted to the farchive or git — design
    discipline: acquired data stays out of the tree).
    """
    import tempfile
    from pathlib import Path

    from lawvm.eu.grafter import parse_eu_regulation_ir

    with tempfile.NamedTemporaryFile(suffix=".xml", delete=True) as tf:
        tf.write(raw)
        tf.flush()
        return parse_eu_regulation_ir(Path(tf.name), celex=celex)
