# nemotron-parse-service

Process-isolated NVIDIA **Nemotron-Parse** page-parse service for LawVM.

Nemotron-Parse is NOT "the parser". It is one more **independent vision
witness** — a purpose-built document-parse VLM (text + bbox + semantic class +
reading order + tables in one pass) whose output enters the SAME
producer-neutral adjudication pipeline as reading-order extraction, pdfplumber,
and every other witness (`lawvm.core.source_document.adjudication`). It is
never trusted alone; it only ever emits typed candidate proposals
(`ExtractionAssertion`s on the main-package side) that adjudication may accept
or reject. It never mutates replay state.

## ⚠️ License caveat — READ BEFORE REDISTRIBUTION OR COMMERCIAL USE

The Nemotron-Parse **model weights** are released under the **NVIDIA Open
Model License Agreement**, NOT under LawVM's MIT license. That license carries
its own conditions (attribution, redistribution terms, acceptable-use /
litigation clauses) that MUST be reviewed before:

- redistributing the weights or bundling them into any LawVM artifact,
- deploying the model in a commercial product, or
- publishing model outputs as part of a released dataset.

Nothing in this repository vendors the weights; they are downloaded at deploy
time by the operator, who accepts the NVIDIA license themselves. The scaffold
code in this directory is MIT like the rest of LawVM.

- Model id: `nvidia/NVIDIA-Nemotron-Parse-v1.2` (override with
  `NEMOTRON_PARSE_MODEL_ID`). Verify the exact Hugging Face repo id and the
  current license text on the model card before standing up a deployment.

## Isolation contract

- Heavy deps (torch, transformers, pillow, …) are declared ONLY in this
  directory's `pyproject.toml`. The root `pyproject.toml` never names them.
- The main package never imports `nemotron_parse`. The only coupling is a
  **process boundary**: the thin client
  `src/lawvm/finland/llm_backends/nemotron_client.py` spawns this project's
  CLI as a subprocess and parses its stdout.
- The main repo's CI never installs or collects this directory (root
  `pyproject.toml`: pytest `norecursedirs`, ruff `extend-exclude`, ty
  `src.exclude`; the ratchet/module-role scanners root at `src/lawvm`).
- If the service is absent, `nemotron_client.is_available()` is `False` and
  the ingest pipeline falls back to reading-order extraction (the determinism
  firewall).

## Serving requirements

- A CUDA GPU with enough VRAM for the ~1B-param model (CPU inference is
  possible but slow); `torch` + `transformers` per `pyproject.toml`.
- Install: `uv sync --project subprojects/nemotron_parse` (downloads several
  GB of wheels — never done by main-repo CI).
- First run downloads the weights from Hugging Face (operator accepts the
  NVIDIA Open Model License at that point).

## Wire contract (frozen)

The client↔service contract is a **stable text format**, not a Python object.

### `probe`

```
<cmd> probe
```

- stdout `READY <model-id>` + exit `0` when the heavy deps import and a model
  id is resolvable. Non-zero exit (`4`) otherwise. The client's
  `is_available()` is exactly "probe exits 0 and says READY".

### `parse`

```
<cmd> parse --page-num N --artifact-digest DIGEST < page.png
```

- stdin: the rendered page image bytes (PNG).
- `--page-num`: 1-indexed page number (provenance echo; the service parses
  exactly the image it was given).
- `--artifact-digest`: SHA-256 of the SOURCE artifact (provenance echo).
- stdout: the compact `KIND: text` block format that
  `lawvm.finland.llm_backends.vision_producer._parse_blocks` already
  understands — one block per region in reading order, blocks continue over
  wrapped lines until the next governed label:

  ```
  HEADING: 4 §
  PARA: Sen lisäksi, mitä 1 momentissa säädetään, hakijalle palautetaan
  valmisteveroa 4 senttiä litralta.
  ITEM: 1) ensimmäinen kohta
  TABLE: Vero | Määrä
  FOOTNOTE: 1) Sovelletaan verovuodesta 2025.
  ```

  Governed labels: `HEADING`, `PARA`, `ITEM`, `TABLE`, `FOOTNOTE`. Nothing
  else appears on stdout — no JSON, no markdown, no commentary. Nemotron
  semantic classes outside the governed mapping (page furniture, pictures,
  formulas — see `wire.NEMOTRON_CLASS_TO_WIRE`) are DROPPED, never relabeled.
- exit codes: `0` ok · `3` bad input (empty/undecodable image, bad args) ·
  `4` model/deps unavailable · `5` inference error. Any non-zero exit is a
  typed `NemotronParseFailure` on the client side — never a silent empty page.

The frozen golden for this format lives at `tests/data/wire_contract_golden.txt`
and is pinned from BOTH sides: `tests/test_wire_contract.py` here (emission,
hermetic — no heavy deps) and `tests/test_fi_nemotron_client.py` in the main
repo (parsing).

### HTTP mode (sketch, not implemented)

For long-lived deployments a localhost HTTP mode would avoid per-page model
reload: `POST /parse` with the PNG body + `X-Page-Num` / `X-Artifact-Digest`
headers, responding with the identical `KIND:` block text. The client's
transport seam (`NemotronParseClient._run_service`) is the single place to add
it. The subprocess contract above is the canonical one today.

## Running

```sh
# hermetic wire-contract + serve-CLI + validation-harness tests
# (no heavy deps needed — the model is faked; the pure comparison core is pure):
uv run --project subprojects/nemotron_parse pytest subprojects/nemotron_parse/tests -p no:cacheprovider

# point the main-package client at the service:
export LAWVM_NEMOTRON_PARSE_CMD="uv run --project subprojects/nemotron_parse python -m nemotron_parse.serve"
```

The subproject carries its OWN `[tool.pytest.ini_options]` block (empty
`addopts`, `testpaths = ["tests"]`) so it becomes the pytest configfile/rootdir
and the MAIN repo's root `addopts = "-n 4"` (which needs `pytest-xdist`, a dep
this project does not carry) never leaks in and breaks the command above.

The client is INERT unless `LAWVM_NEMOTRON_PARSE_CMD` is set — an unset
variable means `is_available() == False` and zero chance of an accidental
multi-gigabyte dependency install from the main package.

## Cross-witness validation harness

`validate_witness.py` is a STANDALONE harness for the whole point of an
independent witness: **cross-witness agreement**. It renders a real page and
runs up to three producers that share no code —

- `pdfium`   — the deterministic pypdfium2 text layer (always available; the
  baseline every model read is scored against);
- `vision`   — the local llama.cpp vision producer at `:8080`, when reachable;
- `nemotron` — this isolated Nemotron-Parse service, when reachable
  (`LAWVM_NEMOTRON_PARSE_CMD` set + probe `READY`) —

then reports, per page, pairwise token agreement (jaccard + shared/unique
counts), the corroborated-read count (fragments ≥ 2 witnesses back), and every
DISAGREEMENT localized to the one witness that saw it (the garble/hallucination
localizer). Output is deterministic and LINE-BASED, **never JSON** (a run-to-run
diff is a stable signal). It does **not** wire into core adjudication — it only
reports the signal.

Design: the comparison itself (`compare_page` / `render_report`) is a PURE
function over `PageWitness` carriers — no PDF, no network, no `lawvm` import — so
it is hermetic-testable with FAKE witnesses (`tests/test_validate_witness.py`).
Only the CLI touches farchive + the real producers, and it imports `lawvm`
lazily, so `--help` and the pure core work with nothing installed.

```sh
export LAWVM_CANONICAL_DATA_ROOT=/path/to/LawVM   # farchive data root
# optional model witnesses: llama.cpp vision server on :8080 and/or
#   export LAWVM_NEMOTRON_PARSE_CMD="uv run --project subprojects/nemotron_parse python -m nemotron_parse.serve"

# a finlex.farchive locator + page range (rendered from the PDF blob):
uv run --extra pdf python subprojects/nemotron_parse/validate_witness.py \
  --farchive data/finlex.farchive \
  --locator 'finlex://sd-cons/1991/1208/fin@20050873/media/4198.pdf' \
  --pages 1-2

# or a local PDF file:
uv run --extra pdf python subprojects/nemotron_parse/validate_witness.py --pdf /tmp/he.pdf --pages 1-5
```

With only `pdfium` present the report degrades to a single-witness inventory
(`agreement none (single witness — no cross signal)`) — honest, not empty. A
witness that is off is reported as `status=absent`; a witness that errored on a
page is `status=error: <detail>` (typed, never a silent empty). This runs in the
MAIN-repo env (it drives the main-package producers); it is an **operator/other-
track** step, not part of main CI.

## Deployment (standing the service up — operator steps)

Standing up a REAL Nemotron-Parse deployment is an operator/other-track task
(the scaffold in `model.py` is a faithful-but-unexercised transformers sketch —
validate the exact processor/generation/decode calls against the model card
before first real use). The steps:

1. **Install the heavy stack** (several GB of wheels; NEVER done by main CI):

   ```sh
   uv sync --project subprojects/nemotron_parse
   ```

2. **Weights + license.** First `probe`/`parse` downloads
   `nvidia/NVIDIA-Nemotron-Parse-v1.2` from Hugging Face; the operator accepts
   the **NVIDIA Open Model License** at that point (see the License caveat
   above — the weights are NOT MIT and their redistribution/commercial terms
   must be reviewed before any released artifact or dataset).

3. **GPU constraint (time-share).** The single **RTX 5090** currently serves the
   **Qwen vision model at `:8080`** (the `vision` witness). Nemotron-Parse's
   ~1B-param model must therefore either **time-share** that GPU (bring one
   model down before loading the other, or fit both within VRAM if they co-
   reside) or run on **CPU** (possible via `torch` but slow — fine for a
   validation batch, not for throughput). `probe` deliberately does NOT load
   weights, so `is_available()` is cheap regardless.

4. **Verify the boundary, then wire.** Point the client at the service
   (`LAWVM_NEMOTRON_PARSE_CMD`) and confirm the wire with the validation
   harness above (nemotron should flip from `absent` to `ok` and start
   contributing agreement/disagreement). Wiring the accepted Nemotron
   `ExtractionAssertion`s into core `source_document.adjudication` is a
   SEPARATE (unification/re-read) track — this subproject only makes the witness
   validatable and deployable standalone.
