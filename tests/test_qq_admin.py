from pathlib import Path
import json,tempfile,unittest
from qq_bridge import QQBridge
from qq_chat import QQConversation
from qq_admin import plus_request
from qq_scope import transport_context,event_mid,memory_peer


class AdminCommandsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.admin='87654321';self.bot='12345678';self.user='76543210';self.second='76543211';self.gid='11223344'
        self.chat=QQConversation(self.root,client=lambda *_:({'content':'收到。'},{}));self.bridge=QQBridge(self.root,self.chat)
        self.bridge.cfg.update(locked_bot_id=self.bot,test_contact={'name':'long','user_id':self.admin});self.bridge.configure(True,self.bot,[self.admin],False)
        self.bridge.connected=True;self.bridge.login_id=self.bot;self.bridge.epoch='unit';self.bridge.batcher.quiet=0
        self.members=[{'user_id':uid} for uid in [self.admin,self.bot,self.user,self.second]];self.sent=[]
        def call(action,params=None,**kw):
            if action=='get_group_list':return [{'group_id':int(self.gid),'group_name':'测试群','member_count':4}]
            if action=='get_group_member_list':return self.members
            if action=='get_msg':return {'message':[{'type':'text','data':{'text':'引用内容'}}]}
            self.sent.append((action,params));return {'message_id':100+len(self.sent)}
        self.bridge.call=call;self.bridge.groups.configure(self.gid,True)
    def tearDown(self):
        self.bridge.group_pool.shutdown(wait=True);self.bridge.pool.shutdown(wait=True);self.tmp.cleanup()
    def event(self,text='',mid=1,uid=None,group=False):
        obj={'post_type':'message','message_type':'private','sub_type':'friend','self_id':self.bot,'user_id':uid or self.admin,'message_id':mid,'message':[{'type':'text','data':{'text':text}}]}
        if group:obj.update(message_type='group',sub_type='normal',group_id=self.gid)
        return obj
    def context(self,obj):
        self.chat.memory.claim(self.bot,event_mid(obj),memory_peer(obj));return transport_context(obj,'unit')
    def command(self,text,mid=1,uid=None,group=False):
        obj=self.event(text,mid,uid,group);return self.bridge.admin_commands.command(memory_peer(obj),text,self.context(obj))
    def test_real_private_admin_bulk_grant_excludes_owner_and_bot_and_is_idempotent(self):
        text='将群里面的所有人，除了我，设置成plus成员';reply=self.command(text)
        self.assertIn('2位',reply);self.assertEqual(self.bridge.contacts.role(self.user),'plus');self.assertEqual(self.bridge.contacts.role(self.second),'plus')
        self.assertEqual(self.bridge.contacts.role(self.admin),'admin');self.assertEqual(self.bridge.contacts.role(self.bot),'user')
        self.command(text)
        with self.bridge.contacts.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM plus_audit').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT count(*) FROM plus_requests').fetchone()[0],1)
    def test_user_and_group_admin_cannot_grant_plus(self):
        text='把群里所有人设置成Plus'
        self.assertIn('long',self.command(text,uid=self.user));self.assertNotEqual(self.bridge.contacts.role(self.user),'plus')
        self.assertIn('私聊',self.command(text,mid=2,group=True));self.assertNotEqual(self.bridge.contacts.role(self.second),'plus')
    def test_preview_or_fabricated_context_cannot_change_roles(self):
        text='将群里面的所有人设置成plus成员'
        for context in [None,{'bot':self.bot,'mid':'missing','epoch':'unit','deliver':True}]:
            self.bridge.admin_commands.command(self.admin,text,context)
        self.assertEqual(self.bridge.contacts.role(self.user),'user')
    def test_negation_reported_speech_and_quotes_never_become_commands(self):
        for text in ['不要把群里所有人设置成plus','他说把群里所有人设置成plus','如果把群里所有人设置成plus会怎样','“把群里所有人设置成plus”','不是让你把群里所有人设置成plus']:
            self.assertIsNone(plus_request(text))
        self.assertEqual(self.bridge.contacts.role(self.user),'user')
    def test_revocation_does_not_disable_group_chat(self):
        self.command('把群里全员设成Plus');obj=self.event('取消群里所有人的Plus',mid=2)
        self.assertTrue(self.bridge.accept(obj,'unit'));self.bridge.pool.submit(lambda:None).result(5)
        self.assertEqual(self.bridge.contacts.role(self.user),'user');self.assertTrue(self.bridge.groups.allowed(self.gid));self.assertTrue(self.sent)
    def test_explicit_qq_target_and_ambiguous_group_are_handled_without_guessing(self):
        self.command('把QQ号76543210设置成Plus');self.assertEqual(self.bridge.contacts.role(self.user),'plus')
        self.assertIn('QQ 号',self.command('给小明开通Plus',mid=2))
        old=self.bridge.call
        self.bridge.call=lambda action,*args,**kw:([{'group_id':11223344,'group_name':'测试群'},{'group_id':22334455,'group_name':'另一个群'}] if action=='get_group_list' else old(action,*args,**kw))
        self.bridge.groups.configure('22334455',True)
        self.assertIn('哪个群',self.command('把群里所有人设成Plus',mid=3));self.assertEqual(self.bridge.contacts.role(self.second),'user')
    def test_account_change_during_member_lookup_prevents_mutation(self):
        old=self.bridge.call
        def call(action,*args,**kw):
            if action=='get_group_member_list':self.bridge.login_id='99999999'
            return old(action,*args,**kw)
        self.bridge.call=call
        self.assertIn('没有改',self.command('把群里所有人设置成Plus'));self.assertEqual(self.bridge.contacts.role(self.user),'user')
    def test_quoted_admin_request_does_not_execute(self):
        obj=self.event('把群里所有人设置成Plus');obj['message'].insert(0,{'type':'reply','data':{'id':'old'}})
        self.assertTrue(self.bridge.accept(obj,'unit'));self.bridge.pool.submit(lambda:None).result(5)
        self.assertEqual(self.bridge.contacts.role(self.user),'user')
    def test_forwarded_admin_request_does_not_execute(self):
        obj=self.event('把群里所有人设置成Plus');obj['message'].insert(0,{'type':'forward','data':{'id':'untrusted-forward'}})
        self.assertTrue(self.bridge.accept(obj,'unit'));self.bridge.pool.submit(lambda:None).result(5)
        self.assertEqual(self.bridge.contacts.role(self.user),'user')
    def test_member_snapshot_does_not_auto_grant_future_joiners(self):
        self.command('把群里所有人设置成Plus');self.members.append({'user_id':'76543212'})
        self.assertEqual(self.bridge.contacts.role('76543212'),'user')
    def test_member_lookup_failure_is_honest_and_leaves_roles_unchanged(self):
        old=self.bridge.call
        def call(action,*args,**kw):
            if action=='get_group_member_list':raise RuntimeError('网络连接失败')
            return old(action,*args,**kw)
        self.bridge.call=call
        self.assertIn('权限还没动',self.command('把群里所有人设置成Plus'));self.assertEqual(self.bridge.contacts.role(self.user),'user')
    def test_role_command_is_not_merged_with_other_messages(self):
        self.assertTrue(self.bridge.batcher.immediate(self.event(),'把群里全员设置成Plus'))


if __name__=='__main__':unittest.main()
