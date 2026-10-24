# yolo_world_v2s (Hailo-10H) — model card

> **Licence: GPL-3.0 — bench/eval, `not-product-cleared`. EVAL-ONLY, never ship**
> (CLAUDE.md rule 6 + `models/LICENCES.md`).

Zero-shot / open-vocabulary object detection on the Hailo-10H: instead of a fixed 80-class head,
the network takes a **runtime class-embedding tensor** alongside the image, so the vocabulary is
whatever text prompts you embed at inference time (e.g. "pen", "keys" — not in COCO's 80). Spike
goal (portal-tracking plan Task 1): confirm the HEF's exact embedding-input contract before Task 7
builds `hosts/pi5/hailo_yolo_world.py` around it.

- **Source:** https://github.com/AILab-CVC/YOLO-World — YOLO-World (CVPR 2024), CVC-AI-Lab.
  **Licence: GPL-3.0.**
- **HEF:** `models/fetch.sh yolo_world_v2s` →
  `.../ModelZoo/Compiled/v5.4.0/hailo10h/yolo_world_v2s.hef` (24 MB, DFC v5.4.0). Git-ignored.
- **Perf (zoo published, not bench-measured):** zero-shot mAP **36.4 (float) / 31.9 (HW/quantized)**,
  **46.6 fps** at batch size 1.

## I/O contract (`hailortcli parse-hef`, verbatim, 2026-08-07 on garuda@10.42.0.128)

```
HEF Compatible for: HAILO15H, HAILO10H
HEF Compiler Version: 5.4.0
Network group name: yolo_world_v2s, Multi Context - Number of contexts: 7
    Network name: yolo_world_v2s/yolo_world_v2s
        VStream infos:
            Input  yolo_world_v2s/input_layer1 UINT8, NHWC(640x640x3)
            Input  yolo_world_v2s/input_layer2 UINT16, NHWC(1x80x512)
            Output yolo_world_v2s/yolov8_nms_postprocess FLOAT32, HAILO NMS BY CLASS(number of classes: 80, maximum bounding boxes per class: 300, maximum frame size: 480320)
            Operation:
                Op YOLOV8
                Name: YOLOV8-Post-Process
                Score threshold: 0.001
                IoU threshold: 0.70
                Classes: 80
                Max bboxes per class: 300
                Image height: 640
                Image width: 640
```

Low-level stream infos (`--parse-streams=1 --parse-vstreams=0`) show the pre-NMS heads the vstream
above fuses, for reference — standard YOLOv8-style 3-scale decoupled head (class-score + DFL box
regression per scale), all UINT16 NHCW:

```
Output yolo_world_v2s/normalization4_244 UINT16, NHCW(80x80x80)   # P3 class scores (80 ch)
Output yolo_world_v2s/conv48_244         UINT16, NHCW(80x80x64)   # P3 box regression (DFL, 64 ch)
Output yolo_world_v2s/normalization6_244 UINT16, NHCW(40x40x80)   # P4 class scores
Output yolo_world_v2s/conv71_244         UINT16, NHCW(20x20x64)   # P5 box regression
Output yolo_world_v2s/conv60_244         UINT16, NHCW(40x40x64)   # P4 box regression
Output yolo_world_v2s/normalization8_244 UINT16, NHCW(20x20x80)   # P5 class scores
```

(Listed exactly in the order `hailortcli --parse-streams=1` printed them — the two box-regression
heads are not adjacent to their matching class-score head in the raw dump; harmless since the
vstream output above is the one actually consumed by application code.)

## Prompt contract (open-vocab embedding input)

The HEF **does** take a runtime class-embedding input — it is not fixed-vocab — confirming the
design spec's assumption (`docs/superpowers/specs/2026-08-07-portal-tracking-integration-design.md`
§2: "Prompt → CLIP ViT-B/32 text encoder on Pi CPU → class embeddings → detector"):

- **`input_layer2`: UINT16, NHWC `(1, 80, 512)`.** 512 is the CLIP ViT-B/32 text-embedding width —
  consistent with YOLO-World's published architecture (CLIP text tower feeds the embedding branch).
  80 is a **fixed slot count**, not a per-request variable `N` — the compiled HEF always expects 80
  rows. This differs from the brief's expectation of a variable `(1, N, 512)`: N is baked to 80 at
  compile time, matching COCO's class count (this HEF likely ships pretrained against the COCO-80
  vocabulary by default, with the embedding tensor swappable — but only if you fill all 80 rows).
  **Task 7 implication:** the wrapper must always feed 80 embedding rows — pad unused class slots
  (e.g. repeat a dummy/zero embedding or duplicate an existing row) and read back only the rows that
  correspond to real prompt classes from the NMS-by-class output (which is keyed by the same 0–79
  class index space).
- **dtype is UINT16 — RESOLVED 2026-08-07 (Task 7): reading 1 was right, the branch is quantized,
  and you never have to touch the uint16 yourself.** The CLI has no `--quant-info` flag, but the
  python API does. Measured on the bench (`hailo_platform.HEF(...).get_input_vstream_infos()`):

  ```
  IN yolo_world_v2s/input_layer1 (640,640,3) UINT8  qp_scale=1.0            qp_zp=0.0     limvals 0.0 .. 255.0
  IN yolo_world_v2s/input_layer2 (1,80,512)  UINT16 qp_scale=2.6535412e-05 qp_zp=9778.0  limvals -0.25946325 .. 0.61002260
  ```

  HailoRT's documented transform (`hailort/include/hailo/quantization.hpp`) is
  `q = x / qp_scale + qp_zp`; the limits check out exactly —
  `(0.61002260 − (−0.25946325)) / 2.6535412e-05 = 32767` and `0.25946325 / 2.6535412e-05 = 9778`,
  i.e. the calibrated float range maps onto `[0, 32767]`.
  **Do NOT hand-pack uint16.** `infer_model.input("…/input_layer2").set_format_type(FormatType.FLOAT32)`
  is accepted and HailoRT applies that quantization itself — verified by a real configure+infer on
  the Hailo-10H, and it is exactly what Hailo's own code does for the structurally identical
  UINT16 `1x77x512` input of their CLIP text-encoder HEF
  (`hailo-apps/hailo_apps/python/pipeline_apps/clip/clip_text_utils.py`, and
  `hailo_model_zoo/core/infer/hw_infer_utils.py:38` which defaults every input to `FormatType.FLOAT32`).
  Note the multi-input trap: bare `infer_model.input()` raises on this HEF — always pass the name.

- **What to put in those 512 floats — the calibrated recipe.** `hailo_model_zoo/cfg/base/yolo_world.yaml`
  calibrates against `coco_val2017_with_text_embeddings.tfrecord`, built by
  `hailo_model_zoo/datasets/create_coco_tfrecord_with_text_embeddings.py`, which is upstream
  YOLO-World's `tools/generate_text_prompts.py` verbatim:
  **`openai/clip-vit-base-patch32` → `CLIPTextModelWithProjection` → `.text_embeds` (512-d) →
  L2-normalise**, prompted with the **bare class name** (that file defines a `PROMPT_TEMPLATES`
  list and a `--pooling` arg, but both are dead code — `texts = [class_name]` is what runs).
  The `"A photo of a "` prefix in hailo-apps/hailo-CLIP belongs to the CLIP *classifier* app; do not
  cross-apply it. `yolo_world_v2s.alls` adds `norm_layer1` for the image only — `input_layer2` gets
  no normalization layer, so feed it exactly those values. Independent confirmation from the bench:
  our CLIP embeddings for "pen, keys, person" span **−0.261 .. 0.598**, against the HEF's calibrated
  **−0.259 .. 0.610** — the tensor is the one the DFC was calibrated on.

- **Filling the 75+ unused slots: use ZEROS.** Measured on `/tmp/bus.jpg` with
  `["person","bus","backpack","pen","keys"]` (2026-08-07, bench):

  | padding of slots 5..79 | bus | best person | phantom boxes |
  |---|---|---|---|
  | zero vector | **0.922** | **0.898** | none |
  | `' '` background embedding (as YOLO-World's demos append) | 0.819 | 0.399 | 1 whole-image box in *every* padded slot @0.005 |

  For reference, YOLO26m's COCO head on the same image gives bus 0.93 / person 0.93 — i.e. the
  zero-padded open-vocab path reproduces the fixed-head scores. A real filler embedding is
  correlated with everything and competes; zero does not. (The zoo's calibration set has all 80
  slots holding real COCO embeddings, which is why the degenerate case needed measuring.)
- **Output is post-NMS already** (`yolov8_nms_postprocess`, `HAILO NMS BY CLASS`), so no separate
  decode step is needed for boxes/scores — same downstream shape as any other Hailo YOLOv8 NMS
  output. `class_id` in that output maps 1:1 to the row index fed into `input_layer2`, so the
  wrapper's `set_prompt(labels, embeddings)` must keep prompt-label-to-slot-index bookkeeping
  consistent between the embedding write and the output read. Concretely (measured):
  `infer_model.output(name).shape == (120080,)` = `80 * (1 + 5*300)` float32, and
  `bindings.output(name).get_buffer()` returns a **python list of 80 arrays, each `(n, 5)`**, rows
  `(y_min, x_min, y_max, x_max, score)` normalised 0..1 against the 640x640 letterbox
  (`hailort/include/hailo/hailort.h`, `hailo_bbox_float32_t`). Preprocessing is a **centered
  letterbox with pad value 114**, UINT8 RGB — the `/255` lives on-chip (`norm_layer1` in the `.alls`).
  On-chip NMS is retunable at runtime without recompiling, before `configure()`:
  `output.set_nms_score_threshold(f)`, `.set_nms_iou_threshold(f)`, `.set_nms_max_proposals_per_class(n)`.

## Verdict — open-vocab path CONFIRMED WORKING (Task 7, 2026-08-07)

Both of the spike's open questions are closed, on-device, and `hosts/pi5/hailo_yolo_world.py`
implements the result. The fixed 80-slot shape is not a blocker (zero-pad the unused slots) and the
UINT16 dtype never has to be handled by hand (FLOAT32 input format + HailoRT's own quantization).

Bench proof that prompts really are runtime-swappable — the same HEF, same image, prompts that are
**not COCO classes** (`/tmp/bus.jpg`, `CONF=0.10`):

```
prompts ['bus wheel','white sneakers','windshield','pavement','rucksack']  -> 37 ms
  pavement    0.395  (6,646,802,434)      # the road strip along the bottom
  bus wheel   0.187  (686,559,77,120)     # the bus's rear wheel
```

Licence is GPL-3.0 → **eval-only, `not-product-cleared`**, `models/` zone, never `services/`,
consistent with `yolov5n-seg` and `scdepthv3` in this manifest. The CPU-side text tower is clean
(OpenAI CLIP MIT, onnxruntime MIT, HF tokenizers Apache-2.0) but is only useful with this HEF.

## Host-side assets (fetch with `models/fetch.sh clip_text`)

| file | what | licence |
|---|---|---|
| `clip_vitb32_text_model.onnx` (254 MB) | `CLIPTextModelWithProjection` text tower, `input_ids -> text_embeds (1,512)` | MIT (OpenAI CLIP) |
| `clip_vitb32_tokenizer.json` (2.2 MB) | CLIP BPE fast-tokenizer | MIT |

Run on the **Pi CPU** via onnxruntime, only when the prompt list changes; `hailo_yolo_world.py`
caches one embedding per prompt word and releases the ORT session immediately (the fp32 tower must
not sit resident beside the NPU models). Do **not** try to chain the zoo's Hailo-compiled CLIP text
encoder: its output is `normalization25`, needing a host-side `text_projection.npy` matmul from the
CLIP-*classifier* app, which is not verified equivalent to `CLIPTextModelWithProjection.text_embeds`
that YOLO-World was trained against.
