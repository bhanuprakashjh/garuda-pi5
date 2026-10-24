# scdepthv3 (Hailo-10H) — model card

> **Licence: GPL-3.0 — EVAL-ONLY, never ship** (CLAUDE.md rule 6 + `models/LICENCES.md`).

Monocular depth on the Hailo-10H, NYU-indoor-trained, **higher accuracy than fast_depth**
(RMSE 0.48 vs 0.61). Evaluated to gauge the quality ceiling of indoor mono depth on this rig — a
**clean-licence equivalent would be needed to ship** anything like it.

- **Source:** https://github.com/JiawangBian/sc_depth_pl — SC-Depth v3. **Licence: GPL-3.0.**
- **HEF:** `models/fetch.sh scdepthv3` → `…/hailo10h/scdepthv3.hef` (12 MB, DFC v5.4.0). Git-ignored.
- **I/O** (`hailortcli parse-hef`): UINT8 NHWC **256×320×3** in → UINT16 NHWC **256×320×1** out.
- **Perf:** model-zoo 737 fps / HW RMSE 0.48. **On-bench measured ~135 fps** (viewer-bound).
- **Integration:** `hosts/pi5/camarray_depth_mono.py --hef …/scdepthv3_h10.hef` (`:8096`).
- **Verdict:** the cleanest/highest-res mono depth we tried (crisp indoor structure), but **GPL-3.0
  → eval-only**, and still monocular (relative scale, not a ranging sensor). Note: its depth
  polarity is inverted vs fast_depth (display concern only). SGBM stays the metric engine.
