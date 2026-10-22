"""One-shot bounded voice decoding and local ASR. No resident GPU model."""
from pathlib import Path
import contextlib,json,sys,time

MAX_SECONDS=120

def decode(source,folder):
    import av,numpy as np,soundfile as sf
    source=Path(source);folder=Path(folder);out=folder/'speech.wav'
    with source.open('rb') as f:header=f.read(16)
    if header.startswith((b'#!SILK_V3',b'\x02#!SILK_V3')):
        raise ValueError('QQ这次没有把语音转成可播放格式，请重新发送这条语音。')
    else:
        chunks=[];count=0
        with av.open(str(source),options={'protocol_whitelist':'file,pipe'}) as container:
            if not set(container.format.name.split(',')) & {'wav','mp3','ogg','amr','aac','flac','mov','mp4','m4a','3gp','3g2','mj2','matroska','webm'}:raise ValueError('这种语音格式暂时读不了。')
            if not container.streams.audio:raise ValueError('没有读到语音音轨。')
            stream=container.streams.audio[0];stream.codec_context.thread_count=2
            resample=av.AudioResampler(format='fltp',layout='mono',rate=16000)
            for frame in container.decode(stream):
                for converted in resample.resample(frame):
                    part=converted.to_ndarray().reshape(-1);count+=len(part)
                    if count>MAX_SECONDS*16000:raise ValueError('这条语音超过两分钟，请分段发送。')
                    chunks.append(part)
            audio=np.concatenate(chunks) if chunks else np.array([],dtype=np.float32)
    if not len(audio) or not np.isfinite(audio).all() or float(np.sqrt(np.mean(audio.astype(np.float64)**2)))<1e-5:raise ValueError('这条语音没有听清，可以靠近麦克风再说一次吗？')
    sf.write(out,audio,16000,subtype='PCM_16')
    return out,round(len(audio)/16000,3)

def run(cfg):
    from qq_video_asr import transcribe
    started=time.monotonic();audio,seconds=decode(cfg['source'],cfg['folder'])
    parts=transcribe({'model':cfg['model'],'audio':str(audio),'threads':2})
    text=''.join(p['text'] for p in parts).strip()
    if not text:raise ValueError('这条语音没有听清，可以再说一次吗？')
    return {'ok':True,'text':text[:3000],'seconds':seconds,'backend':'SenseVoice int8 CPU','gpu_mib':0,'elapsed_seconds':round(time.monotonic()-started,3)}

if __name__=='__main__':
    for s in [sys.stdout,sys.stderr]:s.reconfigure(encoding='utf-8')
    try:
        cfg=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
        with contextlib.redirect_stdout(sys.stderr):result=run(cfg)
        print(json.dumps(result,ensure_ascii=False),flush=True)
    except Exception as exc:
        print(json.dumps({'ok':False,'error':str(exc)[:200]},ensure_ascii=False),flush=True);raise SystemExit(1)
