"""Finland-adjacent local LLM backends (llama.cpp OpenAI-compat).

- ``QwenLocalBackend`` — claim-proposal text backend (manual-claims pipeline).
- ``LlmWorkflowAdjudicator`` — producer-neutral extraction adjudicator (reconciles
  several candidate reads of a region into one composed node + assurance tier).
- ``VisionPageProducer`` — vision transcription of a page image into anchored
  candidate blocks that feed the adjudicator.

The adjudicator + vision producer are jurisdiction-neutral infra placed here beside
the existing backend; they may move to a neutral package later.
"""
