"""Own Bilibili session only; Windows DPAPI, never browser cookie extraction."""
from __future__ import annotations
import ctypes, json, os
from ctypes import wintypes as w
from pathlib import Path
from .common import atomic_json, read_json

ALLOWED = {"SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "buvid3", "buvid4", "b_nut"}

class Blob(ctypes.Structure):
    _fields_ = [("size", w.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

def _dpapi(data: bytes, decrypt=False):
    if os.name != "nt":
        raise RuntimeError("本原型仅在 Windows 使用系统加密保存登录；不会改为明文保存。")
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    dest = Blob()
    crypto = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes=[w.HLOCAL];kernel.LocalFree.restype=w.HLOCAL
    if decrypt:
        fn = crypto.CryptUnprotectData
        fn.argtypes=[ctypes.POINTER(Blob),w.LPVOID,w.LPVOID,w.LPVOID,w.LPVOID,w.DWORD,ctypes.POINTER(Blob)]
        ok = fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(dest))
    else:
        fn = crypto.CryptProtectData
        fn.argtypes=[ctypes.POINTER(Blob),w.LPCWSTR,w.LPVOID,w.LPVOID,w.LPVOID,w.DWORD,ctypes.POINTER(Blob)]
        ok = fn(ctypes.byref(source), "Whale Bilibili local session", None, None, None, 1, ctypes.byref(dest))
    if not ok:
        raise RuntimeError("Windows 未能加解密本地 B 站登录信息。")
    try:
        return ctypes.string_at(dest.data, dest.size)
    finally:
        kernel.LocalFree(dest.data)

class SessionStore:
    def __init__(self, home):
        self.path = Path(home) / "data" / "bilibili-session.dpapi.json"

    def save(self, cookies):
        import base64
        selected = {k: str(v) for k,v in cookies.items() if k in ALLOWED and isinstance(v,str) and len(v)<8192}
        if not selected.get("SESSDATA"):
            raise RuntimeError("扫码已结束，但未取得有效登录信息，请重新扫码。")
        encrypted = _dpapi(json.dumps(selected).encode())
        atomic_json(self.path, {"version":1,"encrypted":base64.b64encode(encrypted).decode("ascii")})

    def load(self):
        import base64
        row=read_json(self.path)
        if not row:
            return {}
        try:
            value=json.loads(_dpapi(base64.b64decode(row["encrypted"]), decrypt=True))
            return {k:v for k,v in value.items() if k in ALLOWED and isinstance(v,str)}
        except Exception:
            raise RuntimeError("本地 B 站登录信息无法解密，请在本地重新扫码。") from None

    def clear(self):
        # Only this explicitly owned credential file, no recursive deletion.
        self.path.unlink(missing_ok=True)
