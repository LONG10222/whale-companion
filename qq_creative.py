"""Background singing, built-in image generation and scoped Codex work for owner."""
from pathlib import Path
from contextlib import contextmanager
import concurrent.futures,json,re,shutil,sqlite3,threading,time,uuid
import httpx
from chat_permissions import ChatPermissions
from codex_workbench import CodexWorkbench
from codex_rpc import validate_choice
from creative_tools import requested_tool,lookup_intent
from qq_identity import Contacts,choose,QUERY_POLICY

class CreativeJobs:
    def __init__(self,root,bridge,resources=None,workbench=None):
        self.root=Path(root);self.bridge=bridge;self.path=self.root/'data/qq/creative.sqlite3';self.path.parent.mkdir(parents=True,exist_ok=True)
        self.pool=concurrent.futures.ThreadPoolExecutor(max_workers=1,thread_name_prefix='whale-work');self.lock=threading.RLock();self.stop=threading.Event();self.cancels={}
        self.workbench=workbench or CodexWorkbench(root);self.permissions=ChatPermissions(root)
        self.contacts=Contacts(root)
        from qq_research import PublicResearch
        self.research=PublicResearch(root)
        from usage_ledger import Ledger
        self.ledger=Ledger(root)
        if resources is None:
            from local_learning import resource_snapshot
            resources=resource_snapshot
        from workspace_policy import WORKSPACE
        self.resources=resources;self.folder=WORKSPACE/'results';self.folder.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,peer TEXT,bot TEXT,mid TEXT,epoch TEXT,kind TEXT,args TEXT,state TEXT,at REAL,updated REAL,
            deliver INTEGER,delivery TEXT,result TEXT,error TEXT,thread_id TEXT DEFAULT '',directory TEXT DEFAULT '',model TEXT DEFAULT '',effort TEXT DEFAULT '');
            CREATE UNIQUE INDEX IF NOT EXISTS creative_origin ON jobs(bot,mid) WHERE mid!='';''')
            db.execute("UPDATE jobs SET state='interrupted',error='程序重启，任务未自动重做。' WHERE state IN ('accepted','running')")
            db.execute("UPDATE jobs SET delivery='uncertain' WHERE delivery='sending'")
            history=[dict(r) for r in db.execute('SELECT id,peer,kind,at,state,model,effort FROM jobs')]
        with self.ledger.db() as db:logged={r[0] for r in db.execute("SELECT event_key FROM events WHERE kind='historical_task'")}
        for row in history:
            key='history:'+row['id']
            if key not in logged:self.ledger.record('historical_task',event_key=key,at=row['at'],peer=row['peer'],job_id=row['id'],model=row['model'],effort=row['effort'],state=row['state'],details={'kind':row['kind'],'note':'从已有任务数据库补录；未返回的费用和Token不推算。'})
        bridge.chat.creative=self
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=12);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()
    def scope(self,peer=None,context=None):
        s=self.bridge.snapshot();cfg=self.bridge.cfg;who=str((s.get('test_contact') or {}).get('user_id',''))
        if who!=self.contacts.admin or cfg.get('locked_bot_id')!=s['bot_id']:raise ValueError('工作台账号绑定不匹配。')
        who=str(peer) if peer is not None else who
        if who not in s['allowed_private_ids']:raise ValueError('工作台联系人不在启用范围内。')
        if context and context.get('deliver') and (not s['enabled'] or not s['connected'] or not s['account_matches'] or context.get('bot')!=s['bot_id'] or context.get('epoch')!=self.bridge.epoch):raise ValueError('账号或回复范围已改变，任务已停止。')
        return who,s
    def update(self,jid,**values):
        allowed={'state','delivery','result','error','thread_id','directory','model','effort'}
        if set(values)-allowed:raise ValueError('Invalid job fields')
        values['updated']=time.time()
        with self.db() as db:db.execute('UPDATE jobs SET '+','.join(k+'=?' for k in values)+' WHERE id=?',list(values.values())+[jid])
    def recent(self,peer=None):
        who,_=self.scope(peer)
        with self.db() as db:rows=[dict(r) for r in db.execute('SELECT * FROM jobs WHERE peer=? ORDER BY at DESC LIMIT 12',(who,))]
        for r in rows:
            for key in ['peer','bot','mid','epoch']:r.pop(key)
            r['args']=json.loads(r['args']);r['result']=json.loads(r['result']) if r['result'] else None
        return rows
    def prompt_context(self,peer):
        rows=self.recent(peer)
        return [{k:r[k] for k in ['id','kind','state','thread_id','at','delivery']}|{'model':r['model'] or r['args'].get('model',''),'effort':r['effort'] or r['args'].get('effort',''),
            'service_tier':'default','task':r['args'].get('task',r['args'].get('prompt',r['args'].get('song',r['args'].get('query',''))))[:180],
            'request_text':r['args'].get('_request_text','')[:1600],'variants':r['args'].get('_variants',[]),
            'error':r['error'][:180],'result_summary':(r.get('result') or {}).get('text','')[:200]} for r in rows]
    def resource_check(self,kind):
        s=self.resources()
        if not s or s['ram_free_mib']<(8192 if kind=='sing_song' else 4096) or s['cpu_percent']>80:raise ValueError('电脑当前较忙或可用内存不足，先不启动这项任务。')
        if kind=='sing_song' and (s['gpu_free_mib']<6144 or s['gpu_util_percent']>50):raise ValueError('显卡正在忙，先不占用本地歌声模型。')
    def request(self,peer,kind,args,text,context=None,raw_text=None):
        from jsonschema import validate
        from creative_tools import TOOLS
        definition=next((t['function'] for t in TOOLS if t['function']['name']==kind),None)
        if not definition:raise ValueError('未知工作工具。')
        validate(args,definition['parameters'])
        from dialogue_context import valid_intent_proof
        actual=raw_text if raw_text is not None else text
        if not (kind=='lookup_web' and lookup_intent(actual)) and requested_tool(text)!=kind and not valid_intent_proof(kind,actual,peer,context):raise ValueError('当前这句话没有提出这项新任务，请先回应用户当前的意思。')
        who,s=self.scope(peer,context);context=context or {};deliver=bool(context.get('deliver'));mid=str(context.get('mid','')) if deliver else ''
        if deliver and not self.permissions.origin(who,context):raise ValueError('任务没有对应的真实QQ来信。')
        role=self.contacts.role(who)
        from dialogue_context import highest_model_requested,comparison_models,named_models
        variants=comparison_models(text) if kind=='draw_picture' else []
        if variants:
            selected=[{'model':m,'effort':choose(role,kind,m,args.get('effort'))[1]} for m in variants]
            args={**args,'model':selected[0]['model'],'effort':selected[0]['effort'],'_variants':selected}
        elif kind=='draw_picture' and len(named_models(text))==1:args={**args,'model':named_models(text)[0]}
        elif role=='admin' and kind=='draw_picture' and highest_model_requested(text):args={**args,'model':'gpt-6.1-sol','effort':'high'}
        if kind=='lookup_web':
            from conversation_tools import public_query
            args={**args,'query':public_query(args['query'])}
        try:model,effort=choose(role,kind,args.get('model'),args.get('effort'))
        except ValueError as exc:
            from creative_tools import RetryableChoiceError
            raise RetryableChoiceError(str(exc)) from None
        if kind!='sing_song':args={**args,'model':model,'effort':effort}
        if kind in {'draw_picture','sing_song'}:args={**args,'_request_text':text[:2000]}
        if role!='admin' and (args.get('read_sources') or args.get('read_paths')):raise ValueError('普通用户不能读取电脑文件或数据库。')
        if kind=='sing_song':
            from creative_tools import DEMO_SONGS,named_song,original_song_requested
            specified=named_song(raw_text if raw_text is not None else text) or named_song(text)
            chosen=args['song'].strip()
            if specified and specified.replace('曉','晓')!=chosen.replace('曉','晓'):
                from creative_tools import RetryableChoiceError
                raise RetryableChoiceError('对方指定的是《'+specified+'》，不能擅自换成《'+chosen+'》。请按真实歌名修正参数。')
            if chosen not in DEMO_SONGS and not original_song_requested(chosen,text):raise ValueError('已识别到点歌《'+chosen+'》，但目前没有这首的可用乐谱；重复点歌不会解决。当前已验证的是《春晓》，也可按要求创作短歌。')
        if kind=='delegate_codex':
            from workspace_policy import validate_read_request
            for source in args.get('read_sources',[]):validate_read_request(raw_text if raw_text is not None else text,source)
            for path in args.get('read_paths',[]):
                if path.replace('\\','/').casefold() not in (raw_text if raw_text is not None else text).replace('\\','/').casefold():raise ValueError('只读地址必须由你在本次原话里指定。')
        with self.lock:
            if self.stop.is_set():raise ValueError('工作台正在退出，稍后重试。')
            with self.db() as db:
                if mid:
                    old=db.execute('SELECT id,state FROM jobs WHERE bot=? AND mid=?',(s['bot_id'],mid)).fetchone()
                    if old:return {'accepted':True,'job_id':old['id'],'state':old['state'],'duplicate':True}
                if db.execute("SELECT count(*) FROM jobs WHERE state IN ('accepted','running')").fetchone()[0]>=4:raise ValueError('这会儿有几件事在处理，稍后再叫我。')
                if role!='admin' and db.execute("SELECT count(*) FROM jobs WHERE peer=? AND state IN ('accepted','running')",(who,)).fetchone()[0]:raise ValueError('你已有一项工作，等它结束再来。')
                if kind!='lookup_web' and db.execute("SELECT count(*) FROM jobs WHERE peer=? AND deliver=? AND kind!='lookup_web' AND at>?",(who,int(deliver),time.time()-3600)).fetchone()[0]>=8:raise ValueError('这小时的创作工作已较多，稍后再继续。')
                if kind=='lookup_web':
                    now=time.time()
                    recent=db.execute("SELECT at FROM jobs WHERE peer=? AND kind='lookup_web' AND at>? ORDER BY at",(who,now-QUERY_POLICY['window_seconds'])).fetchall()
                    if role=='user' and len(recent)>=QUERY_POLICY['max_queries']:
                        import math
                        wait=max(1,math.ceil((recent[-2][0]+900-now)/60))
                        self.ledger.record('query_rejected',peer=who,role=role,state='rate_limited',details={'retry_minutes':wait})
                        raise ValueError(f'这15分钟里已经帮你查了两次，约{wait}分钟后就能继续啦。')
                    args={**args,'model':'gpt-6-luna','effort':'low','_query_timeout_seconds':300 if role=='user' else 0,'_label':self.contacts.label(who)}
                    prior=db.execute("SELECT thread_id,directory FROM jobs WHERE peer=? AND bot=? AND kind='lookup_web' AND thread_id!='' ORDER BY at DESC LIMIT 1",(who,s['bot_id'])).fetchone()
                    if prior:args={**args,'thread_id':prior['thread_id'],'directory':prior['directory']}
                if kind=='delegate_codex' and args.get('mode')!='new':
                    tid=args.get('thread_id','')
                    prior=db.execute("SELECT thread_id,directory FROM jobs WHERE peer=? AND bot=? AND kind='delegate_codex' AND thread_id!='' "+('AND thread_id=? ' if tid else '')+'ORDER BY at DESC LIMIT 1',([who,s['bot_id'],tid] if tid else [who,s['bot_id']])).fetchone()
                    if args.get('mode')=='continue' and not prior:raise ValueError('没有可续接的本工作台对话，请新建。')
                    use=prior and (args.get('mode')=='continue' or bool(re.search('继续|刚才|上个|接着',text)))
                    if tid and not prior:raise ValueError('只能继续owner在本工作台创建的对话。')
                    if use:args={**args,'thread_id':prior['thread_id'],'directory':prior['directory']}
                    else:args={k:v for k,v in args.items() if k!='thread_id'}
                if kind=='delegate_codex' and args.get('mode')=='new':args={k:v for k,v in args.items() if k!='thread_id'}
                if kind!='sing_song' and not args.get('thread_id'):
                    cap=6 if role=='admin' else 3
                    count=db.execute("SELECT count(DISTINCT thread_id) FROM jobs WHERE peer=? AND bot=? AND thread_id!=''",(who,s['bot_id'])).fetchone()[0]
                    if count>=cap:
                        prior=db.execute("SELECT thread_id,directory FROM jobs WHERE peer=? AND bot=? AND kind=? AND thread_id!='' ORDER BY at DESC LIMIT 1",(who,s['bot_id'],kind)).fetchone()
                        if not prior:raise ValueError('该账号的工作对话已达上限，请先继续已有任务。')
                        args={**args,'thread_id':prior['thread_id'],'directory':prior['directory']}
                jid=uuid.uuid4().hex;now=time.time()
                db.execute('INSERT INTO jobs(id,peer,bot,mid,epoch,kind,args,state,at,updated,deliver,delivery,result,error) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (jid,who,s['bot_id'],mid,context.get('epoch',''),kind,json.dumps(args,ensure_ascii=False),'accepted',now,now,int(deliver),'pending' if deliver else 'preview','',''))
            cancelled=threading.Event();self.cancels[jid]=cancelled
            self.ledger.record('task_accepted',event_key='task:'+jid+':accepted',at=now,peer=who,role=role,job_id=jid,model=args.get('model','local_voice'),effort=args.get('effort',''),state='accepted',details={'kind':kind,'delivery':'qq' if deliver else 'preview','query_timeout_seconds':args.get('_query_timeout_seconds'),'variants':args.get('_variants',[])})
            self.pool.submit(self.work,jid,who,kind,args,raw_text if raw_text is not None else text,context,cancelled)
        result={'accepted':True,'job_id':jid,'state':'accepted','message':'工作已实际接收，尚未表示已新建对话或完成；真实进度在工作台，完成后返回结果。' if deliver else '本地预览已接收，完成后可在工作台查看；不会自动发到QQ。'}
        if kind=='draw_picture':
            from dialogue_context import drawing_constraints
            result.update(drawing_constraints=drawing_constraints(text),applied_requirements=text[:700],model=model,effort=effort)
            if variants:result['variants']=selected
        return result
    def local(self,path,body=None,timeout=15):
        cfg=json.loads((self.root/'settings.local.json').read_text(encoding='utf-8'))
        with httpx.Client(trust_env=False,timeout=timeout) as c:
            kw={'headers':{'Authorization':'Bearer '+cfg['api_token']}}
            r=c.get(f'http://127.0.0.1:{cfg["port"]}'+path,**kw) if body is None else c.post(f'http://127.0.0.1:{cfg["port"]}'+path,json=body,**kw)
            if r.status_code!=200:
                try:detail=r.json().get('detail','本地处理未完成。')
                except ValueError:detail='本地处理未完成。'
                raise RuntimeError(str(detail)[:260])
            return r.json()
    def sing(self,args,text):
        song=args['song'].strip()
        from creative_tools import DEMO_SONGS,original_song_requested
        if song in DEMO_SONGS:payload=self.local('/score/demo')
        elif original_song_requested(song,text):
            import deepseek_client
            from agent_tools import TOOLS
            tool=next(t for t in TOOLS if t['function']['name']=='sing_from_score')
            guide=(self.root/'tool-guides/singing.md').read_text(encoding='utf-8')
            response,_=deepseek_client.complete([{'role':'system','content':'按说明写一段全新原创中文短歌，最多20个汉字、25秒，逐字配音符和时长。歌词必须原创，不复现或续写现有歌曲。lyrics_origin必须original，voice=dania，source_url为空。只调用sing_from_score。\n'+guide},{'role':'user','content':text}], [tool],tool_choice='required',timeout=50)
            calls=response.get('tool_calls') or []
            if len(calls)!=1 or calls[0]['function']['name']!='sing_from_score':raise ValueError('未能生成有效的原创短歌乐谱。')
            payload=json.loads(calls[0]['function']['arguments']);payload.update(voice='dania',lyrics_origin='original',source_url='')
        else:raise ValueError('目前没有登记《'+song+'》的乐谱，不能冒充原曲演唱。可以先唱《春晓》，或说“唱一小段原创”。')
        from score_routes import ScoreRequest
        settings_file=self.root/'configs/singing-performance.local.json'
        performance=json.loads(settings_file.read_text(encoding='utf-8')) if settings_file.exists() else {}
        payload.update({k:args.get(k,performance[k] if k in performance else payload.get(k)) for k in ['tempo','rhythm','pitch_stability','phrase_gap'] if k in args or k in performance})
        payload=ScoreRequest.model_validate(payload).model_dump(exclude_none=True)
        status=self.local('/score/status')
        if status['busy']:raise ValueError('本地歌声模型正在处理另一项任务。')
        result=self.local('/v1/score/sing',payload,timeout=1000);aid=result.get('artifact_id','')
        if not re.fullmatch('[a-f0-9]{32}',aid):raise RuntimeError('唱歌服务未返回有效音频。')
        source=self.root/'artifacts'/(aid+'.wav')
        if not source.is_file() or source.stat().st_size>12*1024**2:raise RuntimeError('歌声音频不存在或超过发送大小。')
        dest=self.folder/(uuid.uuid4().hex+'.wav');shutil.copy2(source,dest)
        return {'text':'唱好了一小段《'+payload['title'].split(' · ')[0]+'》。达妮娅参考声线 / AI合成。','attachments':[{'kind':'record','path':str(dest)}],'metadata':result}
    def work(self,jid,peer,kind,args,text,context,cancelled):
        started=time.time()
        from usage_ledger import USAGE_CONTEXT
        usage_token=USAGE_CONTEXT.set({'peer':peer,'role':self.contacts.role(peer),'job_id':jid})
        try:
            if self.stop.is_set() or cancelled.is_set():raise ValueError('任务已停止。')
            if context.get('deliver'):self.scope(peer,context)
            self.resource_check(kind);self.update(jid,state='running')
            if kind=='lookup_web':
                with self.db() as db:accepted_at=db.execute('SELECT at FROM jobs WHERE id=?',(jid,)).fetchone()[0]
                result=self.research.run(jid,peer,args,accepted_at,self.contacts.role(peer),thread_id=args.get('thread_id',''),directory=args.get('directory',''),
                    cancelled=lambda:self.stop.is_set() or cancelled.is_set(),progress=lambda **kw:self.update(jid,**{k:v for k,v in kw.items() if k in {'thread_id','directory','model','effort'}}))
            elif kind=='sing_song':result=self.sing(args,text)
            elif kind=='draw_picture' and args.get('_variants'):
                attachments=[];outputs=[];failures=[];thread_id=args.get('thread_id','');directory=args.get('directory','')
                for index,variant in enumerate(args['_variants']):
                    if cancelled.is_set() or self.stop.is_set():raise ValueError('任务已停止。')
                    try:
                        self.resource_check(kind)
                        output=self.workbench.run(jid,args['prompt'],**variant,thread_id=thread_id,directory=directory,image=True,
                            label=self.contacts.label(peer),role=self.contacts.role(peer),kind=kind,source_text=args.get('_request_text',text),
                            cancelled=lambda:self.stop.is_set() or cancelled.is_set(),progress=lambda **kw:self.update(jid,**{k:v for k,v in kw.items() if k in {'thread_id','directory','model','effort'}}))
                        if not output.get('images'):raise ValueError('没有收到真实生成的图片。')
                        thread_id=output.get('thread_id','');directory=output.get('directory','')
                        source=Path(output['images'][0]);dest=self.folder/(jid+f'-{index}'+source.suffix.lower());shutil.copy2(source,dest)
                        if dest.stat().st_size>10*1024**2:raise ValueError('生成的原图超过10MB。')
                        attachments.append({'kind':'image','path':str(dest),'sticker':False,'id':jid+str(index)})
                        outputs.append({**variant,'service_tier':'default','image_index':len(attachments),'thread_id':thread_id,'directory':directory})
                    except Exception as exc:
                        if cancelled.is_set() or self.stop.is_set():raise
                        failures.append({**variant,'error':str(exc)[:180]})
                if not attachments:raise ValueError('两种模型都没有生成成功：'+'；'.join(f['error'] for f in failures))
                labels='；'.join('第'+str(o['image_index'])+'张：'+o['model']+' / '+o['effort'] for o in outputs)
                result={'text':labels+'，均为普通速度。'+(('另一张未完成：'+'；'.join(f['model']+'：'+f['error'] for f in failures)) if failures else ''),
                    'attachments':attachments,'metadata':{'variants':outputs,'variant_errors':failures,'partial':bool(failures),'model':'multiple','service_tier':'default'}}
            else:
                task=args.get('prompt') if kind=='draw_picture' else args['task']
                output=self.workbench.run(jid,task,model=args.get('model','gpt-6.1-sol'),effort=args.get('effort','medium'),thread_id=args.get('thread_id',''),directory=args.get('directory',''),image=kind=='draw_picture',
                    label=self.contacts.label(peer),role=self.contacts.role(peer),kind=kind,
                    source_text=args.get('_request_text',text) if kind=='draw_picture' else text,read_sources=args.get('read_sources',[]),read_paths=args.get('read_paths',[]),
                    cancelled=lambda:self.stop.is_set() or cancelled.is_set(),progress=lambda **kw:self.update(jid,**{k:v for k,v in kw.items() if k in {'thread_id','directory','model','effort'}}))
                attachments=[]
                if kind=='draw_picture':
                    if not output.get('images'):raise ValueError('没有收到真实生成的图片，不能说已经画好。')
                    for index,path in enumerate(output['images'][:1]):
                        source=Path(path);dest=self.folder/(jid+f'-{index}'+source.suffix.lower());shutil.copy2(source,dest)
                        if dest.stat().st_size>10*1024**2:raise ValueError('生成的原图超过10MB，先在工作台查看原文件。')
                        attachments.append({'kind':'image','path':str(dest),'sticker':False,'id':jid})
                result={'text':'画好了。' if kind=='draw_picture' else output['text'][-1400:],'attachments':attachments,'metadata':output}
            if cancelled.is_set():raise ValueError('任务已停止，结果留在本机。')
            self.update(jid,state='completed',result=json.dumps(result,ensure_ascii=False))
            self.ledger.record('task_finished',event_key='task:'+jid+':finished',peer=peer,role=self.contacts.role(peer),job_id=jid,model=args.get('model','local_voice'),effort=args.get('effort',''),state='partial' if result.get('metadata',{}).get('timed_out') or result.get('metadata',{}).get('partial') else 'completed',duration=time.time()-started,details={'kind':kind,'summary_mode':result.get('metadata',{}).get('summary_mode',''),'source_count':len(result.get('metadata',{}).get('sources',[])),'variants':result.get('metadata',{}).get('variants',[])})
        except Exception as exc:
            self.update(jid,state='cancelled' if cancelled.is_set() else 'failed',error=str(exc)[:500])
            self.ledger.record('task_finished',event_key='task:'+jid+':finished',peer=peer,job_id=jid,model=args.get('model','local_voice'),state='cancelled' if cancelled.is_set() else 'failed',duration=time.time()-started,details={'kind':kind,'error_type':type(exc).__name__})
        finally:
            USAGE_CONTEXT.reset(usage_token)
            self.cancels.pop(jid,None)
            if cancelled.is_set():self.update(jid,delivery='suppressed')
            if context.get('deliver') and not self.stop.is_set() and not cancelled.is_set():
                try:self.bridge.pool.submit(self.deliver,jid)
                except RuntimeError:pass
    def deliver(self,jid,owner=False):
        from qq_delivery import batches
        with self.db() as db:r=db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
        if not r or r['state'] not in {'completed','failed','cancelled','interrupted'} or r['delivery'] not in ({'preview'} if owner else {'pending'}):return {'sent':False}
        context={'bot':r['bot'],'epoch':self.bridge.epoch if owner else r['epoch'],'deliver':True};peer,s=self.scope(r['peer'],context)
        if not owner:
            with self.bridge.chat.memory.db() as db:event=db.execute('SELECT state FROM events WHERE bot=? AND mid=?',(r['bot'],r['mid'])).fetchone()
            if not event or event[0]!='sent':self.update(jid,delivery='suppressed');return {'sent':False}
        result=json.loads(r['result']) if r['result'] else {'text':'这次没完成，'+r['error'],'attachments':[]}
        # A result is sent only after actual completion, never on a model's prediction.
        reply=result['text'];metadata=result.get('metadata',{})
        if r['kind']=='delegate_codex' and r['thread_id']:reply+='\n\nCodex 对话：codex://threads/'+r['thread_id']
        with self.db() as db:
            if not db.execute("UPDATE jobs SET delivery='sending' WHERE id=? AND delivery=?",(jid,r['delivery'])).rowcount:return {'sent':False}
        receipts=[]
        try:
            for packet in batches({'reply':reply,'attachments':result['attachments']},jid,'separate'):
                self.scope(peer,context);receipt=self.bridge.call('send_private_msg',{'user_id':int(peer),'message':packet})
                if not isinstance(receipt,dict) or not receipt.get('message_id'):raise RuntimeError('QQ未返回消息编号，未自动重发。')
                receipts.append(receipt['message_id'])
            self.update(jid,delivery='sent');mid=self.bridge.chat.memory.add(peer,'assistant',reply,origin='task')
            self.ledger.record('task_delivered',event_key='task:'+jid+':delivered',peer=peer,job_id=jid,state='sent',details={'receipt_count':len(receipts)})
            for receipt in receipts:self.bridge.chat.memory.learning.delivery(peer,receipt,mid)
            self.bridge.event('creative_result','创作或Codex任务结果已返回owner，QQ已返回消息编号。')
            return {'sent':True,'receipt_count':len(receipts)}
        except Exception:
            self.update(jid,delivery='uncertain');raise
    def cancel(self,peer):
        self.scope(peer)
        with self.db() as db:rows=db.execute("SELECT id,kind,state FROM jobs WHERE peer=? AND state IN ('accepted','running')",(peer,)).fetchall()
        for r in rows:
            if self.cancels.get(r['id']):self.cancels[r['id']].set()
            if r['kind']=='sing_song' and r['state']=='running':
                try:self.local('/score/cancel',{})
                except Exception:pass
        return '已请求停止当前创作和工作任务。' if rows else '现在没有正在执行的创作或工作任务。'
    def close(self):self.stop.set();self.pool.shutdown(wait=False,cancel_futures=True)
