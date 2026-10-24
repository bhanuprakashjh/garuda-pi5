import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "interfaces"))  # garuda_interfaces
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))                  # cmd_channel (sibling)

from cmd_channel import CmdServer, CmdClient
from garuda_interfaces.tracked_objects import LockCommand


def test_poll_skips_malformed(tmp_path):
    import socket
    sock = str(tmp_path / "m.sock")
    srv = CmdServer(sock)
    raw = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    raw.sendto(b"garbage not json", sock)            # malformed datagram
    CmdClient(sock).send(LockCommand("unlock"))      # valid one after it
    assert [c.cmd for c in srv.poll()] == ["unlock"]  # malformed dropped, valid kept, no crash
    raw.close(); srv.close()


def test_send_poll(tmp_path):
    sock = str(tmp_path / "cmd.sock")
    srv = CmdServer(sock)
    assert srv.poll() == []                      # non-blocking, empty
    cli = CmdClient(sock)
    cli.send(LockCommand("lock", x=1, y=2))
    cli.send(LockCommand("unlock"))
    got = srv.poll()
    assert [c.cmd for c in got] == ["lock", "unlock"]
    assert srv.poll() == []                       # consumed once
    srv.close()
