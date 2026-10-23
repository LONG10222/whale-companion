"""Background singing, built-in image generation and scoped Codex work for long."""
from pathlib import Path
from contextlib import contextmanager
import concurrent.futures,json,re,shutil,sqlite3,threading,time,uuid
import httpx
from chat_permissions import ChatPermissions
from codex_workbench import CodexWorkbench
from codex_rpc import validate_choice
from creative_tools import requested_tool,lookup_intent
from qq_identity import Contacts,choose,QUERY_POLICY
from qq_scope import actor,group,destination
from work_thread_lifecycle import ARCHIVED as ACTIVE_THREAD

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
            CREATE TABLE IF NOT EXISTS work_thread_archives(peer TEXT,bot TEXT,thread_id TEXT,directory TEXT,title TEXT,state TEXT,at REAL,PRIMARY KEY(peer,bot,thread_id));
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,peer TEXT,bot TEXT,mid TEXT,epoch TEXT,kind TEXT,args TEXT,state TEXT,at REAL,updated REAL,
            deliver INTEGER,delivery TEXT,result TEXT,error TEXT,thread_id TEXT DEFAULT '',directory TEXT DEFAULT '',model TEXT DEFAULT '',effort TEXT DEFAULT '');
            CREATE UNIQUE INDEX IF NOT EXISTS creative_origin ON jobs(bot,mid) WHERE mid!='';''')
            columns={r[1] for r in db.execute('PRAGMA table_info(jobs)')}
            for field in ['title','project_id','phase','workflow']:
                if field not in columns:db.execute('ALTER TABLE jobs ADD COLUMN '+field+" TEXT DEFAULT ''")
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
        scoped=str(peer) if peer is not None else who;who=actor(scoped)
        gid=group(scoped) or str((context or {}).get('group_id',''))
        if gid:
            if not getattr(self.bridge,'groups',None) or not self.bridge.groups.allowed(gid,(context or {}).get('group_revision')):raise ValueError('这个群的授权已暂停或改变。')
            if context and context.get('deliver') and (str(context.get('actor_id',''))!=who or context.get('memory_peer')!='group:'+gid+':'+who):raise ValueError('群任务的真实发言者不匹配。')
        elif who not in s['allowed_private_ids']:raise ValueError('工作台联系人不在启用范围内。')
        if context and context.get('deliver') and (not s['enabled'] or not s['connected'] or not s['account_matches'] or context.get('bot')!=s['bot_id'] or context.get('epoch')!=self.bridge.epoch):raise ValueError('账号或回复范围已改变，任务已停止。')
        return who,s
    def update(self,jid,**values):
        allowed={'state','delivery','result','error','thread_id','directory','model','effort','title','project_id','phase','workflow'}
        if set(values)-allowed:raise ValueError('Invalid job fields')
        values['updated']=time.time()
        with self.db() as db:db.execute('UPDATE jobs SET '+','.join(k+'=?' for k in values)+' WHERE id=?',list(values.values())+[jid])
    def native_progress(self,jid,**values):
        fields={k:v for k,v in values.items() if k in {'thread_id','directory','model','effort','title','project_id','phase','workflow'}}
        if not fields:return
        self.update(jid,**fields)
        if fields.get('thread_id') and fields.get('project_id') and fields.get('title'):
            self.ledger.record('codex_thread_bound',event_key='thread:'+jid+':'+fields['thread_id'],job_id=jid,model=fields.get('model',''),effort=fields.get('effort',''),state='verified',
                details={'thread_id':fields['thread_id'],'project_id':fields['project_id'],'project_name':'deepseek娘','title':fields['title']})
    def recent(self,peer=None):
        who,_=self.scope(peer)
        with self.db() as db:rows=[dict(r) for r in db.execute("SELECT * FROM jobs WHERE peer=? AND COALESCE(json_extract(args,'$._memory_peer'),peer)=? ORDER BY at DESC LIMIT 12",(who,str(peer or who)))]
        rows=[r for r in rows if json.loads(r['args']).get('_memory_peer',who)==str(peer or who)]
        for r in rows:
            for key in ['peer','bot','mid','epoch']:r.pop(key)
            r['args']=json.loads(r['args']);r['result']=json.loads(r['result']) if r['result'] else None
            r['workflow']=json.loads(r['workflow']) if r.get('workflow') else None
        return rows
    def prompt_context(self,peer):
        rows=self.recent(peer)
        return [{k:r[k] for k in ['id','kind','state','thread_id','at','delivery']}|{'model':r['model'] or r['args'].get('model',''),'effort':r['effort'] or r['args'].get('effort',''),
            'service_tier':'default','task':r['args'].get('task',r['args'].get('prompt',r['args'].get('song',r['args'].get('query',''))))[:180],
            'request_text':r['args'].get('_request_text','')[:1600],'title':r.get('title',''),'variants':r['args'].get('_variants',[]),'source_image_shas':r['args'].get('_source_image_shas',[]),
            'error':r['error'][:180],'result_summary':(r.get('result') or {}).get('text','')[:200],
            'research_summary_mode':(r.get('result') or {}).get('metadata',{}).get('summary_mode','')} for r in rows]
    def workflow_recent(self):
        who,s=self.scope()
        with self.db() as db:rows=[dict(r) for r in db.execute("SELECT * FROM jobs WHERE peer=? AND bot=? AND (workflow!='' OR json_extract(args,'$._image_workflow')=1) ORDER BY at DESC LIMIT 50",(who,s['bot_id']))]
        result=[]
        for row in rows:
            args=json.loads(row['args']);memory_peer=args.get('_memory_peer',who)
            if actor(memory_peer)!=who or group(memory_peer) and not self.bridge.groups.allowed(group(memory_peer)):continue
            for key in ['peer','bot','mid','epoch']:row.pop(key)
            row.update(args=args,result=json.loads(row['result']) if row['result'] else None,
                       workflow=json.loads(row['workflow']) if row.get('workflow') else None,
                       conversation_scope='group' if group(memory_peer) else 'private')
            result.append(row)
        return result[:12]
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
        local_image_preview=kind=='draw_picture' and context and not context.get('deliver') and context.get('_image_edit') is True and context.get('source_image_shas')
        if not local_image_preview and not (kind=='lookup_web' and lookup_intent(actual)) and requested_tool(text)!=kind and not valid_intent_proof(kind,actual,peer,context):raise ValueError('当前这句话没有提出这项新任务，请先回应用户当前的意思。')
        who,s=self.scope(peer,context);context=context or {};deliver=bool(context.get('deliver'));mid=str(context.get('mid','')) if deliver else ''
        if deliver and not self.permissions.origin(who,context):raise ValueError('任务没有对应的真实QQ来信。')
        role=self.contacts.role(who)
        from creative_tools import image_edit_intent
        editing=kind=='draw_picture' and (image_edit_intent(text) or context.get('_image_edit') is True)
        workflow_enabled=editing and hasattr(self.workbench,'inspect_images')
        if workflow_enabled:
            from image_workflow import settings as workflow_settings
            workflow_enabled=workflow_settings(self.root)['enabled'] or not deliver and context.get('_workflow_preview') is True
        if context.get('group_id') and (kind=='delegate_codex' or args.get('read_sources') or args.get('read_paths')):raise ValueError('电脑工作与读取资料只能由管理员在私聊里提出。')
        args={**args,'_memory_peer':str(peer)}
        if context.get('group_id'):args['_group_context']={k:context[k] for k in ['group_id','actor_id','memory_peer','group_revision']}
        from dialogue_context import highest_model_requested,comparison_models,named_models
        variants=comparison_models(text) if kind=='draw_picture' and not workflow_enabled else []
        if variants:
            selected=[{'model':m,'effort':choose(role,kind,m,args.get('effort'))[1]} for m in variants]
            args={**args,'model':selected[0]['model'],'effort':selected[0]['effort'],'_variants':selected}
        elif kind=='draw_picture' and len(named_models(text))==1:args={**args,'model':named_models(text)[0]}
        elif role=='admin' and kind=='draw_picture' and highest_model_requested(text):args={**args,'model':'gpt-6.1-sol','effort':'xhigh' if workflow_enabled else 'high'}
        if kind=='lookup_web':
            from conversation_tools import public_query
            args={**args,'query':public_query(args['query'])}
        try:
            if workflow_enabled:
                from image_workflow import image_choice
                # Reject an illegal advisory choice; defaults never grant a higher role.
                if args.get('model'):image_choice(role,args['model'],args.get('effort'))
                explicit=not deliver or bool(named_models(actual))
                model,effort=image_choice(role,args.get('model') if explicit else None,args.get('effort') if explicit else None)
            else:model,effort=choose(role,kind,args.get('model'),args.get('effort'))
        except ValueError as exc:
            from creative_tools import RetryableChoiceError
            raise RetryableChoiceError(str(exc)) from None
        if kind!='sing_song':args={**args,'model':model,'effort':effort}
        if kind in {'draw_picture','sing_song'}:args={**args,'_request_text':text[:2000]}
        if kind=='draw_picture':
            from creative_tools import image_edit_intent
            if editing:
                shas=context.get('source_image_shas',[])
                if not isinstance(shas,list) or not 1<=len(shas)<=3:raise ValueError('把要修的原图发来，再告诉我要加什么效果，我拿到原图才能动手。')
                for sha in shas:
                    if not isinstance(sha,str) or not re.fullmatch('[a-f0-9]{64}',sha):raise ValueError('原图编号不正确，请重新发图。')
                    source_peer=str(peer)
                    if not deliver and role=='admin' and context.get('_local_source_peer'):
                        source_peer=str(context['_local_source_peer'])
                        if actor(source_peer)!=who or group(source_peer) and not self.bridge.groups.allowed(group(source_peer)):raise ValueError('预览素材不属于当前管理员或已授权群。')
                    with self.bridge.chat.memory.db() as db:owned=db.execute('SELECT 1 FROM image_messages WHERE peer=? AND sha=?',(source_peer,sha)).fetchone()
                    if not owned:raise ValueError('只能使用这段聊天里你提供的原图，不能取其他人的图片。')
                    self.bridge.chat.images.cached_input(sha)
                args={**args,'_source_image_shas':list(shas)}
                if workflow_enabled:args={**args,'_image_workflow':True}
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
            from creative_tools import bot_repair_intent
            if bot_repair_intent(actual):
                args={**args,'read_sources':list(dict.fromkeys([*args.get('read_sources',[]),'项目代码'])),'_repair':True,
                    'task':args['task']+'\n这是本地机器人功能修复：仅在提供的代码副本中修改，生成补丁、变更说明及测试结果。不得修改运行中的程序、授权策略或账号配置；不得部署、推送GitHub或扩大权限。完成后列出待管理员审核应用的内容。'}
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
                    prior=db.execute("SELECT thread_id,directory FROM jobs WHERE peer=? AND bot=? AND kind='lookup_web' AND thread_id!='' AND "+ACTIVE_THREAD+" AND COALESCE(json_extract(args,'$._memory_peer'),peer)=? ORDER BY at DESC LIMIT 1",(who,s['bot_id'],str(peer))).fetchone()
                    if prior:args={**args,'thread_id':prior['thread_id'],'directory':prior['directory']}
                if kind=='delegate_codex' and args.get('mode')!='new':
                    tid=args.get('thread_id','')
                    prior=db.execute("SELECT thread_id,directory FROM jobs WHERE peer=? AND bot=? AND kind='delegate_codex' AND thread_id!='' AND "+ACTIVE_THREAD+' '+('AND thread_id=? ' if tid else '')+'ORDER BY at DESC LIMIT 1',([who,s['bot_id'],tid] if tid else [who,s['bot_id']])).fetchone()
                    if args.get('mode')=='continue' and not prior:raise ValueError('没有可续接的本工作台对话，请新建。')
                    use=prior and (args.get('mode')=='continue' or bool(re.search('继续|刚才|上个|接着',text)))
                    if tid and not prior:raise ValueError('只能继续long在本工作台创建的对话。')
                    if use:args={**args,'thread_id':prior['thread_id'],'directory':prior['directory']}
                    else:args={k:v for k,v in args.items() if k!='thread_id'}
                if kind=='delegate_codex' and args.get('mode')=='new':args={k:v for k,v in args.items() if k!='thread_id'}
                if kind!='sing_song' and not args.get('thread_id'):
                    cap=6 if role=='admin' else 3
                    from work_thread_lifecycle import count as active_count,reclaim
                    count=active_count(db,who,s['bot_id'])
                    if count>=cap:
                        prior=db.execute("SELECT thread_id,directory FROM jobs WHERE peer=? AND bot=? AND kind=? AND thread_id!='' AND "+ACTIVE_THREAD+" AND COALESCE(json_extract(args,'$._memory_peer'),peer)=? ORDER BY at DESC LIMIT 1",(who,s['bot_id'],kind,str(peer))).fetchone()
                        if prior and not (kind=='delegate_codex' and args.get('mode')=='new'):args={**args,'thread_id':prior['thread_id'],'directory':prior['directory']}
                        else:
                            while active_count(db,who,s['bot_id'])>=cap:reclaim(self,db,who,s['bot_id'])
                if args.get('thread_id'):
                    from work_thread_lifecycle import archived
                    if archived(db,who,s['bot_id'],args['thread_id']):raise ValueError('此工作对话已归档，请先在本机恢复后再续接。')
                jid=uuid.uuid4().hex;now=time.time()
                db.execute('INSERT INTO jobs(id,peer,bot,mid,epoch,kind,args,state,at,updated,deliver,delivery,result,error) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (jid,who,s['bot_id'],mid,context.get('epoch',''),kind,json.dumps(args,ensure_ascii=False),'accepted',now,now,int(deliver),'pending' if deliver else 'preview','',''))
            cancelled=threading.Event();self.cancels[jid]=cancelled
            self.ledger.record('task_accepted',event_key='task:'+jid+':accepted',at=now,peer=who,role=role,job_id=jid,model=args.get('model','local_voice'),effort=args.get('effort',''),state='accepted',details={'kind':kind,'delivery':'qq' if deliver else 'preview','query_timeout_seconds':args.get('_query_timeout_seconds'),'variants':args.get('_variants',[])})
            self.pool.submit(self.work,jid,who,kind,args,raw_text if raw_text is not None else text,context,cancelled)
        result={'accepted':True,'job_id':jid,'state':'accepted','message':'工作已实际接收，尚未表示已新建对话或完成；真实进度在工作台，完成后返回结果。' if deliver else '本地预览已接收，完成后可在工作台查看；不会自动发到QQ。'}
        if kind=='draw_picture':
            from dialogue_context import drawing_constraints
            result.update(drawing_constraints=drawing_constraints(text),applied_requirements=text[:700],model=model,effort=effort,image_edit=editing)
            if variants:result['variants']=selected
        if args.get('_repair'):result['repair_staged_only']=True
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
                    cancelled=lambda:self.stop.is_set() or cancelled.is_set(),progress=lambda **kw:self.native_progress(jid,**kw))
            elif kind=='sing_song':result=self.sing(args,text)
            elif kind=='draw_picture' and args.get('_image_workflow'):
                from image_workflow import ImageWorkflow
                output=ImageWorkflow(self.root,self.workbench).run(jid,args.get('_request_text',text),args['_source_image_shas'],
                    model=args['model'],effort=args['effort'],thread_id=args.get('thread_id',''),directory=args.get('directory',''),
                    role=self.contacts.role(peer),label=self.contacts.label(peer),cancelled=lambda:self.stop.is_set() or cancelled.is_set(),
                    progress=lambda **kw:self.native_progress(jid,**kw))
                attachments=[]
                for index,path in enumerate(output.get('images',[])[:1]):
                    source=Path(path);dest=self.folder/(jid+'-selected'+source.suffix.lower());shutil.copy2(source,dest)
                    attachments.append({'kind':'image','path':str(dest),'sticker':False,'id':jid})
                status=output.get('quality_status')
                text_result='修好了，人物和构图按原图保留，处理过的地方也对照检查了。' if status=='passed' else output.get('text','这版还没达到要求，先留在本机检查。')
                result={'text':text_result,'attachments':attachments,'metadata':output}
            elif kind=='draw_picture' and args.get('_variants'):
                attachments=[];outputs=[];failures=[];thread_id=args.get('thread_id','');directory=args.get('directory','')
                for index,variant in enumerate(args['_variants']):
                    if cancelled.is_set() or self.stop.is_set():raise ValueError('任务已停止。')
                    try:
                        self.resource_check(kind)
                        output=self.workbench.run(jid,args['prompt'],**variant,thread_id=thread_id,directory=directory,image=True,
                            label=self.contacts.label(peer),role=self.contacts.role(peer),kind=kind,source_text=args.get('_request_text',text),
                            source_images=args.get('_source_image_shas',[]),cancelled=lambda:self.stop.is_set() or cancelled.is_set(),progress=lambda **kw:self.native_progress(jid,**kw))
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
                    source_images=args.get('_source_image_shas',[]),cancelled=lambda:self.stop.is_set() or cancelled.is_set(),progress=lambda **kw:self.native_progress(jid,**kw))
                attachments=[]
                if kind=='draw_picture':
                    if not output.get('images'):raise ValueError('没有收到真实生成的图片，不能说已经画好。')
                    for index,path in enumerate(output['images'][:1]):
                        source=Path(path);dest=self.folder/(jid+f'-{index}'+source.suffix.lower());shutil.copy2(source,dest)
                        if dest.stat().st_size>10*1024**2:raise ValueError('生成的原图超过10MB，先在工作台查看原文件。')
                        attachments.append({'kind':'image','path':str(dest),'sticker':False,'id':jid})
                result={'text':'画好了。' if kind=='draw_picture' else output['text'][-1400:],'attachments':attachments,'metadata':output}
            if cancelled.is_set():raise ValueError('任务已停止，结果留在本机。')
            final_state='needs_review' if result.get('metadata',{}).get('quality_status') in {'needs_review','needs_clarification'} else 'completed'
            self.update(jid,state=final_state,result=json.dumps(result,ensure_ascii=False))
            self.ledger.record('task_finished',event_key='task:'+jid+':finished',peer=peer,role=self.contacts.role(peer),job_id=jid,model=args.get('model','local_voice'),effort=args.get('effort',''),state=final_state if final_state=='needs_review' else 'partial' if result.get('metadata',{}).get('timed_out') or result.get('metadata',{}).get('partial') else 'completed',duration=time.time()-started,details={'kind':kind,'summary_mode':result.get('metadata',{}).get('summary_mode',''),'source_count':len(result.get('metadata',{}).get('sources',[])),'variants':result.get('metadata',{}).get('variants',[]),'quality_status':result.get('metadata',{}).get('quality_status','')})
        except Exception as exc:
            from tool_failures import failure_info
            failure=failure_info(exc)
            self.update(jid,state='cancelled' if cancelled.is_set() else 'failed',error=failure['error'])
            self.ledger.record('task_finished',event_key='task:'+jid+':finished',peer=peer,job_id=jid,model=args.get('model','local_voice'),state='cancelled' if cancelled.is_set() else 'failed',duration=time.time()-started,details={'kind':kind,'error_type':type(exc).__name__,'error_code':failure['error_code'],'failure_reason':failure['error']})
        finally:
            USAGE_CONTEXT.reset(usage_token)
            self.cancels.pop(jid,None)
            if cancelled.is_set():self.update(jid,delivery='suppressed')
            if context.get('deliver') and not self.stop.is_set() and not cancelled.is_set():
                try:
                    pool=self.bridge.channel_pool(args.get('_memory_peer',peer)) if hasattr(self.bridge,'channel_pool') else self.bridge.pool
                    pool.submit(self.deliver,jid)
                except RuntimeError:pass
    def deliver(self,jid,owner=False):
        from qq_delivery import batches
        with self.db() as db:r=db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
        if not r or r['state'] not in {'completed','needs_review','failed','cancelled','interrupted'} or r['delivery'] not in ({'preview'} if owner else {'pending'}):return {'sent':False}
        saved_args=json.loads(r['args']);memory_peer=saved_args.get('_memory_peer',r['peer'])
        context={'bot':r['bot'],'epoch':self.bridge.epoch if owner else r['epoch'],'deliver':True,**saved_args.get('_group_context',{})}
        try:peer,s=self.scope(r['peer'],context)
        except ValueError:
            self.update(jid,delivery='suppressed');return {'sent':False,'reason':'原会话或授权已变化，结果保留在本机。'}
        if not owner:
            with self.bridge.chat.memory.db() as db:event=db.execute('SELECT state FROM events WHERE bot=? AND mid=?',(r['bot'],r['mid'])).fetchone()
            if not event or event[0]!='sent':self.update(jid,delivery='suppressed');return {'sent':False}
        result=json.loads(r['result']) if r['result'] else {'text':'这次没完成，'+r['error'],'attachments':[]}
        # A result is sent only after actual completion, never on a model's prediction.
        reply=result['text'];metadata=result.get('metadata',{})
        if r['kind']=='delegate_codex' and r['thread_id']:reply+='\n\nCodex 对话：codex://threads/'+r['thread_id']
        with self.db() as db:
            if not db.execute("UPDATE jobs SET delivery='sending' WHERE id=? AND delivery=?",(jid,r['delivery'])).rowcount:return {'sent':False}
        receipts=[];confirmed_packets=[]
        try:
            for packet in batches({'reply':reply,'attachments':result['attachments']},jid,'separate'):
                self.scope(peer,context);receipt=self.bridge.call(*destination(memory_peer,packet))
                if not isinstance(receipt,dict) or not receipt.get('message_id'):raise RuntimeError('QQ未返回消息编号，未自动重发。')
                receipts.append(receipt['message_id'])
                confirmed_packets.append(packet)
            self.update(jid,delivery='sent');mid=self.bridge.chat.memory.add(memory_peer,'assistant',reply,origin='task')
            self.ledger.record('task_delivered',event_key='task:'+jid+':delivered',peer=peer,job_id=jid,state='sent',details={'receipt_count':len(receipts)})
            for receipt in receipts:
                self.bridge.chat.memory.learning.delivery(memory_peer,receipt,mid)
                if hasattr(self.bridge,'groups'):self.bridge.groups.receipt(memory_peer,receipt,mid)
            self.bridge.event('creative_result','创作或Codex任务结果已返回long，QQ已返回消息编号。')
            return {'sent':True,'receipt_count':len(receipts)}
        except Exception:
            self.update(jid,delivery='uncertain')
            if confirmed_packets:
                from qq_delivery import confirmed_history
                confirmed=confirmed_history({'reply':reply,'attachments':result['attachments']},confirmed_packets,partial=True)
                local=self.bridge.chat.memory.add(memory_peer,'assistant',confirmed,origin='task')
                for receipt in receipts:
                    self.bridge.chat.memory.learning.delivery(memory_peer,receipt,local)
                    if hasattr(self.bridge,'groups'):self.bridge.groups.receipt(memory_peer,receipt,local)
            raise
    def thread_state(self):
        who,s=self.scope()
        from work_thread_lifecycle import count
        with self.db() as db:
            retired=[dict(r) for r in db.execute("SELECT thread_id,title,at FROM work_thread_archives WHERE peer=? AND bot=? AND state='archived' ORDER BY at DESC",(who,s['bot_id']))]
            return {'active':count(db,who,s['bot_id']),'cap':6 if self.contacts.role(who)=='admin' else 3,'automatic_archive':True,'idle_minutes':30,'archived':retired}
    def restore_work_thread(self,tid):
        who,s=self.scope();cap=6 if self.contacts.role(who)=='admin' else 3
        from work_thread_lifecycle import count,reclaim
        with self.lock,self.db() as db:
            row=db.execute("SELECT directory FROM work_thread_archives WHERE peer=? AND bot=? AND thread_id=? AND state='archived'",(who,s['bot_id'],tid)).fetchone()
            if not row:raise ValueError('未找到本人的已归档工作对话。')
            while count(db,who,s['bot_id'])>=cap:reclaim(self,db,who,s['bot_id'],exclude={tid})
            proof=self.workbench.restore_thread(tid,row['directory'])
            if proof.get('restored') is not True:raise ValueError('未确认恢复结果。')
            db.execute("UPDATE work_thread_archives SET state='restored',at=? WHERE peer=? AND bot=? AND thread_id=?",(time.time(),who,s['bot_id'],tid))
        self.ledger.record('work_thread_restored',peer=who,state='restored',details={'thread_id':tid,'files_and_history_preserved':True})
        return {'restored':True,'thread_id':tid}
    def workflow_job(self,jid):
        who,_=self.scope()
        with self.db() as db:row=db.execute('SELECT peer,directory,workflow,state,delivery,result,args FROM jobs WHERE id=?',(jid,)).fetchone()
        if not row or row['peer']!=who:raise ValueError('未找到本人的工作流任务。')
        self.scope(json.loads(row['args']).get('_memory_peer',who))
        if not row['workflow'] and not json.loads(row['args']).get('_image_workflow'):raise ValueError('这不是原图工作流任务。')
        return row
    def workflow_original(self,jid):
        row=self.workflow_job(jid);shas=json.loads(row['args']).get('_source_image_shas',[])
        if not shas:raise ValueError('没有记录本任务的原图。')
        sha=shas[0]
        try:return self.bridge.chat.images.cached_input(sha)['path']
        except ValueError:
            import hashlib
            if not isinstance(sha,str) or not re.fullmatch('[a-f0-9]{64}',sha):raise ValueError('原图编号不正确。')
            directory=Path(row['directory']).resolve()
            if not directory.is_relative_to(self.workbench.workspace.resolve()):raise ValueError('原图副本超出工作台。')
            for ext in ['.jpg','.jpeg','.png']:
                path=directory/('reference-user-'+sha+'-1'+ext)
                if path.is_file() and not path.is_symlink() and path.stat().st_size<=12*1024**2 and hashlib.sha256(path.read_bytes()).hexdigest()==sha:return str(path)
            raise ValueError('原图缓存与核对过的任务副本均不可用。') from None
    def workflow_version(self,jid,version):
        row=self.workflow_job(jid)
        workflow=json.loads(row['workflow']) if row['workflow'] else (json.loads(row['result']).get('metadata',{}).get('workflow',{}) if row['result'] else {})
        candidate=next((v for v in workflow.get('versions',[]) if v['version']==version),None)
        if not candidate:raise ValueError('未找到这个候选版本。')
        path=Path(candidate['path']).resolve();directory=Path(row['directory']).resolve()
        if not directory.is_relative_to(self.workbench.workspace.resolve()) or not path.is_relative_to(directory) or not path.is_file() or path.stat().st_size>10*1024**2:raise ValueError('候选图片超出本任务范围或不可用。')
        if candidate.get('sha256'):
            import hashlib
            if hashlib.sha256(path.read_bytes()).hexdigest()!=candidate['sha256']:raise ValueError('候选文件已改变，请重新核对，不沿用原审核结果。')
        return row,workflow,candidate,path
    def cancel_workflow(self,jid):
        who,_=self.scope()
        with self.db() as db:row=db.execute('SELECT peer,args,state FROM jobs WHERE id=?',(jid,)).fetchone()
        if not row or row['peer']!=who:raise ValueError('未找到本人的工作流任务。')
        args=json.loads(row['args'])
        if not args.get('_image_workflow'):raise ValueError('这不是原图工作流任务。')
        self.scope(args.get('_memory_peer',who))
        flag=self.cancels.get(jid)
        if row['state'] in {'accepted','running'} and flag:
            flag.set();return {'requested':True,'message':'已请求停止这项修图，现有版本保留在本机。'}
        return {'requested':False,'message':'这项修图已结束。'}
    def approve_workflow(self,jid,version):
        row,workflow,candidate,path=self.workflow_version(jid,version)
        if row['state'] in {'accepted','running'}:raise ValueError('请等本轮处理完成，再选择版本。')
        if row['delivery']=='uncertain':raise ValueError('之前的发送回执不明，先核对原会话，不能直接重复发送。')
        if not candidate.get('structural',{}).get('passed'):raise ValueError('此版文件或画幅检查未通过，不能作为发送图片。')
        if workflow.get('selected_version')==version and row['delivery']=='sent':return {'selected':version,'already_sent':True,'sent':False}
        dest=self.folder/(jid+'-owner-selected'+path.suffix.lower());shutil.copy2(path,dest)
        workflow.update(status='owner_approved',selected_version=version,selection_reason='管理员在本机明确选择此版本，未自动发送。')
        result=json.loads(row['result']) if row['result'] else {'metadata':{}}
        result.update(text='这是你在本机选定的版本。',attachments=[{'kind':'image','path':str(dest),'sticker':False,'id':jid}])
        result.setdefault('metadata',{}).update(workflow=workflow,quality_status='owner_approved')
        self.update(jid,state='completed',phase='selected',workflow=json.dumps(workflow,ensure_ascii=False),result=json.dumps(result,ensure_ascii=False),delivery='preview')
        self.ledger.record('image_version_approved',job_id=jid,peer=self.contacts.admin,state='owner_confirmed',details={'version':version,'sent':False})
        return {'selected':version,'sent':False,'ready_for_manual_send':True}
    def cancel(self,peer):
        who,_=self.scope(peer)
        with self.db() as db:rows=db.execute("SELECT id,kind,state FROM jobs WHERE peer=? AND COALESCE(json_extract(args,'$._memory_peer'),peer)=? AND state IN ('accepted','running')",(who,str(peer))).fetchall()
        for r in rows:
            if self.cancels.get(r['id']):self.cancels[r['id']].set()
            if r['kind']=='sing_song' and r['state']=='running':
                try:self.local('/score/cancel',{})
                except Exception:pass
        return '已请求停止当前创作和工作任务。' if rows else '现在没有正在执行的创作或工作任务。'
    def close(self):self.stop.set();self.pool.shutdown(wait=False,cancel_futures=True)
