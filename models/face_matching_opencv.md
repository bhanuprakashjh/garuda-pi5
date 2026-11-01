# Optional local face matching — YuNet + SFace

The demo's optional face path uses existing OpenCV 4.10 on the **Pi CPU**. Hailo-10H
continues person detection / pose. No claim is made that these ONNX models run on Hailo.

Official sources: [YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet)
(MIT) and [SFace](https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface)
(Apache-2.0). Licences are retained in `faces/`. Revision and SHA-256 hashes are pinned in
`fetch_faces.py`; run `python3 models/fetch_faces.py --out <deployment>/models/faces`.
Yocto model recipe is staged, unbuilt.

The worker samples at most once per 750 ms with a one-job queue and no camera ownership.
It associates each detected face with exactly one live person box, rejects small/blurred/
strongly tilted views, aligns five landmarks, and emits normalized 128-component embeddings.
Face service is disabled by default. It can be enabled in the GUI; no images are written.

Enrollment keeps three distinct, mutually consistent samples in browser memory. Releasing the
target, forgetting the face, or reloading the page clears that reference. Camera changes require
reselection. A new ID in face-confirmed mode must pass cosine >=0.50, margin >=0.08, and two
distinct fresh face samples over >=800 ms. It never silently falls back to clothing. These are
conservative initial engineering thresholds, **not measured false-accept guarantees**. There is
no anti-spoof/liveness model; this feature is for the local simulated-flight demo, not access
control. Crowd accuracy, distance/lighting envelope, and sustained thermals remain TBD-bench.
