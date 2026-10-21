"""Background read jobs; requested results are distinct from proactive openers."""
from contextlib import contextmanager
from pathlib import Path
import json,sqlite3,threading,time,uuid
from qq_bridge import QQActionRejected
import mobile_reader
from chat_permissions import ChatPermissions

class QQTasks:
    def __init__(self,root,bridge,mobile,reader=None,resources=None):
        self.root=Path(root);self.bridge=bridge;self.mobile=mobile;self.reader=reader or mobile_reader.run
        if resources is None:
            from local_learning import resource_snapshot
            resources=resource_snapshot
        self.resources=resources;self.path=self.root/'data/qq/tasks.sqlite3';self.path.parent.mkdir(parents=True,exist_ok=True)
        self.exit=threading.Event();self.submit_lock=threading.Lock();self.cached_resource=None;self.checked_at=0;self.permissions=ChatPermissions(self.root)
        with self.db() as db:
            db.executescript('''PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,peer TEXT,query TEXT,max_posts INTEGER,at REAL,updated REAL,
                state TEXT,mobile_job TEXT,bot TEXT,mid TEXT,epoch TEXT,deliver INTEGER,delivery TEXT,result TEXT,error TEXT);
                CREATE UNIQUE INDEX IF NOT EXISTS task_origin ON tasks(bot,mid) WHERE mid!='';''')
            # Restarting never resumes actions or retries a send with an unknown receipt.
            db.execute("UPDATE tasks SET state='interrupted',error='程序重启，旧任务不自动重跑。' WHERE state IN ('accepted','running')")
            db.execute("UPDATE tasks SET delivery='uncertain' WHERE delivery='sending'")
        bridge.chat.tasks=self
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()
    def scope(self,peer=None,epoch=None):
        s=self.bridge.snapshot();target=s.get('test_contact') or {};who=str(target.get('user_id',''))
        if target.get('name')!='owner' or not who or s['allowed_private_ids']!=[who] or self.bridge.cfg.get('locked_bot_id')!=s['bot_id']:
            raise ValueError('当前只允许long使用测试手机。')
        if peer is not None and str(peer)!=who:raise ValueError('联系人不在本轮范围。')
        if epoch is not None and (not s['connected'] or not s['account_matches'] or not s['enabled'] or epoch!=self.bridge.epoch):
            raise ValueError('QQ连接或回复范围已变化，手机任务已停止。')
        return who,s
    def resource_reason(self,during=False):
        if time.monotonic()-self.checked_at>2:
            self.cached_resource=self.resources();self.checked_at=time.monotonic()
        s=self.cached_resource
        if not s:return '无法确认电脑资源，先暂停手机任务。'
        if s['ram_free_mib']<(5120 if during else 8192):return '电脑可用内存不足，手机任务先暂停。'
        if s['gpu_free_mib']<(4096 if during else 6144):return '显存正在被日常应用使用，手机任务先暂停。'
        if s['gpu_util_percent']>(85 if during else 45) or s['cpu_percent']>(90 if during else 70):return '电脑正在忙，手机任务先暂停。'
        return ''
    def recent(self,peer=None):
        who,_=self.scope(peer)
        with self.db() as db:rows=[dict(r) for r in db.execute('SELECT * FROM tasks WHERE peer=? ORDER BY at DESC LIMIT 12',(who,))]
        for r in rows:
            for k in ('peer','bot','mid','epoch'):r.pop(k)
            r['result']=json.loads(r['result']) if r['result'] else None
        return rows
    def activity(self,peer):
        who,_=self.scope(peer)
        with self.db() as db:row=db.execute("SELECT query,state,mobile_job FROM tasks WHERE peer=? AND state IN ('accepted','running') ORDER BY at DESC LIMIT 1",(who,)).fetchone()
        current=self.mobile.snapshot()
        running=bool(row and row['mobile_job'] and row['mobile_job']==current.get('job_id') and current.get('busy') and not self.mobile.cancelled.is_set())
        return {'running':running,'query':row['query'] if running else ''}
    def request(self,peer,query,max_posts=2,context=None):
        who,_=self.scope(peer);query=query.strip()
        from jsonschema import validate
        from conversation_tools import AGENT_TOOLS,public_query
        schema=next(t['function']['parameters'] for t in AGENT_TOOLS if t['function']['name']=='browse_phone')
        validate({'query':query,'max_posts':max_posts},schema);public_query(query)
        context=context or {};deliver=bool(context.get('deliver'));mid=str(context.get('mid','')) if deliver else ''
        if deliver:
            self.scope(who,context.get('epoch'))
            with self.bridge.chat.memory.db() as db:origin=db.execute('SELECT state FROM events WHERE bot=? AND mid=? AND peer=?',(context.get('bot'),mid,who)).fetchone()
            if not origin or origin[0]!='accepted':raise ValueError('任务没有对应的获准QQ请求。')
        if not self.submit_lock.acquire(False):raise ValueError('手机任务正在提交，稍后再看状态。')
        task_id=uuid.uuid4().hex;now=time.time()
        try:
            if self.exit.is_set():raise ValueError('手机服务正在退出。')
            with self.db() as db:
                if mid:
                    old=db.execute('SELECT id,state FROM tasks WHERE bot=? AND mid=?',(context.get('bot'),mid)).fetchone()
                    if old:return {'accepted':True,'job_id':old['id'],'state':old['state'],'duplicate':True}
                if db.execute("SELECT count(*) FROM tasks WHERE peer=? AND state IN ('accepted','running')",(who,)).fetchone()[0]:raise ValueError('已有手机任务，直接查看状态或停止即可。')
                capped=db.execute('SELECT count(*) FROM tasks WHERE peer=? AND at>?',(who,now-3600)).fetchone()[0]>=6
            reason=self.resource_reason()
            if reason:raise ValueError(reason)
            if capped and not self.permissions.consume(who,context,query,max_posts,task_id):self.permissions.block(who,query,max_posts,context)
            with self.db() as db:db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(task_id,who,query,max_posts,now,now,'accepted','',
                context.get('bot',''),mid,context.get('epoch',''),int(deliver),'pending' if deliver else 'preview','',''))
            def work(emit):
                started_here=False;cancel_reason=''
                def cancelled():
                    nonlocal cancel_reason
                    if self.exit.is_set() or self.mobile.cancelled.is_set():cancel_reason='已请求停止';return True
                    try:
                        if deliver:self.scope(who,context.get('epoch'))
                        if hasattr(self.bridge.chat,'agent_settings') and not self.bridge.chat.agent_settings()['phone_enabled']:
                            cancel_reason='手机读取能力已暂停';return True
                    except ValueError as exc:cancel_reason=str(exc);return True
                    cancel_reason=self.resource_reason(during=True)
                    return bool(cancel_reason)
                try:
                    with self.db() as db:db.execute("UPDATE tasks SET state='running',updated=? WHERE id=?",(time.time(),task_id))
                    if cancelled():return {'ok':False,'state':'cancelled','items':[],'reason':cancel_reason}
                    info=self.mobile.bridge.info();was_running=bool(info.get('is_process_started'))
                    if not info.get('is_android_started'):
                        started_here=not was_running;self.mobile.bridge.start(cancelled)
                    elif hasattr(self.mobile.bridge,'wait_ready'):
                        self.mobile.bridge.wait_ready(cancelled)
                    result=self.reader(query,self.mobile.bridge,max_posts=max_posts,emit=emit,cancelled=cancelled)
                    if cancel_reason:result['reason']=cancel_reason
                    return result
                finally:
                    if started_here:
                        try:self.mobile.bridge.stop()
                        except Exception:pass
            def done(jid,outcome):
                result=outcome.get('result');error=outcome.get('error') or '';state=(result or {}).get('state','failed')
                # Keep findings and bounded action evidence; no model reasoning is stored.
                user_cancelled=state=='cancelled' and (result or {}).get('reason')=='已请求停止'
                with self.db() as db:
                    db.execute('UPDATE tasks SET state=?,result=?,error=?,updated=? WHERE id=?',
                        (state,json.dumps(result,ensure_ascii=False) if result else '',error[:300],time.time(),task_id))
                    if user_cancelled:db.execute("UPDATE tasks SET delivery='suppressed' WHERE id=?",(task_id,))
                if deliver and not self.exit.is_set() and not user_cancelled:
                    try:self.bridge.pool.submit(self.deliver,task_id)
                    except RuntimeError:pass
            try:launched=self.mobile.launch(work,on_done=done)
            except Exception:
                self.permissions.refund_unstarted(task_id)
                with self.db() as db:db.execute("UPDATE tasks SET state='failed',delivery='suppressed',error='手机已有任务或未能启动。' WHERE id=?",(task_id,))
                raise ValueError('手机已有任务或未能启动，本轮没有排队。') from None
            with self.db() as db:db.execute('UPDATE tasks SET mobile_job=? WHERE id=?',(launched['job_id'],task_id))
            self.bridge.event('phone_task','已接收一项小黑盒只读任务；'+('结果会回到原请求私聊。' if deliver else '仅本地预览，不发送到QQ。'))
            return {'accepted':True,'job_id':task_id,'state':'accepted','delivery_mode':'requested_reply' if deliver else 'local_preview',
                'message':'任务已实际接收。可以继续聊天，稍后带回真实阅读结果。' if deliver else '本地预览任务已接收，结果只保存在任务页。'}
        finally:self.submit_lock.release()
    def cancel(self,peer):
        who,_=self.scope(peer)
        with self.db() as db:row=db.execute("SELECT mobile_job FROM tasks WHERE peer=? AND state IN ('accepted','running') ORDER BY at DESC LIMIT 1",(who,)).fetchone()
        if not row:return '现在没有正在执行的手机任务。'
        if row['mobile_job']!=self.mobile.snapshot().get('job_id'):return '任务已经结束，刷新查看结果。'
        self.mobile.cancel();return '已请求停止，下一步手机操作前会停下。'
    def status_text(self,peer):
        jobs=self.recent(peer)
        if not jobs:return '还没有手机任务。'
        r=jobs[0];labels={'accepted':'准备中','running':'正在读取','completed':'已读取完成','cancelled':'已停止','failed':'未完成','interrupted':'重启后已停止'}
        return '最近的手机任务：'+r['query']+'，'+labels.get(r['state'],'已结束（'+r['state']+'）')+'。'
    def deliver(self,task_id):
        with self.db() as db:r=db.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone()
        if not r or not r['deliver'] or r['delivery']!='pending':return
        try:
            self.scope(r['peer'],r['epoch'])
            with self.bridge.chat.memory.db() as db:origin=db.execute('SELECT state FROM events WHERE bot=? AND mid=? AND peer=?',(r['bot'],r['mid'],r['peer'])).fetchone()
            if not origin or origin[0]!='sent':raise ValueError('原请求回复未确认，结果留在本机。')
            result=json.loads(r['result']) if r['result'] else {'state':'failed','items':[],'reason':r['error']}
            text=self.bridge.chat.task_reply(r['peer'],r['query'],result)
            self.scope(r['peer'],r['epoch'])
        except Exception:
            with self.db() as db:db.execute("UPDATE tasks SET delivery='suppressed' WHERE id=? AND delivery='pending'",(task_id,))
            self.bridge.event('phone_result_saved','手机结果已留在本机，未取得当前私聊发送条件。');return
        with self.db() as db:
            claimed=db.execute("UPDATE tasks SET delivery='sending' WHERE id=? AND delivery='pending'",(task_id,)).rowcount
        if not claimed:return
        try:
            self.scope(r['peer'],r['epoch'])
            receipt=self.bridge.call('send_private_msg',{'user_id':int(r['peer']),'message':[{'type':'text','data':{'text':text}}]})
            if not isinstance(receipt,dict) or not receipt.get('message_id'):raise RuntimeError('QQ未返回消息编号。')
        except Exception as exc:
            state='failed' if isinstance(exc,(QQActionRejected,ValueError)) else 'uncertain'
            with self.db() as db:db.execute('UPDATE tasks SET delivery=?,error=? WHERE id=?',(state,str(exc)[:200],task_id))
            return
        with self.db() as db:db.execute("UPDATE tasks SET delivery='sent' WHERE id=?",(task_id,))
        try:self.bridge.chat.memory.add(r['peer'],'assistant',text,origin='task')
        except Exception:pass
        self.bridge.event('phone_result_sent','按原请求返回手机阅读结果，QQ已返回消息编号。')
    def close(self):
        self.exit.set()
        try:
            who,_=self.scope();self.cancel(who)
        except Exception:pass
