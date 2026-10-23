from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import copy,json,tempfile,threading,unittest
from fastapi import FastAPI,Header,HTTPException
from fastapi.testclient import TestClient
from qq_chat import QQConversation
from qq_bridge import QQBridge
from qq_creative import CreativeJobs
from qq_scope import memory_peer,event_mid,transport_context
from qq_identity import choose
from chat_permissions import ChatPermissions
from creative_tools import requested_tool
from tool_failures import failure_info,safe_reason


class GroupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.admin='87654321';self.user='76543210';self.bot='12345678';self.gid='11223344';self.other='22334455';self.sent=[];self.next_mid=100
        self.chat=QQConversation(self.root,client=lambda *_:({'content':'群里你好。'},{}));self.bridge=QQBridge(self.root,self.chat)
        self.bridge.cfg.update(locked_bot_id=self.bot,test_contact={'name':'long','user_id':self.admin})
        self.bridge.configure(True,self.bot,[self.admin],False);self.bridge.connected=True;self.bridge.login_id=self.bot;self.bridge.epoch='test';self.bridge.batcher.quiet=0
        def call(action,params=None,**kw):
            if action=='get_group_list':return [{'group_id':int(self.gid),'group_name':'测试群','member_count':4},{'group_id':int(self.other),'group_name':'另一个群','member_count':5}]
            self.sent.append((action,copy.deepcopy(params)));self.next_mid+=1;return {'message_id':self.next_mid}
        self.bridge.call=call;self.bridge.groups.configure(self.gid,True)
        self.creative=None;self.gate=threading.Event();self.gate.set();self.work=[]
    def tearDown(self):
        self.gate.set()
        if self.creative:self.creative.close();self.creative.pool.shutdown(wait=True)
        self.bridge.group_pool.shutdown(wait=True);self.bridge.pool.shutdown(wait=True);self.tmp.cleanup()
    def event(self,uid=None,gid=None,mid=1,text='你好',mention=True):
        return {'post_type':'message','message_type':'group','sub_type':'normal','group_id':gid or self.gid,'self_id':self.bot,'user_id':uid or self.user,'message_id':mid,
            'sender':{'nickname':'long','card':'群友'},'message':([{'type':'at','data':{'qq':self.bot}}] if mention else [])+[{'type':'text','data':{'text':text}}]}
    def context(self,obj):
        obj['_group_revision']=self.bridge.groups.grant(obj['group_id'])['revision'];ctx=transport_context(obj,'test');self.chat.memory.claim(self.bot,event_mid(obj),memory_peer(obj));return ctx
    def settle(self):
        if self.creative:self.creative.pool.submit(lambda:None).result(5)
        self.bridge.group_pool.submit(lambda:None).result(5)
        self.bridge.pool.submit(lambda:None).result(5)
    def service(self):
        def run(jid,query,**kw):self.work.append(kw);kw['progress'](thread_id=jid,directory=str(self.root/jid),model=kw['model'],effort=kw['effort']);return {'text':'补丁已在副本完成。','images':[]}
        self.creative=CreativeJobs(self.root,self.bridge,resources=lambda:{'ram_free_mib':16000,'gpu_free_mib':13000,'gpu_util_percent':0,'cpu_percent':0},workbench=SimpleNamespace(run=run,workspace=self.root))
        def research(jid,peer,args,*a,**kw):
            self.work.append({'args':args,**kw});self.gate.wait(3)
            kw['progress'](thread_id=kw.get('thread_id') or jid,directory=str(self.root/jid),model='gpt-6-luna',effort='low')
            return {'text':'查到公开资料。','attachments':[],'metadata':{'sources':[]}}
        self.creative.research.run=research;return self.creative
    def request_query(self,obj):
        ctx=self.context(obj);self.gate.clear()
        result=self.creative.request(memory_peer(obj),'lookup_web',{'query':'鸣潮公告'},'帮我查一下鸣潮公告',context=ctx,raw_text='帮我查一下鸣潮公告')
        self.chat.memory.state(self.bot,event_mid(obj),'sent');self.gate.set();self.settle();return result
    def test_only_whitelisted_groups_mentions_and_known_bot_replies_trigger(self):
        self.assertTrue(self.bridge.accepted(self.event(),'test'))
        self.assertFalse(self.bridge.accepted(self.event(mention=False),'test'))
        self.assertFalse(self.bridge.accepted(self.event(gid=self.other),'test'))
        event=self.event();event['message'][0]['data']['qq']='all';self.assertFalse(self.bridge.accepted(event,'test'))
        event=self.event(mention=False);event['message'].insert(0,{'type':'reply','data':{'id':'old'}})
        self.assertFalse(self.bridge.accepted(event,'test'))
        self.bridge.groups.receipt('group:'+self.gid+':'+self.admin,'old',1)
        self.assertTrue(self.bridge.accepted(event,'test'))
    def test_transport_send_memory_and_reply_receipts_use_original_group(self):
        event=self.event();self.assertTrue(self.bridge.accept(event,'test'));self.settle()
        self.assertEqual(self.sent[0][0],'send_group_msg');self.assertEqual(self.sent[0][1]['group_id'],int(self.gid))
        self.assertFalse(self.chat.memory.recent(self.user));self.assertTrue(self.chat.memory.recent(memory_peer(event)))
        self.assertTrue(self.bridge.groups.reply_is_bot(self.gid,self.next_mid))
    def test_private_and_other_group_memories_never_enter_group_prompt(self):
        captured=[];self.chat.client=lambda messages,tools:(captured.append(str(messages)) or {'content':'你好。'}, {})
        self.chat.memory.add(self.user,'user','私聊机密标记')
        self.chat.memory.add('group:'+self.other+':'+self.user,'user','另群机密标记')
        self.chat.memory.add('group:'+self.gid+':'+self.admin,'user','本群公开话题')
        self.bridge.accept(self.event(),'test');self.settle()
        self.assertIn('本群公开话题',captured[0]);self.assertNotIn('私聊机密标记',captured[0]);self.assertNotIn('另群机密标记',captured[0])
    def test_member_named_long_stays_ordinary_and_group_admin_cannot_read_balance(self):
        self.assertEqual(self.chat.contacts.role(memory_peer(self.event())),'user')
        self.chat.balance=SimpleNamespace(text=lambda:self.fail('No balance in groups'))
        result=self.chat.prepare(memory_peer(self.event(uid=self.admin)),'查余额',task_context={'deliver':True},remember=False)
        self.assertIn('私聊',result['reply'])
    def test_revocation_invalidates_queued_revision_and_can_happen_offline(self):
        event=self.event();event['_group_revision']=self.bridge.groups.grant(self.gid)['revision']
        self.bridge.connected=False;self.bridge.groups.configure(self.gid,False)
        self.assertFalse(self.bridge.groups.allowed(self.gid));self.bridge.connected=True;self.bridge.groups.configure(self.gid,True)
        self.assertFalse(self.bridge.accepted(event,'test'))
    def test_chat_authorization_is_real_private_admin_only_and_quote_is_not_a_grant(self):
        self.bridge.groups.configure(self.gid,False)
        private={'bot':self.bot,'mid':'private-1','deliver':True,'epoch':'test'};self.chat.memory.claim(self.bot,'private-1',self.admin)
        self.assertIsNone(self.bridge.groups.command(self.admin,'别人说“允许在测试群聊天”',private))
        self.assertFalse(self.bridge.groups.allowed(self.gid))
        reply=self.bridge.groups.command(self.user,'允许在测试群聊天',private);self.assertIn('long',reply)
        reply=self.bridge.groups.command(self.admin,'允许你在测试群聊天',private);self.assertIn('已接通',reply)
        self.assertTrue(self.bridge.groups.allowed(self.gid))
        self.bridge.groups.command(self.admin,'暂停测试群聊天',private);self.assertFalse(self.bridge.groups.allowed(self.gid))
    def test_negation_and_describing_own_activity_never_enable_a_group(self):
        self.bridge.groups.configure(self.gid,False)
        ctx={'bot':self.bot,'mid':'private-neg','deliver':True,'epoch':'test'};self.chat.memory.claim(self.bot,'private-neg',self.admin)
        for text in ['不要开启测试群聊天','我在测试群聊天','我今天在测试群聊天','我没有允许你开启测试群聊天','为什么允许你在测试群聊天','你刚才说允许在测试群聊天','不要在群聊里加权限']:
            self.bridge.groups.command(self.admin,text,ctx);self.assertFalse(self.bridge.groups.allowed(self.gid),text)
        self.bridge.groups.command(self.admin,'允许你在测试群聊天',ctx);self.assertTrue(self.bridge.groups.allowed(self.gid))
        self.bridge.groups.command(self.admin,'不要关闭测试群聊天',ctx);self.assertTrue(self.bridge.groups.allowed(self.gid))
        self.bridge.groups.command(self.admin,'不要在测试群聊天',ctx);self.assertFalse(self.bridge.groups.allowed(self.gid))
    def test_group_source_event_is_bound_to_actual_sender_and_group(self):
        obj=self.event();ctx=self.context(obj);policy=ChatPermissions(self.root)
        self.assertTrue(policy.origin(self.user,ctx))
        self.assertFalse(policy.origin(self.admin,ctx));self.assertFalse(policy.origin(self.user,{**ctx,'group_id':self.other}))
    def test_query_limit_uses_qq_identity_across_groups_and_never_reuses_private_thread(self):
        self.service();self.bridge.groups.configure(self.other,True)
        self.request_query(self.event(mid=11));self.request_query(self.event(gid=self.other,mid=12))
        self.assertEqual(self.work[0]['thread_id'],'');self.assertEqual(self.work[1]['thread_id'],'')
        obj=self.event(mid=13);ctx=self.context(obj)
        with self.assertRaisesRegex(ValueError,'两次'):self.creative.request(memory_peer(obj),'lookup_web',{'query':'鸣潮'},'帮我查一下鸣潮',context=ctx)
        self.assertEqual(len(self.sent),2);self.assertTrue(all(x[0]=='send_group_msg' for x in self.sent))
        self.assertEqual(self.work[0]['args']['_query_timeout_seconds'],300)
    def test_revoked_group_suppresses_late_task_result(self):
        self.service();obj=self.event(mid=15);ctx=self.context(obj);self.gate.clear()
        result=self.creative.request(memory_peer(obj),'lookup_web',{'query':'鸣潮'},'帮我查一下鸣潮',context=ctx)
        self.chat.memory.state(self.bot,event_mid(obj),'sent');self.bridge.groups.configure(self.gid,False);self.gate.set();self.settle()
        self.assertFalse(self.sent)
        with self.creative.db() as db:self.assertEqual(db.execute('SELECT delivery FROM jobs WHERE id=?',(result['job_id'],)).fetchone()[0],'suppressed')
    def test_groups_cannot_delegate_code_even_for_owner_or_access_external_files(self):
        self.service();obj=self.event(uid=self.admin,mid=21);ctx=self.context(obj)
        args={'task':'修代码','mode':'new','model':'gpt-6-luna','effort':'low'}
        with self.assertRaisesRegex(ValueError,'私聊'):self.creative.request(memory_peer(obj),'delegate_codex',args,'让Codex修代码',context=ctx)
        self.assertFalse(self.work)
    def test_cooldowns_are_bounded_and_not_global_between_groups(self):
        self.assertTrue(self.bridge.groups.reserve(self.gid,self.user));self.assertFalse(self.bridge.groups.reserve(self.gid,self.admin))
        self.assertTrue(self.bridge.groups.reserve(self.other,self.admin))
    def test_natural_repair_request_creates_only_staged_code_work_within_ceiling(self):
        self.service();self.assertEqual(requested_tool('帮我修复机器人群聊功能'),'delegate_codex')
        args={'task':'修复群聊功能','mode':'new','model':'gpt-6-sol','effort':'medium'}
        result=self.creative.request(self.admin,'delegate_codex',args,'帮我修复机器人群聊功能');self.settle()
        self.assertTrue(result['repair_staged_only']);self.assertEqual(self.work[0]['read_sources'],['项目代码'])
        self.assertIn('副本',self.work[0]['source_text'] if '副本' in self.work[0]['source_text'] else self.creative.recent()[0]['args']['task'])
        self.assertIsNone(requested_tool('不要修复机器人，先聊天'))
        with self.assertRaises(ValueError):choose('user','delegate_codex','gpt-6-luna','low')
    def test_group_routes_require_auth_and_use_same_policy(self):
        from qq_routes import register_qq_routes
        app=FastAPI()
        def auth(authorization:str=Header(default='')):
            if authorization!='Bearer test':raise HTTPException(401)
        with patch('qq_routes.QQBridge',return_value=self.bridge):register_qq_routes(app,authorize=auth,root=self.root)
        c=TestClient(app);self.assertEqual(c.get('/qq/groups/state').status_code,401)
        self.assertEqual(c.get('/qq/groups/state',headers={'Authorization':'Bearer test'}).status_code,200)
        self.assertEqual(c.post('/qq/groups/config',headers={'Authorization':'Bearer test'},json={'group_id':self.gid,'enabled':False}).status_code,200)
        self.assertFalse(self.bridge.groups.allowed(self.gid))
    def test_probe_is_group_bound_records_receipt_and_has_cooldown(self):
        result=self.bridge.groups.probe(self.gid);self.assertTrue(result['receipt_received'])
        self.assertEqual(self.sent[0][0],'send_group_msg');self.assertEqual(self.sent[0][1]['group_id'],int(self.gid))
        self.assertTrue(self.bridge.groups.reply_is_bot(self.gid,result['message_id']))
        with self.assertRaises(ValueError):self.bridge.groups.probe(self.gid)
        with self.assertRaises(ValueError):self.bridge.groups.probe(self.other)
    def test_failure_records_keep_specific_reason_and_redact_credentials(self):
        text=('网络失败 Bearer abcdef123456 ' + ('sk-' + 'synthetic-secret-fixture-only') + '')
        info=failure_info(RuntimeError(text));self.assertEqual(info['error_code'],'connection_failed')
        self.assertNotIn('abcdef',info['error']);self.assertIn('已遮蔽',safe_reason(text))
    def test_worker_proxy_is_child_only_and_keeps_loopback_direct(self):
        from codex_rpc import worker_environment
        with patch.dict('os.environ',{},clear=True),patch('urllib.request.getproxies',return_value={'http':'127.0.0.1:7897','https':'127.0.0.1:7897'}):
            env=worker_environment();self.assertEqual(env['HTTPS_PROXY'],'http://127.0.0.1:7897');self.assertIn('127.0.0.1',env['NO_PROXY'])
            import os
            self.assertNotIn('HTTPS_PROXY',os.environ)
    def test_account_switch_during_directory_read_cannot_grant_old_group_to_new_bot(self):
        def switch(*a,**kw):
            self.bridge.cfg['bot_id']='99999999';self.bridge.login_id='99999999'
            return [{'group_id':int(self.gid),'group_name':'旧账号的群','member_count':4}]
        self.bridge.call=switch
        with self.assertRaisesRegex(ValueError,'账号已改变'):self.bridge.groups.refresh()
        self.assertEqual(self.bridge.groups.state()['groups'],[]);self.assertFalse(self.bridge.groups.allowed(self.gid))
    def test_temporarily_empty_directory_during_reconnect_does_not_erase_grants(self):
        self.bridge.call=lambda *a,**kw:[]
        state=self.bridge.groups.refresh();self.assertTrue(state['cached_directory_retained']);self.assertTrue(self.bridge.groups.allowed(self.gid))
    def test_group_flood_keeps_a_receive_slot_for_private_owner(self):
        entered=threading.Event();release=threading.Event()
        def prepare(*a,**kw):entered.set();release.wait(4);return {'reply':'收到','attachments':[],'tools':[]}
        self.chat.prepare=prepare
        try:
            with patch.object(self.bridge.groups,'reserve',return_value=True):
                self.assertTrue(self.bridge.accept(self.event(uid='76543211',mid=31),'test'));self.assertTrue(entered.wait(2))
                self.assertTrue(self.bridge.accept(self.event(uid='76543212',mid=32),'test'))
                self.assertTrue(self.bridge.accept(self.event(uid='76543213',mid=33),'test'))
                self.assertFalse(self.bridge.accept(self.event(uid='76543214',mid=34),'test'))
                private={'message_type':'private','sub_type':'friend','self_id':self.bot,'user_id':self.admin,'message_id':35,'message':[{'type':'text','data':{'text':'你好'}}]}
                self.assertTrue(self.bridge.accept(private,'test'))
        finally:release.set();self.settle()
    def test_slow_group_reply_does_not_block_private_conversation(self):
        entered=threading.Event();release=threading.Event()
        def prepare(peer,*a,**kw):
            if peer.startswith('group:'):entered.set();release.wait(4)
            return {'reply':'收到你的消息','attachments':[],'tools':[]}
        self.chat.prepare=prepare
        try:
            self.assertTrue(self.bridge.accept(self.event(mid=41),'test'));self.assertTrue(entered.wait(2))
            private={'message_type':'private','sub_type':'friend','self_id':self.bot,'user_id':self.admin,'message_id':42,'message':[{'type':'text','data':{'text':'你好'}}]}
            self.assertTrue(self.bridge.accept(private,'test'));self.bridge.pool.submit(lambda:None).result(2)
            self.assertTrue(any(action=='send_private_msg' for action,_ in self.sent));self.assertFalse(any(action=='send_group_msg' for action,_ in self.sent))
        finally:release.set();self.settle()

if __name__=='__main__':unittest.main()
