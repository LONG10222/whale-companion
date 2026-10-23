from pathlib import Path
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException
from fastapi.responses import HTMLResponse,FileResponse
from pydantic import BaseModel,Field,ConfigDict,StrictBool
from qq_creative import CreativeJobs

class Preview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    kind:Literal['sing_song','draw_picture','delegate_codex','lookup_web']
    text:str=Field(min_length=2,max_length=2000)
    args:dict

class PlusSetting(BaseModel):
    model_config=ConfigDict(extra='forbid')
    enabled:StrictBool

class MoneyEntry(BaseModel):
    model_config=ConfigDict(extra='forbid')
    direction:Literal['income','expense']
    currency:Literal['CNY','USD']='CNY'
    amount:str=Field(min_length=1,max_length=30)
    note:str=Field(default='',max_length=200)

def register_creative_routes(app,*,root,authorize,bridge):
    root=Path(root);service=CreativeJobs(root,bridge);router=APIRouter(prefix='/qq/creative',dependencies=[Depends(authorize)])
    from image_workflow_routes import register_image_workflow
    register_image_workflow(app,root=root,authorize=authorize,service=service)
    def call(fn):
        try:return fn()
        except ValueError as exc:raise HTTPException(409,str(exc)) from None
        except Exception as exc:raise HTTPException(503,str(exc)[:200]) from None
    @app.get('/qq/creative',response_class=HTMLResponse)
    def page():return (root/'qq_creative.html').read_text(encoding='utf-8')
    @router.get('/state')
    def state():
        who,_=call(service.scope)
        from workspace_policy import ALIASES
        from image_workflow import policy_state
        return {'contact':'long','jobs':service.recent(who),'model_ceiling':'普通工作gpt-6.1-sol / high；原图修图gpt-6.1-sol / xhigh','image_workflow':policy_state(root),'workspace':str(service.workbench.workspace),'sources_read_only':True,'image_layout':bridge.chat.agent_settings()['image_layout'],
            'project':'deepseek娘','external_read_requires_current_request':True,'outside_write_allowed':False,'available_read_sources':list(ALIASES),
            'service_tier':'default','speed':'普通速度','groups_enabled':bool(getattr(bridge,'groups',None) and any(g['enabled'] for g in bridge.groups.state()['groups'])),'admin_id':service.contacts.admin,'contacts':service.contacts.all(),
            'ordinary_user':{'model':'Luna','tools':['聊天','自己的记忆','表情','画图','语音','公开网页查询'],'computer_access':False,'thread_cap':3},'admin_thread_cap':6,'active_workers':1,
            'query_policy':{'window_seconds':900,'max_queries':2,'timeout_seconds':300,'daily_limit':None,'plus_exempt':True,'model':'gpt-6-luna','timeout_includes_queue':True},'pending_cap':3}
    @router.post('/contacts/{peer}/plus')
    def plus(peer:str,body:PlusSetting):
        call(lambda:bridge.directory.resolve(peer))
        contact=call(lambda:service.contacts.set_plus(peer,body.enabled))
        service.ledger.record('plus_changed',peer=peer,role=contact['role'],state='enabled' if body.enabled else 'disabled',details={'origin':'local_owner'})
        return contact
    @router.get('/ledger')
    def ledger():return call(service.ledger.state)
    @router.post('/ledger/money')
    def money(body:MoneyEntry):return {'id':call(lambda:service.ledger.manual_money(body.direction,body.amount,body.currency,body.note))}
    @router.get('/ledger/export/{day}')
    def export(day:str):
        import re
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',day):raise HTTPException(404)
        p=service.ledger.folder/(day+'-使用与收支.jsonl')
        if not p.is_file():raise HTTPException(404)
        return FileResponse(p,media_type='application/x-ndjson',filename=p.name)
    @router.post('/preview')
    def preview(body:Preview):
        who,_=call(service.scope);return call(lambda:service.request(who,body.kind,body.args,body.text))
    @router.post('/cancel')
    def cancel():
        who,_=call(service.scope);return {'message':call(lambda:service.cancel(who))}
    @router.post('/jobs/{jid}/send')
    def send(jid:str):return call(lambda:service.deliver(jid,owner=True))
    @router.get('/jobs/{jid}/media/{index}')
    def media(jid:str,index:int):
        who,_=call(service.scope)
        with service.db() as db:r=db.execute("SELECT result FROM jobs WHERE id=? AND peer=? AND state='completed'",(jid,who)).fetchone()
        import json
        items=json.loads(r['result']).get('attachments',[]) if r else []
        if not 0<=index<len(items):raise HTTPException(404)
        p=Path(items[index]['path']).resolve()
        if not any(p.is_relative_to(base.resolve()) for base in [service.folder,root/'artifacts']):raise HTTPException(404)
        return FileResponse(p,media_type='audio/wav' if items[index]['kind']=='record' else None)
    app.include_router(router);return service
