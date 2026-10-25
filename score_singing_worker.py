"""Explicit phoneme/note singing synthesis, with sequential ONNX sessions and native voice."""
from pathlib import Path
import builtins,contextlib,gc,json,os,sys,time,traceback

def main():
    for stream in (sys.stdin,sys.stdout,sys.stderr):stream.reconfigure(encoding='utf-8')
    cfg=json.loads(sys.argv[1]);started=time.monotonic()
    sys.path.insert(0,cfg['source_root'])
    try:
        with contextlib.redirect_stdout(sys.stderr):
            import numpy as np,soundfile as sf,torch
            import onnxruntime as ort
            threads=max(1,min(8,int(cfg.get('cpu_threads',6))))
            torch.set_num_threads(threads)
            target=cfg.get('device','cuda')
            if target=='cuda':
                if not torch.cuda.is_available():raise RuntimeError('CUDA is unavailable')
                if torch.cuda.mem_get_info()[0]<int(cfg.get('min_free_vram_mib',6144))*1024**2:raise RuntimeError('歌声合成需要更多空闲显存，请稍后再试')
                torch.cuda.set_per_process_memory_fraction(max(.1,min(.95 if cfg.get('performance_mode')=='full' else .75,float(cfg.get('gpu_memory_fraction',.55)))),0)
                ort.preload_dlls(directory=str(Path(torch.__file__).parent/'lib'))
            np.random.seed(cfg.get('seed',20261021));torch.manual_seed(cfg.get('seed',20261021));ort.set_seed(cfg.get('seed',20261021))
            original_open=builtins.open;voice_root=Path(cfg['voice_bank']).resolve()
            def voice_text_open(file,mode='r',*args,**kwargs):
                if isinstance(file,(str,Path)) and 'b' not in mode and not args and 'encoding' not in kwargs:
                    if Path(file).resolve().is_relative_to(voice_root):kwargs['encoding']='utf-8'
                return original_open(file,mode,*args,**kwargs)
            builtins.open=voice_text_open
            from diffsinger_utau.voice_bank.commons.voice_bank_reader import VoiceBankReader,OnnxReader
            from diffsinger_utau.voice_bank.commons.ds_reader import DSReader
            from diffsinger_utau.voice_bank.pred_all import PredAll
            sessions=[];providers_used=set()
            def load_model(reader,device='cpu'):
                options=ort.SessionOptions();options.log_severity_level=3
                options.intra_op_num_threads=threads;options.inter_op_num_threads=1
                providers=['CPUExecutionProvider']
                if target=='cuda':providers=[('CUDAExecutionProvider',{'gpu_mem_limit':2*1024**3,
                    'arena_extend_strategy':'kSameAsRequested','cudnn_conv_algo_search':'HEURISTIC',
                    'cudnn_conv1d_pad_to_nc1d':'1'}),'CPUExecutionProvider']
                reader.session=ort.InferenceSession(str(reader.onnx_path),sess_options=options,providers=providers)
                active=reader.session.get_providers();providers_used.update(active)
                if target=='cuda' and 'CUDAExecutionProvider' not in active:raise RuntimeError('ONNX CUDA provider could not start')
                reader.input_names=[x.name for x in reader.session.get_inputs()]
                reader.output_names=[x.name for x in reader.session.get_outputs()]
                reader.input_shapes={x.name:x.shape for x in reader.session.get_inputs()}
                reader.output_shapes={x.name:x.shape for x in reader.session.get_outputs()}
                reader.model_size=Path(reader.onnx_path).stat().st_size
                sessions.append(reader);return True
            OnnxReader.load_model=load_model
            original_init=VoiceBankReader.__init__
            def lazy_init(reader,path,preload_models=True):original_init(reader,path,preload_models=False)
            VoiceBankReader.__init__=lazy_init
            def release_after(method):
                def predict(*args,**kwargs):
                    try:return method(*args,**kwargs)
                    finally:
                        for reader in sessions:reader.session=None
                        sessions.clear();gc.collect()
                return predict
            pipeline=PredAll(Path(cfg['voice_bank']))
            if cfg.get('constrain_onsets',False):
                original_duration=pipeline.pred_duration.predict
                def duration_with_onset_limits(section,*args,**kwargs):
                    values=np.asarray(original_duration(section,*args,**kwargs),dtype=np.float32).copy()
                    counts=[int(x) for x in section['ph_num'].split()];phones=section['ph_seq'].split();index=0
                    for count in counts:
                        group=phones[index:index+count];total=float(values[index:index+count].sum())
                        if count>=2 and group[0] not in ('AP','SP'):
                            onset=min(float(values[index]),cfg.get('onset_max_seconds',.10),total*.20)
                            onset=max(onset,min(.04,total*.10))
                            tail=values[index+1:index+count]
                            tail*=max(total-onset,0)/max(float(tail.sum()),1e-6)
                            values[index]=onset
                        index+=count
                    return values
                pipeline.pred_duration.predict=duration_with_onset_limits
            if cfg.get('release_between_stages',False):
                for predictor in [pipeline.pred_duration,pipeline.pred_pitch,pipeline.pred_variance,pipeline.pred_acoustic,pipeline.pred_vocoder]:
                    predictor.predict=release_after(predictor.predict)
            sections=json.loads(Path(cfg['score_path']).read_text(encoding='utf-8'))
            sr=44100;pieces=[];finish=0
            for index,raw in enumerate(sections):
                section=DSReader.DSSection(raw)
                speaker=cfg.get('speaker') or (pipeline.available_speakers[0] if pipeline.available_speakers else None)
                result=pipeline.predict_full_pipeline(section,lang='zh',speaker=speaker,key_shift=cfg.get('key_shift',0),
                    pitch_steps=cfg.get('pitch_steps',30),variance_steps=cfg.get('variance_steps',30),
                    acoustic_steps=cfg.get('acoustic_steps',60),gender=cfg.get('gender',0),
                    output_dir=str(Path(cfg['work_dir'])/str(index)),save_intermediate=True)
                audio=np.asarray(result['wav'],dtype=np.float32).reshape(-1)
                if not np.isfinite(audio).all():raise RuntimeError('Singing output contains invalid samples')
                start=round(float(raw['offset'])*sr);finish=max(finish,start+len(audio));pieces.append((start,audio))
            output=np.zeros(finish,dtype=np.float32)
            for start,audio in pieces:output[start:start+len(audio)]+=audio
            peak=float(np.abs(output).max())
            if peak<1e-5:raise RuntimeError('Singing output is silent')
            if peak>.95:output*=.95/peak
            sf.write(cfg['output_audio'],output,sr,subtype='PCM_16')
            result={'ok':True,'engine':'DiffSinger score-driven native voice','voice':cfg.get('voice_name','OpenCpop'),
                'onset_constraints_applied':cfg.get('constrain_onsets',False),'voice_conversion':False,
                'explicit_phonemes_and_notes':True,'seconds':round(len(output)/sr,3),
                'elapsed_seconds':round(time.monotonic()-started,3),'providers':sorted(providers_used),
                'phrase_count':len(pieces),'emotion_and_naturalness_reviewed':False,'cloud_called':False}
        print(json.dumps(result),flush=True)
    except Exception as exc:
        traceback.print_exc(file=sys.stderr);print(json.dumps({'ok':False,'error':str(exc)}),flush=True);raise SystemExit(1)

if __name__=='__main__':main()
