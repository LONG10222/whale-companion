"""Owner-selected installation of the pinned, isolated dialogue environment."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading
import time

import psutil
from self_update import destination, read

VERSION = '4.29.1'
BUSY_PHASES = {'starting', 'preparing', 'installing', 'checking'}


def write(path, value):
    from scripts.beginner_setup import write_json
    write_json(path, value)


def lock_digest(root):
    return hashlib.sha256(b''.join((Path(root)/name).read_bytes() for name in
        ['requirements-astrbot-build.lock', 'requirements-astrbot.lock'])).hexdigest()


def installation_root(root):
    root = Path(root).resolve()
    marker = read(root/'data/setup/installation.local.json', {})
    if marker.get('root') != str(root) or not marker.get('instance'):
        raise ValueError('先运行本目录的 Install.cmd；可选安装器不接管现有私人部署。')
    for name in ['.runtime/astrbot', 'data/astrbot', 'logs/setup', 'configs/chat-engine.local.json']:
        destination(root, name)
    return root


def owns_installer(root, pid):
    try:
        root = Path(root).resolve()
        p = psutil.Process(int(pid));args = p.cmdline()
        return (Path(p.cwd()).resolve() == root and '--root' in args
                and Path(args[args.index('--root')+1]).resolve() == root
                and any(Path(a).resolve() == root/'scripts/install_astrbot.py'
                        for a in args[1:] if a.endswith('.py')))
    except (psutil.Error, OSError, ValueError, TypeError, IndexError):
        return False


def environment_state(root):
    root = Path(root).resolve()
    data = read(root/'data/setup/astrbot-environment.local.json', {})
    ready = False
    try:
        interpreter = destination(root, data.get('python', 'missing'))
        ready = (data.get('version') == VERSION and data.get('lock_sha256') == lock_digest(root)
                 and interpreter.is_file())
    except (ValueError, OSError):
        pass
    progress = read(root/'data/setup/astrbot-install.local.json', {'phase':'not_installed'})
    busy = progress.get('phase') in BUSY_PHASES and (
        owns_installer(root, progress.get('pid')) or
        progress.get('phase') == 'starting' and time.time()-progress.get('at', 0) < 30)
    return {'installed': ready, 'version': VERSION, 'busy': bool(busy),
            'phase': progress.get('phase') if busy or progress.get('phase') not in BUSY_PHASES else 'interrupted',
            'message': progress.get('message', '可选；基础聊天无需安装。'),
            'last_failure': progress.get('failure', ''),
            'license': 'AGPL-3.0-or-later',
            'note': '只安装独立依赖环境，不下载本地大模型；完成后仍使用原有对话，需另行切换。'}


def install(root, *, runner=subprocess.run):
    root = installation_root(root)
    state_file = root/'data/setup/astrbot-install.local.json'
    lock_file = root/'data/setup/astrbot-install.lock'
    previous = read(root/'configs/chat-engine.local.json', {})
    handle = None
    try:
        for _ in range(2):
            try:
                handle = lock_file.open('x', encoding='utf-8')
                json.dump({'pid':os.getpid()}, handle);handle.close()
                break
            except FileExistsError:
                if owns_installer(root, read(lock_file, {}).get('pid')):
                    return environment_state(root)
                lock_file.unlink()
        if handle is None:
            raise ValueError('另一个安装任务仍在准备，请稍后查看。')
        def status(phase, message, **extra):
            write(state_file, {'phase':phase, 'message':message, 'pid':os.getpid(), 'at':time.time(), **extra})
        if environment_state(root)['installed']:
            status('ready', '独立环境已准备好，已复用现有安装。')
            return environment_state(root)
        for name in ['python', 'profile']:
            if previous.get(name):
                value = Path(previous[name])
                if value.is_absolute():
                    try: relative = value.resolve().relative_to(root).as_posix()
                    except ValueError: raise ValueError('已配置手动环境；可选安装器保留它，请按高级文档处理。') from None
                else: relative = value.as_posix()
                destination(root, relative)
                previous[name] = relative
        if shutil.disk_usage(root).free < 4*1024**3:
            raise ValueError('可选环境安装至少预留 4 GiB 磁盘空间。')
        settings = read(root/'settings.local.json', {})
        base = Path(settings.get('python', ''))
        if not base.is_absolute(): base = root/base
        try: base = destination(root, base.resolve().relative_to(root).as_posix())
        except ValueError: raise ValueError('请使用安装器准备的本机 Python 环境。') from None
        if not base.is_file():
            raise ValueError('基础 Python 尚未准备好，请先运行 Install.cmd。')
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0)
        probe = runner([str(base), '-c', 'import sys;print("%s.%s"%sys.version_info[:2])'],
                       capture_output=True, text=True, timeout=20, creationflags=flags)
        if probe.returncode or probe.stdout.strip() != '3.13':
            raise ValueError('一键可选安装使用 CPython 3.13；其它版本请按高级部署文档独立安装。')
        digest = lock_digest(root)
        env_folder = destination(root, '.runtime/astrbot/envs/'+digest[:12]+'-'+secrets.token_hex(3))
        env_folder.parent.mkdir(parents=True, exist_ok=True)
        status('preparing', '正在创建独立对话环境；基础聊天和声音环境保持运行。')
        log = destination(root, 'logs/setup/astrbot-install.log');log.parent.mkdir(parents=True, exist_ok=True)
        env = {k:v for k,v in os.environ.items() if not k.startswith('PIP_')}
        env.update(PYTHONUTF8='1', PIP_CONFIG_FILE=os.devnull, PIP_NO_INPUT='1',
                   OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2', MKL_NUM_THREADS='2',
                   PIP_CACHE_DIR=str(destination(root, '.installer-cache/astrbot-pip')))
        with log.open('ab') as stream:
            created = runner([str(base), '-m', 'venv', str(env_folder)], cwd=root, env=env,
                             stdout=stream, stderr=subprocess.STDOUT, timeout=120, creationflags=flags)
            if created.returncode: raise RuntimeError('venv')
            interpreter = env_folder/'Scripts/python.exe'
            status('installing', '正在校验并安装 AstrBot 4.29.1 独立依赖；纯 Python 源码包会在本机构建。')
            for file, options in [('requirements-astrbot-build.lock', ['--only-binary=:all:']),
                                  ('requirements-astrbot.lock', ['--no-build-isolation'])]:
                result = runner([str(interpreter), '-m', 'pip', 'install', '--disable-pip-version-check',
                                 '--index-url', 'https://pypi.org/simple', '--retries', '2', '--require-hashes',
                                 *options, '-r', str(root/file)], cwd=root, env=env,
                                stdout=stream, stderr=subprocess.STDOUT, timeout=900, creationflags=flags)
                if result.returncode: raise RuntimeError('dependencies')
        status('checking', '正在检查独立解释器与核心依赖版本。')
        code = ('import importlib.metadata as m,json;import fastapi,uvicorn,psutil;'
                'assert m.version("astrbot")=="4.29.1";'
                'print(json.dumps({n:m.version(n) for n in ["astrbot","fastapi","uvicorn","psutil"]}))')
        checked = runner([str(interpreter), '-c', code], cwd=root, env=env,
                         capture_output=True, text=True, timeout=40, creationflags=flags)
        if checked.returncode: raise RuntimeError('dependency_probe')
        versions = json.loads(checked.stdout)
        cfg = dict(previous)
        cfg.update(python=interpreter.relative_to(root).as_posix())
        cfg.setdefault('enabled', True);cfg.setdefault('mode', 'legacy')
        cfg.setdefault('profile', 'data/astrbot');cfg.setdefault('port', 18769)
        destination(root, cfg['profile'])
        write(root/'configs/chat-engine.local.json', cfg)
        write(root/'data/setup/astrbot-environment.local.json', {
            'version':VERSION, 'python':cfg['python'], 'lock_sha256':digest,
            'versions':versions, 'at':time.time()})
        status('ready', '独立环境已准备好。首次仍为原有对话模式；基础账号接通后可在对话设置中切换。')
        return environment_state(root)
    except Exception as exc:
        write(state_file, {'phase':'failed', 'failure':type(exc).__name__, 'pid':os.getpid(), 'at':time.time(),
            'message':str(exc)[:180] if isinstance(exc, ValueError) else
            '独立环境安装未完成，原有配置保留。可重试；详细诊断留在本机 logs/setup。'})
        raise
    finally:
        if handle is not None and read(lock_file, {}).get('pid') == os.getpid():
            lock_file.unlink(missing_ok=True)


class OptionalEngineInstaller:
    def __init__(self, root):
        self.root = Path(root).resolve();self.lock = threading.Lock();self.process = None

    def state(self):
        return environment_state(self.root)

    def start(self):
        with self.lock:
            installation_root(self.root)
            current = self.state()
            if current['installed'] or current['busy'] or self.process and self.process.poll() is None:
                return current
            if read(self.root/'data/setup/update-progress.local.json', {}).get('phase') in {
                    'preparing','waiting_for_tasks','switching','validating','rolling_back'}:
                raise ValueError('请先等待当前升级收尾，再安装可选内核。')
            python = read(self.root/'settings.local.json', {})['python']
            logfile = destination(self.root, 'logs/setup/astrbot-installer.log');logfile.parent.mkdir(parents=True, exist_ok=True)
            write(self.root/'data/setup/astrbot-install.local.json', {
                'phase':'starting', 'message':'已开始准备独立对话环境。', 'at':time.time()})
            with logfile.open('ab') as stream:
                try:
                    self.process = subprocess.Popen([python, '-X', 'utf8', str(self.root/'scripts/install_astrbot.py'),
                        '--root', str(self.root)], cwd=self.root, stdin=subprocess.DEVNULL,
                        stdout=stream, stderr=stream,
                        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)|getattr(subprocess,'BELOW_NORMAL_PRIORITY_CLASS',0))
                except OSError:
                    write(self.root/'data/setup/astrbot-install.local.json', {
                        'phase':'failed','message':'独立安装程序未能启动，请重新运行本机启动器。','at':time.time()})
                    raise ValueError('可选安装程序未能启动，基础配置没有改动。') from None
            return self.state()
