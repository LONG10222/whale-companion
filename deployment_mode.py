"""Fail-closed first-run gate for the real application; no automatic QQ login."""
import hashlib
import secrets
import threading
import time
from pathlib import Path
from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response


class DeploymentGate:
    def __init__(self, root, bridge, clock=time.monotonic):
        self.root = Path(root)
        self.bridge = bridge
        self.clock = clock
        self.lock = threading.Lock()
        self.phase = 'paused'
        self.nonce = ''
        self.expires = 0
        self.preview = None
        with bridge.lock:
            bridge.cfg['enabled'] = False  # Do not overwrite existing operator configuration.

    def check_switch(self):
        switcher = getattr(self.bridge.login_manager, "switcher", None)
        if switcher and switcher.load().get("phase") not in {"idle", "complete"}:
            raise HTTPException(409, "存在未完成的换号流程，安全启动不会自动恢复。请先人工处理原流程。")

    def prepare(self, principal=""):
        self.check_switch()
        with self.lock, self.bridge.lock:
            if self.phase != 'paused':
                raise HTTPException(409, '连接已提交或结果不确定，请查看状态；不会自动重试。')
            self.check_switch()
            self.preview = {'bot_id': str(self.bridge.cfg.get('bot_id', '')),
                            'allowed_private_ids': list(self.bridge.cfg.get('allowed_private_ids', [])),
                            'effect': 'connect_qq_restore_saved_login_keep_replies_paused'}
            self.principal = principal
            self.nonce = secrets.token_urlsafe(32)
            self.expires = self.clock() + 120
            return {'preview': self.preview, 'nonce': self.nonce, 'expires_in': 120}

    def activate(self, body, principal=""):
        with self.lock:
            if (self.phase != 'paused' or principal != getattr(self, 'principal', '') or not self.nonce or self.clock() >= self.expires or
                    body.get('confirmed') is not True or body.get('nonce') != self.nonce or
                    body.get('preview') != self.preview):
                raise HTTPException(409, '确认已过期或内容不匹配，请重新核对。')
            self.check_switch()
            with self.bridge.lock:
                current = {'bot_id': str(self.bridge.cfg.get('bot_id', '')),
                           'allowed_private_ids': list(self.bridge.cfg.get('allowed_private_ids', [])),
                           'effect': 'connect_qq_restore_saved_login_keep_replies_paused'}
                if current != self.preview or not current['bot_id']:
                    self.nonce = ''
                    raise HTTPException(409, '账号配置已变化，请重新核对。')
                self.bridge.cfg['enabled'] = False
            self.nonce = ''
            self.phase = 'starting'
            try:
                self.bridge.start()
                self.bridge.login_manager.start()
                self.phase = 'connected_requested'
            except Exception:
                self.phase = 'uncertain'
                raise HTTPException(503, '连接结果不确定，请人工检查客户端；不会再次提交。') from None
            return {'phase': self.phase, 'reply_enabled': False,
                    'message': '已允许连接，不代表登录成功。回复仍暂停；主动聊天和余额任务未恢复。'}

    def opened(self):
        with self.lock:
            return self.phase == 'connected_requested'


def register_deployment_mode(app, root, bridge, authorize, sessions):
    gate = DeploymentGate(root, bridge)
    public = {('GET', '/deployment'), ('GET', '/deployment.js'), ('POST', '/local/session'),
              ('GET', '/local/session.js'), ('GET', '/deployment/health'),
              ('GET', '/deployment/status'), ('POST', '/deployment/prepare'),
              ('POST', '/deployment/connect'), ('POST', '/agent/config')}

    @app.middleware('http')
    async def safe_entry(request, call_next):
        if not gate.opened() and (request.method, request.url.path) not in public:
            return JSONResponse({'detail': '安全部署模式：请先从 /deployment 核对并明确允许连接。'}, status_code=423)
        response = await call_next(request)
        if request.method == 'GET' and request.url.path == '/deployment' and response.headers.get('content-type', '').startswith('text/html'):
            # This outer middleware strips the legacy auto-retry wrapper before delivery.
            # Other pages and their session behavior remain untouched.
            body = b''.join([chunk async for chunk in response.body_iterator]).decode('utf-8')
            body = body.replace('<script src="/local/session.js" defer></script>', '')
            headers = dict(response.headers)
            headers.pop('content-length', None)
            return HTMLResponse(body, status_code=response.status_code, headers=headers)
        return response

    @app.get('/deployment', response_class=HTMLResponse)
    def page():
        return (Path(root) / 'deployment.html').read_text(encoding='utf-8')

    @app.get('/deployment.js')
    def script():
        return Response((Path(root) / 'deployment.js').read_text(encoding='utf-8'), media_type='application/javascript', headers={'Cache-Control': 'no-store'})

    @app.get('/deployment/health')
    def health():
        return {'product': 'whale-safe-application-v1', 'instance': hashlib.sha256(str(Path(root).resolve()).casefold().encode()).hexdigest(), 'mode': 'safe-start'}

    @app.get('/deployment/status', dependencies=[Depends(authorize)])
    def status():
        return {'phase': gate.phase, 'replies_paused': not bridge.cfg['enabled'], 'background_jobs_started': False}

    def principal(request):
        # Only called after authorization; never retain the raw session/token.
        bearer = request.headers.get('authorization', '')
        if secrets.compare_digest(bearer, 'Bearer ' + sessions.api_token):
            return 'bearer:' + hashlib.sha256(bearer.encode()).hexdigest()
        value = request.cookies.get('whale_local_session', '')
        # authorize already checked this cookie, same-origin and CSRF marker.
        return 'session:' + hashlib.sha256(value.encode()).hexdigest()

    @app.post('/deployment/prepare', dependencies=[Depends(authorize)])
    def prepare(request: Request):
        return gate.prepare(principal(request))

    @app.post('/deployment/connect', dependencies=[Depends(authorize)])
    async def connect(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, '确认内容格式不正确。')
        return gate.activate(body, principal(request))

    return gate
