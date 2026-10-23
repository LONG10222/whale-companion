"""Independent local audio review; it receives audio without the expected lyrics."""
from pathlib import Path
import contextlib,json,os,re,sys,time,traceback

PROMPT='''请审核这段人声音频。仅依据实际听到的内容，不要猜歌名，不要根据熟悉的诗词补全文字。重点找问题，不要为了鼓励而打高分。
检查：字音是否清楚、是否吞字或重复，音高和换气是否连贯，情绪是否有自然变化，是否有机械抖动、金属感、颤音过量或变声失真。
输出一个JSON对象，字段为 transcription（逐字写实际听到的内容，听不清写?），is_singing（布尔），naturalness_1to5，emotional_expression_1to5，pronunciation_1to5，artifacts（字符串列表），problems（字符串列表），verdict（reject或candidate）。
candidate要求自然度至少4、发音至少4、有自然情绪，并且没有明显机械或断裂问题。有疑问时选reject。只输出JSON，不输出推理过程。'''

def main():
    for stream in (sys.stdin,sys.stdout,sys.stderr):stream.reconfigure(encoding='utf-8')
    cfg=json.loads(sys.argv[1]);started=time.monotonic()
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false')
    try:
        with contextlib.redirect_stdout(sys.stderr):
            import numpy as np,soundfile as sf,torch,librosa
            from transformers import Qwen2_5OmniForConditionalGeneration,Qwen2_5OmniProcessor,AutoConfig
            torch.set_num_threads(max(1,min(8,int(cfg.get('cpu_threads',6)))))
            if not torch.cuda.is_available():raise RuntimeError('CUDA is required for this audio reviewer')
            if torch.cuda.mem_get_info()[0]<10*1024**3:raise RuntimeError('Audio review needs 10 GiB free VRAM')
            torch.cuda.set_per_process_memory_fraction(.70,0)
            model_dir=Path(cfg['model_dir'])
            model_config=AutoConfig.from_pretrained(model_dir,local_files_only=True)
            model_config.enable_audio_output=False
            processor=Qwen2_5OmniProcessor.from_pretrained(model_dir,local_files_only=True)
            model=Qwen2_5OmniForConditionalGeneration.from_pretrained(model_dir,config=model_config,
                torch_dtype=torch.bfloat16,attn_implementation='sdpa',local_files_only=True,
                device_map={'thinker.audio_tower':0,'thinker.visual':'cpu','thinker.model':0,'thinker.lm_head':0})
            model.eval()
            reviews=[]
            for item in cfg['audios']:
                paths=item.get('paths',[item.get('path')]);audios=[];content=[]
                for index,path in enumerate(paths):
                    samples,sr=sf.read(path,dtype='float32',always_2d=True)
                    audio=librosa.resample(samples.mean(axis=1),orig_sr=sr,target_sr=16000)
                    if len(audio)>16000*40:raise ValueError('Review clips must be at most 40 seconds')
                    audios.append(audio)
                    if len(paths)>1:content.append({'type':'text','text':'音频'+chr(65+index)+'：'})
                    content.append({'type':'audio','audio':str(path)})
                if sum(map(len,audios))>16000*70:raise ValueError('Combined review audio is too long')
                content.append({'type':'text','text':item.get('prompt',PROMPT)})
                conversation=[{'role':'system','content':[{'type':'text','text':'你是严格的音频质量审核员，只依据音频判断，不美化结果。'}]},
                    {'role':'user','content':content}]
                text=processor.apply_chat_template(conversation,add_generation_prompt=True,tokenize=False)
                inputs=processor(text=text,audio=audios,return_tensors='pt',padding=True)
                inputs=inputs.to('cuda').to(torch.bfloat16)
                torch.manual_seed(20261021)
                with torch.inference_mode():
                    ids=model.generate(**inputs,return_audio=False,thinker_max_new_tokens=550,
                        thinker_do_sample=False,use_audio_in_video=False)
                generated=ids[:,inputs['input_ids'].shape[1]:]
                output=processor.batch_decode(generated,skip_special_tokens=True,clean_up_tokenization_spaces=False)[0]
                try:review=json.loads(output[output.index('{'):output.rindex('}')+1])
                except (ValueError,KeyError):review={'verdict':'reject','problems':['Reviewer did not return structured results'],'raw_output':output}
                reviews.append({'name':item['name'],'review':review})
                print(json.dumps({'progress':item['name'],'review':review},ensure_ascii=False),file=sys.stderr,flush=True)
            result={'ok':True,'reviewer':'Qwen2.5-Omni-3B official thinker, audio-only, no lyric prompt',
                'reviews':reviews,'elapsed_seconds':round(time.monotonic()-started,3),
                'peak_allocated_mib':round(torch.cuda.max_memory_allocated()/1024**2,1),
                'automated_review_only':True,'human_acceptance_not_implied':True}
        print(json.dumps(result,ensure_ascii=False),flush=True)
    except Exception as exc:
        traceback.print_exc(file=sys.stderr);print(json.dumps({'ok':False,'error':str(exc)}),flush=True);raise SystemExit(1)

if __name__=='__main__':main()
