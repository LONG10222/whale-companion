from __future__ import annotations
from pathlib import Path
import json, os, re, time
from datetime import datetime,timezone,timedelta

TZ = timezone(timedelta(hours=8))

def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)

def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, ValueError, OSError):
        return default

def clean_text(value, limit=500):
    import html
    text = html.unescape(re.sub(r"<[^>]*>", "", str(value or "")))
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text).strip()[:limit]

def stamp():
    return datetime.now(TZ).isoformat(timespec="seconds")

def safe_bvid(value):
    if not isinstance(value, str) or not re.fullmatch(r"BV[0-9A-Za-z]{10}", value):
        raise ValueError("请输入有效的 BV 号或 B 站视频链接。")
    return value

def pick_bvid(value):
    from urllib.parse import urlparse
    text = str(value).strip()
    if text.startswith("https://"):
        url = urlparse(text)
        if url.hostname not in {"www.bilibili.com", "bilibili.com"} or url.username or url.password:
            raise ValueError("这里只接收 bilibili.com 的视频链接。")
        text = next((p for p in url.path.split("/") if p.startswith("BV")), "")
    return safe_bvid(text)

class Cancelled(Exception):
    pass

class Deadline:
    def __init__(self, seconds=180, cancel=None):
        self.end = time.monotonic() + min(300, max(15, seconds))
        self.cancel = cancel

    def check(self):
        if self.cancel is not None and self.cancel.is_set():
            raise Cancelled("本轮已取消，已取得的内容保存在本机。")
        if time.monotonic() >= self.end:
            raise Cancelled("达到本轮时间上限，已停止继续查找。")

    def timeout(self):
        self.check()
        return max(.1, min(18, self.end - time.monotonic()))

    def remaining(self):
        self.check()
        return max(.1,self.end-time.monotonic())

    def wait(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.check()
            time.sleep(min(.1, max(0, end-time.monotonic())))
