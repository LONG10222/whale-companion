"""Preserve an approved singing method and change only the reference voice."""
from pathlib import Path
import hashlib,json,tempfile,time,uuid
from score_adapter import make_score
from signal_quality import check_wave

def validate_conversion(metrics):
    voice=metrics.get('voice_metrics') or {}
    if voice.get('duration_difference_seconds',1)>.10:raise RuntimeError('换声后时长发生变化，未返回此结果')
    if voice.get('pitch_median_absolute_cents') is None or voice['pitch_median_absolute_cents']>50:
        raise RuntimeError('换声后的旋律保持检查未通过')
    if voice.get('pitch_p90_absolute_cents',999)>200:raise RuntimeError('换声后局部音高变化过大')
    if voice.get('similarity_improvement',-1)<.02:raise RuntimeError('换声未明显靠近达妮娅参考音色')

def run_dania(service,payload):
    cfg=service.config();preferred=cfg.get('dania_base_voice','soulx')
    candidates=[preferred]+[v for v in cfg.get('dania_fallback_voices',['qixuan']) if v!=preferred]
    candidates=list(dict.fromkeys(candidates))[:2]
    for voice in candidates:
        cached=_run_for_voice(service,payload,voice,cached_only=True)
        if cached:return cached
    failures=[]
    for voice in candidates:
        try:return _run_for_voice(service,payload,voice)
        except RuntimeError as exc:
            if '唱词检查未通过' not in str(exc):raise
            failures.append({'base_voice':voice,'reason':str(exc)})
            (service.root/'cache/last-dania-fallback.json').write_text(json.dumps(failures,ensure_ascii=False,indent=2),encoding='utf-8')
    raise RuntimeError('候选演唱换声后均未通过唱词检查，已停止，不返回不合格音频。')

def _run_for_voice(service,payload,base_voice,cached_only=False):
    started=time.monotonic();cfg=service.config();profile=cfg.get('dania_conversion')
    if not profile:raise ValueError('达妮娅歌声配置尚未准备好')
    profile=dict(profile)
    if payload.get('pitch_stability')=='steady':profile.update(diffusion_steps=50,inference_cfg_rate=.7)
    sections,lyrics,_=make_score(payload['phrases'],cfg['phone_map'])
    if base_voice not in ('soulx','qixuan'):raise ValueError('未知的基础歌手')
    seed=payload.get('seed',20261021)
    reference_sha=hashlib.sha256(Path(profile['reference_audio']).read_bytes()).hexdigest()
    key=service.cache.key({'profile':cfg['cache_version'],'voice':'dania','base_voice':base_voice,'score':sections,
        'seed':seed,'reference_sha256':reference_sha,'conversion_steps':profile['diffusion_steps'],
        'cfg_rate':profile['inference_cfg_rate'],'voice_pipeline':'seed-vc-score-soft-f0-v3','pitch_stability':payload.get('pitch_stability','original')})
    artifact_id=uuid.uuid4().hex;output=service.artifacts.artifacts/(artifact_id+'.wav')
    cached=service.cache.get(key,output)
    if cached:
        cached.update(title=payload['title'],source_url=payload.get('source_url',''),lyrics_origin=payload['lyrics_origin'],
            cache_hit=True,elapsed_seconds=round(time.monotonic()-started,3))
        return service.artifacts.save_metadata(artifact_id,cached)
    if cached_only:return None
    base=service.run({**payload,'voice':base_voice})
    if not service.lock.acquire(blocking=False):raise RuntimeError('另一个音频任务正在运行，基础演唱已保存，可稍后重试')
    service.busy=True;service.cancelled=False
    try:
        service.unload()
        with tempfile.TemporaryDirectory(prefix='dania-',dir=service.jobs) as directory:
            job=Path(directory)
            conversion={**profile,'input_audio':str(service.artifacts.path(base['artifact_id'])),
                'output_audio':str(output),'work_dir':str(job),'semitones':0,'measure_voice':True,'pitch_stability':payload.get('pitch_stability','original'),'score_phrases':payload['phrases']}
            metrics=service.worker(profile['python'],'singing_worker.py',conversion,job,'voice_conversion')
            (service.root/'cache/last-dania-metrics.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2),encoding='utf-8')
            validate_conversion(metrics)
            signal=check_wave(output,sections)
            if not signal['passed']:raise RuntimeError('换声后完整性检查未通过：'+'；'.join(signal['problems']))
            quality=service.worker(cfg['asr_python'],'lyric_check_worker.py',{'input_audio':str(output),
                'asr_backend':'sensevoice','asr_model':cfg['asr_model'],'lyrics':lyrics,'language':'zh'},job,'lyrics_check')
            if not quality.get('passed'):
                (service.root/'cache/last-dania-lyrics.json').write_text(json.dumps(quality,ensure_ascii=False,indent=2),encoding='utf-8')
                raise RuntimeError('达妮娅换声后唱词检查未通过：'+quality.get('asr_text',''))
            result={**base,'title':payload['title'],'voice_reference':'dania','voice':'达妮娅中文参考声线',
                'technology':base['technology']+' + Seed-VC','voice_conversion':True,'base_voice':base_voice,
                'fallback_used':base_voice!=cfg.get('dania_base_voice','soulx'),
                'parent_artifact_id':base['artifact_id'],'base_method_user_approved':True,
                'accepted_by_user':False,'human_naturalness_verified':False,'target_voice_user_accepted':False,
                'quality_status':'automated_review_passed_listening_pending','lyrics_quality':quality,'signal_quality':signal,
                'reference_audio_sha256':reference_sha,'conversion_metrics':metrics,'base_cache_hit':base['cache_hit'],
                'sample_rate':signal['sample_rate'],'seconds':signal['seconds'],'cache_hit':False,'worker_exited':True,
                'elapsed_seconds':round(time.monotonic()-started,3),
                'performance':{k:payload.get(k) for k in ['pitch_stability','tempo','rhythm','phrase_gap']},
                'credits':'AI合成歌声；达妮娅中文参考声线（villia发布参考音频）；源演唱：'+base['voice']+' / '+base['technology']+'；换声：Seed-VC。'}
            for field in ['artifact_id','audio_url','ok']:result.pop(field,None)
            service.cache.put(key,output,result)
            return service.artifacts.save_metadata(artifact_id,result)
    except Exception:
        output.unlink(missing_ok=True);raise
    finally:
        if service.process is not None and service.process.poll() is None:service.process.kill();service.process.wait()
        service.process=None;service.busy=False;service.phase='idle';service.lock.release()
