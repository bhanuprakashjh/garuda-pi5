"""configd whitelist — THE security surface. Everything not listed here is rejected."""
from __future__ import annotations
import ipaddress

def _is_ip(v):
    try: ipaddress.ip_address(v); return True
    except (ValueError, TypeError): return False

CONFIG_SCHEMA = {
    "bridge.agent_ip":   _is_ip,
    "bridge.agent_port": lambda v: isinstance(v, int) and 1024 <= v <= 65535,
    "ptp.enabled":       lambda v: isinstance(v, bool),
    "camera.default_mode": lambda v: isinstance(v, str) and "x" in v and len(v) < 32,
    "inference.hef":     lambda v: isinstance(v, str) and v.endswith(".hef")
                                    and "/" not in v and len(v) < 128,
}
ALLOWED_UNITS = {"garuda-inference", "garuda-cam-portal", "garuda-ptp4l-client", "garuda-mono"}
ALLOWED_UNIT_ACTIONS = {"start", "stop", "restart"}
ALLOWED_SYSTEM_ACTIONS = {"reboot", "poweroff"}

def validate_request(req: dict) -> tuple[bool, str]:
    if not isinstance(req, dict):
        return False, "request not a dict"
    op, args = req.get("op"), req.get("args", {})
    if not isinstance(args, dict):
        return False, "args not a dict"
    if op == "config.get":
        return True, "ok"
    if op == "config.set":
        key = args.get("key")
        if key not in CONFIG_SCHEMA:
            return False, f"config key not whitelisted: {key!r}"
        if not CONFIG_SCHEMA[key](args.get("value")):
            return False, f"invalid value for {key!r}"
        return True, "ok"
    if op == "unit.action":
        if args.get("unit") not in ALLOWED_UNITS:
            return False, f"unit not whitelisted: {args.get('unit')!r}"
        if args.get("action") not in ALLOWED_UNIT_ACTIONS:
            return False, f"action not whitelisted: {args.get('action')!r}"
        return True, "ok"
    if op == "system.action":
        if args.get("action") not in ALLOWED_SYSTEM_ACTIONS:
            return False, f"system action not whitelisted: {args.get('action')!r}"
        return True, "ok"
    return False, f"op not whitelisted: {op!r}"

def apply_config_set(store: dict, key: str, value) -> dict:
    new = dict(store)
    new[key] = value
    return new
