"""Guided mono intrinsics: coverage grid logic (pure) + OpenCV corner/solve.

cv2 is imported lazily inside every function that needs it (never at module
top). That keeps this module importable on machines without OpenCV — portal.py
and the rest of the test suite must not fail to import just because
calibration.py exists. numpy is imported at module top: it is a hard system
dependency already (present on both the dev PC and the Pi), unlike cv2.
"""
from __future__ import annotations
import datetime, json, pathlib, re, threading
import numpy as np

# sensor becomes a filename directly in solve_and_save; guard against path
# traversal (e.g. sensor="../../../etc/passwd"). The charset alone would
# still accept ".." (both chars are individually allowed), so it is rejected
# explicitly too.
_SENSOR_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

def _valid_sensor_name(sensor: str) -> bool:
    return bool(sensor) and ".." not in sensor and bool(_SENSOR_NAME_RE.match(sensor))

class CoverageGrid:
    """3x3 (default) zone coverage tracker for the capture wizard: caps how
    many samples count per zone so a lazy operator waving the board in one
    spot can't fake full coverage."""
    def __init__(self, rows=3, cols=3, per_zone=3):
        self.rows, self.cols, self.per_zone = rows, cols, per_zone
        self.counts = [[0] * cols for _ in range(rows)]

    def add(self, cx: float, cy: float) -> bool:
        c = min(int(cx * self.cols), self.cols - 1)
        r = min(int(cy * self.rows), self.rows - 1)
        if self.counts[r][c] >= self.per_zone:
            return False
        self.counts[r][c] += 1
        return True

    @property
    def complete(self) -> bool:
        return all(n >= self.per_zone for row in self.counts for n in row)

def find_corners(gray, pattern=(9, 6)):
    """Detect a chessboard in `gray`. Returns (found, normalized_centroid)."""
    import cv2
    found, corners = cv2.findChessboardCorners(gray, pattern,
        flags=cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE)
    if not found:
        return False, (0.0, 0.0)
    c = corners.reshape(-1, 2).mean(axis=0)
    h, w = gray.shape[:2]
    return True, (float(c[0] / w), float(c[1] / h))

def find_corners_full(gray, pattern=(9, 6)):
    """Like find_corners but also returns the raw (N,1,2) corner array, which
    the capture flow needs to accumulate calibrateCamera inputs. Deliberately
    duplicates the cv2 call rather than delegating to find_corners, so
    find_corners's body stays byte-for-byte the brief's verbatim version."""
    import cv2
    found, corners = cv2.findChessboardCorners(gray, pattern,
        flags=cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE)
    if not found:
        return False, None, (0.0, 0.0)
    c = corners.reshape(-1, 2).mean(axis=0)
    h, w = gray.shape[:2]
    return True, corners, (float(c[0] / w), float(c[1] / h))

def object_points(pattern, square_size_m) -> np.ndarray:
    """Planar object points for a `pattern` = (cols, rows) chessboard with the
    given square size in metres. Same construction the calibration tests use."""
    cols, rows = pattern
    obj = np.zeros((cols * rows, 3), np.float32)
    obj[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2) * square_size_m
    return obj

def solve(objpoints, imgpoints, image_size) -> dict:
    import cv2
    try:
        rms, K, dist, _, _ = cv2.calibrateCamera(objpoints, imgpoints, image_size,
                                                 None, None)
    except Exception as e:
        # Degenerate/malformed inputs (mismatched point counts, bad shapes,
        # zero image size, ...) raise cv2.error rather than returning a
        # dict. Report it like any other action failure instead of letting
        # it crash the request handler.
        return {"ok": False, "detail": f"solve failed: {type(e).__name__}"}
    return {"rms": float(rms), "K": K.tolist(), "dist": dist.ravel().tolist()}

class CalibSession:
    """Guided-wizard state for one mono intrinsics session: pattern/square
    size, coverage grid, and the accumulated object/image point arrays that
    feed solve(). Instantiated once per portal process (main()'s
    `calib_session = CalibSession()`), reset() on every "Start session" click.

    portal.py's server is a ThreadingHTTPServer, so capture/solve/status/
    reset can all be invoked concurrently from the browser (e.g. a stray
    double-click, or a status poll landing mid-capture). self._lock guards
    every read/mutation of the shared state (grid, objpoints, imgpoints,
    image_size) so the two point-array appends in capture() always land
    together, and solve_and_save() never reads a torn snapshot — same
    convention as InferenceManager/EventBus's self._lock."""

    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self, pattern=(9, 6), square_size_m=0.025):
        with self._lock:
            self.pattern = tuple(pattern)
            self.square_size_m = square_size_m
            self.grid = CoverageGrid()
            self.objpoints = []
            self.imgpoints = []
            self.image_size = None

    def capture(self, stream) -> dict:
        """Decode stream.latest (a JPEG), find the board, and if it lands in
        a zone that still needs coverage, accumulate its points for solve()."""
        frame = stream.latest
        if frame is None:
            return {"ok": False, "detail": "no frame (camera busy or not started)"}

        import cv2
        arr = np.frombuffer(frame, dtype=np.uint8)
        gray = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
        if gray is None:
            return {"ok": False, "detail": "could not decode frame"}

        # Everything from here on touches shared state (pattern, grid,
        # objpoints, imgpoints, image_size) and must be atomic w.r.t. other
        # capture()/solve_and_save()/reset()/status() calls.
        with self._lock:
            self.image_size = (gray.shape[1], gray.shape[0])
            found, corners, centroid = find_corners_full(gray, self.pattern)
            if not found:
                return {"ok": True, "added": False, "corners_found": False,
                         "zone_counts": [row[:] for row in self.grid.counts],
                         "complete": self.grid.complete}

            added = self.grid.add(*centroid)
            if added:
                self.imgpoints.append(corners)
                self.objpoints.append(object_points(self.pattern, self.square_size_m))
            return {"ok": True, "added": added, "corners_found": True,
                     "zone_counts": [row[:] for row in self.grid.counts],
                     "complete": self.grid.complete}

    def solve_and_save(self, sensor: str, calib_dir="/home/garuda/garuda-calib") -> dict:
        """Solve intrinsics from the accumulated captures and persist a
        human-facing calibration record + append it to history.jsonl."""
        if not _valid_sensor_name(sensor):
            return {"ok": False, "detail": "invalid sensor name"}

        # Snapshot the shared state under the lock, then release it before
        # running calibrateCamera (slow-ish) so concurrent captures aren't
        # blocked for the duration of the solve.
        with self._lock:
            if len(self.imgpoints) < 9:
                return {"ok": False,
                         "detail": f"need at least 9 captures (have {len(self.imgpoints)})"}
            objpoints = list(self.objpoints)
            imgpoints = list(self.imgpoints)
            image_size = self.image_size
            pattern = self.pattern
            square_size_m = self.square_size_m

        result = solve(objpoints, imgpoints, image_size)
        if not result.get("ok", True):
            return result  # {"ok": False, "detail": "solve failed: ..."}

        # Wall clock deliberately: t_saved_utc is a persisted human-facing
        # record field, not a message timestamp (monotonic resets each boot).
        t_saved_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
        record = {
            "K": result["K"],
            "dist": result["dist"],
            "rms": result["rms"],
            "pattern": list(pattern),
            "square_size_m": square_size_m,
            "t_saved_utc": t_saved_utc,
        }

        out_dir = pathlib.Path(calib_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{sensor}.json").write_text(json.dumps(record, indent=2))
        with open(out_dir / "history.jsonl", "a") as f:
            f.write(json.dumps(record) + "\n")

        return {"ok": True, **result, "t_saved_utc": t_saved_utc,
                 "file": str(out_dir / f"{sensor}.json")}

    def status(self) -> dict:
        with self._lock:
            return {
                "pattern": list(self.pattern),
                "square_size_m": self.square_size_m,
                "zone_counts": [row[:] for row in self.grid.counts],
                "per_zone": self.grid.per_zone,
                "complete": self.grid.complete,
                "captures": len(self.imgpoints),
                "image_size": list(self.image_size) if self.image_size else None,
            }
