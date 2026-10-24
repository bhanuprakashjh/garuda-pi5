import json, queue, threading, time

class EventBus:
    def __init__(self):
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q = queue.Queue(maxsize=100)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._subs: self._subs.remove(q)

    def publish(self, kind: str, data: dict):
        ev = {"kind": kind, "data": data, "t_ns": time.monotonic_ns()}
        with self._lock:
            for q in list(self._subs):
                try: q.put_nowait(ev)
                except queue.Full: pass
