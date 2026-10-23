"""Authenticated update page; installation is not an LLM or QQ tool."""
from pathlib import Path
from fastapi import Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from self_update import UpdateService


def register_update_routes(app, *, root, authorize):
    root = Path(root)
    service = UpdateService(root)

    @app.get('/updates', response_class=HTMLResponse)
    def page():
        return (root / 'updates.html').read_text(encoding='utf-8')

    @app.get('/updates/ui.js')
    def script():
        return Response((root / 'updates_ui.js').read_text(encoding='utf-8'), media_type='application/javascript')

    @app.get('/updates/style.css')
    def style():
        return Response((root / 'scripts/beginner.css').read_text(encoding='utf-8'), media_type='text/css')

    @app.get('/updates/state', dependencies=[Depends(authorize)])
    def state():
        return service.state()

    @app.post('/updates/check', dependencies=[Depends(authorize)])
    def check():
        return service.check(force=True)

    @app.post('/updates/apply', dependencies=[Depends(authorize)])
    def apply():
        try:
            return service.apply()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.middleware('http')
    async def notice(request, call_next):
        response = await call_next(request)
        if request.method == 'GET' and response.headers.get('content-type', '').startswith('text/html'):
            body = b''.join([chunk async for chunk in response.body_iterator]).decode('utf-8')
            if '/updates/ui.js' not in body:
                body += '<script src="/updates/ui.js" defer></script>'
            headers = dict(response.headers)
            headers.pop('content-length', None)
            return Response(body, status_code=response.status_code, headers=headers)
        return response
    return service
