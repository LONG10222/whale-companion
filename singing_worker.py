"""Offline Seed-VC singing conversion from a supplied or locally generated performance."""
from pathlib import Path
import argparse
import contextlib
import json
import os
import sys
import tempfile
import time
import traceback

def main():
    for stream in (sys.stdin,sys.stdout,sys.stderr):stream.reconfigure(encoding='utf-8')
    config=json.loads(sys.argv[1])
    source_root=Path(config['source_root']).resolve()
    models=Path(config['models_root']).resolve()
    source=Path(config['input_audio']).resolve()
    reference=Path(config['reference_audio']).resolve()
    output=Path(config['output_audio']).resolve()
    steps=int(config.get('diffusion_steps',40))
    semitones=int(config.get('semitones',0))
    if not 20<=steps<=60 or not -12<=semitones<=12:raise ValueError('Invalid singing conversion parameters')
    os.environ['HF_HUB_OFFLINE']='1'
    os.environ['TRANSFORMERS_OFFLINE']='1'
    os.environ['TOKENIZERS_PARALLELISM']='false'
    sys.path.insert(0,str(source_root))
    os.chdir(source_root)
    started=time.monotonic()
    try:
        with contextlib.redirect_stdout(sys.stderr), contextlib.ExitStack() as cleanup:
            import numpy as np
            import soundfile as sf
            import torch
            import yaml
            torch.set_num_threads(max(1,min(8,int(config.get('cpu_threads',6)))))
            if not torch.cuda.is_available():raise RuntimeError('A CUDA GPU is required for this local profile')
            torch.cuda.set_per_process_memory_fraction(max(.1,min(.95 if config.get('performance_mode')=='full' else .75,float(config.get('gpu_memory_fraction',.55)))),0)
            free,_=torch.cuda.mem_get_info()
            if free<int(config.get('min_free_vram_mib',6144))*1024**2:raise RuntimeError('Available GPU memory is below the configured audio budget')
            original_load=torch.load
            def restricted_load(*args,**kwargs):
                kwargs['weights_only']=True
                return original_load(*args,**kwargs)
            torch.load=restricted_load
            def local_weight(repo_id,model_filename='pytorch_model.bin',config_filename=None):
                folder=models/repo_id.replace('/','--')
                file=folder/model_filename
                if not file.is_file():raise FileNotFoundError(f'Model is not prepared: {repo_id}/{model_filename}')
                if config_filename is None:return str(file)
                companion=folder/config_filename
                if not companion.is_file():raise FileNotFoundError(str(companion))
                return str(file),str(companion)
            import hf_utils
            hf_utils.load_custom_model_from_hf=local_weight
            import inference
            measured_models={};stability_metrics={}
            if config.get('measure_voice',False) or config.get('pitch_stability')=='steady':
                original_loader=inference.load_models
                def measured_loader(args):
                    loaded=original_loader(args)
                    raw_f0=loaded[2];measured_models.update(f0=raw_f0,speaker=loaded[4]);calls=[0]
                    def guided_f0(wave,**kwargs):
                        values=raw_f0(wave,**kwargs);calls[0]+=1
                        # Upstream extracts reference first and source second. Measurements keep raw F0.
                        if calls[0]==2 and config.get('pitch_stability')=='steady':
                            from singing_performance import steady_f0
                            values,info=steady_f0(values,len(wave)/16000,config.get('score_phrases'));stability_metrics.update(info)
                        return values
                    result=list(loaded);result[2]=guided_f0;return tuple(result)
                inference.load_models=measured_loader
            seed=models/'Plachta--Seed-VC'
            checkpoint=seed/'DiT_seed_v2_uvit_whisper_base_f0_44k_bigvgan_pruned_ft_ema_v2.pth'
            base=yaml.safe_load((seed/'config_dit_mel_seed_uvit_whisper_base_f0_44k.yml').read_text(encoding='utf-8'))
            base['model_params']['vocoder']['name']=str(models/'nvidia--bigvgan_v2_44khz_128band_512x')
            base['model_params']['speech_tokenizer']['name']=str(models/'openai--whisper-small')
            output.parent.mkdir(parents=True,exist_ok=True)
            temporary=Path(cleanup.enter_context(tempfile.TemporaryDirectory(
                prefix='inference-',dir=config.get('work_dir',str(output.parent)))))
            local_config=temporary/'inference.yml'
            local_config.write_text(yaml.safe_dump(base),encoding='utf-8')
            try:
                source_data,source_sr=sf.read(source,dtype='float32',always_2d=True)
            except Exception:
                # Phone recordings commonly use M4A; decode in-process, without a system FFmpeg install.
                import av
                frames=[]
                with av.open(str(source)) as container:
                    resampler=av.AudioResampler(format='fltp',layout='mono',rate=44100)
                    for frame in container.decode(audio=0):
                        for converted in resampler.resample(frame):frames.append(converted.to_ndarray())
                    for converted in resampler.resample(None):frames.append(converted.to_ndarray())
                if not frames:raise ValueError('Input contains no decodable audio')
                source_data=np.concatenate(frames,axis=1).T
                source_sr=44100
            source_seconds=len(source_data)/source_sr
            if not 1<=source_seconds<=600:raise ValueError('Input must be between 1 second and 10 minutes')
            if not np.isfinite(source_data).all() or np.sqrt(np.mean(source_data**2))<1e-5:
                raise ValueError('Input is empty, silent, or invalid')
            decoded_source=temporary/'source.wav'
            sf.write(decoded_source,source_data,source_sr,subtype='PCM_16')
            del source_data
            ref_data,ref_sr=sf.read(reference,dtype='float32',always_2d=True)
            if not 1<=len(ref_data)/ref_sr<=30:raise ValueError('Reference must be between 1 and 30 seconds')
            del ref_data
            args=argparse.Namespace(source=str(decoded_source),target=str(reference),output=str(temporary),
                diffusion_steps=steps,length_adjust=1.0,inference_cfg_rate=float(config.get('inference_cfg_rate',.7)),f0_condition=True,
                auto_f0_adjust=False,semi_tone_shift=semitones,checkpoint=str(checkpoint),
                config=str(local_config),fp16=True)
            torch.manual_seed(int(config.get('seed',20261021)))
            torch.cuda.reset_peak_memory_stats()
            inference.main(args)
            generated=list(temporary.glob('vc_*.wav'))
            if len(generated)!=1:raise RuntimeError('Singing inference did not return one audio file')
            audio,sr=sf.read(generated[0],dtype='float32',always_2d=True)
            if not np.isfinite(audio).all() or np.sqrt(np.mean(audio**2))<1e-5:
                raise RuntimeError('Converted audio is silent or invalid')
            peak=float(np.abs(audio).max())
            if peak>.95:audio*=.95/peak
            sf.write(output,audio,sr,subtype='PCM_16')
            voice_metrics=None
            if measured_models:
                import torchaudio
                def load_16k(path):
                    samples,rate=sf.read(path,dtype='float32',always_2d=True)
                    wave=torch.from_numpy(samples.mean(axis=1)).to('cuda')[None]
                    return torchaudio.functional.resample(wave,rate,16000)
                def speaker_embedding(wave):
                    feat=torchaudio.compliance.kaldi.fbank(wave,num_mel_bins=80,dither=0,sample_frequency=16000)
                    feat-=feat.mean(dim=0,keepdim=True)
                    return measured_models['speaker'](feat[None])
                with torch.inference_mode():
                    source_wave,reference_wave,output_wave=[load_16k(p) for p in [source,reference,output]]
                    se,re,oe=[speaker_embedding(w) for w in [source_wave,reference_wave,output_wave]]
                    before=float(torch.nn.functional.cosine_similarity(se,re).item())
                    after=float(torch.nn.functional.cosine_similarity(oe,re).item())
                    f0a=measured_models['f0'](source_wave[0],thred=.03)
                    f0b=measured_models['f0'](output_wave[0],thred=.03)
                    count=min(len(f0a),len(f0b));a=np.asarray(f0a[:count]);b=np.asarray(f0b[:count]);mask=(a>50)&(b>50)
                    cents=np.abs(1200*np.log2(b[mask]/a[mask]))
                voice_metrics={'speaker_embedding':'CAMPPlus; relative similarity only, not proof of character identity',
                    'reference_similarity_before':round(before,4),'reference_similarity_after':round(after,4),
                    'similarity_improvement':round(after-before,4),'mutually_voiced_frames':int(mask.sum()),
                    'pitch_median_absolute_cents':round(float(np.median(cents)),2) if len(cents) else None,
                    'pitch_p90_absolute_cents':round(float(np.percentile(cents,90)),2) if len(cents) else None,
                    'duration_difference_seconds':round(abs(len(audio)/sr-source_seconds),4)}
            result={'ok':True,'source_seconds':round(source_seconds,3),'output_seconds':round(len(audio)/sr,3),
                'sample_rate':sr,'elapsed_seconds':round(time.monotonic()-started,3),
                'peak_allocated_mib':round(torch.cuda.max_memory_allocated()/1024**2,1),
                'peak_reserved_mib':round(torch.cuda.max_memory_reserved()/1024**2,1),
                'f0_condition':True,'auto_f0_adjust':False,'length_adjust':1.0,'semitones':semitones,
                'diffusion_steps':steps,'inference_cfg_rate':args.inference_cfg_rate,'voice_metrics':voice_metrics,
                'pitch_stability':stability_metrics,
                'audio_kind':'dry_vocal_conversion','cloud_called':False}
        print(json.dumps(result),flush=True)
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({'ok':False,'error':str(exc)}),flush=True)
        raise SystemExit(1)

if __name__=='__main__':main()
