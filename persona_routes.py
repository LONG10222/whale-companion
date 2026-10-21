from pathlib import Path
import base64,json,threading
from fastapi import Depends,HTTPException
from fastapi.responses import HTMLResponse,Response
from pydantic import BaseModel,Field
from persona_store import PersonaStore,PersonaCard,card_messages,import_card,export_character,export_training,dania_card
from qq_chat import INSTRUCTIONS
import deepseek_client

class ImportRequest(BaseModel):
    encoded:str=Field(min_length=1,max_length=12*1024**2)
class PersonaPreview(BaseModel):
    card:PersonaCard
    message:str=Field(min_length=1,max_length=1200)

def register_persona_routes(app,*,authorize,root):
    root=Path(root);store=PersonaStore(root);gate=threading.Lock()
    @app.get('/qq/persona',response_class=HTMLResponse)
    def page():return (root/'persona.html').read_text(encoding='utf-8')
    @app.get('/qq/persona/config',dependencies=[Depends(authorize)])
    def config():
        card=store.load()
        return {'card':card.model_dump(),'load_error':store.error,'mode':'local_character_card_to_deepseek_api','hot_reload':True}
    @app.post('/qq/persona/config',dependencies=[Depends(authorize)])
    def save(card:PersonaCard):
        try:return {'saved':True,'card':store.save(card).model_dump(),'takes_effect':'next_message'}
        except ValueError as exc:raise HTTPException(400,str(exc)) from None
    @app.get('/qq/persona/dania',dependencies=[Depends(authorize)])
    def preset():return {'card':dania_card().model_dump()}
    @app.post('/qq/persona/restore',dependencies=[Depends(authorize)])
    def restore():
        try:return {'card':store.restore().model_dump(),'restored':True}
        except ValueError as exc:raise HTTPException(400,str(exc)) from None
    @app.post('/qq/persona/import',dependencies=[Depends(authorize)])
    def importer(request:ImportRequest):
        try:
            card=import_card(base64.b64decode(request.encoded,validate=True))
            return {'card':card.model_dump(),'saved':False,'message':'已导入为草稿，检查后点保存生效。'}
        except Exception:raise HTTPException(400,'角色卡无法读取。支持本程序JSON、SillyTavern V1/V2/V3 JSON或带角色资料的PNG；文件最多8MB。') from None
    @app.get('/qq/persona/export',dependencies=[Depends(authorize)])
    def exporter(format:str='character'):
        card=store.load()
        if format=='training':
            return Response(export_training(card),media_type='application/x-ndjson',headers={'Content-Disposition':'attachment; filename="persona-original-examples.jsonl"','Cache-Control':'no-store'})
        if format!='character':raise HTTPException(400,'导出格式不正确。')
        return Response(json.dumps(export_character(card),ensure_ascii=False,indent=2),media_type='application/json',
            headers={'Content-Disposition':'attachment; filename="persona-character-card.json"','Cache-Control':'no-store'})
    @app.post('/qq/persona/preview',dependencies=[Depends(authorize)])
    def preview(request:PersonaPreview):
        if not gate.acquire(blocking=False):raise HTTPException(409,'已有一条人格预览正在生成。')
        try:
            messages=card_messages(request.card,INSTRUCTIONS,request.message)
            messages.append({'role':'user','content':request.message})
            response,usage=deepseek_client.complete(messages,None)
            reply=(response.get('content') or '').strip()
            if not reply:raise RuntimeError('模型未返回预览内容。')
            return {'reply':reply[:1600],'sent_to_qq':False,'memory_written':False,'tools_executed':False,'usage':usage}
        except Exception as exc:raise HTTPException(503,str(exc)[:160]) from None
        finally:gate.release()
    return store
