"""Transcribe short video speech with the existing SenseVoice int8 CPU model, then exit."""
from pathlib import Path
import contextlib,json,sys

def transcribe(cfg):
    import soundfile as sf
    import numpy as np
    import sherpa_onnx
    folder=Path(cfg['model']);data,sr=sf.read(cfg['audio'],dtype='float32',always_2d=True);data=data.mean(axis=1)
    recognizer=sherpa_onnx.OfflineRecognizer.from_sense_voice(model=str(folder/'model.int8.onnx'),tokens=str(folder/'tokens.txt'),
        num_threads=4,use_itn=True,language='auto',provider='cpu')
    parts=[];chunk=sr*25
    for offset in range(0,len(data),chunk):
        audio=data[offset:offset+chunk]
        if not len(audio) or float(np.sqrt(np.mean(audio**2)))<1e-5:continue
        stream=recognizer.create_stream();stream.accept_waveform(sr,audio);recognizer.decode_stream(stream)
        text=stream.result.text.strip()
        if text:parts.append({'from_seconds':round(offset/sr,2),'to_seconds':round(min(offset+chunk,len(data))/sr,2),'text':text[:1000]})
    return parts

if __name__=='__main__':
    for s in [sys.stdout,sys.stderr]:s.reconfigure(encoding='utf-8')
    try:
        cfg=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
        with contextlib.redirect_stdout(sys.stderr):parts=transcribe(cfg)
        print(json.dumps({'ok':True,'parts':parts,'backend':'SenseVoice int8 CPU'},ensure_ascii=False),flush=True)
    except Exception as exc:
        print(json.dumps({'ok':False,'error':str(exc)[:200]},ensure_ascii=False),flush=True);raise SystemExit(1)
