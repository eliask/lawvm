"""Heavy half: Nemotron-Parse inference. ONLY this module touches torch.

SCAFFOLD STATUS: the load/infer path below is a faithful sketch of the
transformers flow for `nvidia/NVIDIA-Nemotron-Parse-v1.2` but has NOT been
exercised against a real GPU deployment — validate the exact processor call,
generation kwargs, and output-decoding against the model card before first
real use. Everything importing this module is behind the ``parse``/``probe``
CLI process boundary, so getting this wrong can never break the main package.

The model emits structured output (regions with semantic class + bbox +
reading order). ``parse_page_png`` reduces that to ``(semantic_class, text)``
tuples in reading order; ``wire.emit_kind_blocks`` owns the mapping to the
frozen wire vocabulary.
"""
from __future__ import annotations

import io
import os
from typing import Any, Sequence, Tuple

DEFAULT_MODEL_ID = "nvidia/NVIDIA-Nemotron-Parse-v1.2"


class ModelUnavailable(Exception):
    """Heavy deps missing / weights not loadable — probe reports NOT ready."""


class InferenceError(Exception):
    """The loaded model failed on a page (typed; the CLI exits 5, never 0)."""


def resolve_model_id() -> str:
    return os.environ.get("NEMOTRON_PARSE_MODEL_ID") or DEFAULT_MODEL_ID


def probe_ready() -> str:
    """Return the model id if the heavy stack imports; raise ModelUnavailable.

    Deliberately does NOT download/load weights: probe must be cheap enough
    for the client's ``is_available()`` to call it with a short timeout.
    """
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError as exc:
        raise ModelUnavailable(f"heavy deps not importable: {exc}") from exc
    return resolve_model_id()


_LOADED: Any = None


def _load() -> Any:
    """Lazy-load (and cache) the processor+model pair."""
    global _LOADED
    if _LOADED is not None:
        return _LOADED
    model_id = probe_ready()
    import torch
    from transformers import AutoModel, AutoProcessor

    processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
    model = AutoModel.from_pretrained(
        model_id,
        trust_remote_code=True,
        torch_dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
    )
    if torch.cuda.is_available():
        model = model.to("cuda")
    model.eval()
    _LOADED = (processor, model)
    return _LOADED


def parse_page_png(png_bytes: bytes) -> Tuple[Tuple[str, str], ...]:
    """One page image -> ``(semantic_class, text)`` regions in reading order."""
    from PIL import Image

    try:
        image = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    except Exception as exc:  # PIL raises a zoo of types; re-typed, never silent
        raise InferenceError(f"undecodable page image: {type(exc).__name__}: {exc}") from exc

    processor, model = _load()
    try:
        import torch

        inputs = processor(images=image, return_tensors="pt").to(model.device)
        with torch.inference_mode():
            generated = model.generate(**inputs, max_new_tokens=4096)
        decoded = processor.batch_decode(generated, skip_special_tokens=False)[0]
        regions = _regions_from_decoded(decoded, processor)
    except InferenceError:
        raise
    except Exception as exc:  # typed re-raise across the process boundary
        raise InferenceError(f"{type(exc).__name__}: {exc}") from exc
    return tuple(regions)


def _regions_from_decoded(decoded: str, processor: Any) -> Sequence[Tuple[str, str]]:
    """Decode the model's structured output into (class, text) regions.

    SCAFFOLD: Nemotron-Parse emits region markup carrying semantic classes,
    bboxes, and reading order; the exact tag grammar must be taken from the
    model card / processor helpers (some releases ship a ``postprocess``
    helper on the processor — prefer it when present).
    """
    post = getattr(processor, "post_process_parse", None) or getattr(
        processor, "postprocess", None
    )
    if post is None:
        raise InferenceError(
            "no postprocess helper on the processor; implement the tag-grammar "
            "decode against the model card before real deployment"
        )
    result = post(decoded)
    regions: list[Tuple[str, str]] = []
    for region in result:
        cls = str(region.get("class") or region.get("category") or "")
        text = str(region.get("text") or "")
        if cls:
            regions.append((cls, text))
    return regions
