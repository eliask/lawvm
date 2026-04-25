from __future__ import annotations

from pathlib import Path

import pytest

from scripts.audit_verified_finlex_yaml import _candidate_db_paths, _resolve_db_path


def test_candidate_db_paths_prefers_local_default_first() -> None:
    candidates = _candidate_db_paths(".tmp/finlex_errors_publication.db")

    assert candidates[0] == Path(".tmp/finlex_errors_publication.db")
    assert candidates == (Path(".tmp/finlex_errors_publication.db"),)


def test_resolve_db_path_falls_back_to_local_default(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = {
        Path(".tmp/finlex_errors_publication.db"),
    }

    monkeypatch.setattr(Path, "exists", lambda self: self in existing)

    assert _resolve_db_path("../nowhere/finlex_errors_publication.db") == Path(".tmp/finlex_errors_publication.db")


def test_resolve_db_path_errors_with_attempted_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "exists", lambda self: False)

    with pytest.raises(SystemExit, match="publication db not found; tried:"):
        _resolve_db_path(".tmp/finlex_errors_publication.db")
