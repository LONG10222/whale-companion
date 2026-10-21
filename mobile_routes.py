"""Authenticated phone workspace, asynchronous jobs and local-only draft storage."""
from pathlib import Path
import json, re, threading, time, uuid
from fastapi import Depends, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel, Field
from typing import Literal
import deepseek_client, mobile_agent
from mobile_bridge import MobileBridge


class TaskRequest(BaseModel):
    message: str = Field(default='在小黑盒搜索鸣潮，阅读1至3篇相关帖子，写一份有来源的原创中文讨论帖草稿，只保存在本机。', min_length=1, max_length=2000)


class DeviceRequest(BaseModel):
    action: Literal['start', 'stop', 'open', 'observe', 'search']
    query: str = Field(default='鸣潮', min_length=1, max_length=60)


class MobileService:
    def __init__(self, root):
        self.bridge = MobileBridge(root)
        self.gate = threading.Lock()
        self.state_lock = threading.Lock()
        self.cancelled = threading.Event()
        self.exit = threading.Event()
        self.state = {'busy': False, 'job_id': None, 'events': [], 'result': None, 'error': None}
        threading.Thread(target=self.watchdog, daemon=True).start()

    def watchdog(self):
        while not self.exit.wait(10):
            if self.gate.acquire(blocking=False):
                try:
                    self.bridge.idle_check()
                except Exception:
                    pass
                finally:
                    self.gate.release()

    def cancel(self):
        self.cancelled.set()

    def snapshot(self):
        with self.state_lock:
            return json.loads(json.dumps(self.state, ensure_ascii=False))

    def launch(self, handler, on_done=None):
        if not self.gate.acquire(blocking=False):
            raise HTTPException(409, '已有手机任务正在执行。')
        self.cancelled.clear()
        jid = uuid.uuid4().hex
        with self.state_lock:
            self.state = {'busy': True, 'job_id': jid, 'events': [], 'result': None, 'error': None}

        def emit(event):
            with self.state_lock:
                self.state['events'].append(event)
                self.state['events'] = self.state['events'][-40:]

        def worker():
            try:
                result = handler(emit)
                with self.state_lock:
                    self.state['result'] = result
            except Exception as exc:
                with self.state_lock:
                    self.state['error'] = str(exc)[:700]
            finally:
                with self.state_lock:
                    self.state['busy'] = False
                completed=self.snapshot()
                self.gate.release()
                if on_done:
                    try:on_done(jid,completed)
                    except Exception:pass
        threading.Thread(target=worker, daemon=True).start()
        return {'accepted': True, 'job_id': jid}


def register_mobile_routes(app, *, authorize, root):
    service = MobileService(root)
    root = Path(root)

    @app.get('/mobile', response_class=HTMLResponse)
    def page():
        return (root / 'mobile.html').read_text(encoding='utf-8')

    @app.get('/mobile/status', dependencies=[Depends(authorize)])
    def status():
        state = service.snapshot()
        state.update({'device': service.bridge.status(), 'deepseek_key_configured': deepseek_client.ready(),
                      'model': deepseek_client.configuration()['model']})
        return state

    @app.post('/v1/mobile/device', dependencies=[Depends(authorize)])
    def device(request: DeviceRequest):
        def operation(emit):
            b = service.bridge
            if request.action == 'start':
                return b.start(service.cancelled.is_set)
            if request.action == 'stop':
                return b.stop()
            if request.action == 'open':
                return b.open()
            if request.action == 'search':
                return b.search(request.query)
            return b.observe()
        return service.launch(operation)

    @app.post('/v1/mobile/run', dependencies=[Depends(authorize)])
    def run(request: TaskRequest):
        if not deepseek_client.ready():
            raise HTTPException(409, 'DeepSeek Key 尚未配置，请先在语音测试页保存。')
        if not service.bridge.info().get('is_android_started'):
            raise HTTPException(409, '请先启动测试手机。')
        return service.launch(lambda emit: mobile_agent.run(request.message, service.bridge,
            emit=emit, cancelled=service.cancelled.is_set))

    @app.post('/mobile/cancel', dependencies=[Depends(authorize)])
    def cancel():
        service.cancel()
        return {'stop_requested': True, 'message': '已请求停止；若模型正在回复，会在回复返回后停止，不执行下一步。'}

    @app.get('/mobile/screens/{sid}', dependencies=[Depends(authorize)])
    def screen(sid: str):
        if not re.fullmatch(r'[a-f0-9]{32}', sid):
            raise HTTPException(404)
        p = service.bridge.cache / (sid + '.jpg')
        if not p.is_file():
            raise HTTPException(404, '截图已清理，请重新读取。')
        return FileResponse(p, media_type='image/jpeg', headers={'Cache-Control': 'no-store'})

    @app.get('/mobile/drafts', dependencies=[Depends(authorize)])
    def drafts():
        folder = root / 'mobile-drafts'
        files = sorted(folder.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:20]
        items = [json.loads(p.read_text(encoding='utf-8')) for p in files if re.fullmatch(r'[a-f0-9]{32}\.json', p.name)]
        return [d for d in items if d.get('quality_status') != 'rejected_during_review']

    return service
