"""Pause new local mutations during a verified update transaction."""
import hmac
from pathlib import Path
from fastapi.responses import JSONResponse
from self_update import read


def register_maintenance_guard(app, root):
    root = Path(root)
    @app.middleware('http')
    async def maintenance(request, call_next):
        progress = read(root / 'data/setup/update-progress.local.json', {})
        if request.method not in {'GET', 'HEAD', 'OPTIONS'} and progress.get('phase') in {
                'switching', 'validating', 'rolling_back', 'recovery_required'}:
            route = request.url.path
            allowed = route in {'/local/session', '/updates/check', '/updates/apply'}
            if route in {'/qq/config', '/admin/unload'}:
                transaction = read(root / 'data/setup/update-request.local.json', {})
                token = str(transaction.get('maintenance_token') or '')
                allowed = bool(token and hmac.compare_digest(token, request.headers.get('x-whale-updater', '')))
                # Route-level authentication still runs after this exception.
            if not allowed:
                return JSONResponse({'detail': '正在切换版本，当前设置和任务保留，稍后即可继续。'}, status_code=503)
        return await call_next(request)
