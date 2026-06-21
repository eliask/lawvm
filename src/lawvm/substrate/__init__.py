"""LawVM distributable-substrate prototype — P0 primitives.

This package is the isolated, import-light home of the substrate object-model
primitives frozen in ``notes_internal/OBJECT_MODEL_AND_PACK_V0.md``:

* :mod:`lawvm.substrate.canonical_json` — the ``lawvm.canonical_json.v1``
  identity encoding, NFC-at-construction normalization, and the
  ``{object_hash, object}`` JSONL wrapper.
* :mod:`lawvm.substrate.roots` — the four named root constructors
  (``LeafHash`` / ``SetRoot`` / ``SeqRoot`` / ``MapRoot``).
* :mod:`lawvm.substrate.hashes` — the explicit three-hash split
  (raw witness / semantic object / storage blob).
* :mod:`lawvm.substrate.manifest` — the self-describing ``PackManifest``.

The canonical-JSON profile and the ``SetRoot``/``SeqRoot``/``LeafHash``
constructors are byte-for-byte re-implementations of the verified-on-disk
trust spine (``lawvm.tools.certificate_bundle``); a test in
``tests/substrate/test_canonical_json.py`` pins equality with that source so
the two implementations cannot drift.
"""

from __future__ import annotations
