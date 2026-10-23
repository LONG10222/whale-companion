"""Local DeepSeek tool-calling UI endpoints and explicit no-model demonstration."""
from pathlib import Path
import json,re,threading,time,uuid
from fastapi import Depends,HTTPException
from pydantic import BaseModel,Field
from typing import Literal
import companion_agent,deepseek_client
from agent_tools import TOOLS,execute

class AgentRequest(BaseModel):
    message:str=Field(min_length=1,max_length=2000)

class KeyRequest(BaseModel):
    api_key:str=Field(min_length=24,max_length=200)

class DemoRequest(BaseModel):
    voice:Literal['dania','qixuan','soulx']='dania'

def register_agent_routes(app,*,authorize,root):
    root=Path(root);gate=threading.Lock()
    state={'busy':False,'events':[]}
    @app.get('/agent/status',dependencies=[Depends(authorize)])
    def status():
        return {'deepseek_key_configured':deepseek_client.ready(),'model':deepseek_client.configuration()['model'],
            'busy':state['busy'],'events':state['events'][-30:],'tools':[t['function']['name'] for t in TOOLS]}
    @app.post('/agent/config',dependencies=[Depends(authorize)])
    def configure(request:KeyRequest):
        if not request.api_key.startswith('sk-'):raise HTTPException(400,'请填写有效的 DeepSeek API Key')
        file=root/'agent.local.json'
        config=json.loads(file.read_text(encoding='utf-8')) if file.exists() else {'model':'deepseek-flash'}
        config['api_key']=request.api_key
        file.write_text(json.dumps(config,indent=2),encoding='utf-8')
        return {'saved':True,'key_verified_with_provider':False}
    def start():
        if not gate.acquire(blocking=False):raise HTTPException(409,'已有助手任务正在执行')
        state['busy']=True;state['events']=[]
    @app.post('/v1/agent/run',dependencies=[Depends(authorize)])
    def run(request:AgentRequest):
        start()
        try:return companion_agent.run(request.message,emit=state['events'].append)
        except RuntimeError as exc:raise HTTPException(503,str(exc))
        finally:state['busy']=False;gate.release()
    @app.post('/v1/agent/demo',dependencies=[Depends(authorize)])
    def demo(request:DemoRequest):
        start();started=time.monotonic();artifacts=[]
        def call(name,args):
            state['events'].append({'type':'tool_call','name':name,'arguments':args})
            try:result=execute(name,args)
            except RuntimeError as exc:
                state['events'].append({'type':'tool_result','name':name,'result':{'ok':False,'error':str(exc)}})
                raise
            state['events'].append({'type':'tool_result','name':name,'result':result})
            if result.get('artifact_id'):artifacts.append(result)
            return result
        try:
            call('get_tool_guide',{'topic':'singing'})
            found=call('web_search',{'query':'春曉','source':'wikisource'})
            item=next((x for x in found['results'] if x['title']=='春曉 (孟浩然)'),None)
            if item is None:raise RuntimeError('这次联网搜索未找到《春晓》的作品页，请稍后重试')
            source=call('read_web_page',{'url':item['url']})
            source_text=re.sub(r'\[\d+\]','',source['text'])
            pairs=[('春眠不覺曉','春眠不觉晓'),('處處聞啼鳥','处处闻啼鸟'),('夜來風雨聲','夜来风雨声'),('花落知多少','花落知多少')]
            for traditional,simplified in pairs:
                if traditional not in source_text and simplified not in source_text:
                    raise RuntimeError('网页未包含预期诗句，停止生成以避免用错歌词')
            call('inspect_resources',{})
            from score_adapter import demo_request
            score_request=demo_request();score_request['source_url']=source['url'];score_request['voice']=request.voice
            if request.voice=='soulx':score_request['title']='春晓 · SoulX-Singer AI合成清唱'
            if request.voice=='dania':score_request['title']='春晓 · 达妮娅中文参考声线 · AI合成'
            call('sing_from_score',score_request)
            result={'ok':True,'run_id':uuid.uuid4().hex,'controller':'local_demo_no_llm','deepseek_api_called':False,
                'reply':'已联网核实《春晓》，按逐字音符生成清唱，并经过唱词与音频完整性检查。'+artifacts[-1]['credits']+' 真人感和情绪仍待听感验收。',
                'artifacts':artifacts,'trace':state['events'].copy(),'elapsed_seconds':round(time.monotonic()-started,2)}
            folder=root/'agent-runs';folder.mkdir(exist_ok=True)
            (folder/(result['run_id']+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            companion_agent.remember_recipe(result['trace'],result['controller'])
            return result
        except (RuntimeError,ValueError,OSError) as exc:raise HTTPException(503,str(exc))
        finally:state['busy']=False;gate.release()
