"""Opt-in, project-local Windows bootstrap. Never imports QQ application modules."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import argparse
import hashlib
import html
import json
import os
import platform
import re
import socket
import struct
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent.parent
PORT = 18767
URL = f'http://127.0.0.1:{PORT}'
MARKER = '.whale-dependencies.json'


def interpreter_ok():
    return sys.version_info[:2] == (3, 12) and struct.calcsize('P') == 8


def python_path(root):
    return root / '.venv' / 'Scripts' / 'python.exe'


def fingerprint(root):
    return hashlib.sha256((root / 'requirements.txt').read_bytes()).hexdigest()


def identity(root):
    return hashlib.sha256(str(root.resolve()).casefold().encode()).hexdigest()


@contextmanager
def deployment_lock(root):
    path = root / '.whale-deploy.lock'
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise RuntimeError('Another deployment may be running. Close it first. If it crashed, remove only .whale-deploy.lock after checking Task Manager.') from None
    try:
        with os.fdopen(fd, 'w') as out:
            out.write(str(os.getpid()))
        yield
    finally:
        path.unlink(missing_ok=True)


VERIFY = r'''
import importlib.metadata as m,json,struct,sys
from pathlib import Path
from pip._vendor.packaging.requirements import Requirement
assert sys.version_info[:2]==(3,12) and struct.calcsize('P')==8
assert Path(sys.prefix).resolve()==Path(sys.argv[1]).resolve()
versions={}
for line in Path(sys.argv[2]).read_text().splitlines():
    line=line.strip()
    if not line or line.startswith('#'):continue
    r=Requirement(line)
    assert not r.url, 'URL requirements are not supported'
    if r.marker and not r.marker.evaluate():continue
    version=m.version(r.name)
    assert version in r.specifier, 'Dependency version mismatch'
    versions[r.name]=version
print(json.dumps(versions,sort_keys=True))
'''


def isolated_venv(root):
    try:
        if (root / '.venv').resolve().parent != root.resolve():
            return False
        cfg = (root / '.venv' / 'pyvenv.cfg').read_text(encoding='utf-8').lower()
        values = dict(line.split('=', 1) for line in cfg.splitlines() if '=' in line)
        return {k.strip(): v.strip() for k, v in values.items()}.get('include-system-site-packages') == 'false'
    except (OSError, ValueError):
        return False


def pip_environment():
    return dict(os.environ, PIP_CONFIG_FILE=os.devnull)


def verify_dependencies(root, run=subprocess.run):
    py = python_path(root)
    if not py.is_file() or not isolated_venv(root):
        return None
    try:
        result = run([str(py), '-I', '-c', VERIFY, str(root / '.venv'), str(root / 'requirements.txt')],
                     cwd=root, capture_output=True, text=True, timeout=60, check=True)
        versions = json.loads(result.stdout)
        run([str(py), '-I', '-m', 'pip', '--isolated', 'check'], cwd=root,
            capture_output=True, text=True, timeout=60, check=True, env=pip_environment())
        return versions
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def installed(root, run=subprocess.run):
    try:
        record = json.loads((root / '.venv' / MARKER).read_text())
        return record['requirements_sha256'] == fingerprint(root) and record['versions'] == verify_dependencies(root, run)
    except (OSError, ValueError, KeyError):
        return False


def install(root, accepted=False, run=subprocess.run):
    if installed(root, run):
        print('Dependencies already verified; no download or upgrade.')
        return
    if not accepted:
        raise RuntimeError('Installation needs explicit download approval. Run the menu and confirm installation.')
    lines = (root / 'requirements.txt').read_text().splitlines()
    if any(line.strip() and not line.lstrip().startswith('#') and not re.fullmatch(r'[A-Za-z0-9_.-]+(?:[<>=!~]+[0-9A-Za-z.*]+)(?:,[<>=!~]+[0-9A-Za-z.*]+)*', line.strip()) for line in lines):
        raise RuntimeError('Only reviewed named PyPI version requirements are supported; URL/options were rejected before installation.')
    env = root / '.venv'
    if env.exists() and (not (env / 'pyvenv.cfg').is_file() or not python_path(root).is_file()):
        raise RuntimeError('Existing .venv is incomplete or not recognized. It is preserved. Move it aside manually before retrying.')
    if not env.exists():
        run([sys.executable, '-I', '-m', 'venv', str(env)], cwd=root, check=True)
    if not isolated_venv(root):
        raise RuntimeError('The virtual environment is not isolated (system site packages). It is preserved; use a clean project-local environment.')
    run([str(python_path(root)), '-I', '-c', "import sys,struct;from pathlib import Path;assert sys.version_info[:2]==(3,12) and struct.calcsize('P')==8 and Path(sys.prefix).resolve()==Path(sys.argv[1]).resolve()", str(env)], cwd=root, check=True)
    # Never use --upgrade or fall back to the global interpreter. Wheels only: no source-build scripts.
    run([str(python_path(root)), '-I', '-m', 'pip', '--isolated', 'install',
         '--index-url', 'https://pypi.org/simple', '--only-binary=:all:',
         '--disable-pip-version-check', '-r', str(root / 'requirements.txt')], cwd=root, check=True, env=pip_environment())
    versions = verify_dependencies(root, run)
    if versions is None:
        raise RuntimeError('Dependency verification failed. Existing configuration and data were not changed; retry installation after fixing the error.')
    record = {'requirements_sha256': fingerprint(root), 'versions': versions}
    temporary = env / (MARKER + '.tmp')
    temporary.write_text(json.dumps(record, indent=2), encoding='utf-8')
    temporary.replace(env / MARKER)


def config_state(root):
    paths = [root / name for name in ('settings.local.json', 'qq.local.json', 'agent.local.json')]
    existing = sum(p.exists() for p in paths)
    # Do not read or display any private configuration values.
    return 'present (not validated)' if existing == 3 else ('partial: preserved; manual review required' if existing else 'not configured')


def configure(root, accepted=False, run=subprocess.run):
    if not accepted:
        raise RuntimeError('Configuration creates persistent local access tokens. Explicit operator confirmation is required.')
    # Refuse partial/existing configs before running the original interactive wizard.
    targets = ['settings.local.json', 'qq.local.json', 'agent.local.json', 'bot-runtime.local.json', 'configs/launcher.local.json']
    if any((root / name).exists() for name in targets):
        raise RuntimeError('Existing or partial local configuration is preserved. Edit it manually; this wizard never resets it.')
    if not installed(root, run):
        raise RuntimeError('Install and verify dependencies first.')
    run([str(python_path(root)), '-I', str(root / 'scripts' / 'configure.py'), '--skip-key'], cwd=root, check=True)


def snapshot(root):
    return {'product': 'whale-deployment-console-v1', 'instance': identity(root),
            'mode': 'setup-only-no-qq', 'configuration': config_state(root)}


def target(root):
    if config_state(root) == 'present (not validated)':
        return 18766, '/deployment/health', 'whale-safe-application-v1', 'safe-start', '/deployment'
    return PORT, '/health', 'whale-deployment-console-v1', 'setup-only-no-qq', '/'


def read_status(root):
    port, health, product, mode, _ = target(root)
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=1):
            pass
    except OSError:
        return 'stopped'
    try:
        # Ignore HTTP proxy environment; never send credentials or follow redirects.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(f'http://127.0.0.1:{port}' + health, timeout=2) as response:
            data = json.loads(response.read(4097))
        expected = {'product': product, 'mode': mode, 'instance': identity(root)}
        if all(data.get(k) == expected[k] for k in ('product', 'instance', 'mode')):
            return 'setup console running'
    except (OSError, ValueError):
        pass
    return 'port occupied or unrecognized; no process was stopped'


def handler(root):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.headers.get('Host') not in (f'127.0.0.1:{PORT}', f'localhost:{PORT}'):
                self.send_error(403)
                return
            state = snapshot(root)
            if self.path == '/health':
                content, kind = json.dumps(state).encode(), 'application/json'
            elif self.path == '/':
                text = html.escape(state['configuration'])
                content = ('''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>鲸娅 · 部署检查</title><body><main><h1>鲸娅 · 本机部署控制台</h1><h2>基础环境已启动，QQ 尚未连接</h2><p>这是安全的部署检查页，不是聊天服务，不会自动登录、调用模型、发送消息或恢复后台任务。</p><p>个人配置：''' + text + '''</p><ol><li>首次安装：关闭启动窗口并重新双击启动器，选择 2 创建本机配置。该步骤会明确询问是否创建访问凭据，不上传或显示凭据。</li><li>按仓库 README 手动安装 NapCat，核对测试账号、访问权限和 API 费用。此工具不会安装或登录 QQ。</li><li>配置完成后，重新双击启动器选择 1 进入安全管理页。不要把本页就绪当作 QQ 功能已经验证。</li></ol><p>重复双击启动器会复用此页；若端口被其他应用使用，将拒绝启动，不杀进程。</p><p>停止本页：关闭显示 Whale deployment console 提示的终端，或在该终端按 Ctrl+C。</p><p>个人配置、记忆和下载素材会保留。更新源码前请自行备份这些本机数据；不要把密钥上传 GitHub。</p></main></body></html>''').encode()
                kind = 'text/html; charset=utf-8'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(content)
    return Handler


def serve(root):
    # Binding itself arbitrates concurrent launches; never reuse an occupied port.
    with ThreadingHTTPServer(('127.0.0.1', PORT), handler(root)) as server:
        print('Whale deployment console (not the QQ service). Ctrl+C to stop.', flush=True)
        server.serve_forever()


def start(root, open_browser=True, spawn=subprocess.Popen, status=read_status):
    state = status(root)
    if state == 'stopped':
        if not installed(root):
            raise RuntimeError('Install dependencies first.')
        if target(root)[0] == 18766:
            env = dict(os.environ, WHALE_SAFE_START='1')
            command = [str(python_path(root)), '-I', '-m', 'uvicorn', 'phone_server:app', '--app-dir', str(root), '--host', '127.0.0.1', '--port', '18766', '--no-access-log']
        else:
            env = dict(os.environ)
            command = [str(python_path(root)), '-I', str(root / 'scripts' / 'deploy_windows.py'), 'serve']
        spawn(command, env=env, cwd=root, creationflags=getattr(subprocess, 'CREATE_NEW_CONSOLE', 0))
        for _ in range(40):
            time.sleep(.25)
            state = status(root)
            if state != 'stopped':
                break
    if state != 'setup console running':
        raise RuntimeError(state + '. Please inspect the console; no automatic retry.')
    port, _, _, _, page = target(root)
    url = f'http://127.0.0.1:{port}' + page
    print('Safe startup console ready. Check QQ status in the page: ' + url)
    if open_browser:
        webbrowser.open(url)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='menu', choices=['menu', 'install', 'status', 'start', 'configure', 'serve'])
    args = parser.parse_args(argv)
    if platform.system() != 'Windows' or not interpreter_ok():
        print('Requires Windows and 64-bit Python 3.12. Install it yourself from https://www.python.org/downloads/windows/ ; no system change was made.')
        return 1
    root = ROOT
    try:
        if args.action == 'serve':
            serve(root)
            return 0
        if args.action == 'status':
            print(read_status(root)); print('Configuration:', config_state(root))
            print('Dependencies:', 'verified' if installed(root) else 'installation/verification required')
            return 0
        with deployment_lock(root):
            action = args.action
            if action == 'menu':
                print('Whale Companion - safe local deployment\n1. Install / verify and open setup console\n2. Create first-time local configuration\n3. Status\n0. Exit')
                choice = input('Choose [1]: ').strip() or '1'
                if choice == '0': return 0
                if choice == '3':
                    print(read_status(root)); print('Configuration:', config_state(root)); return 0
                action = {'1': 'install', '2': 'configure'}.get(choice)
                if not action: raise RuntimeError('Unknown selection; nothing changed.')
            if action == 'install':
                approved = installed(root)
                if not approved:
                    print('Download base Python wheels from PyPI into this project .venv only. Network/data costs may apply. No QQ/CUDA/system installation or credentials.')
                    approved = input('Type INSTALL to approve: ').strip() == 'INSTALL'
                install(root, accepted=approved)
                start(root)
            elif action == 'configure':
                print('Creates new persistent LOCAL access tokens and QQ account settings. Nothing is sent externally. Existing files are never overwritten. No API key is requested.')
                approved = input('Type CREATE to approve local credential creation: ').strip() == 'CREATE'
                configure(root, accepted=approved)
                start(root)
            elif action == 'start':
                start(root)
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        # Do not print child arguments or private config values in error summaries.
        print('Deployment stopped:', str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__)
        return 1
    except (KeyboardInterrupt, EOFError):
        print('\nCancelled; existing data preserved.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
