import sys, pathlib, math, types
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "interfaces"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from follow_controller import compute, FollowConfig
from sim_kinematics import DroneState, step, target_bbox

def test_follow_loop_converges_and_holds():
    cfg = FollowConfig()
    # drone starts at origin facing +x; target off to the side and far away
    st = DroneState(x=0.0, y=0.0, z=0.0, yaw=0.0)
    target_xy = (6.0, 4.0)
    errs = []
    for i in range(400):                       # 400 * 0.05s = 20s
        tgt = target_bbox(st, target_xy, cfg)
        cmd = compute(tgt, (1, 1), cfg)
        st = step(st, cmd, dt=0.05)
        bx, by, bw, bh = tgt.bbox
        cx = bx + bw / 2
        errs.append(abs(cx - 0.5) + abs(cfg.desired_height_frac - bh))
    # framing error must shrink and stay small (converged + held)
    assert errs[-1] < 0.1
    assert sum(errs[-20:]) / 20 < 0.1
    assert errs[-1] < errs[0]                   # actually improved

def test_target_behind_is_not_visible():
    # a target behind the drone -> bbox marked stale; the real controller holds
    # (zero velocities, invalid) rather than inventing motion toward an unseen
    # target. This test only proves the hold; it does NOT claim recovery.
    cfg = FollowConfig()
    st = DroneState(x=0.0, y=0.0, z=0.0, yaw=math.pi)  # facing -x, target at +x
    tgt = target_bbox(st, (6.0, 0.0), cfg)
    assert tgt.time_since_update > cfg.max_stale_frames  # behind camera -> not visible
    cmd = compute(tgt, (1, 1), cfg)
    assert not cmd.valid and cmd.yaw_rate == 0.0 and cmd.forward_vel == 0.0

def test_reacquires_when_target_enters_fov():
    # A target just OUTSIDE the horizontal FOV (not behind, at the edge) is
    # stale, and compute() correctly holds -- it must not invent a yaw_rate to
    # go searching, that would be the controller chasing a target it cannot
    # see. Bringing the target into view is therefore an EXTERNAL search
    # behavior, driven here by the test harness (not follow_controller.py),
    # exactly as a real system's search/scan mode would sit outside the
    # visual-servo controller. Once target_bbox reports the target visible
    # again (time_since_update == 0), the real compute() must resume valid
    # tracking and the real closed loop must then reduce framing error.
    cfg = FollowConfig()
    st = DroneState(x=0.0, y=0.0, z=0.0, yaw=0.0)
    target_xy = (3.0, 3.0)          # bearing ~0.785 rad > hfov/2 (0.6) -> just outside FOV

    tgt = target_bbox(st, target_xy, cfg)
    assert tgt.time_since_update > cfg.max_stale_frames        # starts unseen
    cmd0 = compute(tgt, (1, 1), cfg)
    assert not cmd0.valid                                      # controller holds, doesn't chase

    # external search sweep (test-only, NOT compute()/follow_controller.py):
    # a fixed slow yaw scan until the simulated camera reacquires the target.
    search_cmd = types.SimpleNamespace(yaw_rate=0.3, forward_vel=0.0, vertical_vel=0.0)
    for _ in range(20):
        tgt = target_bbox(st, target_xy, cfg)
        if tgt.time_since_update == 0:
            break
        st = step(st, search_cmd, dt=0.1)
    assert tgt.time_since_update == 0                          # target re-acquired

    cmd = compute(tgt, (1, 1), cfg)
    assert cmd.valid and cmd.reason == "tracking"              # real controller resumes tracking

    bx, by, bw, bh = tgt.bbox
    err0 = abs((bx + bw / 2) - 0.5) + abs(cfg.desired_height_frac - bh)

    # now drive the REAL closed loop (compute()) and confirm it actually
    # reduces framing error from the just-reacquired state.
    for _ in range(100):
        tgt = target_bbox(st, target_xy, cfg)
        cmd = compute(tgt, (1, 1), cfg)
        st = step(st, cmd, dt=0.05)
    bx, by, bw, bh = tgt.bbox
    err1 = abs((bx + bw / 2) - 0.5) + abs(cfg.desired_height_frac - bh)
    assert err1 < err0                                          # loop actually improved framing
