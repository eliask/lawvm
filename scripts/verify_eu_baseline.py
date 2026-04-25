"""Verification script for EU Compiler Frontend."""
from pathlib import Path
from lawvm.eu.grafter import parse_eu_regulation_ir
from lawvm.eu.ops_parser import EUOpsParser

def verify():
    xml_path = Path(".tmp/eur_mock.xml")

    print("--- Phase 1: Structural Parsing ---")
    statute = parse_eu_regulation_ir(xml_path, celex="2026/123")
    print(f"Statute Title: {statute.title}")
    print(f"Body Nodes: {len(statute.body.children)}")
    for node in statute.body.children:
        print(f"  Kind: {node.kind}, Label: {node.label}")
        if node.children:
            for child in node.children:
                print(f"    Child Kind: {child.kind}, Label: {child.label}")

    print("\n--- Phase 2: Amendment Interpretation ---")
    parser = EUOpsParser()
    amendment_text = """
    Article 1 of Regulation (EU) 2026/123 is amended as follows:
    (1) Article 1 is replaced by the following:
    'Article 1: New subject matter';
    (2) in Article 2, paragraph 1 is deleted.
    """
    ops = parser.extract_ops(amendment_text)
    for op in ops:
        print(f"Action: {op.action}, Target: {op.target}")

if __name__ == "__main__":
    verify()
