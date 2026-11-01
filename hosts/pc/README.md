# Isolated ROS 2 / PX4 / Gazebo flight lab

The original Pi demo is the home view; switch to the PX4 flight lab in the same
browser window. The Pi camera remains the primary image. This is a PC simulator
integration; the working Pi deployment remains independently available.

- [Complete installation and user guide](../../docs/DEMO_USER_GUIDE.md): packages,
  pinned builds, setup paths, Pi preparation, gestures, replay and troubleshooting.
- [Implementation handover](../../docs/DEMO_HANDOVER.md): feature history,
  architecture, test evidence and remaining limits.
- [Baseline isolation and recovery](../../docs/px4-demo-isolation.md).
- [GitHub snapshot update plan](../../docs/GARUDA_PI5_UPDATE_PLAN.md).
- [Measured results](../../bench/results/2026-11-01-pc-visual/README.md) and
  [environment versions](../../bench/versions.md).

## Start an already installed lab

From this checkout root:

```bash
GARUDA_SIM=1 python3 hosts/pc/lab_launcher.py
```

Open **http://127.0.0.1:8100/**. Switch to PX4 flight lab, review
**Setup / paths for this computer**, then press **Start PX4 lab + Gazebo + QGC**.
This starts components without arming. Wait for fresh GROUND/disarmed telemetry.
The launcher requires only standard-library Python, but flight startup requires
all dependencies in the user guide. Path discovery is not package installation.

Select a person in the main Pi camera, Takeoff, then Follow or Orbit. Enable
Flight gestures to request pose mode, then reselect if detector switching changed
IDs. The selected person's one raised arm requests takeoff/follow, T-pose orbit,
crossed arms land, and both hands up Hold. Hold is immediate; other commands need
1.2 seconds of fresh observations. Only live Pi mode supports these gestures.

Land and confirm disarmed before changing camera sources or stopping:

```bash
python3 hosts/pc/lab_processes.py stop
```

Then close dedicated Gazebo/QGC windows. Closing owned Gazebo ends its PX4 session;
Start refreshes controller/DDS state for a new session. For stale telemetry after
an interrupted session, **Reset simulation · ground aircraft** discards that
simulated flight and clears selection/gesture authority. Wait for fresh telemetry,
reselect and enable gestures again. The Pi and QGC are not reset.

The fallback remains **http://10.42.0.128:8098/demo** (use your configured Pi address).
The original Pi detector mode is shared: explicitly enabling PX4 gestures can
select pose mode, but PC integration does not replace Pi files or packages.
Switching web views does not stop a flight. Native Gazebo/QGC windows are separate
from the single browser window.

## Scope of the demonstration

Pi-camera follow uses schematic image-center/person-size mapping, not validated
metric localization. Drone-camera mode uses Gazebo RGBD pixels and PC CPU YOLOX;
recorded footage is evaluation-only with flight controls disabled. Neither PC
path measures Hailo throughput. Returning-person matching in the PX4 lab uses
appearance, not face confirmation. The original demo has optional face matching.
A complete live orbit and full physical gesture/return sequence remain test work;
see the handover rather than treating historical partial traces as passes.

## Next things to do — saved 2026-11-01

Requested backlog for the booth demo. These items are planned, not implemented.
Start with the first three, in this order:

- [ ] **Clear gesture feedback:** draw the pose skeleton, highlight the joints
  used for detection, show the hold/countdown, and explain detection problems
  such as a wrist outside the frame.
- [ ] **Readiness and recovery:** extend the startup panel to show camera, Hailo,
  pose detection, ROS connection, PX4, Gazebo and QGC readiness separately.
  Add a Fix connection action for recoverable failures without restarting an
  active flight.
- [ ] **Guided Follow me challenge:** lead visitors through selecting themselves,
  raising a hand, walking, making a T-pose to orbit, and crossing arms to land.
  Display the current instruction and mark each verified step complete.

Then:

- [ ] **Visible target-loss demonstration:** explain Hold when the target hides;
  show returning-person matching restoring the lock, or request confirmation
  when identity is uncertain.
- [ ] **Measured performance comparison:** run the same recorded footage through
  supported processing paths and compare measured latency, detection coverage
  and tracking stability. Label hardware, settings and processing paths; use a
  controlled comparison and do not present PC results as Hailo results.
- [ ] **Automatic presentation mode:** explain the demo during inactivity, invite
  arriving visitors to select themselves, and reset after landing and inactivity.
  Never reset an active flight or select/arm a visitor automatically.
- [ ] **Portable setup wizard:** extend existing discovery and saved paths with
  version validation, a Pi connection test, and named configurations for each
  computer.

Preserve the original Pi demo as the fallback. All PX4 flight work remains
simulation-only. Record validation before marking an item complete.
