"""LawVM Finland frontend package — acquisition through adjudiation for Finland.

Per AGENTS.md §2.3, the core must not import a frontend package; frontends
register themselves. This package's ``__init__`` registers Finland's
canonical ``StrictProfile`` factory with :mod:`lawvm.core.strict_profile_registry`
at import time so that ``compile_metadata_default._default_strict_profile``
can dispatch to it without a hard core→frontend import path.

Importing any ``lawvm.finland.<module>`` triggers this ``__init__``'s
evaluation and thus the registration side-effect; the registration is
idempotent (a dict-key assignment), so multiple imports do not duplicate.
"""

from __future__ import annotations


def _register_default_strict_profile() -> None:
    """Register Finland's canonical strict profile with the core registry.

    Inverts the prior hard ``core → lawvm.finland.strict_profile`` import in
    ``compile_metadata_default._default_strict_profile``. We import the factory
    lazily here (rather than at module top) so a faulty ``finland.strict_profile``
    import surfaces as a clear ``ImportError`` from this registration call
    rather than as a side-effect of importing any unrelated Finland submodule.
    """
    from lawvm.core.strict_profile_registry import register_default_strict_profile
    from lawvm.finland.strict_profile import default_finland_strict_profile

    register_default_strict_profile("fi", default_finland_strict_profile)


_register_default_strict_profile()
