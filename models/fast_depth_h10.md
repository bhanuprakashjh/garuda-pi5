# fast_depth (Hailo-10H) — model card

> **Licence: MIT — clean, product-viable.** Monocular depth, NYU-indoor-trained.

Monocular depth network on the Hailo-10H: ONE image → a learned per-pixel depth. Unlike the
unlicensed, driving-trained StereoNet, fast_depth is MIT-licensed AND trained on NYU (indoor
rooms), so it reads our scenes far better. But it is **monocular** — it *infers* depth from
appearance cues rather than *measuring* it, so the scale is **relative** (not metric) and it can't
truly range. For metric distance, use the CPU SGBM stereo path.

- **Source:** https://github.com/dwofk/fast-depth — ICRA 2019 "FastDepth: Fast Monocular Depth
  Estimation on Embedded Systems". **Licence: MIT.**
- **HEF:** Hailo Model Zoo → `models/fetch.sh fast_depth` →
  `…/ModelZoo/Compiled/v5.4.0/hailo10h/fast_depth.hef` (2.0 MB, DFC v5.4.0). Git-ignored.
- **I/O** (`hailortcli parse-hef`): UINT8 NHWC **224×224×3** in → UINT16 NHWC **224×224×1** out.
- **Perf:** model-zoo 2289 fps / HW RMSE 0.61. **On-bench measured ~221 fps** (viewer-bound).
- **Integration:** `hosts/pi5/hailo_mono_depth.py` (`HailoMonoDepth`) + `camarray_depth_mono.py`
  (viewer, `:8096`). Feeds the left tile only; no rectification needed (monocular).
- **Verdict:** captures indoor structure well (distinct near objects) and is licence-clean, but
  low-res + relative-scale → a cheap dense *structural prior*, not a ranging sensor. SGBM remains
  the metric depth engine on this rig.
