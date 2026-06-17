# Back-compat shim: relocated to lawvm.finland.references.cross_refs
from lawvm.finland.references.cross_refs import *  # noqa: F401,F403
from lawvm.finland.references.cross_refs import (  # noqa: F401
    CrossRefDiagnostic,
    CrossRefEdge,
    extract_cross_refs,
    extract_eu_refs,
)
