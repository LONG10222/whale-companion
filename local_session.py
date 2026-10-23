"""Loopback, same-origin browser sessions. Never expose the owner-lived API key."""
from pathlib import Path
import hashlib,ipaddress,secrets,threading,time
from fastapi import HTTPException,Request
from fastapi.responses import JSONResponse,Response

COOKIE='whale_local_session'
MARKER='whale-local-ui-v1'

class LocalSessions:
    def __init__(self,api_token,port=18766,clock=time.time):
        self.api_token=api_token;self.clock=clock;self.lock=threading.Lock();self.sessions={}
        self.origins={f'http://127.0.0.1:{port}',f'http://localhost:{port}',f'http://[::1]:{port}'}
    def local(self,request):
        try:loopback=bool(request.client and ipaddress.ip_address(request.client.host).is_loopback)
        except ValueError:loopback=False
        base=str(request.base_url).rstrip('/')
        return loopback and base in self.origins and request.headers.get('sec-fetch-site','same-origin') in {'same-origin','none'} and request.headers.get('origin',base)==base
    def create(self,request):
        if not self.local(request) or request.headers.get('x-local-client')!=MARKER:raise HTTPException(403,'请从本机管理页面连接。')
        now=self.clock();sid=secrets.token_urlsafe(36)
        with self.lock:
            self.sessions={k:v for k,v in self.sessions.items() if v>now}
            if len(self.sessions)>128:self.sessions.pop(next(iter(self.sessions)))
            self.sessions[sid]=now+12*3600
        response=JSONResponse({'connected':True,'expires_in':12*3600})
        response.set_cookie(COOKIE,sid,max_age=12*3600,httponly=True,samesite='strict',path='/')
        response.headers['Cache-Control']='no-store';return response
    def authorize(self,request,authorization=''):
        if secrets.compare_digest(authorization,'Bearer '+self.api_token):return 'bearer:'+hashlib.sha256(authorization.encode()).hexdigest()
        sid=request.cookies.get(COOKIE,'')
        with self.lock:valid=bool(self.sessions.get(sid,0)>self.clock())
        safe=request.method in {'GET','HEAD'} or request.headers.get('x-local-client')==MARKER
        if valid and safe and self.local(request):return 'session:'+hashlib.sha256(sid.encode()).hexdigest()
        raise HTTPException(401,'本机连接已过期，刷新页面即可自动重连。')

def register_local_sessions(app,root,api_token,port=18766):
    sessions=LocalSessions(api_token,port)
    @app.post('/local/session')
    def start(request:Request):return sessions.create(request)
    @app.get('/local/session.js')
    def script():return Response((Path(root)/'local_session.js').read_text(encoding='utf-8'),media_type='application/javascript',headers={'Cache-Control':'no-store'})
    @app.middleware('http')
    async def local_pages(request,call_next):
        response=await call_next(request)
        if request.method=='GET' and response.headers.get('content-type','').startswith('text/html'):
            body=b''.join([chunk async for chunk in response.body_iterator]);text=body.decode('utf-8')
            tag='<script src="/local/session.js" defer></script>'
            # Social confirmations must never use the generic 401 retry wrapper.
            if request.url.path != '/qq/social':
                text=text.replace('</html>',tag+'</html>') if '</html>' in text else text+tag
            headers=dict(response.headers);headers.pop('content-length',None);headers.update({'Cache-Control':'no-store','X-Frame-Options':'DENY','Referrer-Policy':'same-origin'})
            return Response(text,status_code=response.status_code,headers=headers)
        return response
    return sessions
