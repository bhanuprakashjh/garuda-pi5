#!/usr/bin/env python3
"""Replay a captured Arducam CamArray ArduChip init sequence on the Trixie bench.

Plan B (docs/camarray-i2c-capture.md): the ArduChip is a Pivariety-style controller
at i2c 0x51 on bus 0 (Pi pins 27/28). Arducam's stack works on Bookworm; we capture
the i2c writes it emits (kernel ftrace i2c events) and replay them here via
`i2ctransfer` — no libcamera swap, so the CM3 stack stays pristine.

Usage:
  # inspect what the trace would replay (no i2c touched):
  python3 camarray_replay.py --trace camarray_i2c_trace.txt --addr 0x51 --dry-run
  # actually replay to the ArduChip:
  sudo python3 camarray_replay.py --trace camarray_i2c_trace.txt --bus 0 --addr 0x51

The parser is pure + unit-tested (services/garuda-camera/tests/test_camarray_replay.py);
the replay shells out to i2ctransfer (i2c-tools). This is a scaffold — the WRITE FRAMING
(16-bit reg + 8/16-bit value vs raw byte blocks) is confirmed against the real captured
trace before trusting the replay; --dry-run prints exactly what will be sent.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from typing import List

# ftrace i2c_write line, e.g.:  "... i2c_write: i2c-0 #0 a=051 f=0000 l=3 [02-00-01]"
_WRITE_RE = re.compile(
    r"i2c_write:\s+i2c-(?P<bus>\d+)\s+#\d+\s+a=(?P<addr>[0-9a-fA-F]+)\s+"
    r"f=[0-9a-fA-F]+\s+l=(?P<len>\d+)\s+\[(?P<data>[0-9a-fA-F-]*)\]"
)


def parse_ftrace_writes(lines, addr: int, bus: int | None = None) -> List[List[int]]:
    """Extract the ordered list of i2c_write payloads (each a list of byte ints)
    addressed to `addr` (and `bus` if given) from kernel ftrace `i2c` event lines.
    Pure: no i2c/hardware access. Empty `[..]` writes and reply/read lines are skipped.
    """
    out: List[List[int]] = []
    for line in lines:
        m = _WRITE_RE.search(line)
        if not m:
            continue
        if int(m.group("addr"), 16) != addr:
            continue
        if bus is not None and int(m.group("bus")) != bus:
            continue
        data = m.group("data").strip()
        if not data:
            continue
        out.append([int(b, 16) for b in data.split("-") if b])
    return out


def _i2ctransfer(bus: int, addr: int, payload: List[int], dry_run: bool) -> None:
    # e.g. i2ctransfer -y 0 w3@0x51 0x02 0x00 0x01
    args = ["i2ctransfer", "-y", str(bus),
            f"w{len(payload)}@{hex(addr)}"] + [hex(b) for b in payload]
    if dry_run:
        print("  would run:", " ".join(args))
        return
    subprocess.run(args, check=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Replay captured ArduChip i2c init on Trixie")
    ap.add_argument("--trace", required=True, help="ftrace i2c capture from the Bookworm run")
    ap.add_argument("--addr", default="0x51", help="ArduChip i2c address (default 0x51)")
    ap.add_argument("--bus", type=int, default=0, help="i2c bus to replay on (default 0)")
    ap.add_argument("--src-bus", type=int, default=None,
                    help="only take writes captured on this bus (default: any)")
    ap.add_argument("--dry-run", action="store_true", help="print, do not touch i2c")
    args = ap.parse_args(argv)

    addr = int(args.addr, 16)
    with open(args.trace) as f:
        writes = parse_ftrace_writes(f.readlines(), addr, args.src_bus)
    if not writes:
        print(f"No i2c_write payloads to {hex(addr)} found in {args.trace}.", file=sys.stderr)
        print("If the driver used userspace /dev/i2c, capture with strace instead "
              "(see docs/camarray-i2c-capture.md).", file=sys.stderr)
        return 1
    print(f"{len(writes)} writes to {hex(addr)} on bus {args.bus}"
          f"{' (dry run)' if args.dry_run else ''}:")
    for w in writes:
        _i2ctransfer(args.bus, addr, w, args.dry_run)
    print("done." if not args.dry_run else "dry run complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
