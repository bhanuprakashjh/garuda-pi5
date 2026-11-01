# Replay sample

The testing release kit includes `playback-check.avi`: 120 generated frames at
640×360 and 20 fps. It contains animated shapes, a frame counter and an explicit
synthetic label. It verifies video decoding, playback and the replay UI. It is
**not real drone footage and not a person-detection or tracking benchmark**;
zero detections are an acceptable result on this fixture.

The generator is `release/make_sample.py`; the generated clip is our own project
asset under the repository's BSD-3-Clause license. No third-party people, video,
photography or dataset footage was copied. `CONTENTS.sha256` in the release kit
records the exact video checksum. Running the installer vision stage regenerates
it for a source checkout. Its bytes may differ with encoder versions.

Real footage: use your own authorized MP4/AVI via `hosts/pc/vision.py --video PATH`,
or an authorized VisDrone dataset copy with the layout in `docs/DEMO_USER_GUIDE.md`.
Raw VisDrone recordings are not redistributed in GitHub assets.
