# StereoNet (Hailo-10H) — model card

> **BENCH-ONLY. NOT product-cleared** (CLAUDE.md rule 6). Depth experiment to prove the NPU path.

Stereo disparity network run on the Hailo-10H NPU for the CamArray depth experiment: the heavy
matching moves off the CPU onto the Hailo, freeing the CPU and raising the depth framerate.

- **Source:** https://github.com/nivosco/StereoNet — "StereoNet: guided hierarchical refinement
  for real-time edge-aware depth prediction".
- **LICENCE: NONE.** The source repo carries no licence (`license: null` via the GitHub API) =
  all-rights-reserved. We only run **Hailo's official pre-compiled HEF** as an experiment. **A
  product build needs a depth model with a clean licence (Apache/MIT/BSD) — do NOT ship this**, and
  no `yocto/meta-garuda` recipe is added for it (product must swap in a licence-clean model first).
- **HEF:** Hailo Model Zoo → `models/fetch.sh stereonet` downloads
  `…/ModelZoo/Compiled/v5.4.0/hailo10h/stereonet.hef` (9.2 MB, DFC v5.4.0). The `.hef` binary is
  git-ignored — fetch it, don't commit it.
- **I/O** (`hailortcli parse-hef`): 2× UINT8 NHWC **368×1232×3** (`input_layer1`/`input_layer2` =
  left/right, must be **rectified**) → UINT8 NHWC **368×1232×1** disparity (`conv53`).
- **Perf:** model-zoo 16.7 FPS batch1 (Float EPE 8.22 / HW EPE 10.4). **On-bench measured ~13.8 fps.**
- **Integration:** `hosts/pi5/hailo_stereo.py` (`HailoStereoNet`, InferModel API) +
  `hosts/pi5/camarray_depth_npu.py` (live viewer, `:8094`). Rectify on CPU with our stereo
  calibration → StereoNet on the NPU → metric depth `Z = f·B/d`.
- **Caveats:** trained on driving/KITTI scenes at the wide 368×1232 aspect, so indoor accuracy is
  rough and our square-ish tiles are resized (vertically squished) into it; the horizontal disparity
  stays valid but absolute scale is **provisional** until a known-distance pin.
