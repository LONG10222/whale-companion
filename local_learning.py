"""Disposable, low-priority local learning assistant. No automatic cloud fallback."""
from pathlib import Path
import concurrent.futures,ctypes,hashlib,json,os,re,secrets,socket,subprocess,tempfile,threading,time
import httpx,psutil

class AssistantBusy(RuntimeError):pass

class PriorityResourceLock:
    """Normal audio requests preempt the optional learning task; idle checks do not."""
    def __init__(self):
        self.base=threading.Lock();self.guard=threading.Lock();self.low_active=False;self.low_cancel=threading.Event()
    def acquire(self,blocking=True,timeout=-1):
        with self.guard:
            if self.base.acquire(False):return True
            low=self.low_active
            if low:self.low_cancel.set()
        if low and not blocking:return self.base.acquire(timeout=2)
        if not blocking:return False
        return self.base.acquire() if timeout==-1 else self.base.acquire(timeout=timeout)
    def acquire_low(self):
        with self.guard:
            if not self.base.acquire(False):return False
            self.low_active=True;self.low_cancel.clear();return True
    def acquire_idle(self):
        with self.guard:
            if self.low_active:return False
            return self.base.acquire(False)
    def release_low(self):
        with self.guard:self.low_active=False;self.base.release()
    def release(self):self.base.release()
    def locked(self):return self.base.locked()
    def __enter__(self):self.acquire();return self
    def __exit__(self,*args):self.release()

DEFAULT_POLICY={'mode':'local','min_free_vram_mib':9216,'min_free_ram_mib':8192,'max_gpu_util_percent':25,
    'max_cpu_percent':50,'reserve_vram_mib':6144,'reserve_ram_mib':6144,'max_seconds':35,'threads':2,'context':4096}

def load_policy(root):
    path=Path(root)/'configs/local-learning.local.json'
    data=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    result={**DEFAULT_POLICY,**data}
    if result['mode'] not in {'local','cloud','paused'}:result['mode']='paused'
    # Saved values may make resource limits stricter, never silently remove them.
    for key in ['min_free_vram_mib','min_free_ram_mib','reserve_vram_mib','reserve_ram_mib']:result[key]=max(DEFAULT_POLICY[key],int(result[key]))
    for key in ['max_gpu_util_percent','max_cpu_percent','max_seconds','threads','context']:result[key]=max(1,min(DEFAULT_POLICY[key],int(result[key])))
    return result

def resource_snapshot():
    try:
        out=subprocess.check_output(['nvidia-smi','--query-gpu=memory.used,memory.free,utilization.gpu','--format=csv,noheader,nounits'],
            text=True,timeout=3,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        used,free,util=[int(v.strip()) for v in out.splitlines()[0].split(',')]
        return {'gpu_used_mib':used,'gpu_free_mib':free,'gpu_util_percent':util,
            'ram_free_mib':int(psutil.virtual_memory().available/1024**2),'cpu_percent':psutil.cpu_percent(interval=.12)}
    except Exception:return None

def busy_reason(snapshot,policy,during=False):
    if snapshot is None:return '暂时无法确认电脑资源，本地辅助已暂缓。'
    if snapshot['gpu_free_mib']<policy['reserve_vram_mib' if during else 'min_free_vram_mib']:return '显存正在被其他工作使用，本地辅助已暂缓。'
    if snapshot['ram_free_mib']<policy['reserve_ram_mib' if during else 'min_free_ram_mib']:return '可用内存不足，本地辅助已暂缓。'
    if snapshot['cpu_percent']>(75 if during else policy['max_cpu_percent']):return '电脑正在处理其他工作，本地辅助已暂缓。'
    if not during and snapshot['gpu_util_percent']>policy['max_gpu_util_percent']:return '显卡正在忙，本地辅助已暂缓。'
    return ''

def gpu_process_memory_mib(pid):
    """WDDM per-process dedicated GPU memory, including Vulkan (NVML may report N/A)."""
    if os.name!='nt':return 0
    from ctypes import wintypes as w
    class ValueUnion(ctypes.Union):_fields_=[('large',ctypes.c_longlong),('number',ctypes.c_double),('pointer',ctypes.c_void_p)]
    class Value(ctypes.Structure):_fields_=[('status',w.DWORD),('value',ValueUnion)]
    class Item(ctypes.Structure):_fields_=[('name',w.LPWSTR),('value',Value)]
    pdh=ctypes.WinDLL('pdh');handle=ctypes.c_void_p();counter=ctypes.c_void_p()
    pdh.PdhOpenQueryW.argtypes=[w.LPCWSTR,ctypes.c_size_t,ctypes.POINTER(ctypes.c_void_p)]
    pdh.PdhAddEnglishCounterW.argtypes=[ctypes.c_void_p,w.LPCWSTR,ctypes.c_size_t,ctypes.POINTER(ctypes.c_void_p)]
    pdh.PdhCollectQueryData.argtypes=[ctypes.c_void_p]
    pdh.PdhGetFormattedCounterArrayW.argtypes=[ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD),ctypes.POINTER(w.DWORD),ctypes.c_void_p]
    pdh.PdhCloseQuery.argtypes=[ctypes.c_void_p]
    try:
        if pdh.PdhOpenQueryW(None,0,ctypes.byref(handle)):return 0
        if pdh.PdhAddEnglishCounterW(handle,r'\GPU Process Memory(*)\Dedicated Usage',0,ctypes.byref(counter)):return 0
        for _ in range(2):
            pdh.PdhCollectQueryData(handle);size=w.DWORD();count=w.DWORD()
            pdh.PdhGetFormattedCounterArrayW(counter,0x400,ctypes.byref(size),ctypes.byref(count),None)
            if size.value:
                buffer=ctypes.create_string_buffer(size.value)
                if not pdh.PdhGetFormattedCounterArrayW(counter,0x400,ctypes.byref(size),ctypes.byref(count),buffer):
                    items=ctypes.cast(buffer,ctypes.POINTER(Item));total=sum(items[i].value.value.large for i in range(count.value)
                        if items[i].name and items[i].name.startswith(f'pid_{pid}_') and items[i].value.status in [0,1])
                    if total:return round(total/1024**2,2)
            time.sleep(.1)
        return 0
    finally:
        if handle:pdh.PdhCloseQuery(handle)

def owned_job(process):
    """Close-on-parent-exit job; private memory bounded to 4GiB. Only our child is assigned."""
    if os.name!='nt':raise RuntimeError('本地辅助目前只配置了Windows运行环境。')
    from ctypes import wintypes as w
    class Basic(ctypes.Structure):
        _fields_=[('PerProcessUserTimeLimit',ctypes.c_longlong),('PerJobUserTimeLimit',ctypes.c_longlong),('LimitFlags',w.DWORD),
            ('MinimumWorkingSetSize',ctypes.c_size_t),('MaximumWorkingSetSize',ctypes.c_size_t),('ActiveProcessLimit',w.DWORD),
            ('Affinity',ctypes.c_size_t),('PriorityClass',w.DWORD),('SchedulingClass',w.DWORD)]
    class Counters(ctypes.Structure):_fields_=[(name,ctypes.c_ulonglong) for name in ['ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount']]
    class Extended(ctypes.Structure):_fields_=[('BasicLimitInformation',Basic),('IoInfo',Counters),('ProcessMemoryLimit',ctypes.c_size_t),('JobMemoryLimit',ctypes.c_size_t),('PeakProcessMemoryUsed',ctypes.c_size_t),('PeakJobMemoryUsed',ctypes.c_size_t)]
    class CpuLimit(ctypes.Structure):_fields_=[('ControlFlags',w.DWORD),('CpuRate',w.DWORD)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR];kernel.CreateJobObjectW.restype=w.HANDLE
    kernel.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD];kernel.SetInformationJobObject.restype=w.BOOL
    kernel.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE];kernel.AssignProcessToJobObject.restype=w.BOOL
    kernel.CloseHandle.argtypes=[w.HANDLE];kernel.CloseHandle.restype=w.BOOL
    handle=kernel.CreateJobObjectW(None,None);limits=Extended();limits.BasicLimitInformation.LimitFlags=0x2000|0x100
    limits.ProcessMemoryLimit=4*1024**3
    if not handle or not kernel.SetInformationJobObject(handle,9,ctypes.byref(limits),ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(handle,int(process._handle)):
        if handle:kernel.CloseHandle(handle)
        raise RuntimeError('未能建立本地辅助进程的退出保护。')
    cpu=CpuLimit(5,max(1,min(10000,round(20000/(psutil.cpu_count() or 16)))))
    if not kernel.SetInformationJobObject(handle,15,ctypes.byref(cpu),ctypes.sizeof(cpu)):
        kernel.CloseHandle(handle);raise RuntimeError('未能建立本地辅助的CPU占用上限。')
    return lambda:kernel.CloseHandle(handle)

class LocalLearningAssistant:
    def __init__(self,root,lock,unload):
        self.root=Path(root);self.lock=lock;self.unload=unload;self.cancelled=threading.Event();self.process=None;self.last={}
        self.cache=self.root/'cache/local-learning';self.cache.mkdir(parents=True,exist_ok=True)
        self.jobs=self.root/'cache/local-learning-jobs';self.jobs.mkdir(parents=True,exist_ok=True)
    def status(self):
        policy=load_policy(self.root)
        return {'mode':policy['mode'],'prepared':Path(policy.get('model','')).is_file() and Path(policy.get('runtime','')).is_file(),
            'running':self.process is not None and self.process.poll() is None,'last':self.last,
            'model':'Qwen3-1.7B Q8_0','policy':{k:policy[k] for k in DEFAULT_POLICY}}
    def cancel(self):self.cancelled.set();return {'cancel_requested':True}
    def prune(self):
        files=sorted((p for p in self.cache.glob('*.json') if re.fullmatch('[a-f0-9]{64}.json',p.name)),key=lambda p:p.stat().st_mtime,reverse=True)
        size=0
        for i,p in enumerate(files):
            stat=p.stat();size+=stat.st_size
            if i>=64 or size>16*1024**2 or time.time()-stat.st_mtime>86400:p.unlink(missing_ok=True)
    def complete(self,messages,schema,task):
        policy=load_policy(self.root)
        if policy['mode']!='local':raise AssistantBusy('本地辅助当前未启用。')
        if not self.status()['prepared']:raise AssistantBusy('本地辅助模型尚未准备完成。')
        raw=json.dumps({'messages':messages,'schema':schema,'task':task,'model':policy.get('model_sha256'),'version':1},ensure_ascii=False,sort_keys=True)
        if len(raw)>18000:raise ValueError('这次资料过长，请缩短后分批整理。')
        fingerprint=hashlib.sha256(raw.encode()).hexdigest();cached=self.cache/(fingerprint+'.json')
        self.prune()
        if cached.exists():
            try:
                answer=json.loads(cached.read_text(encoding='utf-8'));answer['metrics']['cache_hit']=True;self.last=answer['metrics'];return answer
            except (ValueError,OSError,KeyError):cached.unlink(missing_ok=True)
        reason=busy_reason(resource_snapshot(),policy)
        if reason:raise AssistantBusy(reason)
        if not self.lock.acquire_low():raise AssistantBusy('语音或其他音频任务正在运行，本地辅助稍后再做。')
        started=time.monotonic();baseline=None;peak_gpu=0;peak_rss=0;process=None;close_job=None
        self.cancelled.clear()
        def check():
            nonlocal peak_gpu,peak_rss
            if self.cancelled.is_set() or self.lock.low_cancel.is_set():raise AssistantBusy('已让出资源，本地辅助可以稍后重试。')
            if time.monotonic()-started>policy['max_seconds']:raise AssistantBusy('本次本地辅助已达到时间上限，进程已停止。')
            snap=resource_snapshot();reason=busy_reason(snap,policy,during=True)
            if reason:raise AssistantBusy(reason)
            peak_gpu=max(peak_gpu,snap['gpu_used_mib'])
            if process:
                try:peak_rss=max(peak_rss,psutil.Process(process.pid).memory_info().rss)
                except psutil.Error:pass
            if peak_rss>3*1024**3:raise AssistantBusy('本地辅助占用达到保护阈值，已停止。')
        try:
            self.unload();baseline=resource_snapshot();reason=busy_reason(baseline,policy)
            if reason:raise AssistantBusy(reason)
            with tempfile.TemporaryDirectory(dir=self.jobs) as folder:
                folder=Path(folder);token=secrets.token_urlsafe(32);key=folder/'key';key.write_text(token,encoding='utf-8')
                with socket.socket() as reservation:reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
                args=[policy['runtime'],'--model',policy['model'],'--host','127.0.0.1','--port',str(port),'--api-key-file',str(key),
                    '--device','Vulkan0','--n-gpu-layers','99','--ctx-size',str(policy['context']),'--parallel','1',
                    '--threads',str(policy['threads']),'--threads-batch',str(policy['threads']),'--batch-size','64','--ubatch-size','64',
                    '--poll','0','--poll-batch','0','--prio','-1','--flash-attn','on','--jinja','--no-warmup','--no-context-shift',
                    '--chat-template-kwargs','{"enable_thinking":false}','--log-file',str(folder/'llama.log'),'--log-verbosity','3','--log-colors','off']
                with (folder/'worker.log').open('w',encoding='utf-8') as log:
                    process=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=log,stderr=log,cwd=str(Path(policy['runtime']).parent),
                        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)|getattr(subprocess,'BELOW_NORMAL_PRIORITY_CLASS',0))
                    self.process=process
                    try:
                        close_job=owned_job(process)
                        with httpx.Client(base_url=f'http://127.0.0.1:{port}',headers={'Authorization':'Bearer '+token},trust_env=False,timeout=2) as client:
                            while True:
                                check()
                                if process.poll() is not None:raise RuntimeError('本地辅助进程未能启动；运行环境需要检查。')
                                try:
                                    if client.get('/health').status_code==200:break
                                except httpx.HTTPError:pass
                                time.sleep(.12)
                            payload={'messages':messages,'max_tokens':640,'temperature':.3,'top_p':.9,'stream':False,
                                'response_format':{'type':'json_schema','json_schema':{'name':'learning_result','strict':True,'schema':schema}},
                                'chat_template_kwargs':{'enable_thinking':False}}
                            pool=concurrent.futures.ThreadPoolExecutor(max_workers=1)
                            future=pool.submit(client.post,'/v1/chat/completions',json=payload,timeout=policy['max_seconds'])
                            try:
                                while not future.done():
                                    check();time.sleep(.15)
                                response=future.result();response.raise_for_status();body=response.json()
                            finally:pool.shutdown(wait=False,cancel_futures=True)
                            content=body['choices'][0]['message']['content']
                            data=json.loads(content);from jsonschema import validate;validate(data,schema)
                            logs='\n'.join(p.read_text(encoding='utf-8',errors='replace') for p in [folder/'llama.log',folder/'worker.log'] if p.exists())
                            evidence=[line for line in logs.splitlines() if re.search('Vulkan|offload|GPU|buffer|tensor',line,re.I)]
                            process_gpu_mib=gpu_process_memory_mib(process.pid)
                            if process_gpu_mib<1024 and not re.search(r'offloaded\s+\d+/\d+\s+layers to GPU',logs):
                                diagnosis=self.root/'logs/local-learning-init.log';diagnosis.parent.mkdir(exist_ok=True)
                                diagnosis.write_text('\n'.join(evidence),encoding='utf-8')
                                raise RuntimeError('未确认显卡实际接管模型，本次辅助已停止。')
                    finally:
                        if process.poll() is None:process.terminate()
                        try:process.wait(timeout=1)
                        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)
                        if close_job:close_job();close_job=None
                        self.process=None
            metrics={'source':'local','model':'Qwen3-1.7B Q8_0','backend':'Vulkan / RTX5070Ti','gpu_offload_confirmed':True,
                'seconds':round(time.monotonic()-started,3),'cache_hit':False,'worker_exited':True,
                'peak_whole_gpu_mib':peak_gpu,'baseline_whole_gpu_mib':baseline['gpu_used_mib'],'peak_process_ram_mib':round(peak_rss/1024**2),
                'dedicated_process_gpu_mib':process_gpu_mib,'gpu_evidence':'Windows WDDM per-process dedicated memory' if process_gpu_mib>=1024 else 'runtime offload log'}
            answer={'data':data,'metrics':metrics};self.last=metrics
            staged=cached.with_suffix('.tmp');staged.write_text(json.dumps(answer,ensure_ascii=False),encoding='utf-8');staged.replace(cached);self.prune();return answer
        finally:
            if process is not None and process.poll() is None:process.kill();process.wait(timeout=3)
            if close_job:close_job()
            self.process=None;self.lock.release_low()

def host_client(root,path,body=None,timeout=60):
    cfg=json.loads((Path(root)/'settings.local.json').read_text(encoding='utf-8'))
    with httpx.Client(base_url=f'http://127.0.0.1:{cfg["port"]}',headers={'Authorization':'Bearer '+cfg['api_token']},trust_env=False,timeout=timeout) as client:
        response=client.get(path) if body is None else client.post(path,json=body)
        if response.status_code!=200:
            try:detail=response.json().get('detail','本地辅助暂时不可用。')
            except Exception:detail='本地辅助暂时不可用。'
            raise AssistantBusy(str(detail)[:200])
        return response.json()

def assistant_complete(root,messages,schema,task):
    mode=load_policy(root)['mode']
    if mode=='paused':raise AssistantBusy('学习辅助已暂停，可以在页面重新启用。')
    if mode=='cloud':
        import deepseek_client
        result,_=deepseek_client.complete(messages,None);raw=(result.get('content') or '').strip()
        if raw.startswith('```'):raw='\n'.join(raw.splitlines()[1:-1])
        data=json.loads(raw);from jsonschema import validate;validate(data,schema)
        return {'data':data,'metrics':{'source':'cloud','model':'DeepSeek','cache_hit':False}}
    return host_client(root,'/v1/learning/assist',{'messages':messages,'schema':schema,'task':task})

def register_local_assistant_host(app,*,authorize,root,lock,unload):
    from fastapi import Depends,HTTPException
    from pydantic import BaseModel,Field
    class Request(BaseModel):
        messages:list[dict]=Field(max_length=4,min_length=1)
        schema_data:dict=Field(alias='schema')
        task:str=Field(max_length=30)
    assistant=LocalLearningAssistant(root,lock,unload)
    @app.get('/v1/learning/status',dependencies=[Depends(authorize)])
    def status():return {**assistant.status(),'resources':resource_snapshot()}
    @app.post('/v1/learning/cancel',dependencies=[Depends(authorize)])
    def cancel():return assistant.cancel()
    @app.post('/v1/learning/assist',dependencies=[Depends(authorize)])
    def complete(request:Request):
        try:return assistant.complete(request.messages,request.schema_data,request.task)
        except (AssistantBusy,ValueError,RuntimeError) as exc:raise HTTPException(503,str(exc)[:200]) from None
    return assistant
