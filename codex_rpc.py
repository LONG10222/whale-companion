"""Owned stdio app-server client. No public listener or inherited agent permissions."""
from pathlib import Path
import json,queue,shutil,subprocess,threading,time

MODELS=['gpt-6.1-sol','gpt-6-sol','gpt-6-luna','gpt-5.6-sol','gpt-5.6-terra','gpt-5.6-luna']
EFFORTS=['low','medium','high']
def validate_choice(model,effort):
    if model not in MODELS or effort not in EFFORTS:raise ValueError('模型最高只允许 GPT-6.1 Sol / high。')

class CodexRPC:
    def __init__(self,root,overrides=None):
        self.root=Path(root);self.events=queue.Queue();self.seq=0;self.proc=None;self.log=None;self.overrides=overrides or {}
    def start(self):
        binary=shutil.which('codex.exe') or shutil.which('codex')
        if not binary:raise ValueError('未找到本机 Codex。')
        folder=self.root/'cache/codex-worker';folder.mkdir(parents=True,exist_ok=True);self.log=(folder/('worker-'+str(time.time_ns())+'.log')).open('w',encoding='utf-8')
        args=[binary,'app-server','--listen','stdio://']
        for value in ['features.apps=false','features.multi_agent=false','features.computer_use=false','features.browser_use=false','features.in_app_browser=false','features.fast_mode=false','features.ultrafast_mode=false','service_tier="default"','mcp_servers={}','windows.sandbox=mxc']:
            args.extend(['-c',value])
        def toml(value):
            if isinstance(value,dict):return '{'+','.join(json.dumps(k,ensure_ascii=False)+'='+toml(v) for k,v in value.items())+'}'
            if isinstance(value,bool):return 'true' if value else 'false'
            return json.dumps(value,ensure_ascii=False)
        for key,value in self.overrides.items():args.extend(['-c',key+'='+toml(value)])
        self.proc=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,text=True,encoding='utf-8',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)|getattr(subprocess,'BELOW_NORMAL_PRIORITY_CLASS',0))
        def read():
            for line in self.proc.stdout:
                try:self.events.put(json.loads(line))
                except ValueError:pass
            self.events.put({'method':'transport/closed'})
        threading.Thread(target=read,daemon=True).start()
        try:self.request('initialize',{'clientInfo':{'name':'whale_qq_worker','title':'鲸娅工作台','version':'1.0.0'},'capabilities':{'experimentalApi':True}})
        except Exception:self.close();raise
        self.send({'method':'initialized','params':{}});return self
    def send(self,value):
        self.proc.stdin.write(json.dumps(value,ensure_ascii=False)+'\n');self.proc.stdin.flush()
    def next(self,timeout=20):
        try:event=self.events.get(timeout=timeout)
        except queue.Empty:raise TimeoutError('Codex 暂未返回进度。') from None
        if event.get('method')=='transport/closed':raise RuntimeError('Codex 工作连接已结束。')
        if 'id' in event and 'method' in event:
            # Never automatically approve a shell escape, permission expansion, or connector action.
            self.send({'id':event['id'],'error':{'code':-32601,'message':'This QQ integration cannot approve elevated access or interactive requests.'}})
        return event
    def request(self,method,params,timeout=25):
        self.seq+=1;rid=self.seq;self.send({'id':rid,'method':method,'params':params});deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            e=self.next(max(.1,deadline-time.monotonic()))
            if e.get('id')!=rid:continue
            if 'error' in e:raise RuntimeError('Codex 接口未完成请求：'+str(e['error'].get('message',''))[:220])
            return e.get('result')
        raise TimeoutError('Codex 请求超时。')
    def close(self):
        if self.proc:
            try:self.proc.stdin.close();self.proc.wait(4)
            except Exception:
                self.proc.terminate()
                try:self.proc.wait(4)
                except Exception:self.proc.kill();self.proc.wait()
        if self.log:self.log.close()
    def __enter__(self):return self.start()
    def __exit__(self,*args):self.close()
