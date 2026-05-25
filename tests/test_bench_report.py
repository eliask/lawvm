from __future__ import annotations

import argparse
import json

import pytest

from lawvm.tools import bench_report


def _args(path, *, json_output: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        run=str(path),
        errors_only=False,
        threshold=0.999,
        json=json_output,
        top=0,
        bottom=2,
    )


def test_bench_report_supports_finland_similarity_csv(tmp_path, capsys) -> None:
    path = tmp_path / "fi.csv"
    path.write_text(
        "statute_id,similarity,status,amendments,elapsed_s\n"
        "2000/1,0.50,OK,3,1.2\n"
        "2000/2,1.00,OK,4,2.3\n",
        encoding="utf-8",
    )

    bench_report.main(_args(path))

    out = capsys.readouterr().out
    assert "Score column: similarity" in out
    assert "2000/1" in out
    assert "0.500000" in out


def test_bench_report_supports_uk_score_csv_json(tmp_path, capsys) -> None:
    path = tmp_path / "uk.csv"
    path.write_text(
        "statute_id,score,status,n_effects,duration_s\n"
        "ukpga/2000/1,0.25,OK,10,5.5\n"
        "ukpga/2000/2,0.75,ERROR,20,6.5\n",
        encoding="utf-8",
    )

    bench_report.main(_args(path, json_output=True))

    payload = json.loads(capsys.readouterr().out)
    assert payload["score_column"] == "score"
    assert payload["count_column"] == "n_effects"
    assert payload["elapsed_column"] == "duration_s"
    assert payload["rows"][0]["statute_id"] == "ukpga/2000/1"
    assert payload["rows"][0]["score"] == 0.25
    assert payload["rows"][0]["count"] == 10


def test_bench_report_rejects_unknown_score_column(tmp_path, capsys) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("statute_id,status\nx,OK\n", encoding="utf-8")

    with pytest.raises(SystemExit):
        bench_report.main(_args(path))

    err = capsys.readouterr().err
    assert "no recognized score column" in err
