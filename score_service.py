"""A reviewed score-to-vocal pipeline sharing the application's single GPU gate."""
from pathlib import Path
import json,shutil,subprocess,tempfile,time,uuid
from lyrics_quality import assess
from score_adapter import make_score,make_soulx_metadata
from score_cache import ScoreCache
from signal_quality import check_wave

class ScoreService:
    def __init__(self,root,lock,unload,artifacts):
        self.root=Path(root);self.lock=lock;self.unload=unload;self.artifacts=artifacts
        self.process=None;self.busy=False;self.cancelled=False;self.phase='idle'
        self.jobs=self.root/'cache/score-jobs';self.jobs.mkdir(parents=True,exist_ok=True)
        self.cache=ScoreCache(self.root/'cache/score-verified')
    def config(self):return json.loads((self.root/'score.local.json').read_text(encoding='utf-8'))
    def status(self):
        try:
            cfg=self.config()
            paths=['python','source_root','voice_bank','asr_python','asr_model','phone_map']
            if cfg.get('review_enabled',False):paths+=['review_python','review_model_dir']
            prepared=all(Path(cfg[x]).exists() for x in paths)
        except (FileNotFoundError,KeyError):cfg={};prepared=False
        return {'ready':prepared and cfg.get('enabled',False),'prepared':prepared,'busy':self.busy,'phase':self.phase,
            'voice':{'dania':'达妮娅中文参考声线 · AI合成','soulx':'SoulX-Singer · AI合成','qixuan':'绮萱 · DiffSinger AI合成'}.get(cfg.get('default_voice','qixuan')),
            'independent_audio_review':cfg.get('review_enabled',False),'audio_review_is_heuristic':True,
            'default_voice':cfg.get('default_voice','qixuan'),
            'available_voices':(['dania'] if cfg.get('dania_conversion') else [])+['qixuan']+(['soulx'] if cfg.get('soulx') else []),
            'worker_loaded':self.process is not None and self.process.poll() is None}
    def cancel(self):
        self.cancelled=True
        if self.process is not None and self.process.poll() is None:self.process.terminate()
        return {'stopping':self.busy}
    def worker(self,python,script,config,job,label):
        self.phase=label
        with (job/(label+'.log')).open('w',encoding='utf-8') as log:
            self.process=subprocess.Popen([python,'-u',str(self.root/script),json.dumps(config)],stdout=subprocess.PIPE,
                stderr=log,text=True,encoding='utf-8',creationflags=subprocess.CREATE_NO_WINDOW)
            if self.cancelled:self.process.terminate()
            try:stdout,_=self.process.communicate(timeout=600)
            except subprocess.TimeoutExpired:
                self.process.kill();self.process.communicate();raise RuntimeError('本地音频任务超时，工作进程已停止')
            if self.cancelled:raise RuntimeError('本地音频任务已停止')
            try:result=json.loads(stdout.strip().splitlines()[-1])
            except (ValueError,IndexError):result={'ok':False,'error':'工作进程没有返回有效结果'}
            if self.process.returncode or not result.get('ok'):
                (self.root/'cache/last-score-error.log').write_text((job/(label+'.log')).read_text(encoding='utf-8',errors='replace')[-65536:],encoding='utf-8')
                raise RuntimeError(result.get('error','本地音频任务失败'))
        return result
    def run(self,payload):
        from singing_performance import prepare_performance
        payload=prepare_performance(payload)
        started=time.monotonic();cfg=self.config()
        if payload.get('voice',cfg.get('default_voice','qixuan'))=='dania':
            from dania_singing import run_dania
            return run_dania(self,payload)
        sections,lyrics,seconds=make_score(payload['phrases'],cfg['phone_map'])
        voice=payload.get('voice','qixuan')
        if voice not in ('qixuan','soulx'):raise ValueError('未知的本地歌手')
        if voice=='soulx' and not cfg.get('soulx'):raise ValueError('SoulX歌手尚未配置')
        seed=payload.get('seed',20261021)
        key=self.cache.key({'profile':cfg['cache_version'],'voice':voice,'score':sections,'seed':seed})
        artifact_id=uuid.uuid4().hex;output=self.artifacts.artifacts/(artifact_id+'.wav')
        cached=self.cache.get(key,output)
        if cached:
            cached.update(title=payload['title'],source_url=payload.get('source_url',''),lyrics_origin=payload['lyrics_origin'],
                cache_hit=True,elapsed_seconds=round(time.monotonic()-started,3))
            return self.artifacts.save_metadata(artifact_id,cached)
        if not self.lock.acquire(blocking=False):raise RuntimeError('另一个本地音频任务正在运行，请稍后再试')
        self.busy=True;self.cancelled=False
        try:
            self.unload()
            with tempfile.TemporaryDirectory(prefix='score-',dir=self.jobs) as directory:
                job=Path(directory);score=job/'score.ds'
                score.write_text(json.dumps(sections,ensure_ascii=False),encoding='utf-8')
                job_cfg={**cfg,'score_path':str(score),'output_audio':str(output),'work_dir':str(job/'render'),'seed':seed}
                if voice=='soulx':
                    profile=cfg['soulx'];target=job/'target.json'
                    target.write_text(json.dumps(make_soulx_metadata(payload['phrases'],profile['phone_set']),ensure_ascii=False),encoding='utf-8')
                    job_cfg.update(profile,target_metadata=str(target))
                    metrics=self.worker(profile['python'],'soulx_singing_worker.py',job_cfg,job,'synthesis')
                else:metrics=self.worker(cfg['python'],'score_singing_worker.py',job_cfg,job,'synthesis')
                signal=check_wave(output,sections)
                if not signal['passed']:raise RuntimeError('音频完整性检查未通过：'+'；'.join(signal['problems']))
                check={'input_audio':str(output),'asr_backend':'sensevoice','asr_model':cfg['asr_model'],'lyrics':lyrics,'language':'zh'}
                quality=self.worker(cfg['asr_python'],'lyric_check_worker.py',check,job,'lyrics_check')
                if not quality.get('passed'):raise RuntimeError('新歌声没有通过严格的唱词检查，未作为合格结果返回。识别：'+quality.get('asr_text',''))
                decision={'performed':False,'reason':'Reviewer failed calibration and was removed from the default production flow.'}
                review_words=None;review_metrics=None
                if cfg.get('review_enabled',False):
                    review=self.worker(cfg['review_python'],'audio_review_worker.py',{'model_dir':cfg['review_model_dir'],
                        'audios':[{'name':'candidate','path':str(output)}]},job,'audio_review')
                    decision=review['reviews'][0]['review'];review_words=assess(lyrics,decision.get('transcription',''))
                    accepted=(decision.get('verdict')=='candidate' and decision.get('is_singing') is True
                        and not decision.get('artifacts') and not decision.get('problems') and review_words['passed'])
                    if not accepted:
                        (self.root/'cache/last-score-audio-review.json').write_text(json.dumps(review,ensure_ascii=False,indent=2),encoding='utf-8')
                        raise RuntimeError('音频审核未通过：'+json.dumps(decision,ensure_ascii=False)[:1000])
                    review_metrics={k:review[k] for k in ['reviewer','elapsed_seconds','peak_allocated_mib']}
                result={'title':payload['title'],'voice_reference':'qixuan_native','voice':'绮萱 Qixuan 2.7.0',
                    'technology':'DiffSinger','ai_generated':True,'voice_conversion':False,'lyrics':lyrics,
                    'lyrics_origin':payload['lyrics_origin'],'source_url':payload.get('source_url',''),
                    'quality_status':'automated_review_passed_listening_pending','accepted_by_user':False,'human_naturalness_verified':False,
                    'reviewer_calibration_passed':False,
                    'audio_review_limitation':'Heuristic veto only. Scores are not evidence of human naturalness: A/B order test and silence singing-classification control failed.',
                    'lyrics_quality':quality,'signal_quality':signal,'audio_review':decision,'audio_reviewer_lyrics_quality':review_words,
                    'generation_metrics':metrics,'review_metrics':review_metrics,
                    'seconds':signal['seconds'],'sample_rate':signal['sample_rate'],'cache_hit':False,'worker_exited':True,
                    'elapsed_seconds':round(time.monotonic()-started,3),
                    'credits':'AI合成歌声：绮萱；合成技术：DiffSinger；声库制作：YQ之神，数据提供：颜绮萱。'}
                if voice=='soulx':result.update(voice_reference='soulx_native',voice='SoulX-Singer 官方示例音色',technology='SoulX-Singer',
                    credits='AI合成歌声：SoulX-Singer；参考音色：Soul-AILab 官方推理示例；未使用绮萱音频作为采样。')
                self.cache.put(key,output,result)
                return self.artifacts.save_metadata(artifact_id,result)
        except Exception:
            output.unlink(missing_ok=True)
            raise
        finally:
            if self.process is not None and self.process.poll() is None:self.process.kill();self.process.wait()
            self.process=None;self.busy=False;self.phase='idle';self.lock.release()
