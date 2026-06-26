#!/usr/bin/env python3
"""Demo: EU-directive transposition edge + four-way timeliness verdict.

Runs the real FI-layer transposition extractor + edge projector end-to-end on a
REAL corpus witness and prints the typed :class:`TranspositionEdge` it produces.

The witness is the ACTUAL transposition clause from Ympäristönsuojelulaki
**527/2014** (consolidated, corpus locator
``finlex://sd-cons/2014/527/fin@20150423/main.xml``), which transposes the
Industrial Emissions Directive (IED, CELEX ``32010L0075``). The extractor binds
the CELEX from the Finnish directive nickname; the edge layer compares the act's
real enactment (säädöskokoelma issue) date 2014-06-27 against the IED Art. 80(1)
transposition deadline 2013-01-07 and computes ``LATE`` (Finland was historically
late transposing the IED).

This capability has NO dedicated CLI subcommand — the ``transposition edge +
timeliness`` projection is a library API. This script IS the demonstrable
artifact for it.

Run:
    env LAWVM_CANONICAL_DATA_ROOT=/path/to/LawVM \\
        uv run python scripts/demos/eu_transposition_timeliness_demo.py

Honesty boundary: see notes/reach/EU_TRANSPOSITION_TIMELINESS_REACH.md.
"""

from __future__ import annotations

from lawvm.finland.references.eu_transposition import recognize_transposition_claims
from lawvm.finland.references.eu_transposition_edges import build_transposition_edges

# The ACTUAL IED transposition clause from Ympäristönsuojelulaki 527/2014.
_YSL_527_2014_TRANSPOSITION_CLAUSE = (
    "Valtion valvontaviranomainen voi antaa toiminnanharjoittajalle "
    "polttolaitoksen toimintaa koskevia määräyksiä, jos se on tarpeen "
    "teollisuuspäästödirektiivin III luvun ja liitteen V mukaisten "
    "velvoitteiden täytäntöönpanemiseksi."
)
# Verified real dates for the witness act (independently checkable).
_YSL_527_2014_ENACTMENT_DATE = "2014-06-27"  # säädöskokoelma issue date


def main() -> None:
    claims = recognize_transposition_claims(
        _YSL_527_2014_TRANSPOSITION_CLAUSE, citing_engine_id="2014/527"
    )
    edges = build_transposition_edges(
        claims, fi_enactment_date=_YSL_527_2014_ENACTMENT_DATE
    )

    print("EU-DIRECTIVE TRANSPOSITION EDGE + TIMELINESS")
    print("witness: Ympäristönsuojelulaki 527/2014 (consolidated)")
    print("clause:", _YSL_527_2014_TRANSPOSITION_CLAUSE)
    print("=" * 64)
    print(f"transposition claims recognised: {len(claims)}")
    for edge in edges:
        print()
        print(f"  citing FI act      : {edge.fi_citing_engine_id}")
        print(f"  directive surface  : {edge.directive_surface}")
        print(f"  bound CELEX        : {edge.eu_directive_celex}")
        print(f"  binding status     : {edge.binding_status.value}")
        print(f"  edge kind          : {edge.edge_kind}  (DECLARED relation, not conformance)")
        print(f"  directive deadline : {edge.transposition_deadline}  (IED Art. 80(1))")
        print(f"  FI enactment date  : {edge.fi_enactment_date}")
        print(f"  TIMELINESS VERDICT : {edge.timeliness.value.upper()}")
    print()
    print(
        "Reading: the FI act DECLARES it transposes the IED; the edge says the "
        "transposition was LATE (2014-06-27 > 2013-01-07). It does NOT claim the "
        "transposition is materially conformant."
    )


if __name__ == "__main__":
    main()
