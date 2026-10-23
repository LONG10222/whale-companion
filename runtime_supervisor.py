"""Own only this installation's services, independently of the launcher's lifetime.

Never log in, refresh a QR code, send QQ messages, or restart live QQ for being
offline. Task Scheduler starts this process under the current interactive user.
"""
from __future__ import annotations
import argparse, ctypes, hashlib, json, logging, os, socket, sqlite3, subprocess, time
from dataclasses import dataclass, field
from contextlib import closing
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.request import Request, ProxyHandler, build_opener
import psutil

def stamp():
    return datetime.now(timezone.utc).isoformat()

def atomic_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)

def read_json(path, default=None):
    try: return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError: return default

def path_equal(a, b):
    return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))

def within(path, parent):
    try: return Path(path).resolve().is_relative_to(Path(parent).resolve())
    except (OSError, ValueError): return False

def job_info():
    # Membership alone does not prove a lifetime bug. Log it only as a diagnostic.
    if os.name != 'nt': return {'in_job': False}
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.IsProcessInJob.argtypes = [w.HANDLE, w.HANDLE, ctypes.POINTER(w.BOOL)]
    result = w.BOOL()
    ok = kernel.IsProcessInJob(kernel.GetCurrentProcess(), None, ctypes.byref(result))
    info={'in_job': bool(result.value) if ok else None}
    class Basic(ctypes.Structure):
        _fields_=[('process_time',ctypes.c_longlong),('job_time',ctypes.c_longlong),('flags',w.DWORD),
                  ('min_ws',ctypes.c_size_t),('max_ws',ctypes.c_size_t),('active_limit',w.DWORD),
                  ('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
    basic=Basic()
    if result.value and kernel.QueryInformationJobObject(None,2,ctypes.byref(basic),ctypes.sizeof(basic),None):
        info.update(limit_flags=basic.flags,kill_on_close=bool(basic.flags&0x2000),
                    breakaway_allowed=bool(basic.flags&0x800),silent_breakaway=bool(basic.flags&0x1000))
    return info

class InstanceLock:
    def __init__(self, root): self.root=Path(root); self.handle=None
    def acquire(self):
        if os.name != 'nt': raise RuntimeError('Production supervisor requires Windows')
        from ctypes import wintypes as w
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateMutexW.argtypes=[w.LPVOID,w.BOOL,w.LPCWSTR]
        self.kernel.CreateMutexW.restype=w.HANDLE
        self.kernel.CloseHandle.argtypes=[w.HANDLE]
        key=hashlib.sha256(os.path.normcase(str(self.root.resolve())).encode()).hexdigest()[:12]
        self.handle=self.kernel.CreateMutexW(None,False,'Local\\WhaleRuntime-'+key)
        if not self.handle: raise ctypes.WinError(ctypes.get_last_error())
        return ctypes.get_last_error()!=183
    def close(self):
        if self.handle: self.kernel.CloseHandle(self.handle); self.handle=None

@dataclass
class RetryBudget:
    attempts: list=field(default_factory=list)
    next_at: float=0
    failures: int=0
    def wait(self, now):
        self.attempts=[t for t in self.attempts if now-t<900]
        quota=self.attempts[0]+900-now if len(self.attempts)>=3 else 0
        return max(0,self.next_at-now,quota)
    def started(self, now):
        self.attempts.append(now)
        self.next_at=now+min(300,10*2**min(self.failures,5)); self.failures+=1

@dataclass
class Service:
    name: str
    argv: list
    cwd: Path
    port: int | None=None
    client_root: Path | None=None
    env: dict | None=None
    child: object=None
    started_at: float=0
    healthy_since: float=0
    retry: RetryBudget=field(default_factory=RetryBudget)
    last_state: str=''
    previous_pids: set=field(default_factory=set)
    def owns(self, proc):
        try:
            if self.client_root:
                if Path(self.argv[0]).name.lower()=='node.exe':
                    args=proc.cmdline()
                    return (path_equal(proc.exe(),self.argv[0]) and path_equal(proc.cwd(),self.cwd)
                            and len(args)>=2 and len(self.argv)>=2 and path_equal(args[1],self.argv[1]))
                return proc.name().lower() in {'qq.exe','napcatwinbootmain.exe'} and within(proc.exe(),self.client_root)
            if not path_equal(proc.cwd(),self.cwd): return False
            args=proc.cmdline()
            if not args or Path(proc.exe()).name.lower() not in {'python.exe','pythonw.exe'}: return False
            if self.name=='voice':
                return any(path_equal(a,self.cwd/'voice_server.py') for a in args[1:] if not a.startswith('-'))
            return (any(args[i:i+3]==['-m','uvicorn','phone_server:app'] for i in range(len(args)-2))
                    and '--port' in args and args[args.index('--port')+1]==str(self.port))
        except (psutil.Error,OSError,ValueError,IndexError): return False

def make_services(root):
    root=Path(root).resolve(); settings=read_json(root/'settings.local.json')
    if not settings or not settings.get('api_token'): raise ValueError('Missing local settings')
    python=Path(settings['python'])
    if not python.is_absolute(): python=root/python
    if not python.is_file(): raise ValueError('Missing configured Python')
    services=[
        Service('voice',[str(python),'-X','utf8',str(root/'voice_server.py'),'--config',str(root/'settings.local.json')],root,18765),
        Service('companion',[str(python),'-X','utf8','-m','uvicorn','phone_server:app','--host','127.0.0.1','--port','18766','--no-access-log'],root,18766)]
    client=read_json(root/'configs/qq-client.local.json')
    if client:
        folder=Path(client['client_root']).resolve(); boot=Path(client['boot']).resolve()
        if not boot.is_file() or not within(boot,folder): raise ValueError('Invalid dedicated QQ client path')
        env=dict(os.environ,DEEPSEEK_QQ_DATA_DIR=str(Path(client['qq_data']).resolve()))
        argv=[str(boot)]
        if client.get('mode')=='portable-node':
            argv.append(str(root/'scripts/portable_qq.cjs'))
            env.update(WHALE_INSTALL_ROOT=str(root),WHALE_QQ_CLIENT=str(folder),WHALE_QQ_DATA=str(Path(client['qq_data']).resolve()))
        services.append(Service('napcat',argv,folder,client_root=folder,env=env))
    return settings,services

def active_work(root):
    checks=[('qq-memory.sqlite3','events',('accepted','sending')),
            ('creative.sqlite3','jobs',('accepted','running')),
            ('tasks.sqlite3','tasks',('accepted','running'))]
    total=0
    for filename,table,states in checks:
        path=Path(root)/'data/qq'/filename
        if not path.exists(): continue
        try:
            with closing(sqlite3.connect(f'file:{path.as_posix()}?mode=ro',uri=True,timeout=2)) as db:
                total+=db.execute(f'SELECT count(*) FROM {table} WHERE state IN (?,?)',states).fetchone()[0]
        except sqlite3.Error: return None
    return total

class Supervisor:
    def __init__(self,root,*,clock=time.monotonic,processes=None,opener=None):
        self.root=Path(root).resolve(); self.settings,self.services=make_services(self.root)
        self.clock=clock; self.processes=processes or (lambda:list(psutil.process_iter()))
        self.opener=opener or build_opener(ProxyHandler({}))
        self.directory=self.root/'logs/runtime'; self.directory.mkdir(parents=True,exist_ok=True)
        self.state_path=self.directory/'status.json'; self.control_path=self.root/'configs/runtime-control.local.json'
        self.log=logging.getLogger('whale-runtime-'+str(os.getpid())); self.log.setLevel(logging.INFO); self.log.propagate=False
        handler=RotatingFileHandler(self.directory/'events.jsonl',maxBytes=2*1024*1024,backupCount=4,encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(message)s')); self.log.addHandler(handler)
        self.started=stamp(); self.last_control_id=None
        self.retry_path=self.directory/'retry-state.json'
        saved=read_json(self.retry_path,{})
        if saved.get('boot_at')==psutil.boot_time():
            for service in self.services:
                state=saved.get('services',{}).get(service.name,{})
                service.retry=RetryBudget(state.get('attempts',[]),state.get('next_at',0),state.get('failures',0))
    def event(self,event,**values):
        self.log.info(json.dumps({'at':stamp(),'event':event,**values},ensure_ascii=False))
    def probe(self,service):
        try:
            with socket.create_connection(('127.0.0.1',service.port),timeout=.5): pass
        except OSError: return 'missing'
        endpoint='/health' if service.name=='voice' else '/qq/status'
        req=Request(f'http://127.0.0.1:{service.port}{endpoint}',headers={'Authorization':'Bearer '+self.settings['api_token']})
        try:
            with self.opener.open(req,timeout=3) as response: data=json.loads(response.read(256*1024))
            if service.name=='voice': return 'ready' if data.get('status')=='ok' and 'worker_loaded' in data else 'unhealthy'
            return 'ready' if 'allowed_private_ids' in data and 'groups_enabled' in data else 'unhealthy'
        except Exception: return 'unhealthy'
    def listeners(self):
        return {c.laddr.port:c.pid for c in psutil.net_connections(kind='tcp')
                if c.status==psutil.CONN_LISTEN and c.laddr and c.laddr.port in {18765,18766}}
    def spawn(self,service):
        folder=self.root/'logs/qq'; folder.mkdir(parents=True,exist_ok=True); handles=[]
        try:
            for suffix in ('out','err'):
                path=folder/f'{service.name}.{suffix}.log'
                if path.exists() and path.stat().st_size>4*1024*1024:
                    for i in range(3,0,-1):
                        src=path if i==1 else Path(str(path)+f'.{i-1}')
                        if src.exists(): os.replace(src,Path(str(path)+f'.{i}'))
                handles.append(path.open('ab',buffering=0))
            flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
            # Use Windows' documented breakaway only when the parent job permits it.
            if job_info().get('breakaway_allowed'): flags|=getattr(subprocess,'CREATE_BREAKAWAY_FROM_JOB',0)
            service.child=subprocess.Popen(service.argv,cwd=service.cwd,env=service.env,stdin=subprocess.DEVNULL,
                stdout=handles[0],stderr=handles[1],close_fds=True,creationflags=flags)
            self.event('process_started',service=service.name,pid=service.child.pid)
        finally:
            for handle in handles: handle.close()
    def tick(self):
        now=self.clock(); processes=self.processes(); listeners=self.listeners(); rows={}
        for service in self.services:
            matches=[p for p in processes if service.owns(p)]; pids={p.pid for p in matches}
            if service.child and service.child.poll() is not None:
                self.event('launcher_exited',service=service.name,exit_code=service.child.returncode); service.child=None
            if service.previous_pids and not pids:
                self.event('processes_disappeared',service=service.name,pids=sorted(service.previous_pids))
            service.previous_pids=pids; state='running' if pids else 'missing'; health=None; wait=0
            if service.port:
                if service.port in listeners and listeners[service.port] not in pids: state='port_conflict'
                elif pids:
                    health=self.probe(service)
                    state='ready' if health=='ready' else 'starting' if now-service.started_at<90 else 'unhealthy'
            if pids:
                if not service.healthy_since: service.healthy_since=now
                if now-service.healthy_since>=300: service.retry.failures=0
            else:
                service.healthy_since=0
                # Boot can exit while QQ's own child is still starting.
                grace=service.started_at and now-service.started_at<60
                if state!='port_conflict':
                    wait=max(service.retry.wait(now),60-(now-service.started_at) if grace else 0)
                    if wait>0: state='restart_backoff'
                    else:
                        service.retry.started(now); service.started_at=now
                        atomic_json(self.retry_path,{'boot_at':psutil.boot_time(),'services':{
                            s.name:{'attempts':s.retry.attempts,'next_at':s.retry.next_at,'failures':s.retry.failures}
                            for s in self.services}})
                        try: self.spawn(service); state='starting'
                        except Exception as exc:
                            state='start_failed'; self.event('start_failed',service=service.name,error_type=type(exc).__name__)
            # Offline QQ and failed health probes never terminate living processes.
            if state!=service.last_state:
                self.event('state_changed',service=service.name,state=state); service.last_state=state
            rows[service.name]={'state':state,'health':health,'pids':sorted(pids),'retry_in_seconds':round(wait),
                                'starts_in_15_minutes':len(service.retry.attempts)}
        status={'version':1,'state':'running','pid':os.getpid(),'started_at':self.started,'checked_at':stamp(),
                'job':job_info(),'services':rows}; atomic_json(self.state_path,status); return status
    def stop_services(self):
        busy=active_work(self.root)
        if busy is None or busy>0: return False
        matches=[p for p in self.processes() if any(s.owns(p) for s in self.services)]
        for proc in reversed(matches):
            try:
                if any(s.owns(proc) for s in self.services): proc.terminate()
            except psutil.NoSuchProcess: pass
            except psutil.Error:
                self.event('stop_failed',reason='process_access_unavailable'); return False
        _,alive=psutil.wait_procs(matches,timeout=10); return not alive
    def run(self):
        self.event('supervisor_started',pid=os.getpid(),job=job_info())
        while True:
            control=read_json(self.control_path,{})
            if control.get('desired')=='stopped':
                if control.get('stop_services') and not self.stop_services():
                    if self.last_control_id!=control.get('id'):
                        self.event('stop_deferred',reason='finish_current_work_first'); self.last_control_id=control.get('id')
                    time.sleep(5); continue
                else:
                    atomic_json(self.state_path,{'version':1,'state':'stopped','pid':os.getpid(),'checked_at':stamp(),
                                'services_stopped':bool(control.get('stop_services'))})
                    self.event('supervisor_stopped',services_stopped=bool(control.get('stop_services'))); return
            self.tick(); time.sleep(5)

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parent)
    parser.add_argument('--status',action='store_true'); parser.add_argument('--stop',action='store_true')
    parser.add_argument('--stop-services',action='store_true'); args=parser.parse_args(); root=args.root.resolve()
    if args.status:
        print(json.dumps(read_json(root/'logs/runtime/status.json',{'state':'not_started'}),ensure_ascii=False)); return
    if args.stop:
        atomic_json(root/'configs/runtime-control.local.json',{'desired':'stopped','stop_services':args.stop_services,
                                                           'id':time.time_ns(),'at':stamp()}); return
    lock=InstanceLock(root)
    try:
        if not lock.acquire(): return
        supervisor=Supervisor(root)
        try: supervisor.run()
        except Exception as exc:
            supervisor.event('supervisor_failed',error_type=type(exc).__name__); raise
    finally: lock.close()

if __name__=='__main__': main()
