# Working-demo recovery and PX4 integration isolation

User requirement: preserve the working gesture demo while building PX4/Gazebo separately.

## Baseline

- Working branch: `feat/portal-tracking`, commit `99db1c5`.
- Permanent source tag: `demo-working-20261031`.
- Live URL: `http://10.42.0.128:8098/demo`.
- Pi service: `garuda-mono`, deployment `/home/garuda/garuda-sdk-deploy`.
- Pi backup: `/home/garuda/demo-recovery-99db1c5`.
- PC copy: `.local-recovery/` in the `px4-gazebo-demo` worktree (git ignored).
- Archive: `deployment.tar`, SHA-256
  `86693c5fba99cba02053de9199674851c1cceee68a6fe0398bd930f4971e81cb`.
- Archive contains deployed source, models, calibration and systemd unit/drop-ins.
  Package inventory and kernel identification are adjacent text files. This is not an SD
  image or package cache. Browser-local settings/enrollment are not included; leave the
  working browser's origin/storage intact.

## Isolation contract

- Develop on `feat/px4-gazebo-demo` in `.worktrees/px4-gazebo-demo`.
- Do not deploy experiments over the baseline directory or restart/change `garuda-mono`.
- Do not upgrade Pi packages, camera drivers, Hailo stack or change its networking.
- Consume perception read-only by default. The explicit PX4 Flight gestures / Enable pose
  camera action may select pose mode using the existing detector API. This shared runtime
  mode change clears PX4 selection as needed; it does not alter Pi files or services.
  No implicit face enrollment, camera reconfiguration or Pi deployment mutation is allowed.
- Run PX4/Gazebo and ROS bridge experiments on the PC with separate launch configuration,
  explicit simulation endpoint and `GARUDA_SIM=1`. Keep only one setpoint authority per SITL.
- PC ports: main launcher 8100, ROS diagnostics 8099, optional vision 8101, all loopback.
  The original Pi service remains on 8098. See [user guide](DEMO_USER_GUIDE.md) for startup.
- Do not run a second camera/NPU pipeline competing with the live demo. Initially test replay
  using recorded perception or PC-only fixtures. Any later Pi replay session needs a separate,
  explicitly planned source/resource handover; a second port alone does not isolate hardware.
- Pin ROS/PX4/message/agent versions; separate containers/build directories from the baseline.

## Recovery procedure (only if the deployed baseline was changed)

Normally recovery means stopping the separate experiment and opening the existing :8098 URL.
If restoration becomes necessary, verify the archive first with `sha256sum -c SHA256SUMS`
inside its backup directory. Extract to a new staging directory, never directly over a
running service. Inspect `garuda-sdk-deploy/` and `systemd/` in staging. Stop `garuda-mono`,
rename its altered deployment aside, restore the staged deployment to its original path,
restore unit/drop-ins only if changed, run `systemctl daemon-reload`, and start the service.
Verify `/api/state`, camera feed, gesture controls and deployed model availability.

No restore was performed during preservation. The baseline stayed running throughout.
