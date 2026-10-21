from pathlib import Path
import hashlib,json,threading,time,uuid
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException
from fastapi.responses import HTMLResponse,FileResponse
from pydantic import BaseModel,Field,ConfigDict
from qq_tasks import QQTasks
from persona_store import card_messages
from qq_chat import INSTRUCTIONS
import deepseek_client

class Strict(BaseModel):model_config=ConfigDict(extra='forbid')
class AgentConfig(Strict):
    planning_mode:Literal['auto','off','on']='auto'
    web_enabled:bool=True
    phone_enabled:bool=True
    sticker_mode:Literal['requested','natural','frequent']='natural'
    image_layout:Literal['mostly_separate','separate','combined']='mostly_separate'
class Preview(Strict):text:str=Field(min_length=1,max_length=1200)
class Status(Strict):status:Literal['approved','disabled','rejected']
class Block(Strict):
    term:str=Field(min_length=1,max_length=20)
    blocked:bool=True

def register_qq_agent_routes(app,*,authorize,root,bridge,mobile):
    root=Path(root);service=QQTasks(root,bridge,mobile);router=APIRouter(prefix='/qq/agent',dependencies=[Depends(authorize)])
    preview_gate=threading.Lock();learning_gate=threading.Lock();compare_gate=threading.Lock()
    def call(action):
        try:return action()
        except ValueError as exc:raise HTTPException(409,str(exc)) from None
        except Exception as exc:raise HTTPException(503,str(exc)[:200]) from None
    @app.get('/qq/agent',response_class=HTMLResponse)
    def page():return (root/'qq_agent.html').read_text(encoding='utf-8')
    @app.get('/qq/agent-ui.js')
    def script():return FileResponse(root/'qq_agent_ui.js',media_type='application/javascript')
    @router.get('/state')
    def state():
        who,_=call(service.scope)
        return {'contact':'owner','settings':bridge.chat.agent_settings(),'jobs':service.recent(who),'jargon':bridge.chat.jargon.list(who),
            'planning_backend':'DeepSeek Flash / 按任务启用思考','local_model_loaded_for_chat':False,
            'resources_last_checked':service.cached_resource,'only_read_phone':True,'automatic_proactive_send':False,
            'tools':['表情包','语音','历史记忆查询','公开网页搜索与读取','小黑盒后台阅读']}
    @router.post('/config')
    def config(body:AgentConfig):
        call(service.scope);path=root/'configs/agent-tools.local.json';path.parent.mkdir(parents=True,exist_ok=True)
        temp=path.with_suffix('.tmp');temp.write_text(json.dumps(body.model_dump(),ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)
        return {'saved':True}
    @router.post('/preview')
    def preview(body:Preview):
        if not preview_gate.acquire(False):raise HTTPException(409,'已有本地预览正在处理。')
        try:
            who,_=call(service.scope);result=call(lambda:bridge.chat.prepare(who,body.text,remember=False))
            return {'reply':result['reply'],'tools':result['tools'],'attachments':[{'kind':a['kind'],'id':a.get('id'),'url':'/stickers/images/'+a['id']} for a in result['attachments'] if a['kind']=='image'],
                'sent_to_qq':False,'memory_written':False}
        finally:preview_gate.release()
    @router.post('/cancel')
    def cancel():
        who,_=call(service.scope);return {'message':call(lambda:service.cancel(who))}
    @router.post('/jargon/extract')
    def extract():
        if not learning_gate.acquire(False):raise HTTPException(409,'已有词条整理正在执行。')
        try:
            who,_=call(service.scope);return call(lambda:bridge.chat.jargon.extract(who))
        finally:learning_gate.release()
    @router.post('/jargon/{entry_id}/status')
    def status(entry_id:int,body:Status):
        who,_=call(service.scope);call(lambda:bridge.chat.jargon.change(who,entry_id,body.status));return {'saved':True}
    @router.post('/jargon/block')
    def block(body:Block):
        who,_=call(service.scope)
        call(lambda:bridge.chat.jargon.block(who,body.term) if body.blocked else bridge.chat.jargon.unblock(who,body.term));return {'saved':True}
    @router.post('/compare')
    def compare():
        if not compare_gate.acquire(False):raise HTTPException(409,'已有模式对比正在运行。')
        try:
            who,_=call(service.scope);cases=[];card=bridge.chat.persona.load()
            samples=['鸣潮刷材料刷了一下午，今天又坐牢了。','我说我是菜狗，你还真准备给我发养狗建议啊？','刚才说错了，是角色A不是角色B。']
            for text in samples:
                row={'message':text}
                for label,think in [('fast',False),('thinking',True)]:
                    messages=card_messages(card,INSTRUCTIONS,text);jargon=bridge.chat.jargon.prompt(who,text)
                    if jargon:messages.append(jargon)
                    messages.append({'role':'user','content':text});start=time.monotonic()
                    response,usage=deepseek_client.complete(messages,None,thinking=think,max_tokens=1800 if not think else 4096,timeout=45)
                    row[label]={'reply':(response.get('content') or '')[:900],'seconds':round(time.monotonic()-start,2),'usage':usage}
                cases.append(row)
            report={'at':time.time(),'cases':cases,'sent_to_qq':False,'automatic_score':None,
                'core_sha256':hashlib.sha256(bridge.chat.persona.path.read_bytes()).hexdigest(),'note':'同一角色和语境的真实输出；请人工判断接话与耗时，不自动加入训练。'}
            folder=root/'data/qq/agent-comparisons';folder.mkdir(parents=True,exist_ok=True)
            (folder/(uuid.uuid4().hex+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');return report
        except Exception as exc:raise HTTPException(503,str(exc)[:200]) from None
        finally:compare_gate.release()
    @router.get('/comparison')
    def comparison():
        folder=root/'data/qq/agent-comparisons';files=list(folder.glob('*.json'))
        return {'report':json.loads(max(files,key=lambda p:p.stat().st_mtime).read_text(encoding='utf-8')) if files else None}
    app.include_router(router);return service
