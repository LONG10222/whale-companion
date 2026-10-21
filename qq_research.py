"""Read-only public research with durable partial results and a hard wall clock."""
from pathlib import Path
import json,re,sys,time
from owned_query_process import QueryProcess
from workspace_policy import WORKSPACE

EMPTY='这次没找到能确认的资料，我先不拿不确定的消息糊弄你。给我更具体的关键词或原帖链接，下次查会更准。'

def save(file,value):
    file=Path(file);stage=file.with_suffix('.staging.json');stage.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');stage.replace(file)

def load(file):
    try:return json.loads(Path(file).read_text(encoding='utf-8'))
    except (OSError,ValueError):return {}

def fallback(data,timed_out=False):
    # Excerpts are explicitly attributed, never promoted to verified facts.
    sources=[s for s in data.get('sources',[]) if s.get('text','').strip()]
    if not sources:return EMPTY
    lines=['先把这次读到的资料要点给你，剩下的还没核实：' if timed_out else '这次读到这些来源要点，先供你参考：']
    query=set(re.findall(r'[\u3400-\u9fff]|[A-Za-z]+',data.get('query','').casefold()))
    for source in sources[:3]:
        paragraphs=[p.strip() for p in source['text'].splitlines() if len(p.strip())>=25]
        paragraphs.sort(key=lambda p:len(query & set(re.findall(r'[\u3400-\u9fff]|[A-Za-z]+',p.casefold()))),reverse=True)
        excerpt=(paragraphs[0] if paragraphs else source['text'])[:220]
        lines.append('《'+source.get('title','网页资料')[:70]+'》的摘录：'+excerpt+'\n'+source['url'])
    return '\n\n'.join(lines)

class PublicResearch:
    def __init__(self,root,clock=time.time,sleeper=time.sleep,process_factory=QueryProcess):
        self.root=Path(root);self.clock=clock;self.sleep=sleeper;self.process_factory=process_factory
    def run(self,jid,peer,args,accepted_at,role,thread_id='',directory='',cancelled=lambda:False,progress=lambda **kw:None):
        if not re.fullmatch('[a-f0-9]{32}',jid):raise ValueError('无效查询编号。')
        folder=WORKSPACE/'queries'/jid;folder.mkdir(parents=True,exist_ok=True)
        deadline=accepted_at+300 if args.get('_query_timeout_seconds',300) else None
        request={'id':jid,'peer':peer,'query':args['query'],'role':role,'label':args.get('_label','联系人'),'thread_id':thread_id,'directory':directory,'deadline':deadline,'root':str(self.root)}
        req=folder/'request.json';partial=folder/'progress.json';done=folder/'result.json';save(req,request)
        if deadline and self.clock()>=deadline:return {'text':EMPTY,'attachments':[],'metadata':{'timed_out':True,'partial':False,'sources':[]}}
        with (folder/'worker.log').open('w',encoding='utf-8') as log:
            process=self.process_factory([sys.executable,'-X','utf8',str(self.root/'qq_research_worker.py'),str(req)],str(self.root),log)
            timed_out=False;last={}
            try:
                while process.poll() is None:
                    if cancelled():raise RuntimeError('查询已停止。')
                    if deadline and self.clock()>=deadline:timed_out=True;break
                    state=load(partial);native=state.get('native',{})
                    if native and native!=last:progress(**native);last=native
                    self.sleep(.15)
            finally:process.close()
        data=load(done) if not timed_out else {};state=load(partial)
        if data.get('metadata',{}).get('thread_id'):progress(**{k:v for k,v in data['metadata'].items() if k in {'thread_id','directory','model','effort'}})
        if data.get('text'):return data
        return {'text':fallback(state,timed_out),'attachments':[],'metadata':{'timed_out':timed_out,'partial':bool(state.get('sources')),'sources':[{k:s.get(k,'') for k in ['title','url']} for s in state.get('sources',[])],
            'model':'gpt-6-luna','service_tier':'default','summary_mode':'source_excerpts' if state.get('sources') else 'no_reliable_result'}}
