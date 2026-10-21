"""Local upload -> isolated singing worker -> dry vocal result. No remote song retrieval."""
from pathlib import Path
from urllib.parse import unquote
import asyncio
import contextlib
import json
import shutil
import subprocess
import time
import uuid

from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse


def register_singing_routes(app,*,authorize,lock,unload,root):
    root=Path(root)
    config_file=root/'singing.local.json'
    jobs=root/'cache/singing-jobs';jobs.mkdir(parents=True,exist_ok=True)
    results=root/'singing-results';results.mkdir(exist_ok=True)
    state={'busy':False,'last_result':None,'process':None,'cancel_requested':False}

    @app.get('/singing/status')
    def status():
        ready=False
        if config_file.is_file():
            config=json.loads(config_file.read_text(encoding='utf-8'))
            ready=(Path(config['models_root'])/'model-manifest.json').is_file()
        return {'ready':ready,'busy':state['busy'],'source_audio_required':True,'last_result':state['last_result']}

    @app.post('/singing/cancel',dependencies=[Depends(authorize)])
    def cancel():
        if state['busy']:
            state['cancel_requested']=True
            process=state['process']
            if process is not None and process.poll() is None:process.terminate()
        return {'stopping':state['busy']}

    @app.post('/v1/singing/convert',dependencies=[Depends(authorize)])
    async def convert(request:Request,steps:int=Query(default=40,ge=30,le=50),semitones:int=Query(default=0,ge=-12,le=12)):
        if not status()['ready']:raise HTTPException(503,'Singing models are not fully prepared')
        declared=request.headers.get('content-length','0')
        if declared.isdigit() and int(declared)>128*1024**2:raise HTTPException(413,'Audio must be under 128 MiB')
        suffix=Path(unquote(request.headers.get('x-filename','source.wav'))).suffix.lower()
        if suffix not in {'.wav','.flac','.mp3','.m4a','.ogg','.opus','.webm'}:
            raise HTTPException(400,'Choose a WAV, FLAC, MP3, M4A, OGG, Opus, or WebM audio file')
        if not lock.acquire(blocking=False):raise HTTPException(409,'Another local audio job is running; please wait')
        state['busy']=True
        state['cancel_requested']=False
        job_id=uuid.uuid4().hex
        job=(jobs/job_id).resolve()
        if not job.is_relative_to(jobs.resolve()):raise RuntimeError('Invalid job directory')
        try:job.mkdir()
        except Exception:
            state['busy']=False;lock.release();raise
        source=job/('uploaded'+suffix)
        output=results/(job_id+'.wav')
        try:
            # Voice generation and singing conversion share one GPU gate.
            unload()
            total=0
            with source.open('wb') as f:
                async for chunk in request.stream():
                    total+=len(chunk)
                    if total>128*1024**2:raise HTTPException(413,'Audio must be under 128 MiB')
                    await asyncio.to_thread(f.write,chunk)
            if total==0:raise HTTPException(400,'Please choose a singing recording first')
            if state['cancel_requested']:raise HTTPException(409,'Singing conversion was stopped')
            config=json.loads(config_file.read_text(encoding='utf-8'))
            config.update(input_audio=str(source),output_audio=str(output),work_dir=str(job),
                diffusion_steps=steps,semitones=semitones)
            def run():
                with (job/'worker.log').open('w',encoding='utf-8') as log:
                    process=subprocess.Popen([config['python'],'-u',str(root/'singing_worker.py'),json.dumps(config)],
                        stdout=subprocess.PIPE,stderr=log,text=True,encoding='utf-8',
                        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    state['process']=process
                    try:
                        if state['cancel_requested']:process.terminate()
                        output_text,_=process.communicate(timeout=900)
                        return subprocess.CompletedProcess(process.args,process.returncode,stdout=output_text)
                    except subprocess.TimeoutExpired:
                        process.kill();process.communicate();raise
                    finally:state['process']=None
            worker=asyncio.create_task(asyncio.to_thread(run))
            try:completed=await asyncio.shield(worker)
            except asyncio.CancelledError:
                cancel()
                with contextlib.suppress(Exception):await asyncio.shield(worker)
                raise
            except subprocess.TimeoutExpired:raise HTTPException(504,'Singing conversion timed out; its worker was stopped')
            if state['cancel_requested']:raise HTTPException(409,'Singing conversion was stopped')
            try:result=json.loads(completed.stdout.strip().splitlines()[-1])
            except (ValueError,IndexError):raise HTTPException(500,'Singing worker did not return a result')
            if completed.returncode or not result.get('ok'):
                (root/'cache/last-singing-error.log').write_text((job/'worker.log').read_text(encoding='utf-8')[-65536:],encoding='utf-8')
                raise HTTPException(500,result.get('error','Singing conversion failed'))
            state['last_result']=result
            (results/(job_id+'.json')).write_text(json.dumps(result,indent=2),encoding='utf-8')
            return FileResponse(output,media_type='audio/wav',filename='dania-vocals.wav',
                headers={'X-Audio-Metrics':json.dumps(result),'Cache-Control':'no-store'})
        finally:
            state['busy']=False
            lock.release()
            # Only this request's UUID directory is temporary; output WAVs are kept for the user.
            if job.is_relative_to(jobs.resolve()):shutil.rmtree(job,ignore_errors=True)

