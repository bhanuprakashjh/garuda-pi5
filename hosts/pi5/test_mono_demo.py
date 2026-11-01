from mono_demo import ASSETS, asset


def test_all_local_assets_available():
    for path in ASSETS:
        mime, data = asset(path)
        assert data and mime
    assert b"Hailo-10H" in asset("/demo")[1]
    assert asset("/demo/microchip.png")[1].startswith(b"\x89PNG")


def test_asset_allowlist_rejects_traversal():
    for path in ("/demo/../garuda_mono.py", "/demo/controller.test.js", "/demo/%2e%2e/secret", "/etc/passwd"):
        assert asset(path) is None
