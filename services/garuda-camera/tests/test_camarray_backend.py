import io, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "hosts" / "pi5"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "interfaces"))
from camarray_backend import read_exact


class _Dribble(io.RawIOBase):
    """A stream that returns at most `chunk` bytes per read — exercises the
    short-read loop the way a real pipe from v4l2-ctl does."""
    def __init__(self, data, chunk):
        self.data = data
        self.pos = 0
        self.chunk = chunk
    def read(self, n=-1):
        end = min(self.pos + min(self.chunk, n if n > 0 else self.chunk), len(self.data))
        out = self.data[self.pos:end]
        self.pos = end
        return out


def test_read_exact_assembles_across_short_reads():
    data = bytes(range(256)) * 4  # 1024 bytes
    s = _Dribble(data, chunk=7)   # never returns a full frame in one read
    assert read_exact(s, 1024) == data

def test_read_exact_none_on_eof():
    s = _Dribble(b"\x01\x02\x03", chunk=2)
    assert read_exact(s, 10) is None          # stream ends before n bytes

def test_read_exact_exact_boundary():
    s = _Dribble(b"abcd", chunk=4)
    assert read_exact(s, 4) == b"abcd"
    assert read_exact(s, 1) is None           # nothing left
