import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from whitelist import validate_request, apply_config_set

def test_rejects_unknown_op():
    ok, why = validate_request({"op": "shell.exec", "args": {"cmd": "rm -rf /"}})
    assert not ok and "not whitelisted" in why

def test_rejects_non_garuda_unit():
    ok, why = validate_request({"op": "unit.action",
                                "args": {"unit": "ssh.service", "action": "stop"}})
    assert not ok

def test_accepts_garuda_unit_restart():
    ok, _ = validate_request({"op": "unit.action",
                              "args": {"unit": "garuda-inference", "action": "restart"}})
    assert ok

def test_rejects_bad_config_key():
    ok, _ = validate_request({"op": "config.set",
                              "args": {"key": "evil.key", "value": 1}})
    assert not ok

def test_accepts_and_applies_bridge_endpoint():
    req = {"op": "config.set", "args": {"key": "bridge.agent_ip", "value": "10.42.0.128"}}
    ok, _ = validate_request(req)
    assert ok
    store = apply_config_set({}, "bridge.agent_ip", "10.42.0.128")
    assert store["bridge.agent_ip"] == "10.42.0.128"

def test_config_set_rejects_bad_value_type():
    ok, _ = validate_request({"op": "config.set",
                              "args": {"key": "bridge.agent_port", "value": "not-an-int"}})
    assert not ok

def test_accepts_system_reboot_only():
    assert validate_request({"op": "system.action", "args": {"action": "reboot"}})[0]
    assert not validate_request({"op": "system.action", "args": {"action": "halt -f"}})[0]

def test_rejects_non_dict_args_none():
    ok, why = validate_request({"op": "config.set", "args": None})
    assert not ok and "args not a dict" in why

def test_rejects_non_dict_args_string():
    ok, why = validate_request({"op": "config.set", "args": "boom"})
    assert not ok and "args not a dict" in why

def test_rejects_unit_with_path_traversal():
    ok, _ = validate_request({"op": "unit.action",
        "args": {"unit": "garuda-inference ../../ssh", "action": "restart"}})
    assert not ok

def test_rejects_hef_with_path_traversal():
    ok, _ = validate_request({"op": "config.set",
        "args": {"key": "inference.hef", "value": "../../etc/passwd.hef"}})
    assert not ok

def test_rejects_oversized_request():
    ok, _ = validate_request({"op": "config.set",
        "args": {"key": "camera.default_mode", "value": "x" * 10000}})
    assert not ok

def test_get_never_requires_args():
    assert validate_request({"op": "config.get"})[0]

def test_accepts_garuda_mono_start_stop():
    # mono power toggle (2026-08-09 spec): the Tracking-tab button drives these
    for action in ("start", "stop"):
        ok, _ = validate_request({"op": "unit.action",
                                  "args": {"unit": "garuda-mono", "action": action}})
        assert ok
