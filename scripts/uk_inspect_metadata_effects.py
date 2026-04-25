from __future__ import annotations

from lxml import etree

def parse_metadata_effects(xml_path: str) -> list[dict[str, object]]:
    tree = etree.parse(xml_path)
    root = tree.getroot()
    ns = {
        'ukm': 'http://www.legislation.gov.uk/namespaces/metadata',
        'atom': 'http://www.w3.org/2005/Atom'
    }

    effects: list[dict[str, object]] = []
    unapplied_raw = root.xpath(".//ukm:UnappliedEffect", namespaces=ns)
    unapplied: list[etree._Element] = [e for e in unapplied_raw if isinstance(e, etree._Element)] if isinstance(unapplied_raw, list) else []
    print(f"Found {len(unapplied)} UnappliedEffects in metadata.")

    for effect in unapplied:
        eid = effect.get("EffectId")
        etype = effect.get("Type")
        affecting_uri = effect.get("AffectingURI")

        # Get affected provisions
        provisions = []
        prov_raw = effect.xpath(".//ukm:AffectedProvisions/ukm:Section", namespaces=ns)
        for prov in prov_raw if isinstance(prov_raw, list) else []:
            if isinstance(prov, etree._Element):
                provisions.append(prov.get("Ref"))

        effects.append({
            "id": eid,
            "type": etype,
            "affecting": affecting_uri,
            "provisions": provisions,
            "notes": effect.get("Notes", "")
        })

    return effects

if __name__ == "__main__":
    path = "/home/elias/c/civos/book/LawVM/uk/data/raw/ukpga/1998/29/current/data.xml"
    effects = parse_metadata_effects(path)

    # Filter for 2018/12 (the candidate repeal)
    repeals_2018 = [e for e in effects if "2018/12" in str(e["affecting"])]
    print(f"\nEffects affecting from 2018/12: {len(repeals_2018)}")
    for e in repeals_2018[:5]:
        print(f"  {e['id']}: {e['type']} on {e['provisions']}")
