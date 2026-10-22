from pathlib import Path
from fastapi import APIRouter,Depends,HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel,Field
from qq_account import AccountSwitch

class BindAccount(BaseModel):bot_id:str=Field(pattern=r'^[1-9]\d{4,11}$')
def register_account_switch(app,*,root,authorize,bridge,proactive):
    root=Path(root);service=AccountSwitch(root,bridge,proactive);router=APIRouter(prefix='/qq/switch',dependencies=[Depends(authorize)])
    if getattr(bridge,'login_manager',None):bridge.login_manager.switcher=service
    def call(fn):
        try:return fn()
        except ValueError as exc:raise HTTPException(409,str(exc)) from None
        except Exception:raise HTTPException(503,'独立QQ暂未完成操作，请稍后重试。') from None
    @app.get('/qq/switch',response_class=HTMLResponse)
    def page():return (root/'qq_switch.html').read_text(encoding='utf-8')
    @router.get('/state')
    def state():return call(service.state)
    @router.post('/begin')
    def begin():return call(service.begin)
    @router.post('/bind')
    def bind(body:BindAccount):return call(lambda:service.bind(body.bot_id))
    @router.post('/finish')
    def finish():return call(service.finish)
    app.include_router(router);return service
