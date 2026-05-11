from __future__ import annotations

from types import SimpleNamespace

import pytest

from lawvm.tools import bench_curate


def test_oracle_suspect_cache_only_flags_future_effective(monkeypatch):
    monkeypatch.setattr(
        bench_curate,
        "get_consolidated_oracle_suspect_cache_only",
        lambda sid: ("2010/1051 eff 2011-01-01 > cutoff 2010-12-03", ""),
    )

    suspect, pending = bench_curate.get_consolidated_oracle_suspect_cache_only("1974/412")

    assert suspect == "2010/1051 eff 2011-01-01 > cutoff 2010-12-03"
    assert pending == ""


def test_bench_curate_defaults_to_cache_only_and_partitions_suspect(tmp_path, monkeypatch):
    corpus = tmp_path / "bench_corpus.csv"
    corpus.write_text("8,1974/412\n", encoding="utf-8")

    monkeypatch.setattr(
        bench_curate,
        "get_consolidated_oracle_suspect_cache_only",
        lambda sid: ("2010/1051 eff 2011-01-01 > cutoff 2010-12-03", ""),
    )

    args = SimpleNamespace(corpus=str(corpus), output_dir=str(tmp_path), run=None, strict_run=None)
    bench_curate.main(args)

    core = (tmp_path / "bench_core.csv").read_text(encoding="utf-8").strip()
    suspect = (tmp_path / "bench_suspect.csv").read_text(encoding="utf-8").strip()
    audit = (tmp_path / "bench_partition_audit.csv").read_text(encoding="utf-8")

    assert core == ""
    assert suspect == "8,1974/412"
    assert "oracle_suspect" in audit


def test_bench_curate_partitions_source_pathology_from_strict_run(tmp_path):
    corpus = tmp_path / "bench_corpus.csv"
    corpus.write_text("43,1994/1472\n", encoding="utf-8")

    strict_run = tmp_path / "strict.csv"
    strict_run.write_text(
        "\n".join(
            [
                "statute_id,n_canonical,n_failed,n_projection_rows,n_source_pathologies,projection_kinds,source_pathology_codes,fail_reasons,source_incomplete,chain_length,source_available,elapsed_s,error",
                "1994/1472,0,0,2,1,source_pathology|ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY,DESTRUCTIVE_SHAPE_LOSS_RISK,,0,43,1,0.50,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    args = SimpleNamespace(
        corpus=str(corpus),
        output_dir=str(tmp_path),
        run=None,
        strict_run=[str(strict_run)],
        oracle_suspect_check="off",
    )
    bench_curate.main(args)

    core = (tmp_path / "bench_core.csv").read_text(encoding="utf-8").strip()
    suspect = (tmp_path / "bench_suspect.csv").read_text(encoding="utf-8").strip()
    audit = (tmp_path / "bench_partition_audit.csv").read_text(encoding="utf-8")

    assert core == ""
    assert suspect == "43,1994/1472"
    assert "source_pathology" in audit
    assert "DESTRUCTIVE_SHAPE_LOSS_RISK" in audit


def test_bench_curate_partitions_source_pathology_from_structured_rows_when_codes_missing(tmp_path):
    corpus = tmp_path / "bench_corpus.csv"
    corpus.write_text("43,1994/1472\n", encoding="utf-8")

    strict_run = tmp_path / "strict.csv"
    strict_run.write_text(
        "\n".join(
            [
                "statute_id,n_canonical,n_failed,n_projection_rows,n_source_pathologies,projection_kinds,source_pathology_codes,source_pathology_rows_json,fail_reasons,source_incomplete,chain_length,source_available,elapsed_s,error",
                '1994/1472,0,0,2,1,ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY,,\"[{""code"":""DESTRUCTIVE_SHAPE_LOSS_RISK"",""target_label"":""35 §"",""detail"":{""diagnostic_reason"":""partial_body_only""}}]\",APPLY.SOURCE_PATHOLOGY_DETECTED,0,43,1,0.50,',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    args = SimpleNamespace(
        corpus=str(corpus),
        output_dir=str(tmp_path),
        run=None,
        strict_run=[str(strict_run)],
        oracle_suspect_check="off",
    )
    bench_curate.main(args)

    core = (tmp_path / "bench_core.csv").read_text(encoding="utf-8").strip()
    suspect = (tmp_path / "bench_suspect.csv").read_text(encoding="utf-8").strip()
    audit = (tmp_path / "bench_partition_audit.csv").read_text(encoding="utf-8")

    assert core == ""
    assert suspect == "43,1994/1472"
    assert "DESTRUCTIVE_SHAPE_LOSS_RISK@35 §#partial_body_only" in audit


def test_bench_curate_rejects_malformed_structured_source_pathology_rows(tmp_path):
    corpus = tmp_path / "bench_corpus.csv"
    corpus.write_text("43,1994/1472\n", encoding="utf-8")

    strict_run = tmp_path / "strict.csv"
    strict_run.write_text(
        "\n".join(
            [
                "statute_id,n_canonical,n_failed,n_projection_rows,n_source_pathologies,projection_kinds,source_pathology_codes,source_pathology_rows_json,fail_reasons,source_incomplete,chain_length,source_available,elapsed_s,error",
                '1994/1472,0,0,2,1,ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY,,\"[{""code"":""DESTRUCTIVE_SHAPE_LOSS_RISK""},""silently-dropped-before"",42]\",APPLY.SOURCE_PATHOLOGY_DETECTED,0,43,1,0.50,',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    args = SimpleNamespace(
        corpus=str(corpus),
        output_dir=str(tmp_path),
        run=None,
        strict_run=[str(strict_run)],
        oracle_suspect_check="off",
    )

    with pytest.raises(ValueError, match="non-object entries at indexes: 1, 2"):
        bench_curate.main(args)


def test_bench_curate_ignores_projection_kind_only_source_pathology_from_strict_run(tmp_path):
    corpus = tmp_path / "bench_corpus.csv"
    corpus.write_text("43,1994/1472\n", encoding="utf-8")

    strict_run = tmp_path / "strict.csv"
    strict_run.write_text(
        "\n".join(
            [
                "statute_id,n_canonical,n_failed,n_projection_rows,n_source_pathologies,projection_kinds,source_pathology_codes,fail_reasons,source_incomplete,chain_length,source_available,elapsed_s,error",
                "1994/1472,0,0,2,1,ELAB.SOURCE_PATHOLOGY,,,0,43,1,0.50,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    args = SimpleNamespace(
        corpus=str(corpus),
        output_dir=str(tmp_path),
        run=None,
        strict_run=[str(strict_run)],
        oracle_suspect_check="off",
    )
    bench_curate.main(args)

    core = (tmp_path / "bench_core.csv").read_text(encoding="utf-8").strip()
    suspect = (tmp_path / "bench_suspect.csv").read_text(encoding="utf-8").strip()

    assert core == "43,1994/1472"
    assert suspect == ""


def test_bench_curate_ignores_legacy_adjudication_kinds_column(tmp_path):
    corpus = tmp_path / "bench_corpus.csv"
    corpus.write_text("43,1994/1472\n", encoding="utf-8")

    strict_run = tmp_path / "strict.csv"
    strict_run.write_text(
        "\n".join(
            [
                (
                    "statute_id,n_canonical,n_failed,"
                    "n_projection_rows,adjudication_kinds,fail_reasons,source_incomplete,"
                    "chain_length,source_available,elapsed_s,error"
                ),
                (
                    "1994/1472,0,0,2,"
                    "source_pathology|ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY,"
                    ",0,43,1,0.50,"
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    args = SimpleNamespace(
        corpus=str(corpus),
        output_dir=str(tmp_path),
        run=None,
        strict_run=[str(strict_run)],
        oracle_suspect_check="off",
    )
    bench_curate.main(args)

    core = (tmp_path / "bench_core.csv").read_text(encoding="utf-8").strip()
    suspect = (tmp_path / "bench_suspect.csv").read_text(encoding="utf-8").strip()

    assert core == "43,1994/1472"
    assert suspect == ""


def test_bench_curate_partitions_contingent_effective_date_from_strict_run(tmp_path):
    corpus = tmp_path / "bench_corpus.csv"
    corpus.write_text("9,1991/1707\n", encoding="utf-8")

    strict_run = tmp_path / "strict.csv"
    strict_run.write_text(
        "\n".join(
            [
                "statute_id,n_canonical,n_failed,n_projection_rows,n_source_pathologies,n_contingent_effective_dates,projection_kinds,source_pathology_codes,contingent_effective_sources,fail_reasons,source_incomplete,chain_length,source_available,elapsed_s,error",
                "1991/1707,0,0,6,0,3,TIME.CONTINGENT_EFFECTIVE_DATE,,2004/542|2005/544|2006/1322,TIME.CONTINGENT_EFFECTIVE_DATE,0,9,9,0.50,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    args = SimpleNamespace(
        corpus=str(corpus),
        output_dir=str(tmp_path),
        run=None,
        strict_run=[str(strict_run)],
        oracle_suspect_check="off",
    )
    bench_curate.main(args)

    core = (tmp_path / "bench_core.csv").read_text(encoding="utf-8").strip()
    suspect = (tmp_path / "bench_suspect.csv").read_text(encoding="utf-8").strip()
    audit = (tmp_path / "bench_partition_audit.csv").read_text(encoding="utf-8")

    assert core == ""
    assert suspect == "9,1991/1707"
    assert "contingent_effective_date" in audit
    assert "2004/542|2005/544|2006/1322" in audit


def test_bench_curate_ignores_projection_kind_only_contingent_effective_date_from_strict_run(tmp_path):
    corpus = tmp_path / "bench_corpus.csv"
    corpus.write_text("9,1991/1707\n", encoding="utf-8")

    strict_run = tmp_path / "strict.csv"
    strict_run.write_text(
        "\n".join(
            [
                "statute_id,n_canonical,n_failed,n_projection_rows,n_source_pathologies,n_contingent_effective_dates,projection_kinds,source_pathology_codes,contingent_effective_sources,fail_reasons,source_incomplete,chain_length,source_available,elapsed_s,error",
                "1991/1707,0,0,6,0,3,TIME.CONTINGENT_EFFECTIVE_DATE,,,0,9,9,0.50,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    args = SimpleNamespace(
        corpus=str(corpus),
        output_dir=str(tmp_path),
        run=None,
        strict_run=[str(strict_run)],
        oracle_suspect_check="off",
    )
    bench_curate.main(args)

    core = (tmp_path / "bench_core.csv").read_text(encoding="utf-8").strip()
    suspect = (tmp_path / "bench_suspect.csv").read_text(encoding="utf-8").strip()

    assert core == "9,1991/1707"
    assert suspect == ""


def test_bench_curate_ignores_legacy_adjudication_kinds_from_strict_run(tmp_path):
    corpus = tmp_path / "bench_corpus.csv"
    corpus.write_text("43,1994/1472\n", encoding="utf-8")

    strict_run = tmp_path / "strict.csv"
    strict_run.write_text(
        "\n".join(
            [
                "statute_id,n_canonical,n_failed,n_projection_rows,n_source_pathologies,adjudication_kinds,source_pathology_codes,fail_reasons,source_incomplete,chain_length,source_available,elapsed_s,error",
                "1994/1472,0,0,2,1,source_pathology|ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY,DESTRUCTIVE_SHAPE_LOSS_RISK,,0,43,1,0.50,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    args = SimpleNamespace(
        corpus=str(corpus),
        output_dir=str(tmp_path),
        run=None,
        strict_run=[str(strict_run)],
        oracle_suspect_check="off",
    )
    bench_curate.main(args)

    core = (tmp_path / "bench_core.csv").read_text(encoding="utf-8").strip()
    suspect = (tmp_path / "bench_suspect.csv").read_text(encoding="utf-8").strip()
    audit = (tmp_path / "bench_partition_audit.csv").read_text(encoding="utf-8")

    assert core == ""
    assert suspect == "43,1994/1472"
    assert "source_pathology" in audit
