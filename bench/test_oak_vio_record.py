import threading
import time

from oak_vio_record import FrameWriter


def test_slow_disk_does_not_block_the_caller_and_everything_is_written_in_order():
    written, release = [], threading.Event()

    def slow(path, image):
        release.wait(2.0)                                    # the "SD card" stalls until released
        written.append((path, image))

    w = FrameWriter(slow, limit=100)
    t0 = time.monotonic()
    for i in range(40):                                      # more than the 16-frame device queue
        w.put(f"left/{i:08d}.png", i)
    assert time.monotonic() - t0 < 0.5                       # the capture loop was not held up
    assert w.max_backlog >= 39
    release.set(); w.close()
    assert [p for p, _ in written] == [f"left/{i:08d}.png" for i in range(40)]
    assert [im for _, im in written] == list(range(40)) and w.errors == []


def test_write_errors_are_counted_not_swallowed_and_do_not_stop_the_writer():
    done = []

    def flaky(path, image):
        if image == 1:
            raise OSError("disk full")
        done.append(image)

    w = FrameWriter(flaky)
    for i in range(3):
        w.put("x", i)
    w.close()
    assert done == [0, 2] and len(w.errors) == 1 and "disk full" in w.errors[0] and w.errors[0].startswith("x: ")


def test_a_write_that_returns_false_is_an_error_with_its_filename():
    """astra-0153: cv2.imwrite returns False instead of raising."""
    w = FrameWriter(lambda path, image: image != 1)
    for i in range(3):
        w.put(f"left/{i}.png", i)
    w.close()
    assert w.errors == ["left/1.png: write returned False"]


def test_time_the_caller_waits_on_a_full_backlog_is_reported():
    release = threading.Event()
    w = FrameWriter(lambda path, image: release.wait(2.0), limit=1)
    w.put("a", 0); w.put("b", 1)                             # one being written, one queued
    threading.Timer(0.2, release.set).start()
    w.put("c", 2)                                            # backlog full: waits for the release
    w.close()
    assert w.blocked_s >= 0.1 and w.errors == []


def test_log_lines_go_to_disk_from_the_writer_thread_in_order_with_the_images(tmp_path):
    """So an interrupted session keeps its frames.jsonl / imu.jsonl up to the last hand-over."""
    order = []
    w = FrameWriter(lambda path, image: order.append(path))
    with (tmp_path / "frames.jsonl").open("w") as f:
        w.put("left/0.png", 0); w.text(f, "line 0\n"); w.text(f, "")      # an empty chunk is not queued
        w.put("left/1.png", 1); w.text(f, "line 1\nline 2\n")
        deadline = time.monotonic() + 2.0
        while (tmp_path / "frames.jsonl").read_text() != "line 0\nline 1\nline 2\n" and time.monotonic() < deadline:
            time.sleep(0.01)                                 # on disk BEFORE close: flushed per chunk
        assert (tmp_path / "frames.jsonl").read_text() == "line 0\nline 1\nline 2\n"
        w.close()
    assert order == ["left/0.png", "left/1.png"] and w.errors == []


def test_a_failed_log_write_is_reported_with_the_file_name(tmp_path):
    f = (tmp_path / "imu.jsonl").open("w"); f.close()        # writing to a closed file fails
    w = FrameWriter(lambda path, image: True)
    w.text(f, "x\n"); w.close()
    assert len(w.errors) == 1 and "imu.jsonl" in w.errors[0]
