from __future__ import annotations

from lxml import etree as ET

from lawvm.uk_legislation.source_context import evict_source_root_caches
from lawvm.uk_legislation.source_fragment_context import (
    _SOURCE_LEAD_TEXT_CACHE,
    _SOURCE_TAIL_TEXT_CACHE,
    _source_lead_text_before_subordinate_rows,
    _source_tail_text_after_subordinate_rows,
)


_LEG_NS = "http://www.legislation.gov.uk/namespaces/legislation"


def _root_with_subordinate_tail(root_id: str) -> ET._Element:
    return ET.fromstring(
        f"""
        <P1 xmlns="{_LEG_NS}" id="{root_id}">
          <Text>Lead instruction</Text>
          <P2 id="{root_id}-prelude">
            <Text>Nested lead</Text>
            <P3 id="{root_id}-prelude-child">
              <Text>Nested child row</Text>
            </P3>
            Nested tail
          </P2>
          <P2 id="{root_id}-child">
            <Text>Child row</Text>
          </P2>
          Tail instruction after child row
        </P1>
        """
    )


def test_source_fragment_context_caches_evict_by_source_root() -> None:
    root_a = _root_with_subordinate_tail("a")
    root_b = _root_with_subordinate_tail("b")
    root_a_descendant = next(el for el in root_a.iter() if el.get("id") == "a-prelude")
    root_b_descendant = next(el for el in root_b.iter() if el.get("id") == "b-prelude")

    assert _source_lead_text_before_subordinate_rows(root_a)
    assert _source_tail_text_after_subordinate_rows(root_a)
    assert _source_lead_text_before_subordinate_rows(root_a_descendant)
    assert _source_tail_text_after_subordinate_rows(root_a_descendant)
    assert _source_lead_text_before_subordinate_rows(root_b)
    assert _source_tail_text_after_subordinate_rows(root_b)
    assert _source_lead_text_before_subordinate_rows(root_b_descendant)
    assert _source_tail_text_after_subordinate_rows(root_b_descendant)
    assert root_a in _SOURCE_LEAD_TEXT_CACHE
    assert root_a in _SOURCE_TAIL_TEXT_CACHE
    assert root_a_descendant in _SOURCE_LEAD_TEXT_CACHE
    assert root_a_descendant in _SOURCE_TAIL_TEXT_CACHE
    assert root_b in _SOURCE_LEAD_TEXT_CACHE
    assert root_b in _SOURCE_TAIL_TEXT_CACHE
    assert root_b_descendant in _SOURCE_LEAD_TEXT_CACHE
    assert root_b_descendant in _SOURCE_TAIL_TEXT_CACHE

    evict_source_root_caches(root_a)

    assert root_a not in _SOURCE_LEAD_TEXT_CACHE
    assert root_a not in _SOURCE_TAIL_TEXT_CACHE
    assert root_a_descendant not in _SOURCE_LEAD_TEXT_CACHE
    assert root_a_descendant not in _SOURCE_TAIL_TEXT_CACHE
    assert root_b in _SOURCE_LEAD_TEXT_CACHE
    assert root_b in _SOURCE_TAIL_TEXT_CACHE
    assert root_b_descendant in _SOURCE_LEAD_TEXT_CACHE
    assert root_b_descendant in _SOURCE_TAIL_TEXT_CACHE
