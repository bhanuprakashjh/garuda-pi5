import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "hosts" / "pi5"))
from camarray_replay import parse_ftrace_writes

# Representative kernel ftrace i2c lines (writes to the ArduChip at 0x51 on bus 0,
# interleaved with a reply and an unrelated device — the parser must pick only the
# 0x51 writes, in order, and drop replies/reads/empty payloads).
SAMPLE = """
     rpicam-hello-1234  [001] ..... 12.001: i2c_write: i2c-0 #0 a=051 f=0000 l=3 [02-00-01]
     rpicam-hello-1234  [001] ..... 12.002: i2c_reply: i2c-0 #0 a=051 f=0001 l=1 [82]
     rpicam-hello-1234  [001] ..... 12.003: i2c_write: i2c-0 #0 a=051 f=0000 l=4 [03-00-12-34]
     kworker-9          [002] ..... 12.004: i2c_write: i2c-1 #0 a=010 f=0000 l=2 [aa-bb]
     rpicam-hello-1234  [001] ..... 12.005: i2c_write: i2c-0 #0 a=051 f=0000 l=0 []
""".strip().splitlines()


def test_parse_extracts_only_target_writes_in_order():
    writes = parse_ftrace_writes(SAMPLE, addr=0x51)
    assert writes == [[0x02, 0x00, 0x01], [0x03, 0x00, 0x12, 0x34]]

def test_parse_respects_source_bus_filter():
    # a=010 is on i2c-1; restricting to bus 0 must exclude it (and it's a different addr anyway)
    assert parse_ftrace_writes(SAMPLE, addr=0x10, bus=0) == []
    assert parse_ftrace_writes(SAMPLE, addr=0x10, bus=1) == [[0xaa, 0xbb]]

def test_parse_empty_when_no_match():
    assert parse_ftrace_writes(SAMPLE, addr=0x60) == []
    assert parse_ftrace_writes([], addr=0x51) == []
