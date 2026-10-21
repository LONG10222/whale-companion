from pathlib import Path
import re,threading
from fastapi import Depends,HTTPException
from fastapi.responses import HTMLResponse,FileResponse
from pydantic import BaseModel,Field
from qq_bridge import QQBridge

class QQConfig(BaseModel):
    enabled:bool=False
    bot_id:str=Field(default='',max_length=12)
    peers:str=Field(default='',max_length=300)
    voice_enabled:bool=True
class Preview(BaseModel):
    text:str=Field(min_length=1,max_length=1200)

def register_qq_routes(app,*,authorize,root):
    root=Path(root);bridge=QQBridge(root);preview_gate=threading.Lock()
    from qq_login import LoginService
    login_service=LoginService(root,bridge)
    @app.get('/qq',response_class=HTMLResponse)
    def page():return (root/'qq.html').read_text(encoding='utf-8')
    @app.get('/qq/status',dependencies=[Depends(authorize)])
    def status():return bridge.snapshot()
    @app.post('/qq/config',dependencies=[Depends(authorize)])
    def config(request:QQConfig):
        peers=[p for p in re.split(r'[,，\s]+',request.peers.strip()) if p]
        try:bridge.configure(request.enabled,request.bot_id.strip(),peers,request.voice_enabled)
        except ValueError as exc:raise HTTPException(400,str(exc)) from None
        return bridge.snapshot()
    @app.get('/qq/qr',dependencies=[Depends(authorize)])
    def qr():
        import json
        f=root/'configs/qq-client.local.json'
        if not f.exists():raise HTTPException(404,'独立QQ客户端尚未启动。')
        config=json.loads(f.read_text(encoding='utf-8'));image=Path(config['napcat_root'])/'cache/qrcode.png'
        if not image.is_file():raise HTTPException(404,'正在等待QQ生成登录二维码。')
        return FileResponse(image,media_type='image/png',headers={'Cache-Control':'no-store'})
    @app.post('/qq/qr/refresh',dependencies=[Depends(authorize)])
    def refresh_login():
        return login_service.ensure(force=True)
    @app.post('/qq/login/ensure',dependencies=[Depends(authorize)])
    def ensure_login():return login_service.ensure()
    @app.post('/qq/video/recheck',dependencies=[Depends(authorize)])
    def recheck_video():
        try:return bridge.recheck_video()
        except Exception as exc:raise HTTPException(503,str(exc)[:180]) from None
    @app.post('/qq/preview',dependencies=[Depends(authorize)])
    def preview(request:Preview):
        if not preview_gate.acquire(blocking=False):raise HTTPException(409,'已有本地预览正在生成。')
        try:
            result=bridge.chat.prepare('local_preview',request.text,voice_enabled=True,remember=False)
            return {'reply':result['reply'],'tools':result['tools'],'attachments':[{'kind':a['kind'],'id':a.get('id'),
                'url':'/stickers/images/'+a['id'] if a['kind']=='image' else 'http://127.0.0.1:18765/artifacts/'+Path(a['path']).stem} for a in result['attachments']],
                'sent_to_qq':False}
        except Exception as exc:raise HTTPException(503,str(exc)[:200]) from None
        finally:preview_gate.release()
    return bridge
