"""Offline vocal-stem extraction in a one-shot CUDA process."""
from pathlib import Path
import contextlib,json,sys,time,traceback

def main():
    for stream in (sys.stdin,sys.stdout,sys.stderr):stream.reconfigure(encoding='utf-8')
    cfg=json.loads(sys.argv[1]);started=time.monotonic()
    try:
        with contextlib.redirect_stdout(sys.stderr):
            import numpy as np
            import soundfile as sf
            import torch,torchaudio
            torch.set_num_threads(6)
            if not torch.cuda.is_available():raise RuntimeError('Vocal extraction requires CUDA')
            torch.cuda.set_per_process_memory_fraction(.45,0)
            model=torchaudio.models.hdemucs_high(sources=['drums','bass','other','vocals'])
            model.load_state_dict(torch.load(cfg['separation_model'],map_location='cpu',weights_only=True))
            model.eval().to('cuda')
            data,sr=sf.read(cfg['input_audio'],dtype='float32',always_2d=True)
            audio=torch.from_numpy(data.T)
            if audio.shape[0]==1:audio=audio.repeat(2,1)
            if audio.shape[0]!=2:raise ValueError('Source must be mono or stereo')
            if sr!=44100:audio=torchaudio.functional.resample(audio,sr,44100)
            audio=audio.to('cuda');length=audio.shape[-1]
            reference=audio.mean(0);mean=reference.mean();scale=reference.std().clamp_min(1e-6)
            normalized=(audio-mean)/scale
            window=44100*8;hop=44100*6
            vocals=torch.zeros_like(audio);weight=torch.zeros(length,device='cuda')
            torch.cuda.reset_peak_memory_stats()
            with torch.inference_mode():
                for start in range(0,length,hop):
                    end=min(start+window,length)
                    chunk=normalized[:,start:end]
                    prediction=model(chunk[None])[0,3]*scale+mean
                    taper=torch.ones(end-start,device='cuda')
                    overlap=window-hop
                    if start>0:
                        n=min(overlap,len(taper));taper[:n]=torch.linspace(.001,1,n,device='cuda')
                    if end<length:
                        n=min(overlap,len(taper));taper[-n:]=torch.linspace(1,.001,n,device='cuda')
                    vocals[:,start:end]+=prediction*taper
                    weight[start:end]+=taper
            vocals=vocals/weight.clamp_min(1e-6)
            if not torch.isfinite(vocals).all():raise RuntimeError('Invalid separated vocal')
            sf.write(cfg['output_audio'],vocals.T.cpu().numpy(),44100,subtype='PCM_16')
            result={'ok':True,'engine':'TorchAudio Hybrid Demucs','seconds':round(length/44100,3),'sample_rate':44100,
                'elapsed_seconds':round(time.monotonic()-started,3),'vocal_stem_isolated':True,
                'instrumental_absence_verified':False,'residual_music_possible':True,
                'peak_allocated_mib':round(torch.cuda.max_memory_allocated()/1024**2,1),
                'peak_reserved_mib':round(torch.cuda.max_memory_reserved()/1024**2,1),'cloud_audio_called':False}
        print(json.dumps(result),flush=True)
    except Exception as exc:
        traceback.print_exc(file=sys.stderr);print(json.dumps({'ok':False,'error':str(exc)}),flush=True);raise SystemExit(1)

if __name__=='__main__':main()
