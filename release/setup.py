#!/usr/bin/env python3
"""GARUDA testing installer. Standard library only; no changes without --apply.
BSD-3-Clause. Does not arm, start services, upgrade drivers or touch a remote Pi.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys
import tarfile
import urllib.request


def digest(path):
    with Path(path).open('rb') as f:
        h = hashlib.sha256()
        for part in iter(lambda: f.read(1024 * 1024), b''):
            h.update(part)
        return h.hexdigest()


def download(url, target, sha):
    """Never execute or publish a partial/unverified download."""
    target = Path(target)
    if target.is_file() and digest(target) == sha:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + '.partial')
    try:
        with urllib.request.urlopen(url, timeout=90) as src, tmp.open('wb') as dst:
            shutil.copyfileobj(src, dst)
        if digest(tmp) != sha:
            raise RuntimeError('SHA256 mismatch: ' + url)
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)


def os_info():
    values = {}
    for line in Path('/etc/os-release').read_text().splitlines():
        if '=' in line:
            k, v = line.split('=', 1)
            values[k] = v.strip('"')
    return values


def supported(target, info=None, machine=None):
    info = info or os_info()
    machine = machine or platform.machine()
    if target == 'pc':
        return info.get('ID') == 'ubuntu' and info.get('VERSION_ID') == '24.04' and machine == 'x86_64'
    return info.get('VERSION_CODENAME') == 'trixie' and machine == 'aarch64'


class Installer:
    def __init__(self, root, apply=False, jobs=2):
        self.root = Path(root).resolve()
        self.apply = apply
        self.jobs = str(jobs)
        self.manifest = json.loads((self.root / 'release/dependencies.json').read_text())
        self.runtime = self.root / '.runtime'
        self.env = dict(os.environ, PYTHONNOUSERSITE='1')
        # Do not leak an activated ROS/conda environment into native build steps.
        for key in ('PYTHONHOME', 'PYTHONPATH', 'LD_LIBRARY_PATH', 'AMENT_PREFIX_PATH', 'CMAKE_PREFIX_PATH', 'COLCON_PREFIX_PATH'):
            self.env.pop(key, None)
        self.env['PATH'] = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
        self.env['PIXI_CACHE_DIR'] = str(self.runtime / 'pixi-cache')

    def command(self, args, cwd=None):
        args = [str(a) for a in args]
        print('+ ' + shlex.join(args), flush=True)
        if self.apply:
            subprocess.run(args, cwd=cwd or self.root, env=self.env, check=True)

    def write(self, path, contents, mode=None):
        print('Write ' + str(path), flush=True)
        if self.apply:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents)
            if mode:
                path.chmod(mode)

    def fetch(self, name, target):
        item = self.manifest[name]
        print('Download verified ' + item['url'], flush=True)
        if self.apply:
            download(item['url'], target, item['sha256'])

    def checkout(self, key, dest):
        item = self.manifest[key]
        if not dest.exists():
            self.command(['git', 'clone', item['url'], dest])
            self.command(['git', '-C', dest, 'checkout', '--detach', item['revision']])
        elif self.apply:
            actual = subprocess.check_output(['git', '-C', dest, 'rev-parse', 'HEAD'], text=True).strip()
            dirty = subprocess.check_output(['git', '-C', dest, 'status', '--porcelain', '--untracked-files=no'], text=True).strip()
            # Agent's installer-owned CMake pin is deliberately maintained below.
            allowed = False
            if key == 'agent' and dirty == 'M CMakeLists.txt':
                original = subprocess.check_output(['git', '-C', dest, 'show', 'HEAD:CMakeLists.txt'], text=True)
                for dep, old in [('fastdds', '2.14.x'), ('fastcdr', '2.2.x')]:
                    original = original.replace(f'set(_{dep}_tag {old})', f'set(_{dep}_tag {item[dep]})')
                allowed = (dest / 'CMakeLists.txt').read_text() == original
            if actual != item['revision'] or (dirty and not allowed):
                raise RuntimeError(f'Refusing to alter existing {dest}: expected clean {item["revision"]}')
        self.command(['git', '-C', dest, 'submodule', 'update', '--init', '--recursive'])

    def pc_system(self):
        packages = '''git curl ca-certificates gnupg lsb-release build-essential cmake ninja-build pkg-config python3-dev python3-venv python3-pip python3-setuptools python3-wheel unzip zip rsync astyle ccache cppcheck file gdb lcov libssl-dev libxml2-dev libxml2-utils shellcheck gstreamer1.0-plugins-bad gstreamer1.0-libav gstreamer1.0-gl libfuse2t64 libxcb-xinerama0 libxkbcommon-x11-0 libxcb-cursor0'''.split()
        self.command(['sudo', 'apt-get', 'update'])
        self.command(['sudo', 'apt-get', 'install', '-y', *packages])
        self.command(['curl', '-fSL', 'https://packages.osrfoundation.org/gazebo.gpg', '-o', self.runtime / 'gazebo.gpg'])
        self.command(['sudo', 'install', '-m', '0644', self.runtime / 'gazebo.gpg', '/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg'])
        repo = 'deb [arch=amd64 signed-by=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg] https://packages.osrfoundation.org/gazebo/ubuntu-stable noble main\n'
        self.write(self.runtime / 'gazebo-stable.list', repo)
        self.command(['sudo', 'install', '-m', '0644', self.runtime / 'gazebo-stable.list', '/etc/apt/sources.list.d/gazebo-stable.list'])
        self.command(['sudo', 'apt-get', 'update'])
        self.command(['sudo', 'apt-get', 'install', '-y', 'gz-harmonic', 'python3-gz-transport13', 'python3-gz-msgs10', 'python3-protobuf'])

    def pc_px4(self):
        dest = self.runtime / 'PX4-Autopilot'
        self.checkout('px4', dest)
        self.command(['/usr/bin/python3', '-m', 'venv', dest / '.venv'])
        self.command([dest / '.venv/bin/python', '-m', 'pip', 'install', '-r', dest / 'Tools/setup/requirements.txt'])
        # Keep PX4 Python packages out of system Python; its upstream setup script
        # uses --break-system-packages, so install the explicit host package list instead.
        self.command(['cmake', '-S', dest, '-B', dest / 'build/px4_sitl_default', '-G', 'Ninja', '-DCONFIG=px4_sitl_default', '-DCMAKE_BUILD_TYPE=RelWithDebInfo', '-DPYTHON_EXECUTABLE=' + str(dest / '.venv/bin/python')])
        self.command(['cmake', '--build', dest / 'build/px4_sitl_default', '-j', self.jobs])

    def pc_ros(self):
        tools = self.runtime / 'tools'
        archive = self.runtime / 'pixi.tar.gz'
        self.fetch('pixi', archive)
        if self.apply:
            tools.mkdir(parents=True, exist_ok=True)
            with tarfile.open(archive) as tf:
                member = tf.getmember('pixi')
                if not member.isfile():
                    raise RuntimeError('Invalid Pixi archive')
                with tf.extractfile(member) as src, (tools / 'pixi').open('wb') as dst:
                    shutil.copyfileobj(src, dst)
            (tools / 'pixi').chmod(0o755)
        pixi = tools / 'pixi'
        self.command([pixi, 'install', '--locked'])
        agent = self.runtime / 'agent-src'
        self.checkout('agent', agent)
        if self.apply:
            p = agent / 'CMakeLists.txt'
            s = p.read_text()
            for key, old in [('fastdds', '2.14.x'), ('fastcdr', '2.2.x')]:
                before = f'set(_{key}_tag {old})'
                after = f'set(_{key}_tag {self.manifest["agent"][key]})'
                if before not in s and after not in s:
                    raise RuntimeError('Unexpected agent dependency declaration: ' + key)
                s = s.replace(before, after)
            p.write_text(s)
        self.command(['cmake', '-S', agent, '-B', self.runtime / 'agent-build', '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_INSTALL_PREFIX=' + str(self.runtime / 'agent'), '-DUAGENT_P2P_PROFILE=OFF', '-DUAGENT_CED_PROFILE=OFF'])
        self.command(['cmake', '--build', self.runtime / 'agent-build', '-j', self.jobs])
        self.command(['cmake', '--install', self.runtime / 'agent-build'])
        self.command(['/usr/bin/python3', 'hosts/pc/prepare_messages.py', self.runtime / 'PX4-Autopilot', self.runtime / 'matched_ws/src/px4_msgs'])
        self.command([pixi, 'run', 'colcon', 'build', '--base-paths', '.runtime/matched_ws/src', '--build-base', '.runtime/matched_ws/build', '--install-base', '.runtime/matched_ws/install', '--merge-install', '--cmake-args', '-DBUILD_TESTING=OFF'])

    def pc_vision(self):
        venv = self.runtime / 'vision-env'
        self.command(['/usr/bin/python3', '-m', 'venv', '--system-site-packages', venv])
        self.command([venv / 'bin/python', '-m', 'pip', 'install', '-r', 'hosts/pc/vision-requirements.txt'])
        self.command(['/usr/bin/python3', 'hosts/pc/fetch_vision.py'])
        self.command([venv / 'bin/python', '-c', 'import cv2, numpy, onnxruntime; from gz.transport13 import Node; import gz.msgs10.image_pb2; print("Vision imports OK")'])
        self.command([venv / 'bin/python', 'release/make_sample.py'])

    def pc_assets(self):
        qgc = self.runtime / 'tools/QGroundControl-x86_64.AppImage'
        self.fetch('qgc', qgc)
        if self.apply:
            qgc.chmod(0o755)
        self.command(['gz', 'fuel', 'download', '-u', self.manifest['actor']['url']])

    def configure(self):
        config = self.runtime / 'lab-config.json'
        if config.exists():
            print('Preserving existing lab-config.json; use Setup / paths for changes.')
            return
        actor = next((Path.home() / '.gz/fuel').glob('*/mingfei/models/actor/1/meshes/walk.dae'), None)
        value = dict(px4=str(self.runtime / 'PX4-Autopilot'), qgc=str(self.runtime / 'tools/QGroundControl-x86_64.AppImage'), actor=str(actor) if actor else '', data_root='', pi_url='http://10.42.0.128:8098')
        self.write(config, json.dumps(value, indent=2) + '\n')

    def pi(self, model_dir=None, faces=False, fetch_models=False):
        if self.root != Path('/home/garuda/garuda-sdk-deploy'):
            raise RuntimeError('This Pi version requires /home/garuda/garuda-sdk-deploy; unpack there as user garuda.')
        self.command(['sudo', 'apt-get', 'update'])
        self.command(['sudo', 'apt-get', 'install', '-y', 'git', 'curl', 'python3-numpy', 'python3-opencv', 'python3-venv', 'python3-pip', 'rpicam-apps', 'v4l-utils', 'pciutils'])
        if fetch_models:
            for item in self.manifest['pi_models']:
                if item.get('url'):
                    print('Download evaluation model from upstream: ' + item['url'])
                    if self.apply:
                        dst = self.root / item['path']
                        if dst.exists() and digest(dst) != item['sha256']:
                            raise RuntimeError('Refusing to overwrite a different model: ' + str(dst))
                        download(item['url'], dst, item['sha256'])
        if model_dir:
            source = Path(model_dir).resolve()
            # Validate the entire set before copying anything.
            for item in self.manifest['pi_models']:
                path = source / item['path'].removeprefix('models/')
                if self.apply:
                    existing = self.root / item['path']
                    candidate = path if path.is_file() else existing
                    if not candidate.is_file() or digest(candidate) != item['sha256']:
                        raise RuntimeError('Missing or mismatched baseline model: ' + str(path))
            for item in self.manifest['pi_models']:
                src = source / item['path'].removeprefix('models/')
                dst = self.root / item['path']
                print(f'Import verified {src} -> {dst}')
                if self.apply and src.is_file() and src != dst:
                    if dst.exists() and digest(dst) != item['sha256']:
                        raise RuntimeError('Refusing to replace a different model: ' + str(dst))
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
        if faces:
            self.command(['/usr/bin/python3', 'models/fetch_faces.py'])
        print('Hailo driver/runtime installation is manual: see release/README.md. No service was installed or started.')

    def inventory(self):
        if not self.apply:
            return
        state = dict(release=self.manifest['release'], kernel=platform.release(), architecture=platform.machine(), os=os_info())
        self.write(self.runtime / 'install-environment.json', json.dumps(state, indent=2) + '\n')
        if shutil.which('dpkg-query'):
            data = subprocess.check_output(['dpkg-query', '-W'], text=True)
            self.write(self.runtime / 'install-packages.txt', data)


def doctor(root, target):
    """Read-only checks; never load HEFs or open a camera/vehicle."""
    root = Path(root)
    manifest = json.loads((root / 'release/dependencies.json').read_text())
    problems = []
    def check(label, okay, hint):
        print(('OK      ' if okay else 'MISSING ') + label + ('' if okay else ': ' + hint))
        if not okay:
            problems.append(label)
    check('Supported OS/architecture', supported(target), manifest['platforms'][target])
    if target == 'pc':
        for name in ['tools/pixi', 'PX4-Autopilot/build/px4_sitl_default/bin/px4', 'agent/bin/MicroXRCEAgent', 'matched_ws/install/setup.bash', 'vision-env/bin/python', 'models/yolox/object_detection_yolox_2022nov.onnx', 'tools/QGroundControl-x86_64.AppImage']:
            check(name, (root / '.runtime' / name).is_file(), 'run the relevant PC setup stage or configure existing paths in the GUI')
        check('Gazebo', bool(shutil.which('gz')), 'run PC system stage')
        actor = next((Path.home() / '.gz/fuel').glob('*/mingfei/models/actor/1/meshes/walk.dae'), None)
        check('Actor mesh', actor is not None, 'run PC assets stage or set a custom path in the GUI')
        python = root / '.runtime/vision-env/bin/python'
        if python.exists():
            result = subprocess.run([str(python), '-c', 'import cv2, numpy, onnxruntime; from gz.transport13 import Node; import gz.msgs10.image_pb2'], capture_output=True, text=True)
            check('Vision imports', result.returncode == 0, result.stderr[-600:])
        print('Doctor checks installer-managed paths; existing custom installations can be configured in the GUI.')
    else:
        check('Deployment path', root.resolve() == Path('/home/garuda/garuda-sdk-deploy'), 'use the documented garuda account/path')
        for item in manifest['pi_models']:
            path = root / item['path']
            check(item['path'], path.is_file() and digest(path) == item['sha256'], 'supply the known-good artifact; see model manifest')
        cli = shutil.which('hailortcli')
        check('Hailo CLI', bool(cli), 'install matching vendor Hailo-10H 5.3.0 stack')
        if cli:
            r = subprocess.run([cli, 'fw-control', 'identify'], capture_output=True, text=True, timeout=20)
            check('Hailo device/firmware', r.returncode == 0 and 'HAILO10H' in r.stdout and '5.3.0' in r.stdout, (r.stdout + r.stderr)[-600:])
        code = 'import cv2, numpy, hailo_platform; print(getattr(hailo_platform,"__version__","unknown")); assert hasattr(cv2,"FaceDetectorYN_create")'
        r = subprocess.run(['/usr/bin/python3', '-c', code], capture_output=True, text=True)
        check('Pi imports / 5.3.0 binding', r.returncode == 0 and '5.3.0' in r.stdout, (r.stdout + r.stderr)[-600:])
        check('rpicam-vid', bool(shutil.which('rpicam-vid')), 'install rpicam-apps')
        print('Camera detection, HEF loading, model output and actual flight still require operator validation.')
    return 2 if problems else 0


def main(argv=None):
    # PyInstaller changes the library search path for its own runtime. Do not
    # propagate bundled libraries to apt, git, system Python, CMake or Gazebo.
    if getattr(sys, 'frozen', False):
        original = os.environ.pop('LD_LIBRARY_PATH_ORIG', None)
        if original:
            os.environ['LD_LIBRARY_PATH'] = original
        else:
            os.environ.pop('LD_LIBRARY_PATH', None)
    base = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=base, help='extracted GARUDA release/source root')
    sub = p.add_subparsers(dest='action', required=True)
    install = sub.add_parser('install', help='print installation plan; --apply executes it')
    install.add_argument('target', choices=['pc', 'pi'])
    install.add_argument('--apply', action='store_true')
    install.add_argument('--stage', choices=['all', 'system', 'px4', 'ros', 'vision', 'assets'], default='all')
    install.add_argument('--jobs', type=int, default=2)
    install.add_argument('--model-dir', type=Path, help='Pi: directory containing the known-good model tree')
    install.add_argument('--fetch-pi-models', action='store_true', help='Pi: download known-good evaluation models from upstream; custom depth still required')
    install.add_argument('--faces', action='store_true', help='Pi: download optional checksum-pinned face models')
    d = sub.add_parser('doctor', help='read-only prerequisite checks')
    d.add_argument('target', choices=['pc', 'pi'])
    a = p.parse_args(argv)
    if not (a.root / 'release/dependencies.json').is_file():
        p.error('Extract the source kit and set --root to its directory (see release/README.md).')
    if a.action == 'doctor':
        return doctor(a.root, a.target)
    if a.jobs < 1 or a.jobs > 32:
        p.error('--jobs must be 1..32')
    if a.target == 'pc' and (a.model_dir or a.faces or a.fetch_pi_models):
        p.error('--model-dir, --faces and --fetch-pi-models are Pi-only')
    if a.target == 'pi' and a.stage != 'all':
        p.error('--stage is for PC installs')
    if a.apply:
        if os.geteuid() == 0:
            p.error('Run as a normal user; only host-package steps invoke sudo.')
        if not supported(a.target):
            p.error('Unsupported platform; use the documented OS/architecture. Preview works without --apply.')
        if a.target == 'pi' and os.environ.get('USER') != 'garuda':
            p.error('Pi deployment requires user garuda.')
        (a.root / '.runtime').mkdir(parents=True, exist_ok=True)
    setup = Installer(a.root, a.apply, a.jobs)
    if a.target == 'pc':
        for stage in ['system', 'px4', 'ros', 'vision', 'assets']:
            if a.stage in ('all', stage):
                print('\n== ' + stage + ' ==', flush=True)
                getattr(setup, 'pc_' + stage)()
        if a.stage == 'all':
            setup.configure()
    else:
        setup.pi(a.model_dir, a.faces, a.fetch_pi_models)
    setup.inventory()
    if a.apply:
        print('\nStage finished. No service or flight was started.')
        if a.stage == 'all':
            return doctor(a.root, a.target)
    else:
        print('\nPLAN ONLY: nothing installed. Add --apply to execute on the supported machine.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
        print('Setup stopped: ' + str(exc), file=sys.stderr)
        sys.exit(1)
