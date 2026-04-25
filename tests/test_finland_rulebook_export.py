from pathlib import Path

from lawvm.finland.rulebook import FINLAND_RULEBOOK
from lawvm.finland.rulebook.export import (
    render_rulebook_index_json,
    render_rulebook_markdown,
    write_generated_rulebook_assets,
)


def test_rulebook_index_json_mentions_rule_ids() -> None:
    data = render_rulebook_index_json(FINLAND_RULEBOOK)

    assert '"fi.clause.jolloin_renumber_pair"' in data
    assert '"fi.payload.table_with_named_rows"' in data
    assert '"fi.source.editorial_heading_noise"' in data
    assert '"fi.compare.oracle_html_xml_topology_drift"' in data
    assert '"fi.compare.oracle_stale_source"' in data
    assert '"fi.source.editorial_source_tag_reclassification"' in data
    assert '"fi.source.reclassify_subsection_with_item_numbering"' in data


def test_rulebook_generated_assets_write(tmp_path: Path) -> None:
    markdown_path, index_path = write_generated_rulebook_assets(
        FINLAND_RULEBOOK, tmp_path
    )

    assert markdown_path.name == "RULEBOOK.md"
    assert index_path.name == "RULE_INDEX.json"
    assert markdown_path.read_text(encoding="utf-8").startswith("# Finland Rulebook\n")
    assert '"fi.payload.lettered_subitems_attach_previous_if_explicit"' in index_path.read_text(
        encoding="utf-8"
    )
    assert '"fi.payload.intro_list_continuation"' in index_path.read_text(
        encoding="utf-8"
    )
    assert '"fi.payload.lettered_subitems_ambiguous_default"' in index_path.read_text(
        encoding="utf-8"
    )
    assert '"fi.temporal.valiaikaisesti_immediate_target_cluster"' in index_path.read_text(
        encoding="utf-8"
    )
    assert '"fi.compare.oracle_stale_source"' in index_path.read_text(
        encoding="utf-8"
    )
    assert '"fi.source.reclassify_subsection_with_item_numbering"' in index_path.read_text(
        encoding="utf-8"
    )


def test_checked_in_generated_assets_match_rulebook() -> None:
    generated_dir = Path("src/lawvm/finland/rulebook/generated")

    assert (generated_dir / "RULEBOOK.md").read_text(encoding="utf-8") == (
        render_rulebook_markdown(FINLAND_RULEBOOK)
    )
    assert (generated_dir / "RULE_INDEX.json").read_text(encoding="utf-8") == (
        render_rulebook_index_json(FINLAND_RULEBOOK)
    )
