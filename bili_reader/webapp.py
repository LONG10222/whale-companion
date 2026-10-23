"""Loopback-only Bilibili workbench. No pet and no social write endpoints."""
from __future__ import annotations
import argparse, asyncio, hmac, io, json, os, secrets, threading, time
from contextlib import asynccontextmanager
from pathlib import Path
import qrcode
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from .common import Deadline, Cancelled, atomic_json, clean_text, pick_bvid, read_json, stamp
from .bilibili import Bilibili, BiliError
from .credentials import SessionStore
from .patrol import Patrol, DeepSeekJudge

ASSETS=Path(__file__).resolve().parent/'web'

class Workbench:
    def __init__(self,home,bot_root=None):
        self.home=Path(home);self.bot_root=bot_root;self.lock=threading.RLock()
        self.login={'state':'idle','message':'点击生成二维码，用手机 B 站扫码。','logged_in':False,'name':''}
        self.png=None;self.login_cancel=threading.Event();self.generation=0;self.issued_at=0
        self.work={'state':'idle','message':'还未开始浏览','result':None}
        self.work_cancel=threading.Event();self.patrol=Patrol(home,bot_root)

    def state(self):
        with self.lock:
            return {'login':dict(self.login),'has_qr':self.png is not None,
                'qr_seconds_left':max(0,int(180-(time.monotonic()-self.issued_at))) if self.png else 0,
                'work':dict(self.work),'api_ready':DeepSeekJudge(self.bot_root).ready(),
                'limits':{'candidates':5,'reviews_per_round':3,'reviews_per_day':6,'timeout_seconds':180},
                'desktop_pet_enabled':False,'social_write_enabled':False}

    def set_login(self,generation,**value):
        with self.lock:
            if generation==self.generation:self.login.update(value)

    def check_saved(self):
        if not SessionStore(self.home).path.exists():return
        with self.lock:
            generation=self.generation
            self.login.update(state='checking',message='正在核对本地保存的登录…')
        def worker():
            try:
                with Bilibili(self.home,deadline=Deadline(25)) as client:info=client.login_status()
                with self.lock:
                    if generation!=self.generation:return
                    self.login.update(state='logged_in' if info['logged_in'] else 'expired',
                        message='本地登录仍有效，可以开始浏览。' if info['logged_in'] else '本地登录已失效，请重新扫码。',
                        logged_in=info['logged_in'],name=info['name'] if info['logged_in'] else '')
            except Exception as exc:
                self.set_login(generation,state='error',message=clean_text(exc,200))
        threading.Thread(target=worker,daemon=True,name='bili-saved-login').start()

    def start_login(self,force=False):
        with self.lock:
            if self.login['logged_in'] and not force:return
            if self.login['state'] in {'generating','waiting_scan','waiting_confirm'} and not force:return
            self.login_cancel.set();self.login_cancel=threading.Event();cancel=self.login_cancel
            self.generation+=1;generation=self.generation;self.png=None
            self.login.update(state='generating',message='正在生成 B 站官方二维码…',logged_in=False,name='')
        def worker():
            try:
                with Bilibili(self.home,deadline=Deadline(180,cancel),max_requests=65,load_session=False) as client:
                    qr=client.start_login();output=io.BytesIO();qrcode.make(qr['url']).save(output,format='PNG')
                    with self.lock:
                        if generation!=self.generation:return
                        self.png=output.getvalue();self.issued_at=time.monotonic()
                    self.set_login(generation,state='waiting_scan',message='请用手机 B 站 App 扫码，再在手机上点确认。')
                    while not cancel.wait(3):
                        result=client.poll_login(save=False);self.set_login(generation,state=result['state'],message=result['message'])
                        if result['state']=='logged_in':
                            with self.lock:
                                if generation!=self.generation or cancel.is_set():return
                                SessionStore(self.home).save(result.pop('_cookies'))
                            # Verify the stored encrypted session, not just the QR poll's code.
                            with Bilibili(self.home,deadline=Deadline(25)) as saved:info=saved.login_status()
                            if not info['logged_in']:raise BiliError('手机扫码结束，但 B 站未确认网页登录有效，请重新扫码。')
                            self.set_login(generation,state='logged_in',logged_in=True,name=info['name'],message='已确认登录有效，登录信息已加密保存在本机。')
                            with self.lock:
                                if generation==self.generation:self.png=None
                            return
                        if result['state']=='expired':return
            except Cancelled:
                self.set_login(generation,state='expired',message='二维码已到期，请点“刷新二维码”。')
            except Exception as exc:
                self.set_login(generation,state='error',message=clean_text(exc,200))
        threading.Thread(target=worker,daemon=True,name='bili-web-qr').start()

    def browse(self,keyword,bvid=None):
        keyword=clean_text(keyword,80)
        if not keyword:raise ValueError('先填一个你想让鲸娅关注的话题。')
        if bvid:bvid=pick_bvid(bvid)
        if not DeepSeekJudge(self.bot_root).ready():raise ValueError('请先在鲸娅现有页面保存 DeepSeek API Key。')
        with self.lock:
            if self.work['state']=='running':raise ValueError('已有一轮正在读取，可以等待或停止它。')
            self.work_cancel=threading.Event();cancel=self.work_cancel
            self.work={'state':'running','message':'开始查找公开视频…','result':None}
        def notify(text):
            with self.lock:self.work['message']=text
        def worker():
            try:result=self.patrol.run(keyword,bvid=bvid,cancel=cancel,notify=notify)
            except Exception as exc:result={'state':'failed','notice':clean_text(exc,200),'items':[]}
            # Never return arbitrary filesystem paths to the browser.
            result.pop('report',None)
            with self.lock:self.work={'state':result['state'],'message':result.get('notice') or '本轮结束，结果如下。','result':result}
        threading.Thread(target=worker,daemon=True,name='bili-web-read').start()

    def close(self):
        self.login_cancel.set();self.work_cancel.set()

def create_app(home,bot_root=None,*,token=None,port=18768):
    home=Path(home).resolve();home.mkdir(parents=True,exist_ok=True)
    token=token or secrets.token_urlsafe(32);manager=Workbench(home,bot_root)
    @asynccontextmanager
    async def lifespan(app):
        manager.check_saved()
        yield
        manager.close()
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    app.state.manager=manager;app.state.token=token
    allowed_hosts={f'127.0.0.1:{port}',f'localhost:{port}'}
    @app.middleware('http')
    async def guard(request,call_next):
        host=request.headers.get('host','')
        if host not in allowed_hosts:return JSONResponse({'error':'仅允许本机地址'},status_code=403)
        origin=request.headers.get('origin')
        if origin and origin not in {f'http://{x}' for x in allowed_hosts}:
            return JSONResponse({'error':'不允许跨站请求'},status_code=403)
        if request.headers.get('sec-fetch-site')=='cross-site':return JSONResponse({'error':'不允许跨站请求'},status_code=403)
        if request.url.path.startswith('/api/'):
            supplied=request.headers.get('authorization','')
            if not hmac.compare_digest(supplied,'Bearer '+token):
                return JSONResponse({'error':'请从本机启动入口重新打开 B 站页面'},status_code=401)
        response=await call_next(request)
        response.headers.update({'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff',
            'Content-Security-Policy':"default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"})
        return response

    @app.get('/')
    def index():return HTMLResponse((ASSETS/'index.html').read_text(encoding='utf-8'))
    @app.get('/app.js')
    def js():return Response((ASSETS/'app.js').read_text(encoding='utf-8'),media_type='text/javascript')
    @app.get('/style.css')
    def css():return Response((ASSETS/'style.css').read_text(encoding='utf-8'),media_type='text/css')
    @app.get('/health')
    def health():return {'ready':True,'app':'whale-bilibili','pet':False}
    @app.get('/api/state')
    def state():return manager.state()
    @app.post('/api/login/start')
    async def start(request:Request):
        try:body=await request.json()
        except ValueError:body={}
        manager.start_login(force=body.get('refresh') is True)
        return {'accepted':True}
    @app.get('/api/login/qr')
    def qr():
        with manager.lock:png=manager.png
        if not png:return Response(status_code=404)
        return Response(png,media_type='image/png')
    @app.post('/api/browse')
    async def browse(request:Request):
        try:
            raw=await request.body()
            if len(raw)>2048:return JSONResponse({'error':'请求过长'},status_code=413)
            body=json.loads(raw);manager.browse(body.get('keyword'),body.get('video') or None)
            return {'accepted':True}
        except (ValueError,TypeError,AttributeError) as exc:return JSONResponse({'error':str(exc)[:200]},status_code=400)
    @app.post('/api/browse/cancel')
    def cancel():manager.work_cancel.set();return {'stopping':True}
    return app

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--home',required=True);parser.add_argument('--bot-root');parser.add_argument('--port',type=int,default=18768)
    args=parser.parse_args();home=Path(args.home).resolve()
    config=read_json(home/'config.local.json',{}) or {};bot_root=args.bot_root or config.get('bot_root')
    app=create_app(home,bot_root,port=args.port)
    atomic_json(home/'data/web-launch.private.json',{'pid':os.getpid(),'at':stamp(),'port':args.port,
        'url':f'http://127.0.0.1:{args.port}/#token='+app.state.token,'pet':False})
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=args.port,access_log=False,log_level='warning')

if __name__=='__main__':main()
