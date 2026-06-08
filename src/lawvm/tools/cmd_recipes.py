"""lawvm recipes — task-shaped workflow recipes with real command examples.

Each recipe maps a common research task to one or more lawvm commands with
runnable examples.  The recipe table is CI-tested against the live argparse
surface: if a command named here is renamed or removed, the build fails.

Usage:
    lawvm recipes
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Recipe table — keep in sync with the CI guard in tests/test_recipes_ci.py.
# Each entry: (task_description, [command_names], example_lines)
#
# IMPORTANT: command names here are verified by CI against the live parser.
# Do NOT add a command name unless it is a registered lawvm subcommand.
# ---------------------------------------------------------------------------

RECIPES: list[dict[str, Any]] = [
    {
        "task": "Find all laws that cite a given statute (reverse citation graph)",
        "commands": ["refs"],
        "examples": [
            "lawvm refs --to 2007/571          # what provisions cite eläkkeensaajan asumistuki?",
            "lawvm refs --to 1992/1535         # who cites the social welfare act?",
        ],
    },
    {
        "task": "Find a statute's outgoing cross-references",
        "commands": ["cite"],
        "examples": [
            "lawvm cite 2009/738               # what does this statute point at?",
        ],
    },
    {
        "task": "Search statute source text for a keyword or phrase",
        "commands": ["topic", "sgrep"],
        "examples": [
            "lawvm topic --topic kadmium        # full-text search across in-force sections",
            "lawvm topic --topic 'eläke' --statute-filter '2009/*'",
            "lawvm sgrep --oracle-text-matches 'netter|yhteensovitus'  # structural regex search (heavy)",
        ],
    },
    {
        "task": "Read a section's consolidated text at a specific version",
        "commands": ["oracle-text", "provision-state"],
        "examples": [
            "lawvm oracle-text 1992/734                          # list all section labels",
            "lawvm oracle-text 1992/734 --section section:7     # read section 7",
            "lawvm oracle-text 2009/738 --section section:10 --at-amendment 2022/100",
            "lawvm provision-state 1992/734 --address section:7 --as-of 2020-01-01",
        ],
    },
    {
        "task": "Trace a provision's amendment history",
        "commands": ["pit-timeline", "pit-diff", "bisect"],
        "examples": [
            "lawvm pit-timeline 1992/734 --address section:7    # amendment history for one section",
            "lawvm pit-diff 1992/734 --address section:7 --from-amendment 2018/400 --to-amendment 2022/100",
            "lawvm bisect 1992/734                              # find the amendment that hurt replay score",
        ],
    },
    {
        "task": "Search Finnish government proposals (HE corpus)",
        "commands": ["fi-proposals"],
        "examples": [
            "lawvm fi-proposals --query eläke                   # search HE titles/body for keyword",
            "lawvm fi-proposals --statute 2009/738              # show all HEs that touch a statute",
        ],
    },
    {
        "task": "Investigate cross-instrument benefit interactions (composite owner)",
        "commands": ["refs", "cite", "topic", "oracle-text"],
        "examples": [
            "# Step 1: who cites the netting statute?",
            "lawvm refs --to 2007/571",
            "# Step 2: verify each candidate's outgoing refs",
            "lawvm cite 2009/738",
            "# Step 3: search for netting keywords across sections",
            "lawvm topic --topic 'yhteensovitus'",
            "# Step 4: read the candidate section directly",
            "lawvm oracle-text 2009/738 --section section:10",
        ],
    },
]


def _print_recipes() -> None:
    """Print the recipes table to stdout."""
    print("lawvm recipes — common research tasks and the commands that serve them")
    print()
    print("Use these before reading statute sections line-by-line.")
    print("For the full command list: lawvm --help")
    print()
    for i, recipe in enumerate(RECIPES, start=1):
        print(f"{'─' * 72}")
        print(f"{i}. {recipe['task']}")
        print(f"   Commands: {', '.join(recipe['commands'])}")
        print()
        for ex in recipe["examples"]:
            print(f"   {ex}")
        print()
    print(f"{'─' * 72}")


def main(args: Any) -> None:  # noqa: ARG001
    _print_recipes()
