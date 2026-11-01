# Testing release validation — 2026-11-01

Target: `v0.2.0-test.1`. This is packaging evidence, not a fresh-machine acceptance.

- Installer preview and regression tests: 9 passed, no sudo/network/flight needed.
- Follow/controller and launcher suites: 74 passed with `PYTHONPATH=hosts/pc`.
  The initial combined command without that path failed test collection due to
  pytest importlib mode; the guide now specifies it.
- Three upstream HEFs were downloaded/hashed in memory and matched to the preserved
  baseline: primary and DA v5.3.0, pose v5.4.0. The v5.3.0 pose file differed and was
  not substituted. No Hailo inference or model installation on the Pi occurred.
- QGC 5.0.8 checksum comes from GitHub's upstream release-asset digest; this is a
  newly pinned download, not a claim that the previous flight used that release.
- Fresh project-local PC vision installation passed: pinned pip requirements,
  model checksum, Gazebo binding imports and generated video decode/frame count.
- Vision tests: 4 passed, including synthetic sample catalog/label coverage.
- The built x86_64 executable successfully ran the vision installation stage.
- An extracted kit passed all per-file SHA-256 checks (325 files at validation);
  binary preview created no runtime files and doctor returned 2 for missing
  dependencies as intended. No HEFs, ONNX weights or local runtime were packaged.
- The generated sample decoded as 120 frames. A 12-frame offline inference/report
  smoke completed with zero annotated frames and null target coverage, as intended;
  no accuracy or throughput claim is made from this synthetic fixture.

Open gates: full clean Ubuntu/Pi installation; Hailo packages and custom depth
acquisition on a new board; full orbit and gesture sequence; cooled sustained run;
new QGC pin flight test. The Pi did not answer SSH during packaging. Working Pi
and simulator services were not modified or restarted.
