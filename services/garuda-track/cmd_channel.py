from __future__ import annotations
import os, socket
from garuda_interfaces.tracked_objects import LockCommand


class CmdServer:
    def __init__(self, path: str):
        self.path = str(path)
        try: os.unlink(self.path)
        except FileNotFoundError: pass
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.s.bind(self.path); self.s.setblocking(False)

    def poll(self):
        out = []
        while True:
            try:
                data = self.s.recv(4096)
            except BlockingIOError:
                break
            try:
                out.append(LockCommand.from_json(data))
            except (ValueError, KeyError, TypeError):   # ignore a stray/malformed datagram, keep the rest
                continue
        return out

    def close(self):
        self.s.close()
        try: os.unlink(self.path)
        except FileNotFoundError: pass


class CmdClient:
    def __init__(self, path: str):
        self.path = str(path); self.s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)

    def send(self, cmd: LockCommand):
        self.s.sendto(cmd.to_json(), self.path)
