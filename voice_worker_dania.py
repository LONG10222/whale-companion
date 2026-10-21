"""Character GPT-SoVITS worker: CPU residency, GPU only during synthesis."""
from pathlib import Path
import contextlib
import gc
import importlib
import json
import os
import sys
import time
import traceback

def reply(value):
    print(json.dumps(value,ensure_ascii=False),flush=True)

def limit_long_digital_pauses(audio,sample_rate):
    """Keep normal breaks; cap only internal exact-zero runs over 350 ms to 250 ms."""
    import numpy as np
    changes=np.diff(np.concatenate(([False],np.asarray(audio)==0,[False])).astype(np.int8))
    keep=round(sample_rate*.25)
    parts=[];cursor=0;removed=0
    for begin,end in zip(np.flatnonzero(changes==1),np.flatnonzero(changes==-1)):
        if begin==0 or end==len(audio) or end-begin<=round(sample_rate*.35):continue
        left=keep//2
        parts.append(audio[cursor:begin+left]);cursor=end-(keep-left)
        removed+=end-begin-keep
    if not removed:return audio,0.0
    parts.append(audio[cursor:])
    return np.concatenate(parts),removed/sample_rate

def main():
    for stream in (sys.stdin,sys.stdout,sys.stderr):stream.reconfigure(encoding='utf-8')
    config=json.loads(sys.argv[1])
    source=Path(config['dania_source_dir'])
    weights=Path(config['dania_model_dir'])
    os.chdir(source)
    sys.path[:0]=[str(source),str(source/'GPT_SoVITS')]
    os.environ['HF_HUB_OFFLINE']='1'
    os.environ['TRANSFORMERS_OFFLINE']='1'
    os.environ['TOKENIZERS_PARALLELISM']='false'
    os.environ['language']='en_US'
    os.environ['NLTK_DATA']=str(weights/'nltk_data')
    os.environ['PYTHONUTF8']='1'
    started=time.monotonic()
    try:
        with contextlib.redirect_stdout(sys.stderr):
            import numpy as np
            import soundfile as sf
            import torch
            import jieba
            import jieba.posseg
            sys.modules['jieba_fast']=jieba
            sys.modules['jieba_fast.posseg']=jieba.posseg
            from utils import HParams
            torch.serialization.add_safe_globals([HParams])
            torch.set_num_threads(config['cpu_threads'])
            # All supplied checkpoints have plain tensor/dict payloads. Never enable unrestricted pickle.
            original_load=torch.load
            def safe_load(*args,**kwargs):
                kwargs['weights_only']=True
                return original_load(*args,**kwargs)
            torch.load=safe_load
            module=importlib.import_module('TTS_infer_pack.TTS')
            params={'custom':{
                'bert_base_path':str(source/'GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large'),
                'cnhuhbert_base_path':str(source/'GPT_SoVITS/pretrained_models/chinese-hubert-base'),
                'device':'cpu','is_half':False,'version':'v2ProPlus',
                't2s_weights_path':str(weights/'dania-e15.ckpt'),
                'vits_weights_path':str(weights/'dania_e16_s2192.pth'),
            }}
            tts=module.TTS(params)
            if not torch.cuda.is_available():raise RuntimeError('CUDA is unavailable')
            torch.cuda.set_per_process_memory_fraction(config['gpu_memory_fraction'],0)
        reply({'ready':True,'load_seconds':round(time.monotonic()-started,3),'speakers':['dania']})
    except Exception as exc:
        traceback.print_exc(file=sys.stderr);reply({'ready':False,'error':str(exc)});return

    def move_tree(value,device):
        if torch.is_tensor(value):return value.to(device)
        if isinstance(value,dict):return {k:move_tree(v,device) for k,v in value.items()}
        if isinstance(value,list):return [move_tree(v,device) for v in value]
        if isinstance(value,tuple):return tuple(move_tree(v,device) for v in value)
        return value

    def move_to(device):
        tts.set_device(torch.device(device),save=False)
        tts.text_preprocessor.device=torch.device(device)
        if tts.sv_model is not None:
            tts.sv_model.embedding_model.to(device)
        tts.prompt_cache=move_tree(tts.prompt_cache,device)

    for line in sys.stdin:
        try:
            req=json.loads(line);start=time.monotonic()
            with contextlib.redirect_stdout(sys.stderr),torch.inference_mode():
                free,_=torch.cuda.mem_get_info()
                if free<config.get('min_free_vram_mib',5120)*1024**2:
                    raise RuntimeError('Not enough free VRAM for character speech')
                move_to('cuda')
                tts.enable_half_precision(True,save=False)
                tts.sv_model.embedding_model.half()
                tts.sv_model.is_half=True
                torch.cuda.reset_peak_memory_stats()
                # Short chat replies should retain one continuous prosody contour.
                # Per-punctuation splitting plus 300 ms padding made them sound interrupted.
                split_method='cut0' if len(req['text'])<=60 else 'cut2'
                fragment_interval=0.12
                # Calibrate this character's brisk default delivery to a normal chat pace.
                # Use the acoustic model's duration control, with natural pauses intact.
                speed_factor=0.85
                chunks=list(tts.run({
                    'text':req['text'],'text_lang':'all_zh',
                    'ref_audio_path':str(weights/'output.wav_0009342720_0009558400.wav'),
                    'prompt_text':config['dania_prompt_text'],'prompt_lang':'all_zh',
                    'top_k':15,'top_p':1,'temperature':0.8,'text_split_method':split_method,
                    'fragment_interval':fragment_interval,
                    'batch_size':1,'speed_factor':speed_factor,'parallel_infer':True,
                    'use_cuda_graph':False,'use_flash_attention':False,'seed':20261020,
                }))
                sr=chunks[0][0];audio=np.concatenate([x[1] for x in chunks])
                raw_duration=len(audio)/sr
                if audio.dtype==np.int16:audio=audio.astype(np.float32)/32768
                audio,trimmed_silence=limit_long_digital_pauses(audio,sr)
                duration=len(audio)/sr
                if not 0<duration<=config['max_audio_seconds'] or not np.isfinite(audio).all():
                    raise ValueError('Audio is invalid or too owner')
                if np.sqrt(np.mean(audio*audio))<0.0001:raise ValueError('Audio is silent')
                sf.write(req['path'],audio,sr,subtype='PCM_16')
                metrics={'ok':True,'seconds':round(duration,3),'sample_rate':sr,
                    'raw_seconds':round(raw_duration,3),'trimmed_zero_seconds':round(trimmed_silence,3),
                    'speed_factor':speed_factor,'normal_pace':True,'voiced_samples_preserved':True,
                    'text_split_method':split_method,'fragment_interval':fragment_interval,
                    'generation_seconds':round(time.monotonic()-start,3),
                    'peak_allocated_mib':round(torch.cuda.max_memory_allocated()/1024**2,1),
                    'peak_reserved_mib':round(torch.cuda.max_memory_reserved()/1024**2,1)}
                del chunks,audio
                offload_start=time.monotonic()
                move_to('cpu')
                module.resample_transform_dict.clear()
                module.resampy_filter_cache.clear()
                gc.collect();torch.cuda.empty_cache()
                metrics.update(offload_seconds=round(time.monotonic()-offload_start,3),
                    after_offload_allocated_mib=round(torch.cuda.memory_allocated()/1024**2,2),
                    after_offload_reserved_mib=round(torch.cuda.memory_reserved()/1024**2,2))
            reply(metrics)
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            # Failed calls exit so no GPU allocation can remain indefinitely.
            reply({'ok':False,'error':str(exc)})
            return

if __name__=='__main__':main()
