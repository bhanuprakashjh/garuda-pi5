#!/usr/bin/env python3
"""Build the testing source kit and standalone installer; no machine-local assets copied."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def run(args):
    subprocess.run([str(x) for x in args], cwd=ROOT, check=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--vision-python', type=Path, required=True, help='Python with NumPy/OpenCV, used only to generate the sample')
    a = p.parse_args()
    if subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=ROOT).strip():
        raise SystemExit('Commit tracked changes before building a publishable release.')
    spec = json.loads((ROOT / 'release/dependencies.json').read_text())
    version = spec['release']
    out = ROOT / '.runtime/release-dist'
    out.mkdir(parents=True, exist_ok=True)
    run([sys.executable, '-m', 'PyInstaller', '--clean', '--noconfirm', '--onefile', '--name', 'garuda-setup-linux-x86_64', '--distpath', out, '--workpath', ROOT / '.runtime/pyinstaller-work', '--specpath', ROOT / '.runtime', ROOT / 'release/setup.py'])
    run([a.vision_python, ROOT / 'release/make_sample.py'])
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    with tempfile.TemporaryDirectory(prefix='garuda-kit-') as tmp:
        kit = Path(tmp) / ('garuda-demo-' + version)
        kit.mkdir()
        for name in filter(None, tracked):
            rel = Path(name)
            # Do not redistribute evaluation models or external footage with the kit.
            if rel.suffix.lower() in ('.hef', '.onnx', '.mp4', '.avi', '.mov', '.mkv', '.webm'):
                continue
            if rel.parts[0] in ('.git', '.runtime', '.pixi', '.local', '.local-recovery'):
                raise RuntimeError('Forbidden release path: ' + name)
            src = ROOT / rel
            if src.is_symlink():
                raise RuntimeError('Review symlink before release: ' + name)
            dst = kit / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        shutil.copy2(out / 'garuda-setup-linux-x86_64', kit)
        (kit / 'samples').mkdir(exist_ok=True)
        shutil.copy2(ROOT / 'samples/playback-check.avi', kit / 'samples')
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        (kit / 'RELEASE_SOURCE.txt').write_text(f'{version}\nGit revision: {revision}\nPre-release; see release/README.md for validation limits.\n')
        notice = kit / 'release/binary-licenses'
        notice.mkdir(exist_ok=True)
        candidates = {'Python.txt': Path('/usr/lib/python3.12/LICENSE.txt'), 'OpenSSL.txt': Path('/usr/share/doc/openssl/copyright'), 'libffi.txt': Path('/usr/share/doc/libffi8/copyright'), 'zlib.txt': Path('/usr/share/doc/zlib1g/copyright'), 'bzip2.txt': Path('/usr/share/doc/libbz2-1.0/copyright'), 'xz.txt': Path('/usr/share/doc/liblzma5/copyright'), 'expat.txt': Path('/usr/share/doc/libexpat1/copyright')}
        for name, source in candidates.items():
            if not source.exists():
                raise RuntimeError('Missing binary license input: ' + str(source))
            shutil.copy2(source, notice / name)
        inventory = subprocess.check_output([sys.executable, '-m', 'pip', 'freeze'], text=True)
        (notice / 'build-packages.txt').write_text(inventory)
        sums = []
        for path in sorted(kit.rglob('*')):
            if path.is_file():
                sums.append(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + str(path.relative_to(kit)))
        (kit / 'CONTENTS.sha256').write_text('\n'.join(sums) + '\n')
        archive = out / (kit.name + '.tar.gz')
        with tarfile.open(archive, 'w:gz') as tf:
            tf.add(kit, arcname=kit.name)
        # Standalone binary users receive the same runtime notices separately.
        with tarfile.open(out / 'binary-licenses.tar.gz', 'w:gz') as tf:
            tf.add(notice, arcname='binary-licenses')
    artifacts = [out / 'garuda-setup-linux-x86_64', archive, out / 'binary-licenses.tar.gz']
    (out / 'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n' for p in artifacts))
    print('Release assets:', out)


if __name__ == '__main__':
    main()
