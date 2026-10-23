"""Authenticated owner management endpoints, not QQ or LLM commands."""
from pathlib import Path
from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from qq_social import SocialError

class SocialConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: StrictBool

class SocialPrepare(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation: dict

class SocialConfirm(BaseModel):
    model_config = ConfigDict(extra='forbid')
    nonce: str = Field(min_length=32, max_length=128)
    preview: dict
    confirmed: StrictBool


def register_social_routes(app, *, authorize, bridge):
    social = bridge.social
    assets = Path(__file__).resolve().parent

    @app.middleware('http')
    async def private_social_responses(request, call_next):
        response = await call_next(request)
        if request.url.path == '/qq/social' or request.url.path.startswith('/qq/social/'):
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['X-Frame-Options'] = 'DENY'
            response.headers['Referrer-Policy'] = 'same-origin'
        return response

    @app.get('/qq/social', response_class=HTMLResponse)
    def page():
        # Public static shell; all account data and operations stay authenticated.
        return (assets / 'qq_social.html').read_text(encoding='utf-8')

    @app.get('/qq/social/controller.js')
    def controller_script():
        return Response((assets / 'qq_social_controller.js').read_text(encoding='utf-8'), media_type='application/javascript')

    @app.get('/qq/social/app.js')
    def app_script():
        return Response((assets / 'qq_social_app.js').read_text(encoding='utf-8'), media_type='application/javascript')

    @app.get('/qq/social/style.css')
    def stylesheet():
        return Response((assets / 'qq_social.css').read_text(encoding='utf-8'), media_type='text/css')

    def run(fn):
        try:
            return fn()
        except SocialError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.get('/qq/social/status', dependencies=[Depends(authorize)])
    def status():
        return social.status()

    @app.post('/qq/social/config', dependencies=[Depends(authorize)])
    def config(body: SocialConfig):
        return social.configure(body.enabled)

    @app.get('/qq/social/requests', dependencies=[Depends(authorize)])
    def requests():
        return run(lambda: {'requests': social.list_requests()})

    @app.post('/qq/social/prepare')
    def prepare(body: SocialPrepare, owner=Depends(authorize)):
        return run(lambda: social.prepare(owner, body.operation))

    @app.post('/qq/social/confirm')
    def confirm(body: SocialConfirm, owner=Depends(authorize)):
        if not body.confirmed:
            raise HTTPException(422, '必须明确确认本次完整预览。')
        return run(lambda: social.confirm(owner, body.nonce, body.preview))
