"""Loopback, same-origin browser sessions. Never expose the long-lived API key."""
from pathlib import Path
import ipaddress,json,re,secrets,threading,time
from fastapi import HTTPException,Request
from fastapi.responses import FileResponse,JSONResponse,Response

COOKIE='whale_local_session'
MARKER='whale-local-ui-v1'

def local_audio_file(root,kind,name):
    root=Path(root).resolve()
    if kind=='artifacts' and re.fullmatch('[a-f0-9]{32}',name):
        path=root/'artifacts'/(name+'.wav')
    elif kind=='samples' and name in {'dania-daily.wav','dania-playful.wav','dania-caring.wav','dania-playful-smooth.wav','dania-playful-normal.wav'}:
        path=root/'samples'/name
    else:raise HTTPException(404,'Audio artifact not found')
    if not path.is_file() or path.is_symlink() or path.resolve()!=path.absolute() or path.stat().st_nlink!=1:
        raise HTTPException(404,'Audio artifact not found')
    return path

class LocalSessions:
    def __init__(self,api_token,port=18766,clock=time.time,root=None):
        self.api_token=api_token;self.clock=clock;self.lock=threading.Lock();self.sessions={}
        self.root=Path(root) if root is not None else None
        self.cookie=COOKIE if port==18766 else COOKIE+'_'+str(port)
        self.origins={f'http://127.0.0.1:{port}',f'http://localhost:{port}',f'http://[::1]:{port}'}
    def local(self,request):
        try:loopback=bool(request.client and ipaddress.ip_address(request.client.host).is_loopback)
        except ValueError:loopback=False
        base=str(request.base_url).rstrip('/')
        return loopback and base in self.origins and request.headers.get('sec-fetch-site','same-origin') in {'same-origin','none'} and request.headers.get('origin',base)==base
    def create(self,request):
        if not self.local(request) or request.headers.get('x-local-client')!=MARKER:raise HTTPException(403,'请从本机管理页面连接。')
        if self.bootstrap_required() and not self.bootstrap_valid(request):raise HTTPException(401,'请从桌面的一键启动入口打开本机页面。')
        now=self.clock();sid=secrets.token_urlsafe(36)
        with self.lock:
            self.sessions={k:v for k,v in self.sessions.items() if v>now}
            if len(self.sessions)>128:self.sessions.pop(next(iter(self.sessions)))
            self.sessions[sid]=now+12*3600
        response=JSONResponse({'connected':True,'expires_in':12*3600})
        response.set_cookie(self.cookie,sid,max_age=12*3600,httponly=True,samesite='strict',path='/')
        response.headers['Cache-Control']='no-store';return response
    def authorize(self,request,authorization=''):
        if secrets.compare_digest(authorization,'Bearer '+self.api_token):return
        safe=request.method in {'GET','HEAD'} or request.headers.get('x-local-client')==MARKER
        if self.bootstrap_required():
            if safe and self.local(request) and self.bootstrap_valid(request):return
            raise HTTPException(401,'请从桌面的一键启动入口重新连接。')
        sid=request.cookies.get(self.cookie,'')
        with self.lock:valid=bool(self.sessions.get(sid,0)>self.clock())
        if valid and safe and self.local(request):return
        raise HTTPException(401,'本机连接已过期，刷新页面即可自动重连。')
    def bootstrap_required(self):
        return bool(self.root and (self.root/'data/setup/installation.local.json').is_file())
    def bootstrap_valid(self,request):
        try:
            state=json.loads((self.root/'data/setup/browser-session.local.json').read_text(encoding='utf-8'))
            expected=state.get('cookie','')
            return bool(expected and state.get('expires',0)>self.clock() and secrets.compare_digest(
                expected,request.cookies.get('whale_setup_session','')))
        except (OSError,ValueError,TypeError):return False

def register_local_sessions(app,root,api_token,port=18766):
    sessions=LocalSessions(api_token,port,root=root)
    @app.post('/local/session')
    def start(request:Request):return sessions.create(request)
    @app.get('/local/session.js')
    def script():return Response((Path(root)/'local_session.js').read_text(encoding='utf-8'),media_type='application/javascript',headers={'Cache-Control':'no-store'})
    @app.get('/local/media/{kind}/{name}')
    def local_audio(kind:str,name:str,request:Request):
        sessions.authorize(request,request.headers.get('authorization',''))
        return FileResponse(local_audio_file(root,kind,name),media_type='audio/wav',headers={'Cache-Control':'no-store'})
    @app.middleware('http')
    async def local_pages(request,call_next):
        response=await call_next(request)
        if request.method=='GET' and response.headers.get('content-type','').startswith('text/html'):
            body=b''.join([chunk async for chunk in response.body_iterator]);text=body.decode('utf-8')
            tag='<script src="/local/session.js" defer></script>'
            text=text.replace('</html>',tag+'</html>') if '</html>' in text else text+tag
            headers=dict(response.headers);headers.pop('content-length',None);headers.update({'Cache-Control':'no-store','X-Frame-Options':'DENY','Referrer-Policy':'same-origin'})
            return Response(text,status_code=response.status_code,headers=headers)
        return response
    return sessions
