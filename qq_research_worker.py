"""Owned child: fetch a few public pages, ask Luna for a grounded short summary."""
from pathlib import Path
import json,sys,time
from qq_research import save,fallback,EMPTY
from conversation_tools import public_query

def research(request):
    from agent_web import web_search,read_web_page,validate_public_url
    from codex_workbench import CodexWorkbench
    root=Path(request['root']);folder=Path(sys.argv[1]).parent;state={'query':request['query'],'sources':[],'search_results':[]}
    def publish():save(folder/'progress.json',state)
    def left():return request['deadline']-time.time() if request.get('deadline') else float('inf')
    publish()
    # No login sessions, private networks, attachments or executable downloads.
    try:
        result=web_search(public_query(request['query']));state['search_results']=result.get('results',[])[:5];publish()
        for item in state['search_results'][:3]:
            if left()<110:break # Reserve time for the summarizer and clean termination.
            try:
                validate_public_url(item['url']);page=read_web_page(item['url'])
                if page.get('ok') and page.get('text','').strip():state['sources'].append({'title':item.get('title','网页资料'),'url':page['url'],'text':page['text'][:7000]});publish()
            except Exception:continue
    except Exception:pass
    text=fallback(state);metadata={'sources':[{k:s[k] for k in ['title','url']} for s in state['sources']],'model':'gpt-6-luna','service_tier':'default','summary_mode':'source_excerpts'}
    if state['sources'] and left()>15:
        def progress(**kw):
            state['native']=state.get('native',{})|{k:v for k,v in kw.items() if k in {'thread_id','directory','model','effort'}};publish()
        try:
            output=CodexWorkbench(root).run(request['id'],request['query'],model='gpt-6-luna',effort='low',thread_id=request.get('thread_id',''),directory=request.get('directory',''),
                label=request['label'],role=request['role'],kind='lookup_web',public_data=state['sources'],progress=progress)
            answer=output.get('text','').strip()
            allowed={s['url'].rstrip('/') for s in state['sources']}
            import re
            links=re.findall(r'https://[^\s<>\[\]（）。，；！？”“]+',answer)
            if answer and all(u.rstrip('/).,;') in allowed for u in links):text=answer
            metadata.update({k:v for k,v in output.items() if k not in {'text','images'}});metadata['summary_mode']='luna' if text==answer else 'source_excerpts'
        except Exception:pass
    if not state['sources']:metadata['summary_mode']='no_reliable_result';text=EMPTY
    return {'text':text,'attachments':[],'metadata':metadata}

if __name__=='__main__':
    from usage_ledger import USAGE_CONTEXT
    req=Path(sys.argv[1]);request=json.loads(req.read_text(encoding='utf-8'));USAGE_CONTEXT.set({'peer':request['peer'],'role':request['role'],'job_id':request['id']})
    result=research(request);save(req.parent/'result.json',result)
