from pathlib import Path
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException
from fastapi.responses import HTMLResponse,FileResponse
from pydantic import BaseModel,Field,ConfigDict
from companion_life import CompanionLife

class LifeConfig(BaseModel):
    model_config=ConfigDict(extra='forbid')
    enabled:bool=True
    learning:bool=True
    autonomy:bool=True
    web:bool=True
    phone:bool=True
    interval_minutes:int=Field(default=30,ge=15,le=240)
    daily_activities:int=Field(default=2,ge=1,le=4)
    topics:list[str]=Field(min_length=1,max_length=6)
class NoteStatus(BaseModel):status:Literal['active','disabled','pending']
class Recall(BaseModel):query:str=Field(min_length=1,max_length=160)

def register_life_routes(app,*,root,authorize,bridge,proactive):
    root=Path(root);service=CompanionLife(root,bridge,proactive)
    router=APIRouter(prefix='/qq/life',dependencies=[Depends(authorize)])
    def call(fn):
        try:return fn()
        except ValueError as exc:raise HTTPException(409,str(exc)) from None
        except Exception:raise HTTPException(503,'后台服务暂未完成，请查看活动记录后再试。') from None
    @app.get('/qq/life',response_class=HTMLResponse)
    def page():return (root/'life.html').read_text(encoding='utf-8')
    @app.get('/qq/life-ui.js')
    def script():return FileResponse(root/'life_ui.js',media_type='application/javascript')
    @router.get('/state')
    def state():return call(service.state)
    @router.post('/config')
    def config(body:LifeConfig):return {'saved':True,'config':call(lambda:service.configure(body.model_dump()))}
    @router.post('/learn')
    def learn():return call(service.learning_once)
    @router.post('/activity')
    def activity():return call(service.activity_once)
    @router.post('/stop')
    def stop():
        call(service.scope);service.cancelled.set();call(service.cancel_owned_phone)
        return {'stopping':True}
    @router.post('/memories/{nid}/status')
    def change(nid:int,body:NoteStatus):
        call(lambda:service.memory.change(service.scope(),nid,body.status));return {'saved':True}
    @router.post('/recall')
    def recall(body:Recall):return call(lambda:service.memory.recall(service.scope(),body.query))
    app.include_router(router);return service
