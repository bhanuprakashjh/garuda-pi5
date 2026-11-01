# Plan to update bhanuprakashjh/garuda-pi5

> **Executed 2026-11-01 (owner decision, sole maintainer):** published directly onto `main` as a
> single additive commit on top of snapshot `f19835a` — no history rewrite, no force-push. The four
> snapshot-only paths below were preserved; `README.md` and `docs/USER_GUIDE.md` were reconciled
> rather than replaced; the repository is public and its README now says so. The "Installation
> gaps" and "Verification" sections remain open release gates, not claims.

Prepared 2026-11-01 after reading [the GitHub repository](https://github.com/bhanuprakashjh/garuda-pi5) and fetching its `main` into local review ref `pi5-review/main`. This is a publication plan; no remote branch, PR, visibility setting or live Pi deployment was changed by this documentation task.

## Repository findings

- Remote default branch: `main`, one snapshot commit `f19835a3347fb0c754103d158bea942cdbfc5e4f` dated October 24.
- Repository is **public** at review time. Its README calls it a private snapshot; update that wording and review published material accordingly.
- The snapshot predates USB fallback, the branded browser demo and all PC flight-lab work.
- Local candidate: `feat/px4-gazebo-demo`, implementation through `1d28872`, plus this documentation change. It retains the preserved Pi baseline at `99db1c5`.
- Histories have no merge base. Do not merge unrelated histories or force-push the development repository over the snapshot.
- Local `origin` is `garuda-sdk-bench`, not `garuda-pi5`. Use an explicit separate clone/remote for publication.
- Before this documentation change, the source comparison contained 78 changed files, 12,925 insertions and 996 deletions; `pixi.lock` alone has 8,404 lines. Recompute the diff at publication time.

Four snapshot-only paths must survive selective integration:

| Path | Required handling |
|---|---|
| `docs/USER_GUIDE.md` | Preserve its historical bench/portal instructions; add current-demo entry point and reconcile conflicting package guidance |
| `hosts/pi5/units/garuda-mono.service` | Preserve and reconcile with current camera/depth behavior; do not silently remove deployment unit |
| `hosts/pi5/units/garuda-mono.service.d/depth.conf` | Preserve installed `y26d` default/drop-in |
| `models/yolo_world_v2s_h10.hef` | Preserve existing tracked artifact pending explicit artifact/licence decision; record hash and compatibility |

A whole-tree copy with `--delete` would remove these files. The world HEF is about 25 MB; do not accidentally duplicate it into more commits or include local environments, datasets and recovery archives.

## Proposed review branch and commits

Create a fresh clone of `garuda-pi5`, verify its remote SHA is still the reviewed snapshot, and branch from its current `main` as `docs-and-demo/november-flight-lab`. If upstream has changed, compare and reconcile first. Bring over reviewed paths from the development branch using a file manifest; preserve remote-only paths. Keep the snapshot's history and a linear set of additions.

1. **Pi camera and presentation.** Include USB fallback, auto-selection, `/demo`, Microchip/Hailo-10H branding, shadow-flight controls, return matching, optional faces, compact layout and T-pose fixes. Include all imported support modules, UI assets, tests, model cards and license files. Preserve service units and calibration instructions. Document baseline and rollback tag.
2. **Isolated PC flight lab.** Include `hosts/pc`, ROS controller/core changes, the generated-message helper, Pixi manifest/lock, worlds, process lifecycle, setup paths and close/reopen recovery. Include source and lockfiles, not `.runtime`, `.pixi`, external PX4 source, built agent, QGC binaries or Fuel cache.
3. **Camera and aerial-footage evaluation.** Include rendered RGBD adapter, CPU detector fetcher/requirements, replay runner, source/model attribution and compact measured reports. Exclude raw footage and unrelated Hailo-15 exports; maintain links and model hashes.
4. **Documentation and release entry points.** Add [installation/user guide](DEMO_USER_GUIDE.md), [handover](DEMO_HANDOVER.md), current PC README and [isolation/recovery](px4-demo-isolation.md). Reconcile the snapshot's existing USER_GUIDE rather than replacing it blindly. Update root README with current hardware status, public visibility, the two modes, quickstart links and dated limitations. Preserve historical benchmark evidence.

Actual commit boundaries can combine tightly coupled source/tests; none should leave advertised imports or assets missing. Keep model redistribution changes separate from functional changes when their review differs.

## Installation gaps to close before release

The new guide documents the working environment and current manual setup, but publication must not imply a one-command fresh installation is already proven.

- Provide a model manifest with target architecture, compiled/runtime compatibility, filename, exact source, SHA-256, license and whether supplied or fetched. Resolve `models/fetch.sh` v5.4.0 versus working HailoRT 5.3.0; include a supported acquisition/build path for primary and custom-depth HEFs.
- Document the supported Hailo 5.3.0 ARM64 runtime/driver/CPython 3.13 package set and checksums, including access requirements. Do not rely on today's unpinned `hailo-h10-all` resolving to that version or mix old/new package families.
- Separate minimal mono-demo installation from the older portal/GenAI installer. The existing installer adds broad sudo privileges and unrelated legacy tooling and does not provision the complete current mono demo.
- Pin XRCE agent transitive Fast DDS/CDR sources; the agent commit alone is not a complete dependency lock. Record the QGC release and hash. Record apt Gazebo versions because OSRF patch versions can move.
- Improve Pi path portability or explicitly retain the `garuda` account/deployment-path requirement. PC path discovery already exists; it is not a package installer.
- Normalize dataset-root expectations and eliminate machine-specific default paths in standalone replay tooling before advertising portable defaults. Current guide passes explicit paths.
- Validate scripts and instructions in a fresh Ubuntu 24.04 install and on a separate Pi card. Keep the known-good booth environment untouched while doing that work.

These are proposed follow-up implementation tasks, not changes claimed as completed by this documentation commit.

## Verification required for the candidate PR

Run validation in the publication clone so missing imports/assets from selective transfer are caught:

1. Review `git diff --stat`, `git diff --check` and the explicit file manifest; verify all four snapshot-only paths remain. Check local documentation links and shell/Python/JavaScript syntax appropriate to changed files.
2. Run the follow-service tests, launcher tests and vision tests using their intended environments. Verify model checksum failure behavior and missing-prerequisite messages. Keep optional hardware tests clearly identified.
3. Verify original Pi behavior on a separate deployment/card: USB and repaired CSI selection/fallback, click-to-lock, lost/return, face opt-in, all four gestures, follow gap and compact fullscreen. Do not overwrite the preserved booth baseline to obtain these results.
4. Run a dedicated SITL session: start from stopped components, confirm fresh GROUND/disarmed, select/Takeoff/Follow/Hold/Orbit both directions/Land, then complete close/reopen and explicit stale-session reset. No parallel UI commands during smoke harnesses. Record full orbit completion rather than reusing the interrupted 0.456-radian trace as a pass.
5. Replay a known annotated clip and an unannotated custom video; verify source labels, disabled flight controls, report hashes and honest metric coverage. Controlled throughput comparisons must use the same workload/settings.
6. Complete fresh-PC/Pi installation and cooled booth soak. Record package/model inventories and limitations in the PR and benchlog.
7. Run the repository's applicable license/security checks. Review tracked content for credentials, private captures, enrollment data, recovery archives and accidental machine-local settings. Existing AGPL/evaluation model designations remain explicit; a public repository is not the same as a cleared product distribution.

A documentation/source-preview PR may precede closing every hardware test if its description explicitly states those remaining release gates. It must not claim clean-install or full-orbit validation that has not happened.

## Suggested PR framing

Title: **Add Microchip/Hailo-10H booth demo and isolated ROS 2/PX4 flight lab**.

Lead with behavior: the Pi supports USB fallback and a gesture-driven shadow-flight demo; a single PC window switches to an isolated PX4/Gazebo lab with live Pi camera, optional rendered-camera detection and footage analysis. Explain that the baseline remains independently available. Summarize reproducible setup and exact test results, then list unresolved model-install, full-orbit and fresh-machine validation items.

After review, publish the candidate branch/PR to `garuda-pi5`, merge through its normal review flow and tag the tested release. Supply a concise changelog, dependency/model manifests and user-guide link. Deploy separately only when that installation's update is intended; GitHub publication itself must not restart the Pi or upgrade its packages.

## Rollback and boundaries

The remote snapshot remains in Git history and the preserved Pi demo remains at `99db1c5` with its archive checksum. Revert candidate source commits if necessary; use [the recovery guide](px4-demo-isolation.md) only if an actual deployment was changed. Do not force-reset the public repository or delete existing model assets as a shortcut.

The seven visitor-experience improvements remain [future work](../hosts/pc/README.md#next-things-to-do--saved-2026-11-01). They should not be advertised as included in this release simply because they are listed in the plan.
