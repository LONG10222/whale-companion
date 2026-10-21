"""Shared GPU gate and local artifact storage for generated singing and voice conversion."""
from pathlib import Path
import json,re,secrets,subprocess,tempfile,time,uuid

class MusicService:
    def __init__(self,root,lock,unload):
        self.root=Path(root);self.lock=lock;self.unload=unload;self.process=None;self.busy=False
        self.cancel_requested=False
        self.artifacts=self.root/'artifacts';self.artifacts.mkdir(exist_ok=True)
        self.jobs=self.root/'cache/music-jobs';self.jobs.mkdir(parents=True,exist_ok=True)

    def path(self,artifact_id):
        if not re.fullmatch('[a-f0-9]{32}',artifact_id):raise ValueError('Invalid artifact ID')
        path=self.artifacts/(artifact_id+'.wav')
        if not path.is_file():raise ValueError('Audio artifact does not exist')
        return path

    def status(self):
        config=self.root/'music.local.json'
        ready=False
        if config.exists():
            cfg=json.loads(config.read_text(encoding='utf-8'))
            ready=(Path(cfg['models_root'])/'model-manifest.json').is_file() and Path(cfg['python']).is_file()
            ready=ready and (Path(cfg['models_root'])/'model-manifest-lm.json').is_file()
            ready=ready and bool(cfg.get('asr_model')) and Path(cfg.get('asr_python','')).is_file()
            if ready and cfg.get('asr_backend')=='sensevoice':ready=(Path(cfg['asr_model'])/'model.int8.onnx').is_file()
            if cfg.get('legacy_generation_enabled') is False:
                return {'ready':False,'busy':self.busy,'worker_loaded':self.process is not None and self.process.poll() is None,
                    'disabled_reason':'旧方案未通过听感审核，正在切换新的歌声合成方式。'}
        return {'ready':ready,'busy':self.busy,'worker_loaded':self.process is not None and self.process.poll() is None}

    def cancel(self):
        if self.busy:
            self.cancel_requested=True
            if self.process is not None and self.process.poll() is None:self.process.terminate()
        return {'stopping':self.busy}

    def save_metadata(self,artifact_id,result):
        info={**result,'artifact_id':artifact_id,'audio_url':'/artifacts/'+artifact_id,'ok':True}
        (self.artifacts/(artifact_id+'.json')).write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
        return info

    def check_lyrics(self,payload,output,job_dir):
        """Reject audio that does not contain enough of the requested words."""
        settings=json.loads((self.root/'music.local.json').read_text(encoding='utf-8'))
        config={'asr_model':settings['asr_model'],'input_audio':str(output),
            'lyrics':payload['lyrics'],'language':payload.get('language','zh'),
            'asr_backend':settings.get('asr_backend','whisper')}
        with (Path(job_dir)/'lyrics-check.log').open('w',encoding='utf-8') as log:
            self.process=subprocess.Popen([settings['asr_python'],'-u',str(self.root/'lyric_check_worker.py'),json.dumps(config)],
                stdout=subprocess.PIPE,stderr=log,text=True,encoding='utf-8',creationflags=subprocess.CREATE_NO_WINDOW)
            if self.cancel_requested:self.process.terminate()
            try:stdout,_=self.process.communicate(timeout=180)
            except subprocess.TimeoutExpired:
                self.process.kill();self.process.communicate();raise RuntimeError('Lyric recognition timed out')
            if self.cancel_requested:raise RuntimeError('Local audio task was stopped')
            try:quality=json.loads(stdout.strip().splitlines()[-1])
            except (ValueError,IndexError):raise RuntimeError('唱词检查无法完成，本次结果不作为完成的演唱返回')
            if self.process.returncode or not quality.get('ok'):raise RuntimeError(quality.get('error','唱词识别失败'))
        if not quality['passed']:
            (self.root/'cache/last-lyric-quality.json').write_text(json.dumps(quality,ensure_ascii=False,indent=2),encoding='utf-8')
            self.save_metadata(output.stem,{'quality_status':'rejected_missing_lyrics','lyrics_quality':quality})
            raise RuntimeError('唱词检查未通过，未把这段音频作为完成的歌曲返回。识别结果：'+quality['asr_text'][:160])
        return quality

    def run(self,kind,payload):
        if not self.lock.acquire(blocking=False):raise RuntimeError('Another local audio task is running')
        self.busy=True;self.cancel_requested=False
        artifact_id=uuid.uuid4().hex
        output=self.artifacts/(artifact_id+'.wav')
        try:
            self.unload()
            cfg=json.loads((self.root/('singing.local.json' if kind=='voice' else 'music.local.json')).read_text(encoding='utf-8'))
            with tempfile.TemporaryDirectory(prefix='music-',dir=self.jobs) as job_dir:
                cfg.update(work_dir=job_dir,output_audio=str(output))
                if kind=='music':
                    cfg.update(lyrics=payload['lyrics'],caption=payload['style'],
                        duration=payload['seconds'],language=payload['language'],bpm=80,
                        seed=payload['seed'] if payload.get('seed') is not None else secrets.randbelow(2147483647))
                else:
                    cfg.update(input_audio=str(self.path(payload['artifact_id'])))
                    if kind=='voice':cfg.update(semitones=payload['semitones'],diffusion_steps=40)
                with (Path(job_dir)/'worker.log').open('w',encoding='utf-8') as log:
                    worker={'music':'music_worker.py','voice':'singing_worker.py','separate':'separate_worker.py'}[kind]
                    self.process=subprocess.Popen([cfg['python'],'-u',str(self.root/worker),json.dumps(cfg)],
                        stdout=subprocess.PIPE,stderr=log,text=True,encoding='utf-8',creationflags=subprocess.CREATE_NO_WINDOW)
                    try:
                        if self.cancel_requested:self.process.terminate()
                        stdout,_=self.process.communicate(timeout=900)
                    except subprocess.TimeoutExpired:
                        self.process.kill();self.process.communicate();raise RuntimeError('Local audio worker timed out')
                    if self.cancel_requested:raise RuntimeError('Local audio task was stopped')
                    try:result=json.loads(stdout.strip().splitlines()[-1])
                    except (ValueError,IndexError):result={'ok':False,'error':'Worker did not return valid audio'}
                    if self.process.returncode or not result.get('ok'):
                        (self.root/'cache/last-music-error.log').write_text((Path(job_dir)/'worker.log').read_text(encoding='utf-8')[-65536:],encoding='utf-8')
                        raise RuntimeError(result.get('error','Local audio conversion failed'))
                result['worker_exited']=True
                if kind=='music':
                    result.update(title=payload['title'],lyrics=payload['lyrics'],language=payload['language'],
                        lyrics_origin=payload['lyrics_origin'],source_url=payload['source_url'])
                    (self.root/'cache/last-music-generation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
                    result['lyrics_quality']=self.check_lyrics(payload,output,job_dir)
                else:
                    parent=json.loads((self.artifacts/(payload['artifact_id']+'.json')).read_text(encoding='utf-8'))
                    result.update(title=parent.get('title','音色转换'),parent_artifact_id=payload['artifact_id'],
                        instrumental_absence_verified=parent.get('instrumental_absence_verified',False),
                        vocal_stem_isolated=kind=='separate' or parent.get('vocal_stem_isolated',False))
                    if kind=='voice':result['voice_reference']='dania'
                    for field in ('lyrics','language','lyrics_origin','source_url'):
                        if field in parent:result[field]=parent[field]
                    if kind=='voice' and result.get('lyrics'):
                        result['lyrics_quality']=self.check_lyrics(result,output,job_dir)
                if result.get('lyrics_quality',{}).get('passed'):result['quality_status']='automatic_lyrics_check_passed_listening_pending'
                return self.save_metadata(artifact_id,result)
        finally:
            if self.process is not None and self.process.poll() is None:self.process.kill();self.process.wait()
            self.process=None;self.busy=False;self.lock.release()
