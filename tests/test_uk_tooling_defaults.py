from __future__ import annotations

from pathlib import Path
import sys
import types
import pytest
from lawvm.uk_legislation import uk_prefetch

from lawvm.tools import cli, uk_bench, uk_candidates, uk_effect, uk_effects, uk_eids, uk_replay
from scripts import acquire_uk_corpus, fetch_uk_affecting_acts


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_uk_archives_default_to_data_dir_in_tool_modules() -> None:
    expected = _repo_root() / "data" / "uk_legislation.farchive"

    assert uk_bench._DEFAULT_DB == expected
    assert uk_candidates._DEFAULT_DB == expected
    assert uk_effect._DEFAULT_DB == expected
    assert uk_effects._DEFAULT_DB == expected
    assert uk_replay._DEFAULT_DB == expected
    assert uk_eids._DEFAULT_DB == expected
    assert fetch_uk_affecting_acts._DEFAULT_DB == expected
    assert acquire_uk_corpus._DEFAULT_ARCHIVE == expected


def test_uk_cli_help_strings_reference_data_archive_default(capsys) -> None:
    parser = cli._build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["uk-replay", "--help"])
    text = capsys.readouterr().out
    assert "data/uk_legislation.farchive" in text

    with pytest.raises(SystemExit):
        parser.parse_args(["uk-fetch-affecting", "ukpga/2000/10", "--help"])
    text = capsys.readouterr().out
    assert "data/uk_legislation.farchive" in text

    with pytest.raises(SystemExit):
        parser.parse_args(["uk-effect", "ukpga/2000/10", "key", "--help"])
    text = capsys.readouterr().out
    assert "data/uk_legislation.farchive" in text

    with pytest.raises(SystemExit):
        parser.parse_args(["uk-effects", "ukpga/2000/10", "--help"])
    text = capsys.readouterr().out
    assert "data/uk_legislation.farchive" in text

    with pytest.raises(SystemExit):
        parser.parse_args(["uk-eids", "ukpga/2000/10", "--help"])
    text = capsys.readouterr().out
    assert "data/uk_legislation.farchive" in text

    with pytest.raises(SystemExit):
        parser.parse_args(["uk-candidates", "--help"])
    text = capsys.readouterr().out
    assert "data/uk_legislation.farchive" in text


def test_fetch_uk_affecting_acts_main_uses_farchive(monkeypatch, tmp_path) -> None:
    class DummyArchive:
        def __init__(self, path: Path):
            self.path = Path(path)
            self.closed = False

        def close(self) -> None:
            self.closed = True

    calls: dict[str, object] = {}

    def fake_farchive(path: Path) -> DummyArchive:
        archive = DummyArchive(path)
        calls["archive_path"] = Path(path)
        calls["archive_obj"] = archive
        return archive

    fake_farchive_module = types.ModuleType("farchive")
    setattr(fake_farchive_module, "Farchive", fake_farchive)

    def fake_fetch_missing(
        sid: str,
        archive: object,
        delay: float = 0.8,
        dry_run: bool = False,
        verbose: bool = False,
    ) -> tuple[int, int, int]:
        calls["sid"] = sid
        calls["delay"] = delay
        calls["dry_run"] = dry_run
        calls["verbose"] = verbose
        calls["fetch_archive"] = archive
        return (1, 2, 0)

    db = tmp_path / "uk_legislation.farchive"
    db.touch()

    monkeypatch.setitem(sys.modules, "farchive", fake_farchive_module)
    monkeypatch.setattr(uk_prefetch, "fetch_missing_for_statute", fake_fetch_missing)
    monkeypatch.setattr(
        fetch_uk_affecting_acts.sys,
        "argv",
        ["prog", "--statute", "ukpga/2000/10", "--db", str(db), "--verbose"],
    )

    fetch_uk_affecting_acts.main()

    assert "fetch_archive" in calls
    assert calls["sid"] == "ukpga/2000/10"
    assert calls["delay"] == 0.8
    assert calls["dry_run"] is False
    assert calls["verbose"] is True
    assert calls["archive_path"] == db
    assert calls["archive_obj"] is calls["fetch_archive"]
    assert isinstance(calls["archive_obj"], DummyArchive)
    assert calls["archive_obj"].closed is True
