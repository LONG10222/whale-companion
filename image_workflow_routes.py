from pathlib import Path
import json,re
from fastapi import APIRouter,Depends,HTTPException
from fastapi.responses import HTMLResponse,FileResponse
from pydantic import BaseModel,ConfigDict,StrictBool,Field
from image_workflow import policy_state,STAGES
from qq_scope import actor,group

class Strict(BaseModel):model_config=ConfigDict(extra='forbid')
class Config(Strict):enabled:StrictBool
class Selection(Strict):version:int=Field(ge=1,le=2)
class Preview(Strict):
    text:str=Field(min_length=2,max_length=2000)
    source_sha256:str=Field(pattern='^[a-f0-9]{64}$')

def register_image_workflow(app,*,root,authorize,service):
    root=Path(root);router=APIRouter(prefix='/qq/creative/workflow',dependencies=[Depends(authorize)])
    def call(fn):
        try:return fn()
        except ValueError as exc:raise HTTPException(409,str(exc)) from None
    def sources():
        owner=service.contacts.admin
        with service.bridge.chat.memory.db() as db:rows=db.execute('SELECT i.peer,i.sha,max(i.at) AS at FROM image_messages i WHERE i.peer=? OR i.peer LIKE ? GROUP BY i.peer,i.sha ORDER BY at DESC LIMIT 100',(owner,'group:%:'+owner)).fetchall()
        result=[];seen=set()
        for peer,sha,at in rows:
            if actor(peer)!=owner or sha in seen or group(peer) and not service.bridge.groups.allowed(group(peer)):continue
            cache=service.bridge.chat.images.cache
            if not re.fullmatch('[a-f0-9]{64}',sha) or not any((cache/(sha+'.'+ext)).is_file() and (cache/(sha+'.'+ext)).stat().st_size<=12*1024**2 for ext in ['png','jpg','gif','webp']):continue
            result.append({'sha256':sha,'scope':'群内收到的图片' if group(peer) else '私聊收到的图片','at':at,'_peer':peer});seen.add(sha)
        return result[:12]
    @app.get('/qq/creative/workflow',response_class=HTMLResponse)
    def page():return (root/'image_workflow.html').read_text(encoding='utf-8')
    @app.get('/qq/creative/workflow.js')
    def script():return FileResponse(root/'image_workflow_ui.js',media_type='application/javascript',headers={'Cache-Control':'no-store'})
    @router.get('/state')
    def state():
        owner,_=call(service.scope);items=sources()
        return {**policy_state(root),'phases':STAGES,'sources':[{k:v for k,v in s.items() if k!='_peer'} for s in items],
                'jobs':service.workflow_recent(),'thread_slots':service.thread_state()}
    @router.post('/config')
    def config(body:Config):
        path=root/'configs/image-workflow.local.json';tmp=path.with_suffix('.tmp')
        path.parent.mkdir(parents=True,exist_ok=True)
        tmp.write_text(json.dumps({'enabled':body.enabled},ensure_ascii=False),encoding='utf-8');tmp.replace(path)
        return {'saved':True,'enabled':body.enabled}
    @router.post('/preview')
    def preview(body:Preview):
        source=next((s for s in sources() if s['sha256']==body.source_sha256),None)
        if not source:raise HTTPException(409,'这张图片不是当前管理员已收到并缓存的素材。')
        return call(lambda:service.request(service.contacts.admin,'draw_picture',{'prompt':body.text},body.text,
                    context={'deliver':False,'_image_edit':True,'_workflow_preview':True,'_local_source_peer':source['_peer'],'source_image_shas':[body.source_sha256]}))
    @router.get('/sources/{sha}')
    def source_image(sha:str):
        if not re.fullmatch('[a-f0-9]{64}',sha) or sha not in {s['sha256'] for s in sources()}:raise HTTPException(404)
        return FileResponse(service.bridge.chat.images.cached_input(sha)['path'],headers={'Cache-Control':'no-store'})
    @router.get('/jobs/{jid}/versions/{version}')
    def version_image(jid:str,version:int):return FileResponse(call(lambda:service.workflow_version(jid,version))[3],headers={'Cache-Control':'no-store'})
    @router.get('/jobs/{jid}/original')
    def original_image(jid:str):return FileResponse(call(lambda:service.workflow_original(jid)),headers={'Cache-Control':'no-store'})
    @router.post('/jobs/{jid}/select')
    def select(jid:str,body:Selection):return call(lambda:service.approve_workflow(jid,body.version))
    @router.post('/jobs/{jid}/cancel')
    def cancel(jid:str):return call(lambda:service.cancel_workflow(jid))
    @router.post('/threads/{tid}/restore')
    def restore(tid:str):return call(lambda:service.restore_work_thread(tid))
    app.include_router(router)
