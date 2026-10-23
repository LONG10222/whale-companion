"""Independent loopback phone test service; keeps the voice service running."""
from contextlib import asynccontextmanager
from pathlib import Path
import json, secrets, os
from fastapi import FastAPI, Header, HTTPException, Depends,Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field
import uvicorn
from mobile_routes import register_mobile_routes

ROOT = Path(__file__).resolve().parent
SAFE_DEPLOYMENT = os.environ.get('WHALE_SAFE_START') == '1'
CONFIG = json.loads((ROOT/'settings.local.json').read_text(encoding='utf-8'))

@asynccontextmanager
async def lifespan(app):
    if not SAFE_DEPLOYMENT:
        QQ_SERVICE.start()
        QQ_SERVICE.login_manager.start()
        PROACTIVE_SERVICE.start()
        LIFE_SERVICE.start()
        BALANCE_SERVICE.start()
    yield
    QQ_SERVICE.login_manager.close()
    LIFE_SERVICE.close()
    BALANCE_SERVICE.close()
    CREATIVE_SERVICE.close()
    QQ_TASKS.close()
    PROACTIVE_SERVICE.close()
    QQ_SERVICE.close()
    MOBILE_SERVICE.cancel()
    MOBILE_SERVICE.exit.set()

app = FastAPI(title='深小希手机助手', lifespan=lifespan)
from local_session import register_local_sessions
LOCAL_SESSIONS=register_local_sessions(app,ROOT,CONFIG['api_token'])

def authorize(request:Request,authorization: str = Header(default='')):
    return LOCAL_SESSIONS.authorize(request,authorization)

@app.exception_handler(RequestValidationError)
async def invalid_request(request, exc):
    if request.url.path == '/agent/config':
        return JSONResponse(status_code=422, content={'detail':'DeepSeek Key 格式或长度不正确，请填写完整 Key 后保存。'})
    return JSONResponse(status_code=422, content={'detail':'输入格式不正确，请检查后重试。'})

@app.get('/', response_class=HTMLResponse)
def index():
    return (ROOT/'mobile.html').read_text(encoding='utf-8')

class KeyRequest(BaseModel):
    api_key: str = Field(min_length=24,max_length=200)

@app.post('/agent/config', dependencies=[Depends(authorize)])
def configure(request: KeyRequest):
    if not request.api_key.startswith('sk-'):
        raise HTTPException(400,'请填写有效的 DeepSeek API Key。')
    file = ROOT/'agent.local.json'
    config = json.loads(file.read_text(encoding='utf-8')) if file.exists() else {'model':'deepseek-flash'}
    config['api_key'] = request.api_key
    file.write_text(json.dumps(config,indent=2),encoding='utf-8')
    return {'saved':True,'key_verified_with_provider':False}

MOBILE_SERVICE = register_mobile_routes(app,authorize=authorize,root=ROOT)

from sticker_routes import register_sticker_routes
STICKER_LIBRARY = register_sticker_routes(app,root=ROOT)

from qq_routes import register_qq_routes
QQ_SERVICE=register_qq_routes(app,authorize=authorize,root=ROOT)

from qq_agent_routes import register_qq_agent_routes
QQ_TASKS=register_qq_agent_routes(app,authorize=authorize,root=ROOT,bridge=QQ_SERVICE,mobile=MOBILE_SERVICE)

from persona_routes import register_persona_routes
PERSONA_STORE=register_persona_routes(app,authorize=authorize,root=ROOT)

from learning_routes import register_learning_routes
LEARNING_STORE=register_learning_routes(app,authorize=authorize,root=ROOT)

from proactive_routes import register_proactive_routes
PROACTIVE_SERVICE=register_proactive_routes(app,authorize=authorize,root=ROOT,bridge=QQ_SERVICE)

from life_routes import register_life_routes
LIFE_SERVICE=register_life_routes(app,authorize=authorize,root=ROOT,bridge=QQ_SERVICE,proactive=PROACTIVE_SERVICE)

from qq_switch_routes import register_account_switch
ACCOUNT_SWITCH=register_account_switch(app,authorize=authorize,root=ROOT,bridge=QQ_SERVICE,proactive=PROACTIVE_SERVICE)

from creative_routes import register_creative_routes
CREATIVE_SERVICE=register_creative_routes(app,authorize=authorize,root=ROOT,bridge=QQ_SERVICE)

from balance_routes import register_balance_routes
BALANCE_SERVICE=register_balance_routes(app,authorize=authorize,root=ROOT,bridge=QQ_SERVICE)

if __name__ == '__main__':
    # The standard dev reload applies local adapter corrections without
    # interrupting the voice service or issuing process-kill commands.
    uvicorn.run('phone_server:app',host='127.0.0.1',port=18766,reload=True,
                reload_dirs=[str(ROOT)],access_log=False)

# Safe deployment is opt-in for the new launcher; legacy startup remains unchanged.
if SAFE_DEPLOYMENT:
    from deployment_mode import register_deployment_mode
    DEPLOYMENT_GATE = register_deployment_mode(app, ROOT, QQ_SERVICE, authorize)
