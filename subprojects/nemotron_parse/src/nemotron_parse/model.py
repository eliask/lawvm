"""Heavy half: Nemotron-Parse inference. ONLY this module touches torch.

VALIDATED against the shipped model-card reference implementation
(``example_with_processor.py`` + ``postprocessing.py`` +
``golden_outputs.json`` for ``nvidia/NVIDIA-Nemotron-Parse-v1.2``): the load
uses ``trust_remote_code`` (the repo ships ``NemotronParseProcessor`` /
``NemotronParse`` modeling); the generate uses the shipped
``GenerationConfig`` (greedy, ``repetition_penalty=1.1``); and the decode is
the model's OWN region grammar
``<x_..><y_..>TEXT<x_..><y_..><class_NAME>`` — the processor has NO
``post_process_parse``/``postprocess`` helper, so the decode is implemented
here as an exact re-derivation of ``postprocessing.extract_classes_bboxes``
(pure ``re``; no ``latex2html`` dependency needed for text regions). Confirmed
on ``golden_outputs.json``'s documented ``generation.decoded_text`` (one
``Table`` region) and on a real CPU inference of a scanned Finnish gazette.

Everything importing this module is behind the ``parse``/``probe`` CLI process
boundary, so getting this wrong can never break the main package.

The model emits structured output (regions with semantic class + bbox +
reading order). ``parse_page_png`` reduces that to ``(semantic_class, text)``
tuples in reading order; ``wire.emit_kind_blocks`` owns the mapping to the
frozen wire vocabulary.
"""
from __future__ import annotations

import io
import os
import re
from typing import Any, List, Sequence, Tuple

DEFAULT_MODEL_ID = "nvidia/NVIDIA-Nemotron-Parse-v1.2"

#: The model-card task prompt (predict bbox + classes, markdown text, no text
#: inside pictures). Fed verbatim as ``text`` with ``add_special_tokens=False``
#: — the ``</s><s>`` framing is part of the prompt (see example_with_processor).
TASK_PROMPT = "</s><s><predict_bbox><predict_classes><output_markdown><predict_no_text_in_pic>"

#: The region grammar the model emits (postprocessing.extract_classes_bboxes):
#: ``<x_f><y_f> text <x_f><y_f><class_NAME>``. Reading order is emission order.
_RE_REGION = re.compile(
    r"<x_(\d+(?:\.\d+)?)><y_(\d+(?:\.\d+)?)>(.*?)"
    r"<x_(\d+(?:\.\d+)?)><y_(\d+(?:\.\d+)?)><class_([^>]+)>",
    re.DOTALL,
)

_SUP = str.maketrans("0123456789+-=()n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ")
_SUB = str.maketrans("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")


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


def _use_cuda() -> bool:
    """CPU-only unless BOTH cuda is present AND not explicitly forced off.

    The shared box time-shares one GPU with the :8080 vision server; a
    validation batch runs CPU-only. ``NEMOTRON_PARSE_FORCE_CPU=1`` pins CPU
    even when cuda is visible.
    """
    if os.environ.get("NEMOTRON_PARSE_FORCE_CPU") == "1":
        return False
    import torch

    return bool(torch.cuda.is_available())


def _load() -> Any:
    """Lazy-load (and cache) the processor + model + generation config."""
    global _LOADED
    if _LOADED is not None:
        return _LOADED
    model_id = probe_ready()
    import torch
    from transformers import AutoModel, AutoProcessor, GenerationConfig

    use_cuda = _use_cuda()
    processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
    model = AutoModel.from_pretrained(
        model_id,
        trust_remote_code=True,
        torch_dtype=torch.bfloat16 if use_cuda else torch.float32,
    )
    if use_cuda:
        model = model.to("cuda")
    model.eval()
    gen_cfg = GenerationConfig.from_pretrained(model_id, trust_remote_code=True)
    _LOADED = (processor, model, gen_cfg)
    return _LOADED


def parse_page_png(png_bytes: bytes) -> Tuple[Tuple[str, str], ...]:
    """One page image -> ``(semantic_class, text)`` regions in reading order."""
    from PIL import Image

    try:
        image = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    except Exception as exc:  # PIL raises a zoo of types; re-typed, never silent
        raise InferenceError(f"undecodable page image: {type(exc).__name__}: {exc}") from exc

    processor, model, gen_cfg = _load()
    try:
        import torch

        # Model-card call: images=[image] + task_prompt, add_special_tokens=False.
        inputs = processor(
            images=[image],
            text=TASK_PROMPT,
            return_tensors="pt",
            add_special_tokens=False,
        ).to(model.device)
        with torch.inference_mode():
            generated = model.generate(**inputs, generation_config=gen_cfg)
        # skip_special_tokens=True strips </s><s> framing but KEEPS the
        # <x_..>/<class_..> region tags (they are ordinary tokens, not special).
        decoded = processor.batch_decode(generated, skip_special_tokens=True)[0]
        regions = _regions_from_decoded(decoded)
    except InferenceError:
        raise
    except Exception as exc:  # typed re-raise across the process boundary
        raise InferenceError(f"{type(exc).__name__}: {exc}") from exc
    return tuple(regions)


def _mmd_to_plain(text: str) -> str:
    """Nemotron markdown region text -> faithful plain text (witness-comparable).

    Preserves the load-bearing signals (super/subscripts become the real
    unicode glyphs, so a superscript ``³`` survives verbatim) while stripping
    emphasis/heading syntax that would corrupt token comparison. Mirrors
    ``postprocessing.convert_mmd_to_plain_text_ours`` + ``remove_nemotron_formatting``.
    """
    text = text.replace("<tbc>", "").replace("\\<|unk|\\>", "").replace("\\unknown", "")

    def _sup(m: "re.Match[str]") -> str:
        return m.group(1).translate(_SUP)

    def _sub(m: "re.Match[str]") -> str:
        return m.group(1).translate(_SUB)

    text = re.sub(r"<sup>(.*?)</sup>", _sup, text, flags=re.DOTALL)
    text = re.sub(r"<sub>(.*?)</sub>", _sub, text, flags=re.DOTALL)
    text = text.replace("<br>", "\n")
    text = re.sub(r"#+\s", "", text)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"\*(.*?)\*", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"(?<!\w)_([^_]+)_", r"\1", text)
    return text.strip()


def _regions_from_decoded(decoded: str) -> Sequence[Tuple[str, str]]:
    """Decode the model's region grammar into (class, text) in reading order.

    Exact re-derivation of ``postprocessing.extract_classes_bboxes``: the model
    emits ``<x_f><y_f> text <x_f><y_f><class_NAME>`` per region; bboxes are
    dropped (the wire format is class + text only). ``Inline-formula`` is
    normalised to ``Formula`` as the reference does. No processor helper exists.
    """
    regions: List[Tuple[str, str]] = []
    for m in _RE_REGION.finditer(decoded):
        _x1, _y1, body, _x2, _y2, cls = m.groups()
        if cls == "Inline-formula":
            cls = "Formula"
        regions.append((cls, _mmd_to_plain(body)))
    return regions
