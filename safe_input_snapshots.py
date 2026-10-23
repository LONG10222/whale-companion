"""Host-owned immutable input snapshots, outside the agent's writable task.

Sources are read through checked handles; directory links and hard links are
refused. The task receives only a read grant to its fresh snapshot directory.
"""
from contextlib import closing
import os
from pathlib import Path
import re
import sqlite3
import tempfile

BLOCKED_NAMES = {'auth.json', 'config.toml', 'cookies', 'cookies.sqlite', 'credentials.json',
                 'login data', 'local state', '.env', 'id_rsa', 'id_ed25519'}
BLOCKED_DIRECTORIES = {'.git', '.codex', '.ssh', '.aws', '.azure', '.gnupg', '__pycache__',
                       'node_modules', '.venv', 'logs', 'cache', 'models'}
BLOCKED_EXTENSIONS = {'.pem', '.key', '.pfx', '.p12', '.env', '.kdbx'}
SECRET = re.compile(rb'(?:\bsk-[A-Za-z0-9_-]{16,}|\bgh[pousr]_[A-Za-z0-9_]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)')
MAX_FILE = 5 * 1024 ** 2
MAX_BYTES = 30 * 1024 ** 2
MAX_FILES = 1000
MAX_DEPTH = 12


def private_name(path):
    path = Path(path)
    name = path.name.lower()
    return (name in BLOCKED_NAMES or '.local.' in name or name.startswith('.env')
            or path.suffix.lower() in BLOCKED_EXTENSIONS)


def no_links(path, root):
    path, root = Path(path).absolute(), Path(root).absolute()
    if str(path).startswith(('\\\\', '//')):
        raise ValueError('不读取网络共享或设备路径。')
    if path != root and not path.is_relative_to(root):
        raise ValueError('资料路径超出本次指定目录。')
    current = path
    while True:
        try:
            status = current.lstat()
        except FileNotFoundError:
            status = None
        if status and (current.is_symlink() or getattr(status, 'st_file_attributes', 0) & 0x400):
            raise ValueError('资料或输入目录中含链接，不能替代本次明确授权的实际路径。')
        if current == root:
            break
        current = current.parent


def final_path(stream):
    if os.name != 'nt':
        return Path('/proc/self/fd/' + str(stream.fileno())).resolve()
    import ctypes
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    kernel.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel.GetFinalPathNameByHandleW(msvcrt.get_osfhandle(stream.fileno()), buffer, len(buffer), 0)
    if not length or length >= len(buffer):
        raise ValueError('无法核对已打开资料的实际位置。')
    name = buffer.value
    if name.startswith('\\\\?\\UNC\\'):
        raise ValueError('不读取网络共享。')
    if name.startswith('\\\\?\\'):
        name = name[4:]
    return Path(name)


class Snapshot:
    def __init__(self, root, task):
        self.root = Path(root).resolve()
        original_task = Path(task).absolute()
        no_links(original_task, original_task.parent)
        no_links(original_task / 'inputs', original_task)
        store = self.root / 'data/qq/read-snapshots'
        no_links(store, self.root)
        store.mkdir(parents=True, exist_ok=True)
        self.directory = Path(tempfile.mkdtemp(prefix='input-', dir=store))
        self.bytes = 0
        self.count = 0
        self.excluded = []

    def copy(self, source, approved_root, relative):
        source, approved_root = Path(source).absolute(), Path(approved_root).absolute()
        no_links(source, approved_root)
        if private_name(source):
            self.excluded.append(source.name)
            return None
        with source.open('rb') as stream:
            opened = final_path(stream).resolve()
            approved = approved_root.resolve()
            if opened != approved and not opened.is_relative_to(approved):
                raise ValueError('资料位置在读取期间发生变化，已停止。')
            if private_name(opened):
                self.excluded.append(opened.name)
                return None
            status = os.fstat(stream.fileno())
            if status.st_nlink != 1:
                raise ValueError('资料含硬链接，不能作为本次只读输入。')
            if status.st_size > MAX_FILE:
                raise ValueError('资料文件较大，请指定更小的实际文件。')
            data = stream.read(MAX_FILE + 1)
        if len(data) > MAX_FILE:
            raise ValueError('资料在读取期间超出大小上限。')
        if SECRET.search(data):
            self.excluded.append(source.name)
            return None
        self.bytes += len(data)
        self.count += 1
        if self.bytes > MAX_BYTES or self.count > MAX_FILES:
            raise ValueError('本次资料总量较大，请缩小目录或指定文件。')
        relative = Path(relative)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('输入文件名无效。')
        destination = self.directory / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as target:
            target.write(data)
        return destination

    def tree(self, base, relative, suffixes=None):
        base = Path(base).absolute()
        no_links(base, base.parent)
        if not base.is_dir():
            raise ValueError('指定的资料目录不存在。')
        stack = [(base, 0)]
        visited = 0
        while stack:
            current, depth = stack.pop()
            no_links(current, base)
            with os.scandir(current) as entries:
                for entry in entries:
                    visited += 1
                    if visited > 3000 or depth > MAX_DEPTH:
                        raise ValueError('资料目录过深或文件过多，请指定具体范围。')
                    path = Path(entry.path)
                    if private_name(path) or entry.name.lower() in BLOCKED_DIRECTORIES:
                        self.excluded.append(entry.name)
                        continue
                    no_links(path, base)
                    if entry.is_dir(follow_symlinks=False):
                        stack.append((path, depth + 1))
                    elif entry.is_file(follow_symlinks=False) and (suffixes is None or path.suffix.lower() in suffixes):
                        self.copy(path, base, Path(relative) / path.relative_to(base))
        destination = self.directory / relative
        destination.mkdir(parents=True, exist_ok=True)
        return destination

    def database(self, source, approved_root, name):
        source = Path(source)
        no_links(source, approved_root)
        if not source.is_file() or source.stat().st_nlink != 1:
            raise ValueError('该数据库不存在或是链接。')
        target = self.directory / name
        if target.exists():
            return target
        def budget(status, remaining, total):
            if total * page_size > 64 * 1024 ** 2:
                raise ValueError('数据库快照超过本轮大小上限，请使用更具体的查询。')
        with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as original:
            page_size = original.execute('PRAGMA page_size').fetchone()[0]
            with closing(sqlite3.connect(target)) as destination:
                original.backup(destination, pages=128, progress=budget)
        return target
