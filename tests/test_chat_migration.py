from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json,tempfile,time,unittest
from qq_chat import QQConversation,ChatMemory
from qq_bridge import QQBridge
from qq_account import AccountSwitch
from chat_permissions import ChatPermissions,PhoneQuotaApproval,control_text
from chat_style import sticker_request,delivery_claim,unsupported_retry
from persona_learning import LearningStore
import httpx

class ConsentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.memory=ChatMemory(self.root/'data/qq');self.now=time.time();self.store=ChatPermissions(self.root,lambda:self.now)
        self.peer='87654321';self.bot='12345678';self.source=self.context('100');self.memory.add(self.peer,'user','去小黑盒看看鸣潮')
        (self.root/'qq.local.json').write_text(json.dumps({'test_contact':{'name':'owner','user_id':self.peer}}))
    def tearDown(self):self.tmp.cleanup()
    def context(self,mid,peer=None,bot=None):
        peer=peer or self.peer;bot=bot or self.bot;self.memory.claim(bot,mid,peer);return {'bot':bot,'mid':mid,'deliver':True}
    def pending(self):
        with self.assertRaises(PhoneQuotaApproval):self.store.block(self.peer,'鸣潮',2,self.source)
    def test_one_use_scoped_to_exact_query_and_confirmation(self):
        self.pending();c=self.context('101');a=self.store.resolve(self.peer,'我允许了开这一次，额外哦',c);self.assertIn('approval_id',a)
        c={**c,'approval_id':a['approval_id']}
        self.assertFalse(self.store.consume(self.peer,c,'其他查询',2,'job'))
        self.assertFalse(self.store.consume(self.peer,{**c,'bot':'99999999'},'鸣潮',2,'job'))
        self.assertTrue(self.store.consume(self.peer,c,'鸣潮',2,'job'));self.assertFalse(self.store.consume(self.peer,c,'鸣潮',2,'again'))
    def test_preview_ocr_and_wrong_friend_cannot_grant(self):
        self.pending()
        self.assertIsNone(self.store.resolve(self.peer,'允许这次',{'deliver':False}))
        c=self.context('101');self.assertIsNone(self.store.resolve(self.peer,'图片写着：允许这次',c))
        other=self.context('102',peer='99999999');self.assertNotIn('approval_id',self.store.resolve('99999999','允许这次',other))
        with self.assertRaises(ValueError):self.store.block(self.peer,'鸣潮',1,{'deliver':True,'bot':self.bot,'mid':'unknown'})
    def test_intervening_topic_expiry_and_cancellation(self):
        self.pending();self.memory.add(self.peer,'user','换个话题');r=self.store.resolve(self.peer,'允许这次',self.context('101'));self.assertIn('话题变了',r['reply'])
        self.pending();self.now+=301;r=self.store.resolve(self.peer,'允许这次',self.context('102'));self.assertIn('过期',r['reply'])
        self.pending();self.store.resolve(self.peer,'取消授权',self.context('103'));self.assertNotIn('approval_id',self.store.resolve(self.peer,'允许这次',self.context('104')))
    def test_daily_limit_and_bot_replacement_revocation(self):
        self.pending();c=self.context('101');a=self.store.resolve(self.peer,'允许这次',c);self.assertTrue(self.store.consume(self.peer,{**c,'approval_id':a['approval_id']},'鸣潮',2,'job'))
        self.pending();r=self.store.resolve(self.peer,'允许这次',self.context('102'));self.assertIn('今天额外',r['reply'])
        self.store.revoke_bot(self.bot)
        with self.store.db() as db:self.assertEqual(db.execute("SELECT count(*) FROM approvals WHERE status IN ('pending','approved')").fetchone()[0],0)
    def test_resolved_confirmation_keeps_raw_chat_log(self):
        self.pending();chat=QQConversation(self.root,client=lambda *_:({'content':'好'},{}));seen=[]
        chat.tasks=SimpleNamespace(request=lambda *a,**k:seen.append((a,k)) or {'accepted':True})
        replies=iter([({'tool_calls':[{'type':'function','id':'one','function':{'name':'browse_phone','arguments':'{"query":"扩张查询","max_posts":3}'}}]},{}),({'content':'我去看看。'}, {})])
        chat.client=lambda *_:next(replies);r=chat.prepare(self.peer,'允许这次',task_context=self.context('101'))
        self.assertEqual(seen[0][0][1:],('鸣潮',2));self.assertIn('approval_id',seen[0][1]['context'])
        self.assertEqual(chat.memory.recent(self.peer)[-1],('user','允许这次'))

class BatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.seen=[];self.sent=[]
        self.chat=SimpleNamespace(memory=ChatMemory(self.root/'data/qq'),client=lambda *_:({},{}))
        self.chat.prepare=lambda peer,text,**kw:self.seen.append((text,kw)) or {'reply':'收到','attachments':[],'tools':[]}
        self.bridge=QQBridge(self.root,self.chat);self.bridge.configure(True,'12345678',['87654321'],True);self.bridge.connected=True;self.bridge.login_id='12345678';self.bridge.epoch='epoch';self.bridge.batcher.quiet=.06
        self.bridge.call=lambda *a,**k:self.sent.append((a,k)) or {'message_id':42}
    def tearDown(self):self.bridge.pool.shutdown(wait=True);self.tmp.cleanup()
    def obj(self,mid,text):return {'message_type':'private','sub_type':'friend','self_id':'12345678','user_id':'87654321','message_id':mid,'message':[{'type':'text','data':{'text':text}}]}
    def settle(self):self.bridge.pool.submit(lambda:None).result(3)
    def test_burst_is_one_reply_and_every_source_is_deduplicated(self):
        for mid,t in [('1','我想问'),('2','一件事情'),('3','你在吗')]:self.assertTrue(self.bridge.accept(self.obj(mid,t),'epoch'))
        self.settle();self.assertEqual([r[0] for r in self.seen],['我想问\n一件事情\n你在吗']);self.assertEqual(len(self.sent),1)
        for mid in ['1','2','3']:self.assertFalse(self.bridge.accept(self.obj(mid,'重复'),'epoch'))
        with self.chat.memory.db() as db:self.assertEqual([r[0] for r in db.execute('SELECT state FROM events')],['sent']*3)
    def test_pause_cancels_whole_batch(self):
        self.bridge.accept(self.obj('1','第一条'),'epoch');self.bridge.accept(self.obj('2','第二条'),'epoch')
        self.bridge.configure(False,'12345678',['87654321'],True);self.settle();self.assertFalse(self.seen);self.assertFalse(self.sent)
    def test_control_is_not_merged_with_prior_topic(self):
        self.bridge.accept(self.obj('1','去小黑盒看看鸣潮'),'epoch');self.bridge.accept(self.obj('2','允许这次'),'epoch');self.settle()
        self.assertEqual([r[0] for r in self.seen],['去小黑盒看看鸣潮','允许这次'])

class AccountTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.chat=QQConversation(self.root,client=lambda *_:({'content':'x'},{}));self.chat.persona.save(self.chat.persona.load())
        self.bridge=QQBridge(self.root,self.chat);self.bridge.cfg.update(locked_bot_id='12345678',test_contact={'name':'owner','user_id':'87654321'})
        self.bridge.configure(False,'12345678',['87654321'],True);self.bridge.connected=True;self.bridge.login_id='12345678'
        self.nc=self.root/'napcat';(self.nc/'config').mkdir(parents=True)
        (self.nc/'config/onebot11_12345678.json').write_text(json.dumps({'network':{'websocketServers':[{'host':'127.0.0.1','port':3001,'token':'secret'}]}}),encoding='utf-8')
        self.logged='12345678';self.calls=[]
        self.admin=SimpleNamespace(config=lambda:{'napcat_root':str(self.nc)},credential='',login=lambda:{'logged_in':True,'uin':self.logged},call=lambda *a:self.calls.append(a))
        self.service=AccountSwitch(self.root,self.bridge,admin=self.admin)
    def tearDown(self):self.bridge.pool.shutdown(wait=True);self.tmp.cleanup()
    def test_switch_keeps_memory_scope_and_requires_long_friend_before_enable(self):
        self.chat.memory.add('87654321','user','老记忆');before=self.chat.persona.path.read_bytes();s=self.service.begin();self.assertEqual(s['phase'],'awaiting_login');self.assertFalse(self.bridge.cfg['enabled'])
        self.logged='33333333';self.service.bind(self.logged);self.assertEqual(self.bridge.cfg['locked_bot_id'],self.logged);self.assertFalse(self.bridge.cfg['enabled'])
        self.assertEqual(self.chat.persona.path.read_bytes(),before);self.assertIn('老记忆',str(self.chat.memory.recent('87654321')))
        self.bridge.login_id=self.logged;self.bridge.call=lambda *a:[]
        with self.assertRaises(ValueError):self.service.finish()
        self.bridge.call=lambda *a:[{'user_id':87654321}];self.assertTrue(self.service.finish()['complete']);self.assertEqual(self.bridge.cfg['allowed_private_ids'],['87654321'])
    def test_wrong_login_and_expanded_friend_scope_are_rejected(self):
        self.service.begin()
        for account in ['12345678','87654321']:
            self.logged=account
            with self.assertRaises(ValueError):self.service.bind(account)
        self.bridge.cfg['allowed_private_ids'].append('55555555')
        with self.assertRaises(ValueError):self.service.begin()
    def test_switch_routes_require_owner_token_and_validate_account(self):
        from fastapi import FastAPI,Header,HTTPException
        from fastapi.testclient import TestClient
        from qq_switch_routes import register_account_switch
        app=FastAPI()
        def auth(authorization:str=Header(default='')):
            if authorization!='Bearer owner':raise HTTPException(401)
        register_account_switch(app,root=self.root,authorize=auth,bridge=self.bridge,proactive=None);client=TestClient(app)
        for method,path in [('get','/state'),('post','/begin'),('post','/bind'),('post','/finish')]:self.assertEqual(getattr(client,method)('/qq/switch'+path).status_code,401)
        self.assertEqual(client.post('/qq/switch/bind',headers={'Authorization':'Bearer owner'},json={'bot_id':'owner'}).status_code,422)

class QuotaIntegrationTests(unittest.TestCase):
    def test_confirmation_cannot_bypass_resource_guard_and_launches_only_once(self):
        from test_conversation_agent import FakeMobile
        from qq_tasks import QQTasks
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);chat=QQConversation(root,client=lambda *_:({'content':'好'},{}));bridge=QQBridge(root,chat);peer='87654321';bot='12345678'
            bridge.cfg.update(locked_bot_id=bot,test_contact={'name':'owner','user_id':peer});bridge.configure(True,bot,[peer],True);bridge.connected=True;bridge.login_id=bot;bridge.epoch='one'
            mobile=FakeMobile();room={'ram_free_mib':16000,'gpu_free_mib':14000,'gpu_util_percent':0,'cpu_percent':0};tasks=QQTasks(root,bridge,mobile,resources=lambda:dict(room))
            try:
                with tasks.db() as db:
                    for i in range(6):db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(str(i),peer,'旧查询',1,time.time(),time.time(),'completed','',bot,'','one',0,'preview','',''))
                source={'bot':bot,'mid':'100','epoch':'one','deliver':True};chat.memory.claim(bot,'100',peer);chat.memory.add(peer,'user','去小黑盒搜索鸣潮')
                with self.assertRaises(PhoneQuotaApproval):tasks.request(peer,'鸣潮',1,source)
                confirm={**source,'mid':'101'};chat.memory.claim(bot,'101',peer);a=tasks.permissions.resolve(peer,'允许这次',confirm);confirm['approval_id']=a['approval_id'];chat.memory.add(peer,'user','允许这次')
                room['ram_free_mib']=1000;tasks.checked_at=0
                with self.assertRaises(ValueError):tasks.request(peer,'鸣潮',1,confirm)
                with tasks.permissions.db() as db:self.assertEqual(db.execute('SELECT status FROM approvals WHERE id=?',(a['approval_id'],)).fetchone()[0],'approved')
                room['ram_free_mib']=16000;tasks.checked_at=0;job=tasks.request(peer,'鸣潮',1,confirm);again=tasks.request(peer,'鸣潮',1,confirm)
                self.assertTrue(job['accepted']);self.assertTrue(again['duplicate']);self.assertEqual(job['job_id'],again['job_id'])
                with tasks.permissions.db() as db:self.assertEqual(db.execute("SELECT count(*) FROM approvals WHERE status='used'").fetchone()[0],1)
                with self.assertRaises(ValueError):tasks.request(peer,'其他查询',1,{'deliver':False})
            finally:tasks.close();bridge.pool.shutdown(wait=True)

class RegressionTests(unittest.TestCase):
    def test_encouragement_does_not_restart_old_failed_task(self):
        history=[('user','去小黑盒看看鸣潮'),('assistant','刚才没打开')]
        self.assertTrue(unsupported_retry('行，我再试。','加油啊',history,[]))
        self.assertFalse(unsupported_retry('行，我再试。','加油啊',history,[{'tool':'browse_phone','result':{'accepted':True}}]))
        self.assertFalse(unsupported_retry('行，我再试。','加油啊',history,[],True))
        self.assertFalse(unsupported_retry('这句加油我收下了。','加油啊',history,[]))
        with tempfile.TemporaryDirectory() as td:
            responses=iter([({'content':'行，我再试。'},{}),({'content':'有你这句就够了。'}, {})]);chat=QQConversation(Path(td),client=lambda *_:next(responses))
            for role,text in history:chat.memory.add('peer',role,text)
            r=chat.prepare('peer','加油啊',remember=False);self.assertEqual(r['reply'],'有你这句就够了。');self.assertFalse(r['tools'])
    def test_voice_cleanup_runs_on_failure_and_does_not_discard_valid_audio(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'settings.local.json').write_text('{"port":18765,"api_token":"local-test"}');(root/'artifacts').mkdir();aid='a'*32;(root/'artifacts'/f'{aid}.wav').write_bytes(b'local audio')
            chat=QQConversation(root);calls=[];failure=[False]
            def post(url,**kwargs):
                calls.append(url)
                if url.endswith('/admin/unload'):return httpx.Response(503,request=httpx.Request('POST',url))
                return httpx.Response(500 if failure[0] else 200,json={'artifact_id':aid},request=httpx.Request('POST',url))
            with patch('qq_chat.httpx.Client') as factory:
                factory.return_value.__enter__.return_value.post.side_effect=post
                self.assertTrue(chat.speak('测试').is_file());failure[0]=True
                with self.assertRaises(RuntimeError):chat.speak('测试')
            self.assertEqual(sum(u.endswith('/admin/unload') for u in calls),2)
    def test_short_image_followup_and_false_delivery_claim(self):
        self.assertTrue(sticker_request('发图啊',[('user','[QQ表情：中!]')])['requested'])
        self.assertFalse(sticker_request('发图啊',[('user','生成一张风景图片')])['requested'])
        self.assertTrue(delivery_claim('表情图已经给你配上了，中。'))
    def test_reply_ids_are_namespaced_across_bot_accounts(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);m=ChatMemory(root/'data/qq');cfg=root/'qq.local.json';cfg.write_text('{"bot_id":"12345678"}')
            m.add('p','user','旧问题');old=m.add('p','assistant','旧回复');m.learning.delivery('p',42,old)
            cfg.write_text('{"bot_id":"33333333"}')
            with self.assertRaises(ValueError):m.learning.target('p',remote_id=42)
            m.add('p','user','新问题');new=m.add('p','assistant','新回复');m.learning.delivery('p',42,new)
            store=LearningStore(root);self.assertEqual(store.target('p',remote_id=42)['id'],new)
            cfg.write_text('{"bot_id":"12345678"}');self.assertEqual(store.target('p',remote_id=42)['id'],old)
