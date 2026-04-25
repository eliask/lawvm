"""lawvm ops — list compiled operations with provenance.

Shows all operations compiled during replay, with their source amendment
and target address. Useful for understanding what the pipeline did and
for correlating score changes with specific operations.

Usage:
    lawvm ops <statute_id>                         # all compiled ops
    lawvm ops <statute_id> --source 2017/794       # ops from one amendment
    lawvm ops <statute_id> --target "section:9a"   # ops targeting one provision
    lawvm ops <statute_id> --source 2017/794 --target "section:9"
"""
from __future__ import annotations

from typing import Literal, Optional

from lawvm.finland.grafter import replay_xml


# ---------------------------------------------------------------------------
# Address formatting
# ---------------------------------------------------------------------------

def _fmt_target(target: dict) -> str:
    """Format an IRTargetRef dict as a human-readable address."""
    container = target.get("container", "?")
    section = target.get("section") or ""
    subsection = target.get("subsection")
    item = target.get("item")
    special = target.get("special")

    if container == "section":
        addr = f"§ {section}"
        if subsection is not None:
            addr += f" mom {subsection}"
        if item:
            addr += f" kohta {item}"
        if special:
            addr += f" ({special})"
    elif container == "chapter":
        addr = f"luku {section}"
        # chapter-level ops may carry subsection = target section number
        inner_sec = target.get("subsection")
        if inner_sec:
            addr += f" / § {inner_sec}"
    elif container == "part":
        addr = f"osa {section}"
    else:
        addr = f"{container}:{section}"
        if subsection:
            addr += f"/{subsection}"

    return addr


def _matches_source(op: dict, source_filter: str) -> bool:
    return op.get("source_statute", "").strip() == source_filter.strip()


def _matches_target(op: dict, target_filter: str) -> bool:
    """Check if op matches a 'kind:label' address filter."""
    if ":" not in target_filter:
        return False
    kind, label = target_filter.split(":", 1)
    kind = kind.strip().lower()
    label = label.strip().lower()

    target = op.get("target", {})
    container = target.get("container", "").lower()
    section = (target.get("section") or "").lower().replace(" ", "").replace("§", "")
    label_norm = label.replace(" ", "").replace("§", "")

    return container.startswith(kind) and section == label_norm


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def _ops_sync(
    sid: str,
    source_filter: Optional[str],
    target_filter: Optional[str],
    mode: Literal["finlex_oracle", "legal_pit"],
) -> None:
    compiled_ops: list = []
    replay_xml(sid, mode=mode, compiled_ops_out=compiled_ops, quiet=True, build_full_products=False)

    # Apply filters
    ops = compiled_ops
    if source_filter:
        ops = [op for op in ops if _matches_source(op, source_filter)]
    if target_filter:
        ops = [op for op in ops if _matches_target(op, target_filter)]

    print(f"Statute  : {sid}")
    print(f"Ops total: {len(compiled_ops)}  shown: {len(ops)}")
    if source_filter:
        print(f"Filter   : source={source_filter}")
    if target_filter:
        print(f"Filter   : target={target_filter}")
    print()

    if not ops:
        print("(no operations match filters)")
        return

    # Group by source amendment for readability
    current_source = None
    for op in ops:
        src = op.get("source_statute", "?")
        action = op.get("action", "?").upper()
        target = op.get("target", {})
        addr = _fmt_target(target)
        title = op.get("source_title", "")[:50]
        seq = op.get("sequence", "?")

        if src != current_source:
            print(f"--- {src}  {title}")
            current_source = src

        print(f"  [{seq:3}] {action:<8}  {addr}")

    print()


def main(args) -> None:
    _ops_sync(
        sid=args.statute_id,
        source_filter=getattr(args, "source", None),
        target_filter=getattr(args, "target", None),
        mode=getattr(args, "mode", "finlex_oracle"),
    )
