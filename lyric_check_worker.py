"""One-shot local ASR check; expected lyrics are never passed to the recognizer."""
from pathlib import Path
import contextlib,json,os,sys,time,traceback

def main():
    for stream in (sys.stdin,sys.stdout,sys.stderr):stream.reconfigure(encoding='utf-8')
    cfg=json.loads(sys.argv[1]);started=time.monotonic()
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false')
    try:
        with contextlib.redirect_stdout(sys.stderr):
            from lyrics_quality import assess
            import soundfile as sf
            if cfg.get('asr_backend')=='sensevoice':
                import sherpa_onnx
                folder=Path(cfg['asr_model'])
                recognizer=sherpa_onnx.OfflineRecognizer.from_sense_voice(model=str(folder/'model.int8.onnx'),
                    tokens=str(folder/'tokens.txt'),num_threads=max(1,min(8,int(cfg.get('cpu_threads',6)))),use_itn=True,language=cfg.get('language','zh'),provider='cpu')
                data,sr=sf.read(cfg['input_audio'],dtype='float32',always_2d=True)
                stream=recognizer.create_stream();stream.accept_waveform(sr,data.mean(axis=1))
                recognizer.decode_stream(stream);transcript=stream.result.text
                recognizer_name='SenseVoice int8 CPU, no lyric prompt';peak_gpu=0
            else:
                import torch,librosa
                from transformers import WhisperProcessor,WhisperForConditionalGeneration
                torch.set_num_threads(max(1,min(8,int(cfg.get('cpu_threads',6)))));torch.cuda.set_per_process_memory_fraction(.25,0)
                processor=WhisperProcessor.from_pretrained(cfg['asr_model'],local_files_only=True)
                model=WhisperForConditionalGeneration.from_pretrained(cfg['asr_model'],local_files_only=True,
                    torch_dtype=torch.float16).eval().to('cuda')
                data,sr=sf.read(cfg['input_audio'],dtype='float32',always_2d=True)
                audio=librosa.resample(data.mean(axis=1),orig_sr=sr,target_sr=16000)
                features=processor(audio,sampling_rate=16000,return_tensors='pt',return_attention_mask=True,truncation=False)
                model.generation_config.forced_decoder_ids=None
                with torch.inference_mode():
                    tokens=model.generate(input_features=features.input_features.to('cuda',dtype=torch.float16),
                        attention_mask=features.attention_mask.to('cuda'),language=cfg.get('language','zh'),
                        task='transcribe',do_sample=False,max_new_tokens=250,return_timestamps=True)
                transcript=processor.batch_decode(tokens,skip_special_tokens=True)[0]
                recognizer_name='Whisper-small local, no lyric prompt';peak_gpu=round(torch.cuda.max_memory_allocated()/1024**2,1)
            result=assess(cfg['lyrics'],transcript)
            result.update(ok=True,elapsed_seconds=round(time.monotonic()-started,3),
                recognizer=recognizer_name,peak_allocated_mib=peak_gpu)
        print(json.dumps(result,ensure_ascii=False),flush=True)
    except Exception as exc:
        traceback.print_exc(file=sys.stderr);print(json.dumps({'ok':False,'error':str(exc)}),flush=True);raise SystemExit(1)

if __name__=='__main__':main()
