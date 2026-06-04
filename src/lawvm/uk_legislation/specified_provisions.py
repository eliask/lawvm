"""Membership proof helpers for UK "specified provisions" source lists."""

from __future__ import annotations


def target_appears_in_specified_provisions_preview(
    *,
    target: str,
    source_preview: str,
) -> bool:
    """Return True when a target is explicitly listed by a UK source list.

    This proves only source-list membership for a deterministic compiler
    frontier. It does not select a text preimage, emit an operation, or authorize
    replay.
    """
    haystack = normalize_citation_text(source_preview)
    if not haystack:
        return False
    if any(
        needle in haystack
        for needle in specified_provision_membership_needles(target)
        if needle
    ):
        return True
    normalized_target = normalize_citation_text(target)
    return _grouped_section_target_appears(
        target=normalized_target,
        haystack=haystack,
    ) or _grouped_schedule_paragraph_target_appears(
        target=normalized_target,
        haystack=haystack,
    )


def specified_provision_membership_needles(target: str) -> tuple[str, ...]:
    normalized = normalize_citation_text(target)
    if not normalized:
        return ()
    needles = [normalized]
    if normalized.startswith("s. "):
        needles.append(f"section {normalized[3:].strip()}")
    elif normalized.startswith("s "):
        needles.append(f"section {normalized[2:].strip()}")
    if normalized.startswith("sch. "):
        needles.append(f"schedule {normalized[5:].strip()}")
    elif normalized.startswith("sch "):
        needles.append(f"schedule {normalized[4:].strip()}")
    return tuple(dict.fromkeys(needles))


def normalize_citation_text(text: str) -> str:
    cleaned = (
        str(text or "")
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u00a0", " ")
        .lower()
    )
    return " ".join(cleaned.split())


def _grouped_section_target_appears(*, target: str, haystack: str) -> bool:
    parsed = _parse_section_target(target)
    if parsed is None:
        return False
    section, suffix = parsed
    return _suffix_appears_after_anchor(
        haystack=haystack,
        anchor=f"section {section}",
        suffix=suffix,
        window=260,
    )


def _grouped_schedule_paragraph_target_appears(*, target: str, haystack: str) -> bool:
    parsed = _parse_schedule_paragraph_target(target)
    if parsed is None:
        return False
    schedule, paragraph, suffix = parsed
    anchor = f"paragraph {paragraph}"
    for start in _anchor_positions(haystack, anchor):
        segment = haystack[start : start + 320]
        if suffix in segment and f"of schedule {schedule}" in segment:
            return True
    schedule_anchor = f"of schedule {schedule}"
    listed_token = f"{paragraph}{suffix}"
    for end in _anchor_positions(haystack, schedule_anchor):
        segment = haystack[max(0, end - 320) : end]
        if (
            ("paragraph " in segment or "paragraphs " in segment)
            and _listed_suffix_appears(segment=segment, token=listed_token)
        ):
            return True
    return False


def _parse_section_target(target: str) -> tuple[str, str] | None:
    tail = _remove_prefix(target, ("section ", "s. ", "s "))
    if tail is None:
        return None
    return _split_number_suffix(tail)


def _parse_schedule_paragraph_target(target: str) -> tuple[str, str, str] | None:
    schedule: str | None = None
    tail = target
    schedule_tail = _remove_prefix(tail, ("schedule ", "sch. ", "sch "))
    if schedule_tail is not None:
        schedule, tail = _split_first_token(schedule_tail)
    paragraph_tail = _remove_prefix(tail, ("paragraph ", "para. ", "para "))
    if paragraph_tail is None:
        return None
    paragraph_part, tail_schedule = _split_schedule_tail(paragraph_tail)
    parsed = _split_number_suffix(paragraph_part)
    if parsed is None:
        return None
    paragraph, suffix = parsed
    schedule = schedule or tail_schedule
    if not schedule:
        return None
    return schedule, paragraph, suffix


def _remove_prefix(text: str, prefixes: tuple[str, ...]) -> str | None:
    for prefix in prefixes:
        if text.startswith(prefix):
            return text[len(prefix) :].strip()
    return None


def _split_first_token(text: str) -> tuple[str, str]:
    token, _, rest = text.strip().partition(" ")
    return token.strip(), rest.strip()


def _split_schedule_tail(text: str) -> tuple[str, str | None]:
    marker = " of schedule "
    before, found, after = text.partition(marker)
    if not found:
        return text.strip(), None
    schedule, _rest = _split_first_token(after)
    return before.strip(), schedule


def _split_number_suffix(text: str) -> tuple[str, str] | None:
    value = text.strip()
    index = 0
    while index < len(value) and value[index].isalnum():
        index += 1
    number = value[:index]
    suffix = value[index:].strip()
    if not number or not suffix.startswith("(") or ")" not in suffix:
        return None
    return number, suffix


def _suffix_appears_after_anchor(
    *,
    haystack: str,
    anchor: str,
    suffix: str,
    window: int,
) -> bool:
    for start in _anchor_positions(haystack, anchor):
        if suffix in haystack[start : start + window]:
            return True
    return False


def _anchor_positions(haystack: str, anchor: str) -> tuple[int, ...]:
    positions: list[int] = []
    start = 0
    while True:
        index = haystack.find(anchor, start)
        if index < 0:
            return tuple(positions)
        positions.append(index)
        start = index + len(anchor)


def _listed_suffix_appears(*, segment: str, token: str) -> bool:
    start = 0
    while True:
        index = segment.find(token, start)
        if index < 0:
            return False
        before = segment[index - 1] if index else ""
        after_index = index + len(token)
        after = segment[after_index] if after_index < len(segment) else ""
        if not before.isalnum() and not after.isalnum() and after != "(":
            return True
        start = index + len(token)
