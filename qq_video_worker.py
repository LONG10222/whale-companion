"""One-shot local video decoder: prefer NVIDIA hardware, then fall back to CPU."""
from pathlib import Path
import json,math,sys

def decode_video(source,folder,limit=120,max_frames=16,backend='cpu'):
    import av
    import numpy as np
    import soundfile as sf
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    options={'protocol_whitelist':'file,pipe'}
    accelerator=av.codec.hwaccel.HWAccel(device_type='cuda',allow_software_fallback=False) if backend=='cuda' else None
    with av.open(str(source),options=options,hwaccel=accelerator) as container:
        if not set(container.format.name.split(',')) & {'mov','mp4','matroska','webm','avi','mpeg','mpegts','flv','3gp'}:
            raise ValueError('暂不支持这种视频容器。')
        if not container.streams.video:raise ValueError('文件没有可读取的视频画面。')
        stream=container.streams.video[0];stream.codec_context.thread_count=2
        if stream.width*stream.height>20_000_000:raise ValueError('视频分辨率过大，请压缩后再发。')
        duration=float(stream.duration*stream.time_base) if stream.duration else (float(container.duration/av.time_base) if container.duration else 0)
        if not math.isfinite(duration) or duration<=0:raise ValueError('无法确认视频时长，请换成普通MP4。')
        covered=min(duration,float(limit));count=min(max_frames,max(2,math.ceil(covered*1.5)))
        targets=[i*max(0,covered-.15)/(count-1) for i in range(count)]
        start=float(stream.start_time*stream.time_base) if stream.start_time else 0
        frames=[]
        for i,target in enumerate(targets):
            container.seek(int((target+start)/float(stream.time_base)),stream=stream,backward=True,any_frame=False)
            last=None;chosen=None
            for frame in container.decode(stream):
                if frame.pts is None:continue
                last=frame
                if float(frame.pts*stream.time_base)-start+1e-5>=target:chosen=frame;break
            chosen=chosen or last
            if chosen is None:continue
            image=chosen.to_image();image.thumbnail((1000,1000));path=folder/f'frame-{i:02d}.jpg';image.save(path,quality=87)
            frames.append({'path':str(path),'seconds':round(max(0,float(chosen.pts*stream.time_base)-start),3)})
        has_audio=bool(container.streams.audio)
        hardware_used=bool(stream.codec_context.is_hwaccel)
    if not frames:raise ValueError('没有抽取到视频画面。')
    audio_file=None;audio_error=None
    if has_audio:
        try:
            with av.open(str(source),options=options) as container:
                stream=container.streams.audio[0];stream.codec_context.thread_count=2
                resampler=av.AudioResampler(format='fltp',layout='mono',rate=16000);chunks=[];samples=0;maximum=round(covered*16000)
                for frame in container.decode(stream):
                    for converted in resampler.resample(frame):
                        values=converted.to_ndarray().reshape(-1);chunks.append(values);samples+=len(values)
                    if samples>=maximum:break
                if chunks:
                    data=np.concatenate(chunks)[:maximum]
                    if float(np.sqrt(np.mean(data.astype(np.float64)**2)))>1e-5:
                        audio_file=folder/'speech.wav';sf.write(audio_file,data,16000,subtype='PCM_16')
        except Exception:audio_error='音轨解码失败，仅能读取画面。'
    return {'duration_seconds':round(duration,3),'analyzed_seconds':round(covered,3),'truncated':duration>limit,
            'frames':frames,'has_audio':has_audio,'audio_file':str(audio_file) if audio_file else None,'audio_error':audio_error,
            'decode_backend':backend,'hardware_used':hardware_used}

def decode_request(cfg):
    backend=cfg.get('backend','auto');fallback=False
    try:
        result=decode_video(cfg['source'],cfg['folder'],cfg.get('limit_seconds',120),cfg.get('max_frames',16),'cuda' if backend=='auto' else backend)
        if backend=='auto' and not result['hardware_used']:fallback=True
    except Exception:
        if backend!='auto':raise
        fallback=True
    if fallback:result=decode_video(cfg['source'],cfg['folder'],cfg.get('limit_seconds',120),cfg.get('max_frames',16),'cpu')
    return {'ok':True,**result,'automatic_cpu_fallback':fallback}

if __name__=='__main__':
    for s in [sys.stdout,sys.stderr]:s.reconfigure(encoding='utf-8')
    try:
        cfg=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
        print(json.dumps(decode_request(cfg),ensure_ascii=False),flush=True)
    except Exception as exc:
        print(json.dumps({'ok':False,'error':str(exc)[:300]},ensure_ascii=False),flush=True);raise SystemExit(1)
