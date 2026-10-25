"""Loopback-only speech API, one CUDA worker, bounded disk cache, idle exit."""
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import argparse
import hashlib
import json
import os
import queue
import re
import secrets
import subprocess
import sys
import threading
import time

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field
import uvicorn

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument("--config", default=str(ROOT / "settings.local.json"))
ARGS, _ = parser.parse_known_args()
CONFIG_PATH = Path(ARGS.config).resolve()
CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
for key in ("model_dir", "python", "kokoro_model_dir"):
    CONFIG[key] = str((ROOT / CONFIG[key]).resolve())
CONFIG.setdefault("dania_python", CONFIG["python"])
CONFIG.setdefault("dania_config", str(ROOT / "dania.local.json"))
if CONFIG["host"] != "127.0.0.1":
    raise ValueError("This test service must bind to 127.0.0.1")
if len(CONFIG["api_token"]) < 24 or CONFIG["api_token"].startswith("REPLACE"):
    raise ValueError("Set a random local api_token with at least 24 characters")
CACHE = ROOT / "cache" / "audio"
CACHE.mkdir(parents=True, exist_ok=True)
from local_learning import PriorityResourceLock,register_local_assistant_host
LOCK = PriorityResourceLock()
PROCESS = None
LOADED_ENGINE = None
RESPONSES = queue.Queue()
LOG = None
LAST_USED = 0.0
LAST_METRICS = {}
STOP = threading.Event()


def gpu_info():
    try:
        raw = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.total,memory.used,memory.free", "--format=csv,noheader,nounits"],
            timeout=8, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        total, used, free = map(int, raw.splitlines()[0].split(","))
        return {"total_mib": total, "used_mib": used, "free_mib": free}
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def prune_cache(protect=None):
    """Only inspect files directly owned by this audio cache, never model weights."""
    now = time.time()
    limit = CONFIG["cache_max_mib"] * 1024**2
    files = []
    for path in CACHE.glob("*.wav"):
        if not re.fullmatch(r"[a-f0-9]{64}\.wav", path.name) or path == protect:
            continue
        stat = path.stat()
        if now - stat.st_mtime > CONFIG["cache_ttl_hours"] * 3600:
            path.unlink(missing_ok=True)
        else:
            files.append((stat.st_mtime, stat.st_size, path))
    total = sum(x[1] for x in files)
    if protect and protect.exists():
        total += protect.stat().st_size
    for _, size, path in sorted(files):
        if total <= limit:
            break
        path.unlink(missing_ok=True)
        total -= size


def unload_locked():
    global PROCESS, LOG, RESPONSES, LOADED_ENGINE
    if PROCESS is not None:
        if PROCESS.poll() is None:
            PROCESS.terminate()
            try:
                PROCESS.wait(timeout=10)
            except subprocess.TimeoutExpired:
                PROCESS.kill()
                PROCESS.wait(timeout=10)
        for stream in (PROCESS.stdin, PROCESS.stdout):
            if stream:
                stream.close()
        PROCESS = None
        LOADED_ENGINE = None
    if LOG:
        LOG.close()
        LOG = None
    RESPONSES = queue.Queue()


def read_worker(process, responses):
    for line in process.stdout:
        try:
            responses.put(json.loads(line))
        except json.JSONDecodeError:
            continue
    responses.put({"ok": False, "error": "Voice worker exited; inspect cache/worker.log"})


def receive():
    try:
        return RESPONSES.get(timeout=CONFIG["worker_timeout_seconds"])
    except queue.Empty:
        unload_locked()
        raise HTTPException(504, "Voice worker timed out and was stopped")


def ensure_worker(engine):
    global PROCESS, LOG, RESPONSES, LAST_METRICS, LOADED_ENGINE
    if PROCESS is not None and PROCESS.poll() is None and LOADED_ENGINE == engine:
        return
    unload_locked()
    if engine in {'qwen3-tts-local', 'dania-gptsovits'}:
        gpu = gpu_info()
        if gpu is None or gpu["free_mib"] < CONFIG["min_free_vram_mib"]:
            raise HTTPException(503, "Insufficient free GPU memory; release other GPU work or select the CPU model")
    LOG = (ROOT / "cache" / "worker.log").open("w", encoding="utf-8")
    worker_config = {key: CONFIG[key] for key in (
        "model_dir", "kokoro_model_dir", "cpu_threads", "gpu_memory_fraction", "max_audio_seconds",
    )}
    worker_python = CONFIG["python"]
    worker_name = "voice_worker_cpu.py" if engine == 'kokoro-cpu' else "voice_worker.py"
    if engine == 'dania-gptsovits':
        worker_config = json.loads(Path(CONFIG["dania_config"]).read_text(encoding="utf-8"))
        worker_python = CONFIG["dania_python"]
        worker_name = "voice_worker_dania.py"
    from audio_budget import apply
    worker_config=apply(ROOT,worker_name,worker_config)
    PROCESS = subprocess.Popen(
        [worker_python, "-u", str(ROOT / worker_name), json.dumps(worker_config)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=LOG,
        text=True, encoding="utf-8", bufsize=1,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)|getattr(subprocess,'BELOW_NORMAL_PRIORITY_CLASS',0),
    )
    threading.Thread(target=read_worker, args=(PROCESS, RESPONSES), daemon=True).start()
    message = receive()
    if not message.get("ready"):
        unload_locked()
        raise HTTPException(503, message.get("error", "Model initialization failed"))
    LOADED_ENGINE = engine
    LAST_METRICS = {"engine": engine, "load_seconds": message["load_seconds"]}


def idle_watchdog():
    while not STOP.wait(2):
        if LOCK.acquire_idle():
            try:
                if PROCESS and time.monotonic() - LAST_USED >= CONFIG["idle_seconds"]:
                    unload_locked()
            finally:
                LOCK.release()


@asynccontextmanager
async def lifespan(_app):
    prune_cache()
    threading.Thread(target=idle_watchdog, daemon=True).start()
    yield
    STOP.set()
    MUSIC_SERVICE.cancel()
    SCORE_SERVICE.cancel()
    MOBILE_SERVICE.cancel()
    MOBILE_SERVICE.exit.set()
    LOCAL_LEARNING.cancel()
    with LOCK:
        unload_locked()


app = FastAPI(title="本地语音测试台", lifespan=lifespan)
from local_session import register_local_sessions
LOCAL_SESSIONS=register_local_sessions(app,ROOT,CONFIG['api_token'],port=CONFIG['port'])


@app.exception_handler(RequestValidationError)
async def validation_error(_request, exc):
    # Validation errors must never echo a submitted API key or other form value.
    errors = [{k: e[k] for k in ('loc', 'msg', 'type') if k in e} for e in exc.errors()]
    if any('api_key' in e.get('loc', ()) for e in errors):
        return JSONResponse(status_code=422, content={'detail': 'DeepSeek Key 格式或长度不正确，请填写完整 Key 后保存。'})
    return JSONResponse(status_code=422, content={'detail': errors})


def authorize(request: Request, authorization: str = Header(default="")):
    return LOCAL_SESSIONS.authorize(request,authorization)


class SpeechRequest(BaseModel):
    model: Literal["dania-gptsovits", "kokoro-cpu", "qwen3-tts-local", "tts-v4"] = "dania-gptsovits"
    input: str = Field(min_length=1, max_length=120)
    voice: str = ""
    response_format: Literal["wav"] = "wav"
    speed: float = Field(default=1.0, ge=1, le=1)


def synthesize(request):
    global LAST_USED, LAST_METRICS
    text = request.input.strip()
    if not text or len(text) > CONFIG["max_text_chars"]:
        raise HTTPException(400, "Text is empty or too long; split the reply into shorter sentences")
    engine = 'qwen3-tts-local' if request.model == 'tts-v4' else request.model
    speakers = ({"dania"} if engine == 'dania-gptsovits' else {"zh-3", "zh-12"} if engine == 'kokoro-cpu' else
                {"vivian", "serena", "uncle_fu", "dylan", "eric", "ryan", "aiden", "ono_anna", "sohee"})
    voice = request.voice.lower() if request.voice else ('dania' if engine == 'dania-gptsovits' else 'zh-3' if engine == 'kokoro-cpu' else 'serena')
    if voice not in speakers:
        raise HTTPException(400, "Unsupported voice")
    signature = ["qwen3-0.6b", "85e237c", "normal-pace-v3-edges", voice, text] if engine == 'qwen3-tts-local' else ["kokoro-int8-v1.1", voice, text]
    if engine == 'dania-gptsovits':
        signature = ['dania-v2ProPlus', '021de96f', '668b7ff9', '98cde6a6', 'pace-v5-short60-gentle350keep250', 0.85, 0.12, 0.8, 20261020, text]
    key = hashlib.sha256(json.dumps(signature, ensure_ascii=False).encode()).hexdigest()
    path = CACHE / f"{key}.wav"
    # Do not queue unlimited requests. The caller can retry or return a text response.
    if not LOCK.acquire(blocking=False):
        raise HTTPException(429, "Voice worker is busy; retry after the current utterance")
    try:
        prune_cache()
        if path.exists():
            os.utime(path, None)
            return path.read_bytes(), {"cache_hit": True}
        ensure_worker(engine)
        partial = CACHE / f"{key}.part.wav"
        try:
            PROCESS.stdin.write(json.dumps({"text": text, "voice": voice, "path": str(partial)}, ensure_ascii=False) + "\n")
            PROCESS.stdin.flush()
            result = receive()
            if not result.get("ok"):
                unload_locked()
                raise HTTPException(503, result.get("error", "Speech synthesis failed"))
            partial.replace(path)
            LAST_USED = time.monotonic()
            LAST_METRICS = {**LAST_METRICS, **result}
            prune_cache(protect=path)
            return path.read_bytes(), {**result, "cache_hit": False}
        finally:
            partial.unlink(missing_ok=True)
    finally:
        LOCK.release()


@app.post("/v1/audio/speech", dependencies=[Depends(authorize)])
def speech(request: SpeechRequest):
    from fastapi import Response
    data, metrics = synthesize(request)
    return Response(data, media_type="audio/wav", headers={
        "X-Cache-Hit": str(metrics["cache_hit"]).lower(),
        "X-Audio-Metrics": json.dumps(metrics),
        "Cache-Control": "no-store",
    })


@app.get('/v1/audio/voices',dependencies=[Depends(authorize)])
def voice_catalog():
    from voice_catalog import available
    return {'voices':available(CONFIG),'default':'dania','rate':1}


@app.get('/v1/audio/previews',dependencies=[Depends(authorize)])
def voice_previews():
    file=ROOT/'configs/voice-previews.local.json'
    try:items=json.loads(file.read_text(encoding='utf-8'))
    except (OSError,ValueError):items=[]
    if not isinstance(items,list):items=[]
    return {'items':[{'title':str(i.get('title','AI合成试听'))[:100],'artifact_id':i['artifact_id']} for i in items[:8] if isinstance(i,dict) and re.fullmatch(r'[a-f0-9]{32}',str(i.get('artifact_id',''))) and (ROOT/'artifacts'/(i['artifact_id']+'.wav')).is_file()]}


@app.get("/health")
def health():
    qq_channel='not_connected'
    try:
        import httpx
        with httpx.Client(trust_env=False,timeout=.5) as client:
            channel=client.get('http://127.0.0.1:18766/qq/status',headers={'Authorization':'Bearer '+CONFIG['api_token']})
            if channel.status_code==200:
                state=channel.json()
                if state.get('connected') and state.get('account_matches'):qq_channel='connected'
    except Exception:
        pass
    return {"status": "ok", "worker_loaded": PROCESS is not None and PROCESS.poll() is None,
            "worker_pid": PROCESS.pid if PROCESS and PROCESS.poll() is None else None,
            "engine": LOADED_ENGINE,
            "gpu": gpu_info(), "idle_unload_seconds": CONFIG["idle_seconds"],
            "cache_bytes": sum(p.stat().st_size for p in CACHE.glob("*.wav")),
            "last_metrics": LAST_METRICS,
            "channels": {"qq": qq_channel, "wechat": "not_connected"},
            "audio_busy":LOCK.locked() and not LOCK.low_active,"learning_running":LOCK.low_active}


@app.post("/admin/unload", dependencies=[Depends(authorize)])
def unload():
    if not LOCK.acquire(blocking=False):
        raise HTTPException(409, "A synthesis is running; wait until it finishes")
    try:
        unload_locked()
    finally:
        LOCK.release()
    return {"worker_loaded": False}


@app.get("/", response_class=HTMLResponse)
def index():
    return (ROOT / "testbench.html").read_text(encoding="utf-8")


@app.get("/samples/{name}",dependencies=[Depends(authorize)])
def sample(name: str):
    if name not in {"dania-daily.wav", "dania-playful.wav", "dania-caring.wav", "dania-playful-smooth.wav", "dania-playful-normal.wav"}:
        raise HTTPException(404, "Unknown sample")
    return FileResponse(ROOT / "samples" / name, media_type="audio/wav", headers={"Cache-Control":"no-store"})


from singing_routes import register_singing_routes
register_singing_routes(app,authorize=authorize,lock=LOCK,unload=unload_locked,root=ROOT)

from music_routes import register_music_routes
MUSIC_SERVICE=register_music_routes(app,authorize=authorize,lock=LOCK,unload=unload_locked,root=ROOT,
    synthesize=lambda text,voice='dania':synthesize(SpeechRequest(model=__import__('voice_catalog').profile(voice)['model'],input=text,voice=__import__('voice_catalog').profile(voice)['speaker'])))
from score_routes import register_score_routes
SCORE_SERVICE=register_score_routes(app,authorize=authorize,lock=LOCK,unload=unload_locked,root=ROOT,artifacts=MUSIC_SERVICE)
from agent_routes import register_agent_routes
register_agent_routes(app,authorize=authorize,root=ROOT)

from mobile_routes import register_mobile_routes
MOBILE_SERVICE=register_mobile_routes(app,authorize=authorize,root=ROOT)

LOCAL_LEARNING=register_local_assistant_host(app,authorize=authorize,root=ROOT,lock=LOCK,unload=unload_locked)


from maintenance_guard import register_maintenance_guard
register_maintenance_guard(app,ROOT)

if __name__ == "__main__":
    uvicorn.run(app, host=CONFIG["host"], port=CONFIG["port"], access_log=False)
