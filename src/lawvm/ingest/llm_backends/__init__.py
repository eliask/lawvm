"""Neutral local-LLM ingest backends (llama.cpp / OpenAI-compat, nemotron, docling).

- ``VisionPageProducer`` — vision transcription of a page image into anchored
  candidate blocks that feed the adjudicator.
- ``LlmWorkflowAdjudicator`` — producer-neutral extraction adjudicator (reconciles
  several candidate reads of a region into one composed node + assurance tier).
- ``nemotron_client`` — process-isolated Nemotron-Parse thin client.
- ``docling_producer`` — learned-layout + TableFormer structural producer.

Jurisdiction-neutral vision-ingest infra (moved out of ``finland.llm_backends``
in Track A). The FI manual-CLAIMS backend ``qwen_local`` STAYS in
``lawvm.finland.llm_backends``.
"""
