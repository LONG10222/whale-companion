"""HTTP boundary for typed music tools; generated audio is served by artifact ID."""
from pathlib import Path
from typing import Literal
import uuid
from fastapi import Depends,HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel,Field
from music_service import MusicService

class GenerateRequest(BaseModel):
    title:str=Field(min_length=1,max_length=100)
    lyrics:str=Field(min_length=1,max_length=2000)
    style:str=Field(min_length=1,max_length=600)
    seconds:int=Field(ge=10,le=40)
    language:Literal['zh','en']='zh'
    lyrics_origin:Literal['public_domain','original','user_provided','licensed']
    source_url:str=Field(default='',max_length=1000)
    seed:int|None=Field(default=None,ge=0,le=2147483647)

class ConvertRequest(BaseModel):
    artifact_id:str=Field(pattern='^[a-f0-9]{32}$')
    semitones:int=Field(default=0,ge=-12,le=12)

class SpeakRequest(BaseModel):
    text:str=Field(min_length=1,max_length=120)
    voice:Literal['dania','vivian','serena','dylan','zh-3','zh-12']='dania'

def register_music_routes(app,*,authorize,lock,unload,root,synthesize):
    service=MusicService(root,lock,unload)
    @app.get('/music/status',dependencies=[Depends(authorize)])
    def status():return service.status()
    @app.post('/music/cancel',dependencies=[Depends(authorize)])
    def cancel():return service.cancel()
    @app.get('/artifacts/{artifact_id}',dependencies=[Depends(authorize)])
    def artifact(artifact_id:str):
        try:path=service.path(artifact_id)
        except ValueError as exc:raise HTTPException(404,str(exc))
        return FileResponse(path,media_type='audio/wav',filename='singing.wav',headers={'Cache-Control':'no-store'})
    @app.post('/v1/music/generate',dependencies=[Depends(authorize)])
    def generate(request:GenerateRequest):
        if not service.status()['ready']:raise HTTPException(503,'Local singing generation models are not ready')
        try:return service.run('music',request.model_dump())
        except RuntimeError as exc:raise HTTPException(503,str(exc))
    @app.post('/v1/music/convert-voice',dependencies=[Depends(authorize)])
    def convert(request:ConvertRequest):
        try:return service.run('voice',request.model_dump())
        except ValueError as exc:raise HTTPException(400,str(exc))
        except RuntimeError as exc:raise HTTPException(503,str(exc))
    @app.post('/v1/music/separate',dependencies=[Depends(authorize)])
    def separate(request:ConvertRequest):
        try:return service.run('separate',request.model_dump())
        except ValueError as exc:raise HTTPException(400,str(exc))
        except RuntimeError as exc:raise HTTPException(503,str(exc))
    @app.post('/v1/music/speak',dependencies=[Depends(authorize)])
    def speak(request:SpeakRequest):
        data,metrics=synthesize(request.text,request.voice)
        metrics={**metrics,'voice':request.voice}
        artifact_id=uuid.uuid4().hex
        (service.artifacts/(artifact_id+'.wav')).write_bytes(data)
        return service.save_metadata(artifact_id,{'kind':'speech','metrics':metrics,'title':'聊天语音'})
    return service
