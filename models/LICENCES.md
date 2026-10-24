# models/ — evaluation zone + licence manifest

`models/` is the **model evaluation zone**: any depth/vision model may be fetched and run here to
benchmark it on the bench. **Evaluation is not distribution** — running a model privately triggers
no licence obligations — so GPL and even unlicensed models are fine *to try*. The wall this manifest
enforces:

- **SHIP-ELIGIBLE** models (BSD/MIT/Apache/LGPL-dynamic) may graduate into `services/`.
- **EVAL-ONLY** models (GPLv3, unlicensed, unclear) stay in `models/` + host viewers, **never** get
  wired into `services/`, and **never** ship. CI licence-scan polices `services/`, not this zone.
- Every model gets a card + a row here stating its licence and eligibility. HEF binaries are
  git-ignored (`models/*.hef`) — fetch via the `fetch_*.sh` scripts.

This keeps CLAUDE.md rule 6 intact (clean `services/`) while letting us evaluate the whole zoo,
exactly like the object-detection models. When a model proves out and is ship-eligible, it moves to
`services/`; when a good one is EVAL-ONLY, that's the signal to find/train a clean-licence equivalent.

## Manifest

| model | task | licence | ship-eligible | card | notes |
|---|---|---|---|---|---|
| **fast_depth** | mono depth | **MIT** | ✅ yes | `fast_depth_h10.md` | NYU-indoor; relative scale; ~221 fps |
| stereonet | stereo depth | **none (unlicensed)** | ❌ eval-only | `stereonet_h10.md` | driving-trained; range-collapses indoors |
| scdepthv3 | mono depth | **GPL-3.0** | ❌ eval-only | `scdepthv3_h10.md` | NYU-indoor; better RMSE (0.48); GPL → never ship |
| yolov5n-seg | instance seg (dynamic masking) | **GPL-3.0** (Ultralytics) | ❌ eval-only | `yolov5n_seg_h10.md` | system model-zoo HEF (`/usr/share/hailo-models/`), not fetched here; feeds `garuda-mask` behind `IFrontEnd`; ~11.5 fps sync (benchlog 2026-08-02) |
| yolo_world_v2s | zero-shot detection | GPL-3.0 | ❌ eval-only, **`not-product-cleared`** | yolo_world_v2s_h10.md | prompt-driven open-vocab; runtime CLIP embeddings confirmed working 2026-08-07 (`hosts/pi5/hailo_yolo_world.py`) |
| yolov8m_pose | pose keypoints (17 COCO) | AGPL-3.0 | ❌ eval-only | yolov8m_pose_h10.md | 17-kpt skeleton; gesture demo (yolo26-pose exporter-gated, fallback to zoo) |
| clip_vitb32_text_model (+tokenizer) | CPU text encoder for the above | **MIT** (OpenAI CLIP) | ✅ yes (but only useful with the GPL HEF) | yolo_world_v2s_h10.md | ONNX `CLIPTextModelWithProjection`; runs under onnxruntime (MIT) + HF tokenizers (Apache-2.0) |

> The only **ship-eligible** depth model here is `fast_depth`. For metric ranging on this rig, the
> licence-clean **CPU SGBM** stereo path (`services/garuda-camera`) remains the production choice;
> these NPU models are dense priors / evaluation candidates.
