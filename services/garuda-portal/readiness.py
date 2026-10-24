"""Declarative readiness checks. Pure function of (health, config) — no shell here."""
from __future__ import annotations
import time

def run_checks(health: dict, config: dict) -> dict:
    checks = []
    def add(id, status, evidence):
        checks.append({"id": id, "status": status, "evidence": evidence})

    add("npu.identify", "pass" if health.get("npu") == "HAILO10H" else "fail",
        f"identify: {health.get('npu')}")
    flags = health.get("throttled", "unknown")
    add("power.flags", "pass" if flags == "0x0" else "fail", f"get_throttled={flags}")
    add("power.supply5a", "warn",
        "supply not verified 5V/5A (bench charger has prior UV strikes)")
    t = health.get("temp_c")
    add("thermals", "na" if t is None else ("pass" if t < 75 else "warn"),
        f"SoC {t} C")
    svc = health.get("services", {})
    if not isinstance(svc, dict):
        svc = {}
    add("services.core", "pass" if all(svc.values()) and svc else "warn",
        ", ".join(f"{k}={'up' if v else 'down'}" for k, v in svc.items()) or "none")
    v = health.get("disk_free_gb")
    add("storage.space", "na" if v is None else ("pass" if v > 5 else "warn"),
        f"{v} GB free" if v is not None else "disk free: unknown")
    if config.get("bridge.agent_ip"):
        add("bridge.session", "warn",
            "endpoint configured; live session not probed in V1 (needs FC/SITL up)")
    else:
        add("bridge.session", "na", "no FC endpoint configured")
    add("simgate.status", "pass", "portal sends no arming commands (by design)")

    # Overall rule: any fail -> no-go; any warn EXCEPT power.supply5a -> caution;
    # else go. supply5a is a permanent advisory on this bench (unverified charger)
    # and must not demote an otherwise-green board.
    statuses = [c["status"] for c in checks]
    non_supply_warns = [c for c in checks
                        if c["status"] == "warn" and c["id"] != "power.supply5a"]
    overall = ("no-go" if "fail" in statuses
               else "caution" if non_supply_warns else "go")
    return {"t_ns": time.monotonic_ns(), "overall": overall, "checks": checks}
