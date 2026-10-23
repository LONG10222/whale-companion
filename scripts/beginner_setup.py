"""Local first-run wizard and small supervisor for the public Windows bundle.

No QQ messages are sent by this wizard. A friend chosen in this local page is
the initial administrator; first incoming messages never claim ownership.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import http.cookies
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
MARKER = 'whale-setup-v1'
MAX_BODY = 8192
QQ_RE = re.compile(r'^[1-9]\d{4,11}$')


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return default


def child_path(path, parent):
    path, parent = Path(path), Path(parent).resolve()
    if not path.is_absolute():
        path = parent / path
    resolved = path.resolve()
    if resolved == parent or not resolved.is_relative_to(parent):
        raise ValueError('目录必须位于本安装目录内。')
    # Reject links even when the link currently resolves within our root.
    current = path
    while current != parent and current.parent != current:
        if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
            raise ValueError('安装目录中不能使用目录链接。')
        current = current.parent
    return resolved


def protect_private(path):
    if os.name != 'nt':
        os.chmod(path, 0o600)
        return
    answer = subprocess.check_output(
        ['whoami.exe', '/user', '/fo', 'csv', '/nh'],
        creationflags=subprocess.CREATE_NO_WINDOW,
    ).decode(errors='replace')
    sid = re.search(r'\bS-\d+(?:-\d+){2,}\b', answer)
    if not sid:
        raise RuntimeError('无法确认当前 Windows 用户，未开放本机设置。')
    result = subprocess.run(
        ['icacls.exe', str(path), '/inheritance:r', '/grant:r',
         '*' + sid.group() + ':F', '*S-1-5-18:F'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise RuntimeError('未能限制本机配置文件的读取权限。')


def write_json(path, data, *, private=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + secrets.token_hex(6) + '.tmp')
    try:
        with temp.open('x', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
        if private:
            protect_private(temp)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def prepare(root, python=None):
    root = Path(root).resolve()
    marker = root / 'data/setup/installation.local.json'
    existing = load_json(marker)
    if existing:
        if existing.get('root') != str(root):
            raise ValueError('这个目录含有另一位置的本机数据；请保留原目录，重新解压一份干净发行包。')
        if existing.get('phase') != 'preparing':
            return existing
    # Never take over an author's live installation or reset an existing account.
    for name in ['settings.local.json', 'qq.local.json', 'agent.local.json', 'configs/qq-client.local.json']:
        if not existing and (root / name).exists():
            raise ValueError('已有个人配置，安装向导没有覆盖。请使用原启动器，或解压到新目录。')
    python = Path(python or sys.executable).resolve()
    client = child_path(root / '.runtime/qq', root)
    if not (client / 'node.exe').is_file():
        raise ValueError('QQ 运行包尚未准备好，请重新运行 Install.cmd。')
    result = existing or {'version': 1, 'root': str(root), 'instance': secrets.token_hex(16),
                          'completed': False, 'created': time.time(), 'phase': 'preparing'}
    write_json(marker, result)
    def put(path, value):
        path = Path(path)
        if path.exists():
            protect_private(path)
            return load_json(path)
        write_json(path, value)
        return value
    settings = load_json(root / 'configs/settings.example.json', {})
    recommended = None
    if settings:
        from feature_profiles import hardware_snapshot, recommendation
        recommended = recommendation(hardware_snapshot())
        settings.update(recommended['settings'])
    settings.update(python=str(python), dania_python=str(python), api_token=secrets.token_urlsafe(36),
                    host='127.0.0.1', port=18765)
    onebot = secrets.token_urlsafe(36)
    put(root / 'settings.local.json', settings)
    qq = put(root / 'qq.local.json', {'enabled': False, 'bot_id': '', 'allowed_private_ids': [],
                                    'voice_enabled': False, 'onebot_token': onebot})
    onebot = qq['onebot_token']
    put(root / 'agent.local.json', {'api_key': '', 'model': 'deepseek-chat'})
    put(root / 'bot-runtime.local.json', {})
    put(root / 'configs/qq-client.local.json', {
        'mode': 'portable-node', 'client_root': str(client), 'boot': str(client / 'node.exe'),
        'qq_data': str(root / 'data/qq-client'), 'napcat_root': str(client / 'napcat'),
    })
    nc_config = child_path(client / 'napcat/config', root)
    if any(nc_config.glob('onebot11_*.json')):
        raise ValueError('下载运行包里出现已有账号配置，未采用该包。')
    put(nc_config / 'webui.json', {'host': '127.0.0.1', 'port': 6099,
                                'token': secrets.token_urlsafe(36)})
    put(nc_config / 'onebot11.json', {'network': {
        'httpServers': [], 'httpSseServers': [], 'httpClients': [], 'websocketClients': [],
        'websocketServers': [{'name': 'whale-local', 'enable': True, 'host': '127.0.0.1', 'port': 3001,
                              'messagePostFormat': 'array', 'reportSelfMessage': False,
                              'enableForcePushEvent': True, 'token': onebot, 'debug': False}],
        'plugins': [],
    }})
    catalog = root / 'assets/stickers/catalog.json'
    if not catalog.exists():
        write_json(catalog, load_json(root / 'assets/stickers/catalog.example.json', {'items': []}))
    # Self-directed browsing/phone use is an optional owner choice, not first-run consent.
    put(root / 'configs/beginner-features.local.json', {
        'voice': False, 'codex': False, 'autonomy': False,
        'note': 'Base chat only. Enable optional features in the local dashboard after configuring their own dependencies.',
    })
    if recommended:
        put(root / 'configs/feature-profile.local.json', {
            'version': 1, 'mode': 'balanced', 'selected': recommended['selected'], 'overrides': {},
            'settings': recommended['settings'], 'at': time.time(), 'dependencies_installed': False})
        put(root / 'configs/local-learning.local.json', {**recommended['learning'], 'mode': 'paused'})
        if not (root / 'configs/persona.local.json').exists():
            from persona_store import dania_card
            persona = dania_card().model_copy(update={'name': '鲸娅'})
            put(root / 'configs/persona.local.json', persona.model_dump(mode='json'))
    result['phase'] = 'prepared'
    write_json(marker, result)
    return result


class LocalPolicy:
    def __init__(self, port, token=None, clock=time.time):
        self.origin = f'http://127.0.0.1:{port}'
        self.token = token or secrets.token_urlsafe(36)
        self.cookie = secrets.token_urlsafe(36)
        self.clock = clock
        self.expires = clock() + 12 * 3600

    def same_origin(self, host, origin, site, client):
        return (client in {'127.0.0.1', '::1'} and host == urlsplit(self.origin).netloc
                and origin in {None, self.origin} and site in {None, 'none', 'same-origin'})

    def authorized(self, headers, client):
        if not self.same_origin(headers.get('Host'), headers.get('Origin'),
                                headers.get('Sec-Fetch-Site'), client):
            return False
        try:
            cookies = http.cookies.SimpleCookie(headers.get('Cookie', ''))
            value = cookies.get('whale_setup_session')
            return bool(value and self.clock() < self.expires
                        and hmac.compare_digest(value.value, self.cookie)
                        and headers.get('X-Whale-Setup') == MARKER)
        except http.cookies.CookieError:
            return False


class SetupLock:
    def __init__(self, root):
        self.root, self.handle = Path(root), None

    def acquire(self):
        if os.name != 'nt':
            raise RuntimeError('安装服务目前只支持 Windows。')
        import ctypes
        from ctypes import wintypes
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateMutexW.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        key = hashlib.sha256(str(self.root).lower().encode()).hexdigest()[:16]
        self.handle = self.kernel.CreateMutexW(None, False, 'Local\\WhaleSetupHost-' + key)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        return ctypes.get_last_error() != 183

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class ManagedProcess:
    """Observe/launch only one exact executable, working directory and argument list."""
    def __init__(self, root, name, argv, cwd, ports, env=None):
        self.root, self.name = Path(root), name
        self.argv, self.cwd, self.ports = list(map(str, argv)), Path(cwd).resolve(), ports
        self.env, self.child = env, None
        self.error = ''
        self.attempts = []
        self.last_try = 0

    def owns(self, process):
        import psutil
        try:
            args = process.cmdline()
            return (Path(process.exe()).resolve() == Path(self.argv[0]).resolve()
                    and Path(process.cwd()).resolve() == self.cwd and args == self.argv)
        except (psutil.Error, OSError, ValueError):
            return False

    def tick(self):
        import psutil
        owned = [p for p in psutil.process_iter() if self.owns(p)]
        if owned:
            self.error = ''
            return 'running'
        occupied = {c.laddr.port for c in psutil.net_connections(kind='tcp')
                    if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port in self.ports}
        if occupied:
            self.error = '端口被其他程序占用：' + ', '.join(map(str, sorted(occupied))) + '。未关闭任何其他程序。'
            return 'port_conflict'
        now = time.time()
        file = self.root / 'data/setup' / (self.name + '-retries.local.json')
        state = load_json(file, {})
        self.attempts = [n for n in state.get('attempts', []) if 0 <= now - n < 900]
        if len(self.attempts) >= 3 or now - state.get('last_try', 0) < 30:
            self.error = '启动失败已退避，保留登录数据，稍后会再检查。'
            return 'backoff'
        folder = self.root / 'logs/setup'
        folder.mkdir(parents=True, exist_ok=True)
        self.attempts.append(now)
        write_json(file, {'attempts': self.attempts, 'last_try': now})
        # Log bounded output, never the launch URL, credentials or exception bodies.
        log = folder / (self.name + '.log')
        if log.exists() and log.stat().st_size > 2 * 1024 * 1024:
            old = log.with_suffix('.previous.log')
            os.replace(log, old)
        with log.open('ab') as stream:
            self.child = subprocess.Popen(
                self.argv, cwd=self.cwd, env=self.env,
                stdin=subprocess.DEVNULL, stdout=stream, stderr=stream,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        return 'starting'


class SetupEngine:
    def __init__(self, root, *, prepare_files=True):
        self.root = Path(root).resolve()
        self.lock = threading.RLock()
        self.install = prepare(root) if prepare_files else load_json(self.root / 'data/setup/installation.local.json')
        self.config = load_json(self.root / 'configs/qq-client.local.json')
        self.credential = ''
        self.key_error = ''
        self.key_checked = False
        self.last_qr = 0
        self.runtime = None
        self.qq_state = 'not_started'
        self.api_state = 'not_started'
        self.voice = None
        self.voice_settings_hash = ''
        from self_update import UpdateService
        self.updates = UpdateService(self.root)
        from feature_profiles import FeatureProfiles
        self.features = FeatureProfiles(self.root)

    def services(self):
        if self.runtime is not None:
            return self.runtime
        client = child_path(self.config['client_root'], self.root)
        data = child_path(self.config['qq_data'], self.root)
        env = dict(os.environ, WHALE_INSTALL_ROOT=str(self.root), WHALE_QQ_CLIENT=str(client),
                   WHALE_QQ_DATA=str(data), PYTHONUTF8='1', NO_PROXY='127.0.0.1,localhost,::1')
        env['PATH']=str(self.root/'.runtime/python')+os.pathsep+env.get('PATH','')
        qq = ManagedProcess(self.root, 'qq', [client / 'node.exe', self.root / 'scripts/portable_qq.cjs'],
                            client, [6099, 3001], env)
        python = load_json(self.root / 'settings.local.json')['python']
        api = ManagedProcess(self.root, 'api', [python, '-X', 'utf8', '-m', 'uvicorn', 'phone_server:app',
                                              '--host', '127.0.0.1', '--port', '18766', '--no-access-log'],
                             self.root, [18766], env)
        self.runtime = qq, api
        return self.runtime

    def tick(self):
        qq, api = self.services()
        self.qq_state = qq.tick()
        self.install = load_json(self.root / 'data/setup/installation.local.json')
        progress = load_json(self.root / 'data/setup/update-progress.local.json', {})
        if self.install.get('completed') and progress.get('phase') != 'switching':
            self.api_state = api.tick()
            selected = load_json(self.root / 'configs/feature-profile.local.json', {}).get('selected', [])
            if any(name in selected for name in ['voice_output', 'singing', 'learning']):
                readiness = self.features.readiness()
                if any(name in selected and readiness[name]['ready'] for name in ['voice_output', 'singing', 'learning']):
                    self.ensure_voice()

    def ensure_voice(self):
        settings = load_json(self.root / 'settings.local.json')
        names = ['cpu_threads', 'gpu_memory_fraction', 'idle_seconds', 'min_free_vram_mib',
                 'cache_max_mib', 'cache_ttl_hours']
        fingerprint = hashlib.sha256(json.dumps({k: settings.get(k) for k in names}, sort_keys=True).encode()).hexdigest()
        if self.voice is not None and self.voice_settings_hash != fingerprint:
            import httpx
            import psutil
            owned = [p for p in psutil.process_iter() if self.voice.owns(p)]
            if owned:
                try:
                    with httpx.Client(trust_env=False, timeout=2) as client:
                        response = client.get('http://127.0.0.1:18765/health',
                                              headers={'Authorization': 'Bearer ' + settings['api_token']})
                        response.raise_for_status()
                        health = response.json()
                    if health.get('worker_loaded') or health.get('audio_busy') or health.get('learning_running'):
                        return  # Finish audio and let its worker unload before applying new limits.
                    for process in owned:
                        if self.voice.owns(process):
                            process.terminate()
                            process.wait(timeout=10)
                except Exception:
                    return
            self.voice = None
        if self.voice is None:
            self.voice = ManagedProcess(self.root, 'voice', [settings['python'], '-X', 'utf8',
                self.root / 'voice_server.py', '--config', self.root / 'settings.local.json'], self.root, [18765])
            self.voice_settings_hash = fingerprint
        self.voice.tick()

    def napcat(self, route, body=None):
        import httpx
        nc = child_path(Path(self.config['napcat_root']) / 'config/webui.json', self.root)
        ui = load_json(nc)
        with httpx.Client(trust_env=False, timeout=6) as client:
            for attempt in range(2):
                if not self.credential:
                    response = client.post('http://127.0.0.1:6099/api/auth/login', json={
                        'hash': hashlib.sha256((ui['token'] + '.napcat').encode()).hexdigest()})
                    response.raise_for_status()
                    self.credential = (response.json().get('data') or {}).get('Credential', '')
                    if not self.credential:
                        raise ValueError('独立 QQ 尚未就绪。')
                response = client.post('http://127.0.0.1:6099/api/' + route, json=body or {},
                                       headers={'Authorization': 'Bearer ' + self.credential})
                data = response.json()
                if response.status_code in {401, 403} or data.get('message') in {'Unauthorized', 'Token expired'}:
                    self.credential = ''
                    if attempt == 0:
                        continue
                response.raise_for_status()
                if data.get('code') != 0:
                    raise ValueError('QQ 未完成这次操作，请按本地页面和手机提示处理。')
                return data.get('data') or {}

    def onebot(self, action):
        # The setup wizard exposes a closed list of read-only discovery calls.
        if action not in {'get_friend_list', 'get_login_info'}:
            raise ValueError('安装向导只允许读取登录账号和好友目录。')
        from websockets.sync.client import connect
        token = load_json(self.root / 'qq.local.json')['onebot_token']
        echo = secrets.token_hex(12)
        with connect('ws://127.0.0.1:3001', additional_headers={'Authorization': 'Bearer ' + token},
                     open_timeout=5, close_timeout=2, max_size=1024 * 1024) as websocket:
            websocket.send(json.dumps({'action': action, 'params': {}, 'echo': echo}))
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                item = json.loads(websocket.recv(timeout=max(0.1, deadline - time.monotonic())))
                if item.get('echo') == echo:
                    if item.get('status') != 'ok' or item.get('retcode') != 0:
                        raise ValueError('QQ 好友目录暂未就绪，稍后再试。')
                    return item.get('data')
        raise ValueError('QQ 目录读取超时，稍后再试。')

    def save_key(self, key):
        import httpx
        key = str(key).strip()
        if not key.startswith('sk-') or not 24 <= len(key) <= 200:
            raise ValueError('请填写完整的 DeepSeek API Key。')
        with self.lock:
            try:
                with httpx.Client(timeout=20) as client:
                    response = client.get('https://api.deepseek.com/models',
                                          headers={'Authorization': 'Bearer ' + key})
                    response.raise_for_status()
                    models = {r.get('id') for r in response.json().get('data', [])}
                selected = next((m for m in ('deepseek-flash', 'deepseek-chat') if m in models), None)
                if not selected:
                    raise ValueError('这个 Key 尚未返回可用的聊天模型，未保存为已验证。')
                config = load_json(self.root / 'agent.local.json', {})
                config.update(api_key=key, model=selected)
                write_json(self.root / 'agent.local.json', config)
                self.key_checked = True
            except httpx.HTTPStatusError:
                raise ValueError('DeepSeek 未接受这个 Key，请在官方平台核对。') from None
            except httpx.RequestError:
                raise ValueError('暂时连不到 DeepSeek，请检查网络后重试；Key 没有发给其他网站。') from None
        return {'saved': True, 'verified': True, 'model': selected}

    def friends(self):
        friends = self.onebot('get_friend_list')
        if not isinstance(friends, list):
            raise ValueError('QQ 好友目录返回格式不正确。')
        return [{'qq': str(f['user_id']), 'name': str(f.get('nickname') or f['user_id'])[:64]}
                for f in friends[:2000] if QQ_RE.fullmatch(str(f.get('user_id', '')))]

    def finish(self, administrator):
        with self.lock:
            self.install = load_json(self.root / 'data/setup/installation.local.json')
            if self.install.get('completed'):
                return {'completed': True, 'dashboard': 'http://127.0.0.1:18766/qq'}
            if not load_json(self.root / 'agent.local.json', {}).get('api_key'):
                raise ValueError('请先保存并验证 DeepSeek Key。')
            login = self.onebot('get_login_info') or {}
            bot = str(login.get('user_id', ''))
            administrator = str(administrator)
            if not QQ_RE.fullmatch(bot) or administrator == bot:
                raise ValueError('请先登录独立机器人 QQ，再选择不同的管理账号。')
            if administrator not in {f['qq'] for f in self.friends()}:
                raise ValueError('管理账号必须先添加机器人为好友，再从当前好友目录确认。')
            config = load_json(self.root / 'qq.local.json')
            config.update(bot_id=bot, locked_bot_id=bot, enabled=True, voice_enabled=False,
                          allowed_private_ids=[administrator],
                          test_contact={'name': 'long', 'user_id': administrator})
            write_json(self.root / 'qq.local.json', config)
            # Store an account-specific copy without restarting a logged-in client.
            folder = child_path(Path(self.config['napcat_root']) / 'config', self.root)
            default = load_json(folder / 'onebot11.json')
            target = folder / ('onebot11_' + bot + '.json')
            if not target.exists():
                write_json(target, default)
            self.install.update(completed=True, completed_at=time.time())
            write_json(self.root / 'data/setup/installation.local.json', self.install)
            return {'completed': True, 'dashboard': 'http://127.0.0.1:18766/qq'}

    def status(self):
        installation = load_json(self.root / 'data/setup/installation.local.json')
        state = {'completed': installation.get('completed', False), 'qq_runtime': self.qq_state,
                 'test_instance': bool(installation.get('test_instance', False)),
                 'api_runtime': self.api_state, 'key_saved': bool(load_json(self.root / 'agent.local.json', {}).get('api_key')),
                 'logged_in': False, 'qr_ready': False, 'message': '正在启动独立 QQ，请稍等。',
                 'updates': self.updates.state(),
                 'optional': {'codex_installed': bool(shutil.which('codex')), 'voice': False,
                              'note': '绘图需自己的 Codex 登录和项目配置；语音需自己的模型。基础聊天不需要。'}}
        if self.qq_state == 'port_conflict':
            state['message'] = self.services()[0].error
            return state
        try:
            login = self.napcat('QQLogin/CheckLoginStatus')
            state['logged_in'] = bool(login.get('isLogin'))
            state['phase'] = login.get('loginPhase', '')
            image = child_path(Path(self.config['napcat_root']) / 'cache/qrcode.png', self.root)
            state['qr_ready'] = image.is_file()
            state['qr_version'] = str(image.stat().st_mtime_ns) if image.is_file() else ''
            pending = bool(login.get('qrLoginAccepted') or state['phase'] in {
                'qrcode_scanned', 'initializing', 'reconnecting', 'captcha', 'new_device'})
            if not state['logged_in'] and not pending and time.time() - self.last_qr > 90:
                self.napcat('QQLogin/RefreshQRcode')
                self.last_qr = time.time()
            state['message'] = ('QQ 已登录，请选择你的管理账号。' if state['logged_in']
                                else '已扫码，按手机提示确认；当前流程不会被重复刷新。' if pending
                                else '请用专门的机器人 QQ 扫码，并在手机确认。')
        except Exception:
            if self.qq_state == 'backoff':
                state['message'] = self.services()[0].error
        if installation.get('completed'):
            state['message'] = '设置已保存。机器人只回复已确认的管理账号，群聊默认关闭。'
        return state


class SetupHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, engine):
        super().__init__(address, Handler)
        self.engine = engine
        self.policy = LocalPolicy(self.server_port)
        self.session_file = None
        if isinstance(getattr(engine, 'root', None), Path):
            self.session_file = engine.root / 'data/setup/browser-session.local.json'
            previous = load_json(self.session_file, {})
            if previous.get('expires', 0) > time.time() and isinstance(previous.get('cookie'), str):
                self.policy.cookie = previous['cookie']
                self.policy.expires = previous['expires']
            self.persist_session()
        self.slots = threading.BoundedSemaphore(24)

    def persist_session(self):
        if self.session_file:
            write_json(self.session_file, {'cookie': self.policy.cookie, 'expires': self.policy.expires})

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = 'WhaleSetup'

    def setup(self):
        super().setup()
        self.connection.settimeout(8)

    def log_message(self, *args):
        pass  # No URLs, credentials, friend names or request bodies in access logs.

    def respond(self, code, body, content_type='application/json; charset=utf-8', cookie=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; form-action 'self'")
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        size = int(self.headers.get('Content-Length', 0))
        if not 0 < size <= MAX_BODY or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            raise ValueError('请求格式不正确。')
        return json.loads(self.rfile.read(size))

    def safe_host(self):
        policy = self.server.policy
        return policy.same_origin(self.headers.get('Host'), self.headers.get('Origin'),
                                  self.headers.get('Sec-Fetch-Site'), self.client_address[0])

    def do_GET(self):
        if not self.safe_host():
            return self.respond(403, {'error': '请从本机安装页面打开。'})
        route = urlsplit(self.path).path
        public = {'/': ('beginner.html', 'text/html; charset=utf-8'),
                  '/setup.js': ('beginner_ui.js', 'application/javascript; charset=utf-8'),
                  '/setup.css': ('beginner.css', 'text/css; charset=utf-8')}
        if route in public:
            name, kind = public[route]
            return self.respond(200, (Path(__file__).parent / name).read_bytes(), kind)
        if route == '/health':
            return self.respond(200, {'instance': self.server.engine.install['instance'], 'state': 'ready'})
        if not self.server.policy.authorized(self.headers, self.client_address[0]):
            return self.respond(401, {'error': '请从桌面快捷方式重新打开，连接凭据不会显示在页面里。'})
        try:
            if route == '/state':
                return self.respond(200, self.server.engine.status())
            if route == '/friends':
                return self.respond(200, {'friends': self.server.engine.friends()})
            if route == '/updates':
                return self.respond(200, self.server.engine.updates.state())
            if route == '/features/state':
                return self.respond(200, self.server.engine.features.state())
            if route == '/qr':
                file = child_path(Path(self.server.engine.config['napcat_root']) / 'cache/qrcode.png', self.server.engine.root)
                if not file.is_file() or file.stat().st_size > 1024 * 1024:
                    return self.respond(404, {'error': '二维码尚未就绪。'})
                return self.respond(200, file.read_bytes(), 'image/png')
            return self.respond(404, {'error': '没有这个页面。'})
        except Exception:
            return self.respond(503, {'error': '独立 QQ 正在准备或重连，请稍后再试。'})

    def do_POST(self):
        # Read a bounded body before refusing a request, so Windows does not
        # reset the socket while a small request body is still unread.
        try:
            data = self.body()
            if not isinstance(data, dict):
                raise ValueError('请求格式不正确。')
        except (ValueError, json.JSONDecodeError):
            return self.respond(400, {'error': '请求格式或长度不正确。'})
        if not self.safe_host() or self.headers.get('X-Whale-Setup') != MARKER:
            return self.respond(403, {'error': '请从本机安装页面操作。'})
        route = urlsplit(self.path).path
        try:
            if route == '/session':
                if not hmac.compare_digest(str(data.get('token', '')), self.server.policy.token):
                    return self.respond(401, {'error': '启动连接已失效，请重新运行桌面快捷方式。'})
                self.server.policy.expires = self.server.policy.clock() + 12 * 3600
                self.server.persist_session()
                cookie = 'whale_setup_session=' + self.server.policy.cookie + '; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200'
                return self.respond(200, {'connected': True}, cookie=cookie)
            if not self.server.policy.authorized(self.headers, self.client_address[0]):
                return self.respond(401, {'error': '请从桌面快捷方式重新打开。'})
            if route == '/key':
                return self.respond(200, self.server.engine.save_key(data.get('api_key', '')))
            if route == '/finish':
                return self.respond(200, self.server.engine.finish(data.get('administrator', '')))
            if route == '/qr/refresh':
                state = self.server.engine.status()
                return self.respond(200, state)
            if route == '/updates/check':
                return self.respond(200, self.server.engine.updates.check(force=True))
            if route == '/updates/apply':
                return self.respond(200, self.server.engine.updates.apply())
            if route == '/features/profile':
                return self.respond(200, self.server.engine.features.save(
                    data.get('mode'), data.get('selected'), data.get('overrides')))
            if route == '/features/preview':
                from feature_profiles import recommendation
                return self.respond(200, recommendation(self.server.engine.features.hardware(), data.get('mode')))
            return self.respond(404, {'error': '没有这个操作。'})
        except (ValueError, json.JSONDecodeError) as exc:
            return self.respond(400, {'error': str(exc)[:200]})
        except Exception:
            return self.respond(503, {'error': '这次设置未完成，原有配置保留。请稍后重试。'})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--port', type=int, default=18767)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.prepare:
        prepare(root)
        print('Local setup prepared. No keys or account IDs were printed.')
        return
    sys.path.insert(0, str(root))
    lock = SetupLock(root)
    if not lock.acquire():
        lock.close()
        return
    try:
        engine = SetupEngine(root)
        server = None
        for port in range(args.port, args.port + 10):
            try:
                server = SetupHTTPServer(('127.0.0.1', port), engine)
                break
            except OSError:
                continue
        if server is None:
            raise RuntimeError('本机安装页面端口被占用。')
        write_json(root / 'data/setup/host.local.json', {'pid': os.getpid(), 'port': server.server_port,
                   'instance': engine.install['instance'], 'launch_token': server.policy.token, 'at': time.time()})
        def maintain():
            while True:
                try:
                    engine.tick()
                except Exception:
                    engine.qq_state = 'start_failed'
                write_json(root / 'data/setup/runtime.local.json', {
                    'at': time.time(), 'pid': os.getpid(), 'qq': engine.qq_state, 'api': engine.api_state})
                time.sleep(5)
        threading.Thread(target=maintain, daemon=True).start()
        def check_updates():
            while True:
                try:
                    engine.updates.check()
                except Exception:
                    pass
                time.sleep(60)
        threading.Thread(target=check_updates, daemon=True).start()
        server.serve_forever(poll_interval=1)
    finally:
        lock.close()


if __name__ == '__main__':
    main()
