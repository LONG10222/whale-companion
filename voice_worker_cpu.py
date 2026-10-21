"""Offline Kokoro int8 synthesis on CPU. This worker never imports CUDA/Torch."""
from pathlib import Path
import contextlib
import json
import sys
import time
import traceback

sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

def reply(data):
    print(json.dumps(data),flush=True)

def main():
    config=json.loads(sys.argv[1])
    root=Path(config['kokoro_model_dir'])
    started=time.monotonic()
    try:
        with contextlib.redirect_stdout(sys.stderr):
            import numpy as np
            import psutil
            import sherpa_onnx
            import soundfile as sf
            model=sherpa_onnx.OfflineTtsKokoroModelConfig(
                model=str(root/'model.int8.onnx'),voices=str(root/'voices.bin'),
                tokens=str(root/'tokens.txt'),data_dir=str(root/'espeak-ng-data'),
                dict_dir=str(root/'dict'),lexicon=','.join(str(root/f) for f in ['lexicon-zh.txt','lexicon-us-en.txt']),lang='zh',
            )
            tts=sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(
                model=sherpa_onnx.OfflineTtsModelConfig(kokoro=model,num_threads=config['cpu_threads'],provider='cpu'),
                rule_fsts=','.join(str(root/f) for f in ['date-zh.fst','number-zh.fst','phone-zh.fst']),
            ))
        reply({'ready':True,'load_seconds':round(time.monotonic()-started,3),'speakers':['zh-3','zh-12']})
    except Exception as exc:
        traceback.print_exc(file=sys.stderr);reply({'ready':False,'error':str(exc)});return
    for line in sys.stdin:
        try:
            req=json.loads(line);started=time.monotonic()
            with contextlib.redirect_stdout(sys.stderr):
                generated=tts.generate(req['text'],sid=int(req['voice'].split('-')[1]),speed=1.0)
                audio=np.asarray(generated.samples,dtype=np.float32)
                duration=len(audio)/generated.sample_rate
                if not 0 < duration <= config['max_audio_seconds'] or not np.isfinite(audio).all():
                    raise ValueError('Invalid or overlong audio; shorten the text')
                if np.sqrt(np.mean(audio*audio))<0.0001:
                    raise ValueError('Generated audio is silent')
                sf.write(req['path'],audio,generated.sample_rate,subtype='PCM_16')
                info={'ok':True,'seconds':round(duration,3),'sample_rate':generated.sample_rate,
                    'generation_seconds':round(time.monotonic()-started,3),'gpu_memory_mib':0,
                    'process_memory_mib':round(psutil.Process().memory_info().rss/1024**2,1)}
                del audio,generated
            reply(info)
        except Exception as exc:
            traceback.print_exc(file=sys.stderr);reply({'ok':False,'error':str(exc)})

if __name__=='__main__':main()
