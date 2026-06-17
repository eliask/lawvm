"""Finnish surface lenses: recognizers that emit Legal Surface Graph seeds.

Each lens reads a ``SourceSurfaceBundle`` and returns a ``SurfaceLensResult``
(node/edge/residual seeds) per the ``lawvm.core.legal_surface_lens.SurfaceLens``
protocol. The core algebra mints ids and assembles the graph; these lenses own
only Finnish recognition. See ``notes_internal/pro_on_fi_theory_grammar5.txt``.
"""
