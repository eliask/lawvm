from __future__ import annotations

from pathlib import Path

import yaml

from scripts.scan_absent_ajantasa import (
    _confidence_label_fi,
    _load_corrections,
    _mechanism_label_fi,
    _status_label_fi,
)


def test_load_corrections_filters_unified_ledger_by_scope(tmp_path: Path) -> None:
    path = tmp_path / "corrections.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "validity_judgements": [
                    {
                        "statute_id": "2000/1",
                        "scope": "review_ledger",
                        "confidence": "high",
                    },
                    {
                        "statute_id": "2000/2",
                        "scope": "stale_in_force",
                        "confidence": "confirmed",
                        "mechanism": "sunset_clause",
                    },
                ]
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    corrections = _load_corrections(path)

    assert list(corrections) == ["2000/2"]
    assert corrections["2000/2"]["scope"] == "stale_in_force"
    assert corrections["2000/2"]["confidence"] == "confirmed"


def test_load_corrections_keeps_legacy_stale_in_force_fallback(tmp_path: Path) -> None:
    path = tmp_path / "legacy.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "stale_in_force": [
                    {
                        "statute_id": "1901/34-001",
                        "confidence": "confirmed",
                        "mechanism": "explicitly_repealed",
                    }
                ]
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    corrections = _load_corrections(path)

    assert list(corrections) == ["1901/34-001"]
    assert corrections["1901/34-001"]["mechanism"] == "explicitly_repealed"


def test_finnish_labels_for_corrections_are_localized() -> None:
    assert _status_label_fi("not valid") == "ei voimassa"
    assert _status_label_fi("valid") == "voimassa"
    assert _confidence_label_fi("confirmed") == "vahvistettu"
    assert _mechanism_label_fi("eu_accession_superseded") == "EU-jäsenyyden myötä korvautunut"
