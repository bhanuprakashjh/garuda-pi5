"""Health probes. All shell access goes through the injected `run` callable."""
from __future__ import annotations
import re, shutil, subprocess, time

WATCHED_UNITS = ["garuda-portal", "garuda-configd", "garuda-ptp4l-client", "garuda-mono"]

def shell_runner(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout

def _safe(run, cmd) -> str:
    try: return run(list(cmd))
    except Exception: return ""

def _cpu_util(sample_s: float = 0.12) -> dict:
    """Aggregate + per-core CPU utilisation (%) over a short /proc/stat delta.
    Stateless (safe for the several callers of collect_health) — the ~0.12s
    blocking sample runs inside the 2s health tick / an /api/health request.
    Returns {"cpu": <aggregate %>, "cpu0": %, "cpu1": %, ...}."""
    def snap():
        out = {}
        with open("/proc/stat") as f:
            for line in f:
                if not line.startswith("cpu"):
                    break
                p = line.split()
                v = [int(x) for x in p[1:8]]        # user nice system idle iowait irq softirq
                out[p[0]] = (v[3] + v[4], sum(v))   # (idle+iowait, total)
        return out
    a = snap(); time.sleep(sample_s); b = snap()
    res = {}
    for k, (ia, ta) in a.items():
        ib, tb = b.get(k, (ia, ta))
        dt = tb - ta
        res[k] = round(100.0 * (1 - (ib - ia) / dt), 1) if dt > 0 else 0.0
    return res

# PCIe per-lane usable bandwidth (GB/s) by link speed (GT/s): 8b/10b for Gen1-2,
# 128b/130b for Gen3+. Multiply by lane width for the link capacity.
_PCIE = {2.5: (1, 0.25), 5.0: (2, 0.5), 8.0: (3, 0.985), 16.0: (4, 1.969), 32.0: (5, 3.938)}

def _pcie_link() -> dict | None:
    """Hailo NPU PCIe link (gen / width / GB-per-s capacity) from sysfs — no sudo,
    static for a boot. This is the NPU's host<->device bandwidth ceiling; there is
    no non-intrusive per-transfer bandwidth counter, so throughput is estimated
    client-side from frame size x fps. Returns None if no Hailo device is found."""
    import glob
    for dev in glob.glob("/sys/bus/pci/devices/*"):
        try:
            with open(dev + "/vendor") as f:
                if f.read().strip().lower() != "0x1e60":   # Hailo vendor id
                    continue
            with open(dev + "/current_link_speed") as f:
                gts = float(f.read().split()[0])
            with open(dev + "/current_link_width") as f:
                width = int(f.read().strip())
        except Exception:
            continue
        gen, per_lane = _PCIE.get(gts, (None, 0.0))
        return {"gen": gen, "width": width, "gbps": round(per_lane * width, 2)}
    return None

def collect_health(run=shell_runner) -> dict:
    throttled = "unknown"
    m = re.search(r"throttled=(0x[0-9a-fA-F]+)", _safe(run, ["vcgencmd", "get_throttled"]))
    if m: throttled = m.group(1)
    temp = None
    m = re.search(r"temp=([\d.]+)", _safe(run, ["vcgencmd", "measure_temp"]))
    if m: temp = float(m.group(1))
    npu = None
    m = re.search(r"Device Architecture:\s*(\S+)",
                  _safe(run, ["hailortcli", "fw-control", "identify"]))
    if m: npu = m.group(1)
    services = {u: _safe(run, ["systemctl", "is-active", u]).strip() == "active"
                for u in WATCHED_UNITS}
    disk_free_gb = None
    try:
        du = shutil.disk_usage("/")
        disk_free_gb = round(du.free / 1e9, 1)
    except Exception: pass
    uptime_s = None
    try:
        with open("/proc/uptime") as f:
            uptime_s = float(f.read().split()[0])
    except Exception: pass
    cpu_pct = None; cpu_cores = None
    try:
        u = _cpu_util()
        cpu_pct = u.get("cpu")
        cpu_cores = [u[k] for k in sorted(u) if k != "cpu"]
    except Exception: pass
    loadavg = None
    try:
        with open("/proc/loadavg") as f:
            loadavg = [float(x) for x in f.read().split()[:3]]
    except Exception: pass
    pcie = None
    try: pcie = _pcie_link()
    except Exception: pass
    return {"throttled": throttled, "temp_c": temp, "npu": npu, "services": services,
            "disk_free_gb": disk_free_gb, "uptime_s": uptime_s,
            "cpu_pct": cpu_pct, "cpu_cores": cpu_cores, "loadavg": loadavg,
            "pcie": pcie, "t_probe_ns": time.monotonic_ns()}

def collect_logs(run=shell_runner, n: int = 200) -> str:
    """Last n journal lines, text/plain. Falls back to a fixed message rather than
    ever raising — /api/logs is a diagnostics convenience, not load-bearing."""
    try:
        out = run(["journalctl", "-n", str(n), "--no-pager"])
    except Exception:
        return "logs unavailable"
    return out if out else "logs unavailable"
