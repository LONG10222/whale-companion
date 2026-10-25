"""Official SoulX-Singer with typed score, isolated dependency overlay and bounded GPU use."""
from pathlib import Path
import contextlib,json,os,sys,time,traceback

def main():
    for stream in (sys.stdin,sys.stdout,sys.stderr):stream.reconfigure(encoding='utf-8')
    cfg=json.loads(sys.argv[1]);started=time.monotonic()
    sys.path.insert(0,cfg['dependency_overlay']);sys.path.insert(0,cfg['source_root'])
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false')
    try:
        with contextlib.redirect_stdout(sys.stderr):
            import numpy as np,torch,soundfile as sf
            from soulxsinger.utils.file_utils import load_config
            from soulxsinger.utils.data_processor import DataProcessor
            from cli.inference import build_model
            torch.set_num_threads(max(1,min(8,int(cfg.get('cpu_threads',6)))))
            if not torch.cuda.is_available() or torch.cuda.mem_get_info()[0]<int(cfg.get('min_free_vram_mib',6144))*1024**2:
                raise RuntimeError('SoulX歌声合成需要至少6GiB空闲显存')
            torch.cuda.set_per_process_memory_fraction(max(.1,min(.95 if cfg.get('performance_mode')=='full' else .75,float(cfg.get('gpu_memory_fraction',.65)))),0)
            seed=cfg.get('seed',20261021);torch.manual_seed(seed);np.random.seed(seed)
            source=Path(cfg['source_root']);config=load_config(str(source/'soulxsinger/config/soulxsinger.yaml'))
            model=build_model(cfg['model_path'],config,device='cuda',use_fp16=True)
            processor=DataProcessor(hop_size=480,sample_rate=24000,phoneset_path=cfg['phone_set'],device='cuda')
            prompt=json.loads(Path(cfg['prompt_metadata']).read_text(encoding='utf-8'))[0]
            prompt_data=processor.process(prompt,cfg['prompt_audio'])
            meta=json.loads(Path(cfg['target_metadata']).read_text(encoding='utf-8'));target_data=processor.process(meta,None)
            with torch.inference_mode():
                generated=model.infer({'prompt':prompt_data,'target':target_data},auto_shift=False,pitch_shift=0,
                    n_steps=32,cfg=3,control='score',use_fp16=True)
            audio=generated.squeeze().float().cpu().numpy()
            if not np.isfinite(audio).all() or abs(audio).max()<1e-5:raise RuntimeError('SoulX音频为静音或存在无效采样')
            peak=float(np.abs(audio).max())
            if peak>.95:audio*=.95/peak
            sf.write(cfg['output_audio'],audio,24000,subtype='PCM_16')
            result={'ok':True,'engine':'SoulX-Singer official score control','voice':cfg['voice_name'],
                'sample_rate':24000,'seconds':round(len(audio)/24000,3),'elapsed_seconds':round(time.monotonic()-started,3),
                'peak_allocated_mib':round(torch.cuda.max_memory_allocated()/1024**2,1),'voice_conversion':False,
                'reference':'official SoulX-Singer repository zh_prompt','explicit_phonemes_and_notes':True,
                'per_phrase_expression_applied':False,'human_naturalness_verified':False,'cloud_called':False}
        print(json.dumps(result,ensure_ascii=False),flush=True)
    except Exception as exc:
        traceback.print_exc(file=sys.stderr);print(json.dumps({'ok':False,'error':str(exc)}),flush=True);raise SystemExit(1)

if __name__=='__main__':main()
