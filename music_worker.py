"""One-shot offline ACE-Step generation; process exit releases its GPU resources."""
from pathlib import Path
import contextlib,json,os,random,sys,time,traceback

def main():
    for stream in (sys.stdin,sys.stdout,sys.stderr):stream.reconfigure(encoding='utf-8')
    cfg=json.loads(sys.argv[1]);started=time.monotonic()
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false',
        ACESTEP_CHECKPOINTS_DIR=cfg['models_root'],ACESTEP_PROJECT_ROOT=cfg['source_root'])
    sys.path.insert(0,cfg['source_root'])
    try:
        with contextlib.redirect_stdout(sys.stderr):
            import torch
            torch.set_num_threads(max(1,min(8,int(cfg.get('cpu_threads',6)))))
            if not torch.cuda.is_available():raise RuntimeError('Local music generation requires CUDA')
            if torch.cuda.mem_get_info()[0]<int(cfg.get('min_free_vram_mib',8192))*1024**2:raise RuntimeError('Music generation needs more free GPU memory')
            torch.cuda.set_per_process_memory_fraction(max(.1,min(.75,float(cfg.get('gpu_memory_fraction',.65)))),0)
            import acestep.model_downloader as downloader
            use_lm=cfg.get('use_music_lm',True)
            downloader.MAIN_MODEL_COMPONENTS=['acestep-v15-turbo','vae','Qwen3-Embedding-0.6B']
            if use_lm:
                if not (Path(cfg['models_root'])/'model-manifest-lm.json').is_file():
                    raise RuntimeError('The music language model is not fully prepared')
                downloader.MAIN_MODEL_COMPONENTS.append('acestep-5Hz-lm-1.7B')
            from acestep.handler import AceStepHandler
            from acestep.inference import GenerationParams,GenerationConfig,generate_music
            handler=AceStepHandler()
            status,ok=handler.initialize_service(project_root=cfg['source_root'],config_path='acestep-v15-turbo',
                device='cuda',use_flash_attention=False,compile_model=False,offload_to_cpu=True,
                offload_dit_to_cpu=True,quantization=None,use_mlx_dit=False)
            if not ok:raise RuntimeError(status)
            lm=None
            if use_lm:
                from acestep.llm_inference import LLMHandler
                lm=LLMHandler()
                status,ok=lm.initialize(checkpoint_dir=cfg['models_root'],lm_model_path='acestep-5Hz-lm-1.7B',
                    backend='pt',device='cuda',offload_to_cpu=True,dtype=torch.bfloat16)
                if not ok:raise RuntimeError(status)
            params=GenerationParams(caption=cfg['caption'],lyrics=cfg['lyrics'],duration=float(cfg['duration']),
                vocal_language=cfg.get('language','zh'),bpm=int(cfg.get('bpm',80)),
                seed=int(cfg.get('seed',20261021)),inference_steps=8,thinking=use_lm,instrumental=False,
                use_cot_metas=cfg.get('lm_cot',True),use_cot_caption=cfg.get('lm_cot',True),
                use_cot_language=cfg.get('lm_cot',True),dcw_enabled=False,
                lm_temperature=.75,lm_cfg_scale=2.0)
            config=GenerationConfig(batch_size=1,use_random_seed=False,seeds=[params.seed],audio_format='wav')
            # The upstream single-item PyTorch LM path does not apply its seeds argument.
            import numpy as np
            random.seed(params.seed);np.random.seed(params.seed)
            torch.manual_seed(params.seed);torch.cuda.manual_seed_all(params.seed)
            torch.cuda.reset_peak_memory_stats()
            output=generate_music(handler,lm,params,config,save_dir=cfg['work_dir'])
            if not output.success:raise RuntimeError(output.error)
            import soundfile as sf
            audio,sr=sf.read(output.audios[0]['path'],dtype='float32',always_2d=True)
            if not np.isfinite(audio).all() or np.sqrt(np.mean(audio**2))<1e-5:
                raise RuntimeError('Music generation returned silent or invalid audio')
            target=Path(cfg['output_audio']);target.parent.mkdir(parents=True,exist_ok=True)
            sf.write(target,audio,sr,subtype='PCM_16')
            result={'ok':True,'sample_rate':sr,'seconds':round(len(audio)/sr,3),
                'elapsed_seconds':round(time.monotonic()-started,3),
                'peak_allocated_mib':round(torch.cuda.max_memory_allocated()/1024**2,1),
                'peak_reserved_mib':round(torch.cuda.max_memory_reserved()/1024**2,1),
                'engine':'ACE-Step 1.5 2B turbo','extra_music_lm':use_lm,
                'music_lm':'acestep-5Hz-lm-1.7B' if use_lm else None,'cloud_audio_called':False,
                'seed':params.seed,'global_rng_seeded':True,
                'exact_original_melody_guaranteed':False,'acappella_requested':False,
                'instrumental_absence_verified':False}
        print(json.dumps(result),flush=True)
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({'ok':False,'error':str(exc)}),flush=True)
        raise SystemExit(1)

if __name__=='__main__':main()
