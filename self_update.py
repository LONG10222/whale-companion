"""User-triggered application updates from this project's public GitHub releases.

This module never sends a chat message or uploads local files. Update discovery
is automatic; installation requires a click in the authenticated local page.
"""
from __future__ import annotations
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
import zipfile

REPOSITORY = 'LONG10222/whale-companion'
LATEST = 'https://api.github.com/repos/' + REPOSITORY + '/releases/latest'
MAX_ARCHIVE = 32 * 1024 * 1024
MAX_EXPANDED = 128 * 1024 * 1024
ACTIVE_PHASES = {'preparing', 'waiting_for_tasks', 'switching', 'validating', 'rolling_back'}
PRIVATE_PARTS = {'.git', '.venv', '.runtime', '.installer-cache', 'data', 'logs', 'cache',
                 'models', 'workspace', 'robot-instance', 'integrations', '__pycache__'}
PRIVATE_SUFFIXES = {'.sqlite', '.sqlite3', '.db', '.key', '.pem', '.pfx', '.wav', '.mp3',
                    '.mp4', '.onnx', '.gguf', '.pth', '.pt', '.ckpt', '.safetensors', '.lnk', '.exe', '.dll'}


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return default


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def version(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', str(value))
    if not match:
        raise ValueError('发布版本号格式不正确。')
    return tuple(map(int, match.groups()))


def application_path(name):
    if '\\' in name or ':' in name or '\x00' in name:
        raise ValueError('更新包路径不正确。')
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(p in {'', '.', '..'} for p in path.parts):
        raise ValueError('更新包不能写入安装目录以外的位置。')
    if (any(p.lower() in PRIVATE_PARTS for p in path.parts) or '.local.' in path.name.lower()
            or path.name.lower().startswith(('cookies', '.env'))
            or path.name.lower() in {'auth.json', '.release-owned'}
            or path.suffix.lower() in PRIVATE_SUFFIXES):
        raise ValueError('更新包包含个人数据或运行文件，未采用该包。')
    return path.as_posix()


def destination(root, name):
    root = Path(root).resolve()
    path = root.joinpath(*PurePosixPath(name).parts)
    resolved = path.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise ValueError('更新路径超出安装目录。')
    part = path
    while part != root:
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise ValueError('更新路径含目录链接，请先在本机处理。')
        part = part.parent
    return path


def select_release(payload, current):
    if payload.get('draft') or payload.get('prerelease'):
        return None
    tag = payload.get('tag_name', '')
    if version(tag) <= version(current):
        return None
    expected = 'WhaleCompanion-' + tag + '-windows-x64.zip'
    asset = next((a for a in payload.get('assets', []) if a.get('name') == expected), None)
    if not asset:
        return None
    digest = asset.get('digest', '')
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', digest) or not 0 < asset.get('size', 0) <= MAX_ARCHIVE:
        raise ValueError('新版下载尚无完整校验信息，请稍后再试。')
    url = asset.get('browser_download_url', '')
    expected_url = f'https://github.com/{REPOSITORY}/releases/download/{tag}/{expected}'
    if url != expected_url:
        raise ValueError('新版下载地址不属于当前项目。')
    html = payload.get('html_url', '')
    if html != f'https://github.com/{REPOSITORY}/releases/tag/{tag}':
        raise ValueError('发布说明地址不正确。')
    return {'tag': tag, 'version': tag.lstrip('v'), 'url': url, 'sha256': digest.split(':')[1],
            'bytes': asset['size'], 'release_url': html,
            'notes': str(payload.get('body') or '')[:6000]}


def validate_archive(archive, stage, expected_version):
    stage = Path(stage).resolve()
    stage.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive) as bundle:
        if len(bundle.infolist()) > 5000:
            raise ValueError('更新包文件数超出上限。')
        names = [entry.filename for entry in bundle.infolist() if not entry.is_dir()]
        if len(names) != len(set(n.lower() for n in names)) or 'release-manifest.json' not in names:
            raise ValueError('更新包重复文件或缺少发行清单。')
        entry = bundle.getinfo('release-manifest.json')
        if entry.file_size > 1024 * 1024:
            raise ValueError('发行清单过大。')
        manifest = json.loads(bundle.read(entry))
        if manifest.get('schema_version') != 1 or version(manifest.get('version')) != version(expected_version):
            raise ValueError('更新包版本和发布说明不一致。')
        records = manifest.get('files')
        if not isinstance(records, dict) or set(names) != set(records) | {'release-manifest.json'}:
            raise ValueError('更新包内容与发行清单不一致。')
        total = 0
        for item in bundle.infolist():
            application_path(item.filename.rstrip('/'))
            if (item.external_attr >> 16) & 0xF000 == 0xA000:
                raise ValueError('更新包中不能含文件链接。')
            total += item.file_size
            if total > MAX_EXPANDED or item.file_size > 16 * 1024 * 1024:
                raise ValueError('更新包展开大小超出上限。')
            if item.is_dir():
                continue
            data = bundle.read(item)
            if item.filename != 'release-manifest.json':
                record = records[item.filename]
                if record.get('bytes') != len(data) or record.get('sha256') != hashlib.sha256(data).hexdigest():
                    raise ValueError('更新包文件校验失败。')
            path = destination(stage, item.filename)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    required = {'phone_server.py', 'scripts/beginner_setup.py', 'scripts/bootstrap.ps1',
                'scripts/update_worker.py', 'self_update.py', 'configs/distribution.json', 'configs/release.json',
                'requirements-bootstrap.lock', 'maintenance_guard.py', 'feature_profiles.py'}
    if not required.issubset(records):
        raise ValueError('更新包缺少启动和回退所需文件。')
    return manifest


def active_tasks(root):
    total = 0
    for filename, table in [('qq-memory.sqlite3', 'events'), ('creative.sqlite3', 'jobs'),
                            ('tasks.sqlite3', 'tasks'), ('companion-life.sqlite3', 'jobs')]:
        path = Path(root) / 'data/qq' / filename
        if not path.exists():
            continue
        with closing(sqlite3.connect('file:' + path.as_posix() + '?mode=ro', uri=True, timeout=2)) as db:
            states = ('accepted', 'sending') if table == 'events' else ('accepted', 'running')
            total += db.execute(f'SELECT count(*) FROM {table} WHERE state IN (?,?)', states).fetchone()[0]
    return total


class UpdateService:
    def __init__(self, root, *, client_factory=None, clock=time.time):
        self.root = Path(root).resolve()
        self.folder = self.root / 'data/setup'
        self.cache = self.folder / 'update-check.local.json'
        self.progress = self.folder / 'update-progress.local.json'
        self.client_factory = client_factory
        self.clock = clock
        self.lock = threading.Lock()

    def current(self):
        return read(self.root / 'configs/release.json', {'version': '0.2.0'})['version']

    def state(self):
        check = read(self.cache, {})
        progress = read(self.progress, {'phase': 'idle'})
        release = check.get('release')
        available = bool(release and version(release['version']) > version(self.current()))
        return {'current': self.current(), 'available': available,
                'release': release if available else None, 'checked_at': check.get('at'),
                'check_message': check.get('message', '尚未检查新版。'), 'progress': progress,
                'can_upgrade': bool(read(self.folder / 'installation.local.json', {}).get('completed')),
                'automatic_check': True, 'automatic_install': False}

    def check(self, *, force=False):
        with self.lock:
            old = read(self.cache, {})
            if self.clock() - old.get('at', 0) < (60 if force else 6 * 3600):
                return self.state()
            import httpx
            factory = self.client_factory or httpx.Client
            record = {'at': self.clock(), 'release': None, 'message': '当前已经是已发布的最新版。'}
            try:
                with factory(timeout=15, follow_redirects=False) as client:
                    response = client.get(LATEST, headers={'Accept': 'application/vnd.github+json',
                                                          'User-Agent': 'WhaleCompanion-update-check'})
                    if response.status_code == 404:
                        record['message'] = '还没有可供自动升级的正式发行包；基础聊天不受影响。'
                    else:
                        response.raise_for_status()
                        record['release'] = select_release(response.json(), self.current())
                        if record['release']:
                            record['message'] = '发现新版 ' + record['release']['tag'] + '。'
            except Exception:
                # Keep a previously verified update visible when a later check is offline.
                record.update(release=old.get('release'), message='暂时连不到 GitHub，稍后会再检查；未执行升级。')
            atomic(self.cache, record)
            return self.state()

    def apply(self):
        with self.lock:
            state = self.state()
            if not state['can_upgrade']:
                raise ValueError('请先完成这份发行包的初次设置；不会接管已有的其他安装。')
            if state['progress']['phase'] in ACTIVE_PHASES:
                return state
            if not state['available']:
                raise ValueError('还没有校验完整的新版发行包，请先检查更新。')
            lockfile = self.folder / 'update-request.lock'
            self.folder.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(lockfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            except FileExistsError:
                raise ValueError('已有升级任务在准备或恢复，请查看当前进度。') from None
            os.close(fd)
            request = self.folder / 'update-request.local.json'
            atomic(request, {'root': str(self.root), 'release': state['release'], 'requested': self.clock(),
                             'maintenance_token': secrets.token_urlsafe(36)})
            atomic(self.progress, {'phase': 'preparing', 'message': '正在校验新版并准备备份，当前聊天继续运行。', 'at': self.clock()})
            try:
                subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                                str(self.root / 'scripts/run-update.ps1'), '-Root', str(self.root)],
                               check=True, timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except Exception:
                atomic(self.progress, {'phase': 'failed', 'message': '未能启动独立升级程序，原版本仍在运行。', 'at': self.clock()})
                lockfile.unlink(missing_ok=True)
                raise ValueError('升级程序未能启动，原版本没有改动。') from None
            return self.state()
