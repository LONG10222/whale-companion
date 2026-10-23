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
    def terms(value):
        value=re.sub(r'\bopen\s+ai\b','openai',value,flags=re.I)
        english=set(re.findall(r'[a-z][a-z0-9+.-]{2,}',value.casefold()))
        chinese={s[i:i+2] for s in re.findall(r'[\u3400-\u9fff]{2,}',value) for i in range(len(s)-1)}
        return english|(chinese-{'是不','不是','一下','查一','检查','去检','看看','帮我','给我','能不','不能','可不','不可','什么','怎么','如何','消息','是否','今天','下是'})
    query=terms(data.get('query',''))
    if 'openai' in query:query|={'chatgpt','codex'}
    for source in sources[:3]:
        paragraphs=[p.strip() for p in source['text'].splitlines() if len(p.strip())>=16 and len(re.findall(r'台数限定|店長おまかせ|中古|Office付|優惠券|优惠券|立即购买|加入购物车|限时抢购|Sponsored|Advertisement',p,re.I))<2]
        if query:paragraphs=[p for p in paragraphs if query & terms(p)]
        paragraphs.sort(key=lambda p:len(query & terms(p)),reverse=True)
        if not paragraphs:continue
        excerpt=paragraphs[0][:220]
        lines.append('《'+source.get('title','网页资料')[:70]+'》的摘录：'+excerpt+'\n'+source['url'])
    return '\n\n'.join(lines) if len(lines)>1 else EMPTY

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
        if not cancelled() and (deadline is None or self.clock()+10<deadline):
            try:
                from jargon_store import JargonStore
                learned=JargonStore(self.root).learn_public(peer,args['query'],state.get('sources',[]))
                if learned:data.setdefault('metadata',{})['slang_references']=learned
            except Exception:pass  # Research still returns its verified sources if optional learning fails.
        if data.get('metadata',{}).get('thread_id'):progress(**{k:v for k,v in data['metadata'].items() if k in {'thread_id','directory','model','effort','title','project_id'}})
        if data.get('text'):return data
        return {'text':fallback(state,timed_out),'attachments':[],'metadata':{'timed_out':timed_out,'partial':bool(state.get('sources')),'sources':[{k:s.get(k,'') for k in ['title','url']} for s in state.get('sources',[])],
            'model':'gpt-6-luna','service_tier':'default','summary_mode':'source_excerpts' if state.get('sources') else 'no_reliable_result'}}
