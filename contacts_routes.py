from pathlib import Path
from fastapi import APIRouter,Depends,HTTPException
from fastapi.responses import HTMLResponse,FileResponse
from pydantic import BaseModel,ConfigDict,Field,StrictBool
from qq_contacts import permission_rows

class ContactName(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name:str=Field(default='',max_length=64)
class ContactRole(BaseModel):
    model_config=ConfigDict(extra='forbid')
    enabled:StrictBool

def register_contacts_routes(app,*,root,authorize,bridge,creative):
    root=Path(root);router=APIRouter(prefix='/qq/contacts',dependencies=[Depends(authorize)])
    def call(fn):
        try:return fn()
        except ValueError as exc:raise HTTPException(409,str(exc)) from None
        except Exception:raise HTTPException(503,'联系人操作暂未完成，原有权限保持不变。') from None
    @app.get('/qq/contacts',response_class=HTMLResponse)
    def page():return (root/'qq_contacts.html').read_text(encoding='utf-8')
    @app.get('/qq/contacts.js')
    def script():return FileResponse(root/'qq_contacts.js',media_type='application/javascript',headers={'Cache-Control':'no-store'})
    @router.get('/state')
    def state():return {'contacts':bridge.contacts.all(),'identity_key':'qq','admin_qq':bridge.contacts.admin,'bot_qq':str(bridge.cfg.get('bot_id','')),'permissions':permission_rows(),
        'online':bridge.directory.online(),'default_name':'qq_nickname','rename_scope':'local_display_name_only',
        'groups_enabled':any(g['enabled'] for g in bridge.groups.state()['groups']),
        'group_trigger':'仅@机器人或回复机器人时回应','visible_limit':500,'plus_only_changes_query_limits':True}
    @router.post('/refresh')
    def refresh():return call(bridge.directory.refresh)
    @router.post('/{peer}/name')
    def rename(peer:str,body:ContactName):
        if not body.name.strip():call(lambda:bridge.directory.resolve(peer))
        result=call(lambda:bridge.contacts.set_name(peer,body.name))
        creative.ledger.record('contact_name_changed',peer=peer,state='completed',details={'origin':'local_owner','identity_key_unchanged':True})
        return result
    @router.post('/{peer}/plus')
    def plus(peer:str,body:ContactRole):
        from qq_identity import qq_id
        call(lambda:qq_id(peer))
        if peer==str(bridge.cfg.get('bot_id')):raise HTTPException(409,'机器人自己不设置为Plus。')
        if peer==bridge.contacts.admin:raise HTTPException(409,'此QQ号已经是管理员，不能在这里改变管理员身份。')
        resolution=call(lambda:bridge.directory.resolve(peer))
        result=call(lambda:bridge.contacts.set_plus(peer,body.enabled))
        creative.ledger.record('plus_changed',peer=peer,role=result['role'],state='enabled' if body.enabled else 'disabled',details={'origin':'local_owner_contacts'})
        return {**result,'nickname_resolution':resolution}
    app.include_router(router)
