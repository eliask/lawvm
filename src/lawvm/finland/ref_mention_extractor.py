# Back-compat shim: relocated to lawvm.finland.references.ref_mention_extractor
from lawvm.finland.references.ref_mention_extractor import *  # noqa: F401,F403
from lawvm.finland.references.ref_mention_extractor import (  # noqa: F401
    ExtractionResult,
    PlainTextStatuteCitationRecognizer,
    PlainTextStatuteHit,
    extract_affected_document_mentions,
    extract_all_reference_mentions,
    extract_eu_reference_mentions,
    extract_plain_text_statute_mentions,
    extract_preparatory_reference_mentions,
    extract_reference_mentions,
)
