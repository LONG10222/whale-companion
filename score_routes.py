"""Typed HTTP interface for explicit, independently reviewed Mandarin singing."""
from typing import Literal
from fastapi import Depends,HTTPException
from pydantic import BaseModel,Field
from score_adapter import demo_request
from score_service import ScoreService

class Phrase(BaseModel):
    text:str=Field(min_length=1,max_length=40)
    notes:list[str]=Field(min_length=1,max_length=40)
    durations:list[float]=Field(min_length=1,max_length=40)
    pinyin:list[str]|None=None
    expression:float=Field(default=1.0,ge=.7,le=1.4)
class ScoreRequest(BaseModel):
    voice:Literal['dania','qixuan','soulx']='dania'
    title:str=Field(min_length=1,max_length=100)
    phrases:list[Phrase]=Field(min_length=1,max_length=8)
    lyrics_origin:Literal['public_domain','original','user_provided','licensed']
    source_url:str=Field(default='',max_length=1000)
    seed:int=Field(default=20261021,ge=0,le=2147483647)
    pitch_stability:Literal['original','steady']='original'
    tempo:float=Field(default=1,ge=.8,le=1.15)
    rhythm:Literal['normal','connected','gentle']='normal'
    phrase_gap:float|None=Field(default=None,ge=.10,le=.80)

def register_score_routes(app,*,authorize,root,lock,unload,artifacts):
    service=ScoreService(root,lock,unload,artifacts)
    @app.get('/score/status')
    def status():return service.status()
    @app.get('/score/demo')
    def demo():
        payload=demo_request();payload.update(voice='dania',title='春晓 · 达妮娅中文参考声线 · AI合成')
        return payload
    @app.post('/score/cancel',dependencies=[Depends(authorize)])
    def cancel():return service.cancel()
    @app.post('/v1/score/sing',dependencies=[Depends(authorize)])
    def sing(request:ScoreRequest):
        if not service.status()['prepared']:raise HTTPException(503,'新的歌声与音频审核模型尚未准备完整')
        try:return service.run(request.model_dump(exclude_none=True))
        except ValueError as exc:raise HTTPException(400,str(exc))
        except RuntimeError as exc:raise HTTPException(503,str(exc))
    return service
