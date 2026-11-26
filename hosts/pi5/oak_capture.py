"""OAK RGB capture plugin. Local raw-frame IPC, no model/preprocessing changes."""
import sys
import threading
import time
from pathlib import Path
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'interfaces'))
from garuda_interfaces.capture import ICaptureBackend
from garuda_interfaces.messages import FrameMeta


class OakRGBBackend(ICaptureBackend):
    def configure(self, width, height, fps, pixel_format='BGR888', trigger_external=False):
        if (width, height, fps, pixel_format, trigger_external) != (1280, 720, 15, 'BGR888', False):
            raise ValueError('OAK bench stream is fixed at 1280x720 BGR888, 15 fps')
        self._stop = threading.Event()

    def start(self, on_frame):
        self._thread = threading.Thread(target=self._run, args=(on_frame,), daemon=True)
        self._thread.start()

    def _run(self, on_frame):
        previous = None
        last_error = 0
        while not self._stop.is_set():
            try:
                after = previous[1] if previous else -1
                with urlopen(f'http://127.0.0.1:8096/frame?after={after}', timeout=3) as response:
                    h = response.headers
                    seq, epoch = int(h['X-Oak-Sequence']), h['X-Oak-Epoch']
                    capture_ns = int(h['X-Oak-Capture-Ns'])
                    if (int(h['X-Oak-Width']), int(h['X-Oak-Height']), h['X-Oak-Format']) != (1280,720,'BGR888'):
                        raise ValueError('Unexpected OAK image geometry/format')
                    if int(h['Content-Length']) != 1280*720*3:
                        raise ValueError('Unexpected OAK image payload size')
                    pixels = response.read(1280*720*3+1)
                if len(pixels) != 1280*720*3:
                    raise ValueError('Truncated OAK frame')
                if not 0 <= time.monotonic_ns()-capture_ns < 1_000_000_000:
                    raise ValueError('Stale OAK frame or incompatible clock')
                if previous == (epoch, seq):
                    self._stop.wait(.02)
                    continue
                previous = epoch, seq
                meta = FrameMeta(seq, capture_ns, 'mono', f'oak-rgb:{epoch}',1280,720,'BGR888')
                on_frame(meta, memoryview(pixels))
            except Exception as exc:
                if time.monotonic()-last_error > 5:
                    print('OAK capture waiting:', str(exc), flush=True)
                    last_error = time.monotonic()
                self._stop.wait(.5)

    def stop(self):
        self._stop.set()
        if hasattr(self, '_thread'):
            self._thread.join(timeout=4)


class OakPairBackend(ICaptureBackend):
    """RGB + paired stereo depth from the owner process's /pair endpoint (contract v1).

    Delivers RGB through the ordinary on_frame callback, so detection is unchanged, and the
    whole validated Pair (depth may be None) through on_pair. A contract breach, a stale
    frame or an unavailable owner drops that response and retries; it never delivers a
    partial or stale frame, and missing depth never stops RGB delivery."""

    STALE_NS = 1_000_000_000
    BASE = 'http://127.0.0.1:8096'

    def __init__(self, on_pair=None, on_epoch_change=None, want_depth=lambda: True):
        self._on_pair = on_pair
        self._on_epoch_change = on_epoch_change
        self._want_depth = want_depth
        self._stop = threading.Event()
        self.counters = {'delivered': 0, 'no_new_frame': 0, 'rejected': 0, 'stale': 0,
                         'unavailable': 0, 'epoch_changes': 0, 'depth_absent': 0}

    def configure(self, width, height, fps, pixel_format='BGR888', trigger_external=False):
        if (width, height, fps, pixel_format, trigger_external) != (1280, 720, 15, 'BGR888', False):
            raise ValueError('OAK bench stream is fixed at 1280x720 BGR888, 15 fps')
        self._size = (width, height)

    def start(self, on_frame):
        self._thread = threading.Thread(target=self._run, args=(on_frame,), daemon=True)
        self._thread.start()

    def poll_once(self, cursor, on_frame):
        """One request. Returns a counter name describing what happened."""
        from urllib.error import HTTPError, URLError
        from oak_pair import PairError, read_pair, CONTENT_TYPE
        try:
            with urlopen(self.BASE + cursor.query(bool(self._want_depth())), timeout=3) as response:
                if response.status == 204:
                    return 'no_new_frame'
                if response.headers.get('Content-Type') != CONTENT_TYPE:
                    return 'rejected'
                pair = read_pair(response, int(response.headers['Content-Length']), self._size)
        except HTTPError as exc:
            return 'unavailable' if exc.code == 503 else 'rejected'
        except (URLError, OSError):
            return 'unavailable'
        except (PairError, KeyError, ValueError, TypeError):
            return 'rejected'
        rgb = pair.meta['rgb']
        if not 0 <= time.monotonic_ns() - rgb['capture_host_ns'] < self.STALE_NS:
            return 'stale'
        try:
            outcome = cursor.accept(pair)
        except PairError:
            return 'rejected'
        if outcome == 'epoch_changed':
            self.counters['epoch_changes'] += 1
            if self._on_epoch_change:
                self._on_epoch_change(pair.meta['epoch'])
        if pair.depth_mm is None:
            self.counters['depth_absent'] += 1
        meta = FrameMeta(rgb['seq'], rgb['capture_host_ns'], 'mono',
                         f"oak-rgb:{pair.meta['epoch']}", self._size[0], self._size[1], 'BGR888')
        if self._on_pair:
            self._on_pair(pair)        # before on_frame: depth is stored before the frame is visible
        on_frame(meta, memoryview(pair.rgb))
        return 'delivered'

    def _run(self, on_frame):
        from oak_pair import PairCursor
        cursor = PairCursor()
        last_report = 0
        while not self._stop.is_set():
            outcome = self.poll_once(cursor, on_frame)
            self.counters[outcome] += 1
            if outcome == 'delivered':
                continue
            if outcome == 'no_new_frame':
                self._stop.wait(.005)      # the owner already waited; this only stops a hot spin
                continue
            if time.monotonic() - last_report > 5:
                print('OAK pair capture waiting:', outcome, flush=True)
                last_report = time.monotonic()
            self._stop.wait(.5)

    def stop(self):
        self._stop.set()
        if hasattr(self, '_thread'):
            self._thread.join(timeout=4)
