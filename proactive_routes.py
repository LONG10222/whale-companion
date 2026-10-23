from pathlib import Path
from fastapi import APIRouter,Depends,HTTPException
from fastapi.responses import HTMLResponse,FileResponse
from pydantic import BaseModel,Field,ConfigDict
from typing import Literal
from proactive_chat import ProactiveChat

class StrictRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
class ProactiveConfig(StrictRequest):
    enabled:bool
    mode:Literal['preview_only','automatic']|None=None
    news_enabled:bool|None=None
    bilibili_enabled:bool|None=None
class ProactivePreview(StrictRequest):
    regenerate:bool=False
class ProactiveEdit(StrictRequest):
    message:str=Field(min_length=1,max_length=160)
class ProactiveSend(StrictRequest):
    draft_id:str=Field(pattern='^[a-f0-9]{32}$')
    approved_text:str=Field(min_length=1,max_length=160)

def register_proactive_routes(app,*,authorize,root,bridge):
    root=Path(root);service=ProactiveChat(root,bridge);bridge.proactive=service
    router=APIRouter(prefix='/qq/proactive',dependencies=[Depends(authorize)])
    def run(action):
        try:return action()
        except ValueError as exc:raise HTTPException(409,str(exc)) from None
        except Exception:raise HTTPException(503,'本次操作未完成。若刚点过发送，请刷新查看记录；不要重复点击。') from None
    @app.get('/qq/proactive',response_class=HTMLResponse)
    def page():return (root/'proactive.html').read_text(encoding='utf-8')
    @app.get('/qq/proactive-ui.js')
    def script():return FileResponse(root/'proactive_ui.js',media_type='application/javascript')
    @router.get('/state')
    def state():return run(service.state)
    @router.post('/config')
    def config(body:ProactiveConfig):
        run(lambda:service.configure(body.enabled,body.mode,body.news_enabled,body.bilibili_enabled));return {'saved':True,'automatic_send':service.settings()['mode']=='automatic' and body.enabled}
    @router.post('/preview')
    def preview(body:ProactivePreview):return {'draft':run(lambda:service.generate(manual=True,regenerate=body.regenerate)),'sent':False}
    @router.post('/drafts/{draft_id}/edit')
    def edit(draft_id:str,body:ProactiveEdit):
        run(lambda:service.edit(draft_id,body.message));return {'saved':True,'sent':False}
    @router.post('/drafts/{draft_id}/dismiss')
    def dismiss(draft_id:str):
        run(lambda:service.dismiss(draft_id));return {'dismissed':True,'sent':False}
    @router.post('/contact/resume')
    def resume():
        run(service.resume_contact);return {'saved':True}
    @router.post('/send')
    def send(body:ProactiveSend):return run(lambda:service.send_manual(body.draft_id,body.approved_text))
    app.include_router(router);return service
