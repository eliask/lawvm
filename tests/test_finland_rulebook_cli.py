from argparse import Namespace

from lawvm.tools.finland_rulebook import main


def test_finland_rulebook_cli_renders_markdown(capsys) -> None:
    main(Namespace(validate=False))

    out = capsys.readouterr().out
    assert out.startswith("# Finland Rulebook\n")
    assert "### fi.temporal.valiaikaisesti_immediate_target_cluster" in out


def test_finland_rulebook_cli_validates(capsys) -> None:
    main(Namespace(validate=True))

    out = capsys.readouterr().out
    assert out.strip() == "OK: Finland rulebook vocabulary is valid"
