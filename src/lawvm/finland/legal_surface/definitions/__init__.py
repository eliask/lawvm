"""Shared definition-recognition core for the Finnish Legal Surface stack.

Houses the ONE canonical definiendum-entry recognition pipeline
(:mod:`shared_definition_parser`) that BOTH the production binder
(``references.defined_terms``) and the SourceSyntaxGraph forest
(``legal_surface.definition_parse``) call, so the two lanes cannot drift.
"""
