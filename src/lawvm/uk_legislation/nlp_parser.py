import re
from dataclasses import dataclass
from typing import List, Dict

# ASCII Unit Separator
US = "\x1f"


def _normalize_quotes(text: str) -> str:
    return (text.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")).strip()


@dataclass
class UKLegalRef:
    kind: str # 'section', 'subsection', 'paragraph', 'item'
    label: str # '1', '(a)', '(i)'

@dataclass
class UKAmendmentIntent:
    operation: str # 'substitution', 'omission', 'insertion'
    scope: List[UKLegalRef]
    targets: List[str] # literal strings or ranges like FROM_X_TO_Y

def parse_fragment_substitution(text: str) -> List[Dict[str, str]]:
    """
    NLP-enhanced fragment extraction. Returns a list of substitution dicts.
    'for "the Lord Chancellor" substitute "the Secretary of State"'
    'from "(a)" to "(b)" are omitted'
    """
    subs = []

    # Clean up newlines/extra spaces
    text = " ".join(text.split())

    # Pattern 1: Substitution (Multiple possible)
    # Use non-greedy match for the fragments.
    # Allow an optional comma (and whitespace) between the quoted original and “substitute”,
    # which is the standard Scottish/UK drafting style: for “X”, substitute “Y”
    matches = re.finditer(r"for [“\"'‘](.*?)[”\"'’],?\s*substitute [“\"'‘](.*?)[”\"'’]", text, re.I)
    for m in matches:
        subs.append({"original": m.group(1), "replacement": m.group(2)})

    # Pattern 1aa: "for the words from 'X' to 'Y' substitute 'Z'"
    # This is a text-span replacement across the target subtree, not a
    # structural child-label range like FROM_(a)_TO_(b).
    matches_range_substituted = re.finditer(
        r"for (?:the )?words? from [“\"'‘](.*?)[”\"'’] to [“\"'‘](.*?)[”\"'’]\s+substitute\s+[“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_range_substituted:
        subs.append(
            {
                "original": f"TEXT_FROM_{m.group(1).strip()}_TO_{m.group(2).strip()}",
                "replacement": m.group(3).strip(),
            }
        )

    matches_range_to_end_substituted = re.finditer(
        r"for (?:the )?words? from [“\"'‘](.*?)[”\"'’] to the end\s+substitute\s+[“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_range_to_end_substituted:
        subs.append(
            {
                "original": f"TEXT_FROM_{m.group(1).strip()}_TO_END",
                "replacement": m.group(2).strip(),
            }
        )

    matches_from_beginning_substituted = re.finditer(
        r"for (?:the )?words? from the beginning to [“\"'‘](.*?)[”\"'’]\s+substitute\s+[“\"'‘](.*)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_from_beginning_substituted:
        subs.append(
            {
                "original": f"TEXT_FROM__TO_{m.group(1).strip()}",
                "replacement": m.group(2).strip(),
            }
        )

    # Pattern 1a: "for the words 'X' are substituted the words 'Y'"
    matches_are_substituted = re.finditer(
        r"for (?:(?:the )?words? )?[“\"'‘](.*?)[”\"'’](?:\s*\([^)]*\))?\s+(?:is|are|shall\s+be)\s+substituted\s+(?:(?:the )?words? )?[“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_are_substituted:
        subs.append({"original": m.group(1), "replacement": m.group(2)})

    matches_there_is_substituted = re.finditer(
        r"for (?:(?:the )?words? )?[“\"'‘](.*?)[”\"'’](?:\s*\([^)]*\))?\s+there\s+(?:is|are|shall\s+be)\s+substituted\s+(?:(?:the )?words? )?[“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_there_is_substituted:
        subs.append({"original": m.group(1), "replacement": m.group(2)})

    matches_is_replaced_with = re.finditer(
        r"(?:(?:the )?words? )?[“\"'‘](.*?)[”\"'’]\s+(?:is|are)\s+replaced\s+with\s+[“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_is_replaced_with:
        subs.append({"original": m.group(1), "replacement": m.group(2)})

    # Pattern 1b: Insertion after a quoted fragment.
    # Treat this as a text replacement on the matched fragment so replay can
    # materialize the inserted words without inventing structural descendants.
    matches_after_insert = re.finditer(
        r"after [“\"'‘](.*?)[”\"'’]\s+(?:there is inserted|there are inserted|insert)\s+[“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_after_insert:
        original = m.group(1)
        inserted = m.group(2)
        joiner = "" if inserted.startswith((" ", ",", ".", ";", ":", ")")) else " "
        subs.append(
            {
                "original": original,
                "replacement": f"{original}{joiner}{inserted}",
            }
        )

    matches_before_insert = re.finditer(
        r"before [“\"'‘](.*?)[”\"'’] insert [“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_before_insert:
        original = m.group(1)
        inserted = m.group(2)
        joiner = "" if inserted.endswith((" ", "(", "/", "-")) else " "
        subs.append(
            {
                "original": original,
                "replacement": f"{inserted}{joiner}{original}",
            }
        )

    matches_at_end_insert = re.finditer(
        r"at the end insert [“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_at_end_insert:
        inserted = m.group(1).strip()
        subs.append(
            {
                "original": "TEXT_FROM__TO_END",
                "replacement": inserted,
            }
        )

    # Pattern 2: Omission from A to B
    matches_omit = re.finditer(r"from [“\"'‘](.*?)[”\"'’] to [“\"'‘](.*?)[”\"'’] (?:are omitted|is omitted|omit)", text, re.I)
    for m in matches_omit:
        subs.append({"original": f"FROM_{m.group(1)}_TO_{m.group(2)}", "replacement": ""})

    matches_omit_to_end = re.finditer(
        r"omit (?:the )?words? from [“\"'‘](.*?)[”\"'’] to the end",
        text,
        re.I,
    )
    for m in matches_omit_to_end:
        subs.append({"original": f"TEXT_FROM_{m.group(1).strip()}_TO_END", "replacement": ""})

    # Pattern 3: Reversed-order substitution: substitute "X" for "Y"
    # Requires that the original (after "for") starts with a quote character —
    # this prevents false positives when "for" appears inside the replacement text,
    # e.g. 'substitute "the Commissioner for Public Appointments" ...' would
    # otherwise split on the "for" inside the quoted string.
    if not subs:
        m = re.search(r"substitute (.*?) for ([\"'\u201c\u201d\u2018\u2019].*)", text, re.I)
        if m:
            subs.append({"original": m.group(2).strip(), "replacement": m.group(1).strip()})

    return subs

def is_whole_node_replacement(text: str, effect_type: str) -> bool:
    """
    Decide if the text implies a whole node replacement or a word-level change.
    """
    if "word" in effect_type.lower():
        return False

    # If text contains "for ... substitute ...", it's likely a fragment
    if parse_fragment_substitution(text):
        return False

    return True
