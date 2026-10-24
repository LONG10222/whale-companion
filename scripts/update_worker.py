"""Independent update task. Preserve user data; restore the prior app on failure."""
from __future__ import annotations
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from self_update import (REPOSITORY, MAX_ARCHIVE, active_tasks, application_path,
                         atomic, destination, read, validate_archive, version)
from beginner_setup import write_json as write_private


class Upgrade:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.data = self.root / 'data/setup'
        self.progress_file = self.data / 'update-progress.local.json'
        self.request = read(self.data / 'update-request.local.json')
        self.release = self.request['release']
        self.settings = read(self.root / 'settings.local.json')
        self.id = str(int(time.time())) + '-' + self.release['tag']
        self.work = destination(self.root, 'data/update-staging/' + self.id)
        self.backup = destination(self.root, 'data/update-backups/' + self.id)
        self.was_enabled = None
        self.switched = False
        self.stopped = False
        self.owned_voice = False
        self.changed = []
        self.previous_manifest = read(self.root / 'release-manifest.json', {})

    def status(self, phase, message):
        atomic(self.progress_file, {'phase': phase, 'message': message, 'at': time.time(),
                                    'target': self.release['tag'], 'operation': self.id})

    def api(self, route, body=None):
        import httpx
        headers = {'Authorization': 'Bearer ' + self.settings['api_token'],
                   'X-Whale-Updater': str(self.request.get('maintenance_token', ''))}
        with httpx.Client(trust_env=False, timeout=8) as client:
            if body is None:
                response = client.get('http://127.0.0.1:18766' + route, headers=headers)
            else:
                response = client.post('http://127.0.0.1:18766' + route, json=body, headers=headers)
            response.raise_for_status()
            return response.json()

    def set_replies(self, enabled):
        cfg = read(self.root / 'qq.local.json')
        return self.api('/qq/config', {'enabled': enabled, 'bot_id': cfg['bot_id'],
                                      'peers': ','.join(cfg['allowed_private_ids']),
                                      'voice_enabled': cfg.get('voice_enabled', False)})

    def voice_idle(self):
        import httpx
        import psutil
        pids = set()
        for process in psutil.process_iter():
            try:
                if (Path(process.cwd()).resolve() == self.root and Path(process.exe()).resolve().is_relative_to(self.root)
                        and any(Path(arg).resolve() == self.root / 'voice_server.py'
                                for arg in process.cmdline()[1:] if not arg.startswith('-'))):
                    pids.add(process.pid)
            except (psutil.Error, OSError, ValueError):
                pass
        if not pids:
            self.owned_voice = False
            return True
        self.owned_voice = True
        listeners = [c for c in psutil.net_connections(kind='tcp')
                     if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == 18765]
        if not listeners or any(c.pid not in pids for c in listeners):
            return False
        with httpx.Client(trust_env=False, timeout=4) as client:
            headers = {'Authorization': 'Bearer ' + self.settings['api_token'],
                       'X-Whale-Updater': str(self.request.get('maintenance_token', ''))}
            response = client.get('http://127.0.0.1:18765/health', headers=headers)
            response.raise_for_status()
            health = response.json()
            return not health.get('audio_busy') and not health.get('learning_running')

    def engine_idle(self):
        from chat_engine import ChatEngine
        engine=ChatEngine(self.root)
        if not engine.owns_listener():
            return True
        state=engine.status().get('kernel',{})
        return state.get('ok') is True and not state.get('busy') and not state.get('pending')

    def download(self):
        import httpx
        tag = self.release['tag']
        version(tag)
        expected = f'https://github.com/{REPOSITORY}/releases/download/{tag}/WhaleCompanion-{tag}-windows-x64.zip'
        if self.release['url'] != expected or self.request['root'] != str(self.root):
            raise ValueError('升级目标不匹配，原版本没有改动。')
        if time.time() - self.request['requested'] > 3600:
            raise ValueError('这次升级请求已过期，请重新点击检查更新。')
        self.work.mkdir(parents=True, exist_ok=False)
        archive = self.work / 'release.zip'
        sha = hashlib.sha256()
        total = 0
        with httpx.Client(timeout=60, follow_redirects=True) as client:
            with client.stream('GET', expected, headers={'User-Agent': 'WhaleCompanion-updater'}) as response:
                response.raise_for_status()
                if response.url.scheme != 'https' or response.url.host not in {
                    'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}:
                    raise ValueError('更新下载没有保持官方 HTTPS 来源。')
                with archive.open('xb') as stream:
                    for part in response.iter_bytes(128 * 1024):
                        total += len(part)
                        if total > MAX_ARCHIVE or total > self.release['bytes']:
                            raise ValueError('更新包大小超出发布记录。')
                        sha.update(part)
                        stream.write(part)
        if total != self.release['bytes'] or sha.hexdigest() != self.release['sha256']:
            raise ValueError('新版下载校验失败；原版本继续运行。')
        stage = self.work / 'application'
        manifest = validate_archive(archive, stage, self.release['version'])
        old_dist = read(self.root / 'configs/distribution.json')
        new_dist = read(stage / 'configs/distribution.json')
        if any(old_dist[k]['sha256'] != new_dist[k]['sha256'] for k in ['python', 'qq']):
            raise ValueError('新版同时变更了 Python 或 QQ 运行组件，需要使用完整新版安装包；当前配置和版本已保留。')
        return stage, manifest

    def prepare_environment(self, stage):
        old_lock = (self.root / 'requirements-bootstrap.lock').read_bytes()
        new_lock = (stage / 'requirements-bootstrap.lock').read_bytes()
        if hashlib.sha256(old_lock).digest() == hashlib.sha256(new_lock).digest():
            return self.settings['python']
        folder = destination(self.root, '.runtime/envs/' + self.id)
        base = self.root / '.runtime/python/python.exe'
        subprocess.run([str(base), '-m', 'venv', str(folder)], check=True, timeout=120,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        python = folder / 'Scripts/python.exe'
        log = self.work / 'dependencies.private.log'
        with log.open('wb') as stream:
            subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
                            '--only-binary=:all:', '--require-hashes', '--index-url', 'https://pypi.org/simple',
                            '-r', str(stage / 'requirements-bootstrap.lock')], check=True, timeout=900,
                           stdout=stream, stderr=stream)
        subprocess.run([str(python), '-c', 'import fastapi,httpx,websockets,PIL,psutil,sqlite3'],
                       check=True, timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return str(python)

    def stop_owned(self, kind):
        import psutil
        for process in psutil.process_iter():
            try:
                args = process.cmdline()
                if Path(process.cwd()).resolve() != self.root:
                    continue
                if kind != 'astrbot' and not Path(process.exe()).resolve().is_relative_to(self.root):
                    continue
                if kind == 'api':
                    matches = (any(args[i:i + 3] == ['-m', 'uvicorn', 'phone_server:app']
                                   for i in range(len(args) - 2)) and '--port' in args
                               and args[args.index('--port') + 1] == '18766')
                elif kind == 'voice':
                    matches = any(Path(arg).resolve() == self.root / 'voice_server.py'
                                  for arg in args[1:] if not arg.startswith('-'))
                elif kind == 'astrbot':
                    from chat_engine import settings as engine_settings
                    cfg=engine_settings(self.root)
                    interpreter=Path(cfg['python'])
                    if not interpreter.is_absolute():interpreter=self.root/interpreter
                    profile=Path(cfg['profile'])
                    if not profile.is_absolute():profile=self.root/profile
                    matches=(Path(process.exe()).name.lower() in {'python.exe','pythonw.exe'}
                        and args and (Path(args[0]).resolve()==interpreter.resolve() or Path(process.exe()).resolve()==interpreter.resolve())
                        and any(Path(arg).resolve()==self.root/'astrbot_sidecar.py' for arg in args[1:] if arg.endswith('.py'))
                        and '--root' in args and Path(args[args.index('--root')+1]).resolve()==self.root
                        and '--profile' in args and Path(args[args.index('--profile')+1]).resolve()==profile.resolve()
                        and '--port' in args and args[args.index('--port')+1]==str(cfg['port']))
                else:
                    matches = any(Path(arg).resolve() == self.root / 'scripts/beginner_setup.py'
                                  for arg in args[1:] if not arg.startswith('-'))
                if matches:
                    process.terminate()
                    process.wait(timeout=20)
            except psutil.NoSuchProcess:
                pass
        # Never match QQ, Node, ordinary Python tools, or processes by name alone.

    def snapshot_private(self):
        target = self.backup / 'private'
        files = list(self.root.glob('*.local.json')) + list((self.root / 'configs').glob('*.local.*'))
        files += [self.data / 'python-environment.local.json']
        files += [self.data / 'astrbot-environment.local.json']
        files += [self.root / '.runtime/dependencies.sha256']
        files += [self.root / 'assets/stickers/catalog.json', self.data / 'browser-session.local.json']
        for source in files:
            if not source.exists():
                continue
            relative = source.relative_to(self.root).as_posix()
            checked = destination(self.root, relative)
            dest = destination(target, relative)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(checked, dest)

    def snapshot_databases(self):
        folder = self.root / 'data'
        if not folder.exists():
            return
        for current, directories, names in os.walk(folder, topdown=True, followlinks=False):
            directories[:]=[n for n in directories if n not in {
                'update-backups','update-staging','setup','qq-client','read-snapshots','account-backups'}]
            for name in directories:
                destination(self.root,(Path(current)/name).relative_to(self.root).as_posix())
            for name in names:
                source=Path(current)/name
                if source.suffix not in {'.sqlite', '.sqlite3', '.db'}:continue
                relative = source.relative_to(self.root).as_posix()
                checked = destination(self.root, relative)
                target = destination(self.backup / 'databases', relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                with closing(sqlite3.connect('file:' + checked.as_posix() + '?mode=ro', uri=True)) as live:
                    with closing(sqlite3.connect(target)) as copy:
                        live.backup(copy)

    def drain(self):
        self.status('waiting_for_tasks', '新版已校验。暂缓接收新消息，等当前任务收尾后升级。')
        cfg = read(self.root / 'qq.local.json')
        self.was_enabled = cfg['enabled']
        self.backup.mkdir(parents=True, exist_ok=False)
        self.snapshot_private()
        self.set_replies(False)
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            if active_tasks(self.root) == 0 and self.voice_idle() and self.engine_idle():
                self.status('switching', '已完成当前任务，正在备份数据库并切换程序。')
                if not self.voice_idle() or not self.engine_idle():
                    time.sleep(3)
                    continue
                # Release only this application's idle audio child before its host exits.
                import httpx
                try:
                    with httpx.Client(trust_env=False, timeout=15) as client:
                        if self.owned_voice:
                            response = client.post('http://127.0.0.1:18765/admin/unload', json={}, headers={
                                'Authorization': 'Bearer ' + self.settings['api_token'],
                                'X-Whale-Updater': str(self.request.get('maintenance_token', ''))})
                            response.raise_for_status()
                except httpx.ConnectError:
                    pass
                self.stop_owned('host')
                self.stop_owned('api')
                self.stop_owned('voice')
                self.stop_owned('astrbot')
                self.stopped = True
                if active_tasks(self.root) != 0:
                    raise ValueError('停止前仍有任务更新，已中止升级并准备恢复。')
                self.snapshot_databases()
                return
            time.sleep(3)
        self.set_replies(self.was_enabled)
        raise ValueError('当前任务还没有结束，本次升级已推迟；聊天已恢复，稍后可再试。')

    def replace_application(self, stage, manifest):
        new = manifest['files']
        old = self.previous_manifest.get('files', {})
        planned = sorted(set(new) | {'release-manifest.json'})
        # Delete only unchanged files explicitly owned by the previous release.
        for name in set(old) - set(new):
            application_path(name)
            path = destination(self.root, name)
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == old[name]['sha256']:
                planned.append(name)
        for name in planned:
            application_path(name)
            path = destination(self.root, name)
            exists = path.is_file()
            if path.exists() and not exists:
                raise ValueError('程序文件位置被其他目录占用，未覆盖。')
            if exists:
                backup = destination(self.backup / 'application', name)
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, backup)
            self.changed.append({'name': name, 'existed': exists})
        atomic(self.backup / 'rollback-manifest.private.json', self.changed)
        self.switched = True
        for item in self.changed:
            name = item['name']
            path = destination(self.root, name)
            source = destination(stage, name)
            if source.is_file():
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_name(path.name + '.update-tmp')
                shutil.copyfile(source, temporary)
                os.replace(temporary, path)
            else:
                path.unlink(missing_ok=True)

    def start(self):
        subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                        str(self.root / 'scripts/bootstrap.ps1'), '-StartOnly', '-NoBrowser', '-NoShortcut'],
                       check=True, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        deadline = time.monotonic() + 75
        while time.monotonic() < deadline:
            try:
                state = self.api('/qq/status')
                original = read(self.backup / 'private/qq.local.json')
                if state.get('bot_id') != original['bot_id'] or state.get('allowed_private_ids') != original['allowed_private_ids']:
                    raise ValueError('新版账号绑定发生变化。')
                return
            except Exception:
                time.sleep(2)
        raise ValueError('新版启动检查没有通过。')

    def rollback(self):
        self.status('rolling_back', '新版未通过启动检查，正在恢复上一版本和升级前数据。')
        self.status('switching', '正在恢复上一版本，暂缓接收新消息。')
        self.stop_owned('host')
        self.stop_owned('api')
        self.stop_owned('voice')
        self.stop_owned('astrbot')
        if self.switched:
            for item in reversed(self.changed):
                path = destination(self.root, item['name'])
                backup = destination(self.backup / 'application', item['name'])
                if item['existed']:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = path.with_name(path.name + '.rollback-tmp')
                    shutil.copy2(backup, temporary)
                    os.replace(temporary, path)
                else:
                    path.unlink(missing_ok=True)
        for section in ['private', 'databases']:
            folder = self.backup / section
            if not folder.exists():
                continue
            for source in folder.rglob('*'):
                if not source.is_file():
                    continue
                relative = source.relative_to(folder).as_posix()
                path = destination(self.root, relative)
                path.parent.mkdir(parents=True, exist_ok=True)
                if section == 'databases':
                    for suffix in ('-wal', '-shm'):
                        sidecar = destination(self.root, relative + suffix)
                        sidecar.unlink(missing_ok=True)
                temporary = path.with_name(path.name + '.rollback-tmp')
                shutil.copy2(source, temporary)
                os.replace(temporary, path)
        pointer = 'data/setup/python-environment.local.json'
        if not (self.backup / 'private' / pointer).exists():
            destination(self.root, pointer).unlink(missing_ok=True)
        self.settings = read(self.root / 'settings.local.json')
        self.status('validating', '上一版本已恢复，正在检查服务。')
        self.start()
        self.status('rolled_back', '新版没有通过检查，已恢复上一版本；账号、Key 和原数据库保留。')
        self.set_replies(bool(self.was_enabled))

    def run(self):
        try:
            stage, manifest = self.download()
            python = self.prepare_environment(stage)
            self.drain()
            self.replace_application(stage, manifest)
            settings = read(self.root / 'settings.local.json')
            settings['python'] = python
            write_private(self.root / 'settings.local.json', settings)
            write_private(self.data / 'python-environment.local.json', {'python': python})
            lock_hash = hashlib.sha256((stage / 'requirements-bootstrap.lock').read_bytes()).hexdigest().upper()
            (self.root / '.runtime/dependencies.sha256').write_text(lock_hash, encoding='ascii')
            self.status('validating', '正在检查新版服务并恢复聊天。')
            self.start()
            self.status('succeeded', '升级完成，聊天已恢复；备份保存在本机。')
            try:
                self.set_replies(bool(self.was_enabled))
            except Exception:
                self.status('updated_paused', '升级与启动检查已完成，回复仍保持暂停；请在本地 QQ 页确认恢复。')
        except Exception as exc:
            if self.stopped:
                try:
                    self.rollback()
                except Exception:
                    self.status('recovery_required', '已保留完整备份，但自动恢复未通过检查。请从本机备份恢复或联系维护者，勿重复安装覆盖。')
            else:
                if self.was_enabled is not None:
                    try:
                        self.set_replies(self.was_enabled)
                    except Exception:
                        pass
                message = str(exc)[:180] if isinstance(exc, ValueError) else '升级准备未完成，原程序与数据保留，请稍后重试。'
                self.status('deferred' if self.was_enabled is not None else 'failed', message)
        finally:
            (self.data / 'update-request.lock').unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    options = parser.parse_args()
    if options.root.resolve() != ROOT.resolve():
        raise SystemExit('Update root mismatch')
    Upgrade(options.root).run()
