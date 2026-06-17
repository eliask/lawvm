# Back-compat shim: relocated to lawvm.finland.references.inline_citation_extractor
from lawvm.finland.references.inline_citation_extractor import *  # noqa: F401,F403
from lawvm.finland.references.inline_citation_extractor import (  # noqa: F401
    _RECOGNIZER,
    InlineCitationExtractionResult,
    InlineCitationRecognizer,
    extract_inline_citations,
)
