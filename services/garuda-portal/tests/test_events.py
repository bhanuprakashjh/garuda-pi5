import sys, pathlib, threading, time, socket, queue as queue_module
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from events import EventBus
from camera_stream import split_mjpeg, CameraStream
from portal import mjpeg_client_loop, sse_client_loop

def test_bus_fanout():
    bus = EventBus()
    q1, q2 = bus.subscribe(), bus.subscribe()
    bus.publish("health", {"x": 1})
    assert q1.get_nowait()["kind"] == "health"
    assert q2.get_nowait()["data"] == {"x": 1}

def test_split_mjpeg_extracts_frames():
    junk = b"xx"
    f1 = b"\xff\xd8\xff" + b"A" * 5 + b"\xff\xd9"
    f2 = b"\xff\xd8\xff" + b"B" * 3 + b"\xff\xd9"
    frames, rest = split_mjpeg(junk + f1 + f2 + b"\xff\xd8\xffpartial")
    assert frames == [f1, f2]
    assert rest.startswith(b"\xff\xd8\xff")

def test_mjpeg_client_loop_detects_peer_disconnect():
    """Test mjpeg_client_loop exits cleanly when peer closes."""
    # Create a socketpair for client-server simulation
    client_sock, server_sock = socket.socketpair()
    client_wfile = client_sock.makefile("wb")

    # Create a stub CameraStream with no frames
    stream = CameraStream(cmd=["sleep", "60"])  # never produces frames
    stream.start()

    # Run mjpeg_client_loop in a thread, close the server end -> should exit
    def run_loop():
        mjpeg_client_loop(client_wfile, client_sock, stream, max_wait=0.5)

    loop_thread = threading.Thread(target=run_loop)
    loop_thread.start()

    # Give loop time to start waiting, then close server socket (peer gone)
    time.sleep(0.3)
    server_sock.close()

    # Loop should detect disconnect and exit within ~2 ticks (2 * 0.5s timeout)
    loop_thread.join(timeout=2.0)
    assert not loop_thread.is_alive(), "mjpeg_client_loop did not exit on peer disconnect"

    stream.stop()

def test_sse_client_loop_keep_alive_on_queue_empty():
    """Test sse_client_loop writes keep-alive comment and continues on queue.Empty timeout."""
    writes = []
    unsub_calls = [0]

    class FakeWFile:
        def __init__(self):
            self.write_count = 0

        def write(self, data):
            writes.append(data)
            self.write_count += 1
            # After 2 keep-alive writes (b": ka\n\n"), raise to end loop naturally
            if self.write_count >= 2 and data == b": ka\n\n":
                raise BrokenPipeError("test: exit after N keep-alives")

        def flush(self):
            pass

    q = queue_module.Queue()  # Empty queue
    wfile = FakeWFile()

    def unsub_recorder():
        unsub_calls[0] += 1

    # Call real sse_client_loop end-to-end with short timeout (0.1s per tick)
    sse_client_loop(wfile, q, unsub_recorder, timeout_s=0.1)

    # Assert: (a) at least 2 keep-alive writes
    ka_count = sum(1 for w in writes if w == b": ka\n\n")
    assert ka_count >= 2, f"Expected >=2 keep-alive writes, got {ka_count}"

    # Assert: (b) function returned (no hang)
    # (Already returned if we reach here)

    # Assert: (c) unsubscribe called exactly once
    assert unsub_calls[0] == 1, f"Expected unsubscribe called once, got {unsub_calls[0]}"

def test_sse_client_loop_exits_on_write_error():
    """Test sse_client_loop exits when wfile raises BrokenPipeError."""
    call_count = [0]

    class FakeWFile:
        def write(self, data):
            call_count[0] += 1
            if call_count[0] == 2:
                raise BrokenPipeError("peer closed")
        def flush(self):
            pass

    q = queue_module.Queue()
    q.put({"kind": "test", "data": {}})
    q.put({"kind": "test2", "data": {}})
    wfile = FakeWFile()
    unsubscribed = [False]

    def unsub():
        unsubscribed[0] = True

    sse_client_loop(wfile, q, unsub)
    assert unsubscribed[0], "unsubscribe not called"

def test_camera_stream_stop_prompt():
    """Test CameraStream.stop() causes reader thread to exit within 1.0s."""
    stream = CameraStream(cmd=["sleep", "60"])
    stream.start()
    time.sleep(0.1)
    stream.stop()
    stream._thread.join(timeout=1.0)
    assert not stream._thread.is_alive(), "Reader thread did not exit within 1.0s"

def test_camera_stream_retry_on_exec_failure():
    """Test CameraStream retries on binary not found, stays alive."""
    stream = CameraStream(cmd=["/nonexistent-binary-garuda-test"])
    stream.start()
    time.sleep(0.3)
    # Thread should still be alive (retrying after OSError backoff)
    assert stream._thread.is_alive(), "Reader thread exited on exec failure (should retry)"
    stream.stop()
    stream._thread.join(timeout=1.0)

def test_camera_stream_start_is_idempotent_while_running():
    """A second start() while the reader is already running must not spawn a
    second thread/subprocess — this is what makes acquire()/release() (below)
    safe to ref-count: acquire() only calls start() on the 0->1 transition,
    but nothing stops another caller (or a racing acquire()) from calling
    start() again while already running."""
    stream = CameraStream(cmd=["sleep", "60"])
    stream.start()
    t1 = stream._thread
    time.sleep(0.05)
    stream.start()
    assert stream._thread is t1, "start() while running relaunched the reader"
    stream.stop()
    t1.join(timeout=1.0)
    assert not t1.is_alive()

def test_camera_stream_acquire_release_ref_counts_the_reader():
    """acquire()/release() mirror this repo's existing viewer-counting pattern
    (garuda_mono.py _viewers, camarray_depth_explorer.py _viewers): the reader
    (and thus rpicam-vid, which holds the CM3) only runs while >=1 viewer is
    attached, and N concurrent viewers share one running process rather than
    each spawning their own."""
    stream = CameraStream(cmd=["sleep", "60"])
    assert stream._thread is None

    stream.acquire()  # viewer #1 -> starts the reader
    assert stream._thread is not None and stream._thread.is_alive()
    t1 = stream._thread

    stream.acquire()  # viewer #2 -> must NOT relaunch
    assert stream._thread is t1

    stream.release()  # viewer #2 leaves -> one viewer still attached, stays up
    assert stream._thread is t1 and stream._thread.is_alive()

    stream.release()  # last viewer leaves -> released
    stream._thread.join(timeout=1.0)
    assert not stream._thread.is_alive(), "reader stayed alive after the last viewer released"

def test_camera_stream_release_without_acquire_is_a_harmless_noop():
    """A stray release() with no matching acquire() (a bug in a caller) must
    not underflow the counter or start anything."""
    stream = CameraStream(cmd=["sleep", "60"])
    stream.release()
    assert stream._thread is None
