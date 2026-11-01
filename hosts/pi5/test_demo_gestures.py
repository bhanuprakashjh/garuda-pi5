from mono_gestures import track_gestures, GestureFSM, classify_demo_pose


def person(tid, name=None, coasting=False):
    k = [[50, 90, 1] for _ in range(17)]
    k[0] = [50, 20, 1]; k[5] = [40, 60, 1]; k[6] = [60, 60, 1]
    if name == 'stop':
        k[9] = [40, 0, 1]; k[10] = [60, 0, 1]
    elif name == 'point_left':
        k[9] = [0, 60, 1]
    elif name == 'follow_me':
        k[9] = [40, 0, 1]
    return (tid, 'person', 0, 0, 100, 200, .9, coasting, k)


def test_per_person_gestures_do_not_bleed_between_subjects():
    clock = [1.0]
    fsms = {i: GestureFSM(clock=lambda: clock[0]) for i in (1, 2)}
    tracks = [person(1, 'stop'), person(2, 'point_left')]
    track_gestures(tracks, fsms); clock[0] += 1.1
    out = track_gestures(tracks, fsms)
    assert out[1]['name'] == 'stop' and out[2]['name'] == 'point_left'
    clock[0] += .5
    track_gestures([person(1, coasting=True)], fsms)
    clock[0] += .5
    assert track_gestures([person(1, coasting=True)], fsms)[1]['name'] is None
    assert 2 not in fsms


def test_t_pose_circles_and_crossed_arms_land():
    k = person(1)[8]
    k[9] = [0, 60, 1]; k[10] = [100, 60, 1]
    assert classify_demo_pose(k, 100, 200) == 'circle'
    k[9] = [65, 85, 1]; k[10] = [35, 85, 1]
    assert classify_demo_pose(k, 100, 200) == 'land'
    # Mirrored coordinates must preserve the same commands.
    for p in k:
        p[0] = 100-p[0]
    assert classify_demo_pose(k, 100, 200) == 'land'


def test_ambiguous_launch_and_low_quality_two_arm_gestures_rejected():
    k = person(1, 'follow_me')[8]
    k[10][2] = .1
    assert classify_demo_pose(k, 100, 200) is None
    k = person(1)[8]
    assert classify_demo_pose(k, 100, 200) is None
    k[9] = [65, 85, 1]; k[10] = [35, 85, .1]
    assert classify_demo_pose(k, 100, 200) is None


def test_circle_is_debounced_before_command():
    now = [1.0]
    fsms = {1: GestureFSM(clock=lambda: now[0])}
    t = person(1)
    t[8][9] = [0, 60, 1]; t[8][10] = [100, 60, 1]
    assert track_gestures([t], fsms)[1]['name'] is None
    now[0] += 1.1
    assert track_gestures([t], fsms)[1]['name'] == 'circle'
def test_t_pose_uses_shoulders_not_expanded_box_and_allows_slight_slope():
    k = person(1)[8]
    k[5] = [200, 100, 1]; k[6] = [300, 100, 1]
    k[9] = [110, 125, 1]; k[10] = [390, 75, 1]
    assert classify_demo_pose(k, 350, 200) == 'circle'
    assert classify_demo_pose(k, 500, 200) == 'circle'
    k[9][1] = 220; k[10][1] = 220
    assert classify_demo_pose(k, 350, 200) != 'circle'
