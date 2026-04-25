"""lawvm ee-residual-inventory — print known EE residual adjudication inventory."""
from __future__ import annotations

import json
from collections import Counter
from typing import TYPE_CHECKING

from lawvm.estonia.residual_inventory import (
    get_ee_residual_inventory,
    list_known_ee_residual_inventories,
)

if TYPE_CHECKING:
    import argparse


def _inventory_payload(base_id: str | None = None, oracle_id: str | None = None) -> dict:
    if base_id is not None and oracle_id is not None:
        inventories = []
        inventory = get_ee_residual_inventory(base_id, oracle_id)
        if inventory is not None:
            inventories.append(inventory)
    else:
        inventories = list(list_known_ee_residual_inventories())

    payload = {
        "inventories": [
            {
                "base_id": inv.base_id,
                "oracle_id": inv.oracle_id,
                "statute_title": inv.statute_title,
                "comparison_class": inv.comparison_class,
                "residual_count": len(inv.residuals),
                "bucket_counts": dict(Counter(record.bucket for record in inv.residuals)),
                "residuals": [
                    {
                        "address": record.address,
                        "bucket": record.bucket,
                        "evidence": record.evidence,
                    }
                    for record in inv.residuals
                ],
            }
            for inv in inventories
        ]
    }
    return payload


def main(args: "argparse.Namespace") -> None:
    base_id = getattr(args, "base_id", None)
    oracle_id = getattr(args, "oracle_id", None)
    payload = _inventory_payload(base_id=base_id, oracle_id=oracle_id)

    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print()
    print("=== EE Residual Inventory ===")
    if not payload["inventories"]:
        print("  no known inventory for the requested pair")
        return

    for inv in payload["inventories"]:
        print(
            f"  {inv['base_id']} -> {inv['oracle_id']}  "
            f"{inv['statute_title']}  residuals={inv['residual_count']}"
        )
        if inv["bucket_counts"]:
            counts = ", ".join(
                f"{bucket}={count}" for bucket, count in sorted(inv["bucket_counts"].items())
            )
            print(f"    buckets: {counts}")
        for record in inv["residuals"]:
            print(f"    {record['address']}  [{record['bucket']}]")


__all__ = ["main"]
