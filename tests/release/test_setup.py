"""Installer regression checks: no sudo, network, hardware or simulator required."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('garuda_setup', ROOT / 'release/setup.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class InstallerTests(unittest.TestCase):
    def kit(self, folder):
        root = Path(folder)
        (root / 'release').mkdir()
        (root / 'release/dependencies.json').write_bytes((ROOT / 'release/dependencies.json').read_bytes())
        return root

    def test_plan_has_no_process_or_file_side_effects(self):
        with tempfile.TemporaryDirectory() as d:
            root = self.kit(d)
            before = sorted(str(p.relative_to(root)) for p in root.rglob('*'))
            with patch.object(setup.subprocess, 'run', side_effect=AssertionError('executed a command')):
                self.assertEqual(setup.main(['--root', str(root), 'install', 'pc']), 0)
            self.assertEqual(before, sorted(str(p.relative_to(root)) for p in root.rglob('*')))

    def test_platform_gate(self):
        self.assertTrue(setup.supported('pc', {'ID':'ubuntu','VERSION_ID':'24.04'}, 'x86_64'))
        self.assertFalse(setup.supported('pc', {'ID':'ubuntu','VERSION_ID':'22.04'}, 'x86_64'))
        self.assertFalse(setup.supported('pc', {'ID':'ubuntu','VERSION_ID':'24.04'}, 'aarch64'))
        self.assertTrue(setup.supported('pi', {'VERSION_CODENAME':'trixie'}, 'aarch64'))

    def test_checksum_failure_preserves_destination(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / 'source'
            dst = Path(d) / 'download'
            src.write_bytes(b'untrusted')
            dst.write_bytes(b'previous')
            with self.assertRaisesRegex(RuntimeError, 'SHA256'):
                setup.download(src.as_uri(), dst, '0'*64)
            self.assertEqual(dst.read_bytes(), b'previous')
            self.assertFalse(dst.with_name('download.partial').exists())

    def test_verified_download_and_cache(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / 'source'
            dst = Path(d) / 'download'
            src.write_bytes(b'expected')
            sha = hashlib.sha256(b'expected').hexdigest()
            setup.download(src.as_uri(), dst, sha)
            src.unlink()
            setup.download(src.as_uri(), dst, sha)
            self.assertEqual(dst.read_bytes(), b'expected')

    def test_preserves_existing_configuration(self):
        with tempfile.TemporaryDirectory() as d:
            root = self.kit(d)
            (root / '.runtime').mkdir()
            cfg = root / '.runtime/lab-config.json'
            cfg.write_text('{"pi_url":"http://existing:8098"}')
            original = cfg.read_bytes()
            setup.Installer(root, True).configure()
            self.assertEqual(cfg.read_bytes(), original)

    def test_wrong_existing_checkout_is_never_reset(self):
        with tempfile.TemporaryDirectory() as d:
            root = self.kit(d)
            repo = root / 'existing'
            repo.mkdir()
            with patch.object(setup.subprocess, 'check_output', side_effect=['other-revision\n','']), patch.object(setup.subprocess,'run') as run:
                with self.assertRaisesRegex(RuntimeError,'Refusing'):
                    setup.Installer(root,True).checkout('px4',repo)
                run.assert_not_called()

    def test_dirty_existing_checkout_is_never_reset(self):
        with tempfile.TemporaryDirectory() as d:
            root = self.kit(d)
            repo = root / 'existing'
            repo.mkdir()
            revision = setup.Installer(root).manifest['px4']['revision']
            with patch.object(setup.subprocess,'check_output',side_effect=[revision+'\n',' M src/user.cpp\n']), patch.object(setup.subprocess,'run') as run:
                with self.assertRaisesRegex(RuntimeError,'Refusing'):
                    setup.Installer(root,True).checkout('px4',repo)
                run.assert_not_called()

    def test_missing_installation_doctor_returns_failure(self):
        with tempfile.TemporaryDirectory() as d:
            root = self.kit(d)
            with patch.object(setup.shutil,'which',return_value=None):
                self.assertEqual(setup.doctor(root,'pc'),2)

    def test_pi_wrong_destination_rejected_before_commands(self):
        with tempfile.TemporaryDirectory() as d:
            root = self.kit(d)
            with patch.object(setup.subprocess,'run') as run:
                with self.assertRaisesRegex(RuntimeError,'requires /home/garuda'):
                    setup.Installer(root,True).pi()
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
