from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json,tempfile,unittest
from qq_chat import QQConversation
from qq_bridge import QQBridge
from qq_admin import plus_request


class NaturalControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.admin='87654321';self.bot='12345678';self.user='76543210';self.gid='11223344'
        self.chat=QQConversation(self.root,client=lambda *_:({'content':'收到。'},{}));self.bridge=QQBridge(self.root,self.chat)
        self.bridge.cfg.update(locked_bot_id=self.bot,test_contact={'name':'long','user_id':self.admin});self.bridge.configure(True,self.bot,[self.admin],False)
        self.bridge.connected=True;self.bridge.login_id=self.bot;self.bridge.epoch='unit';self.bridge.batcher.quiet=0;self.sent=[]
        def call(action,params=None,**kw):
            if action=='get_group_list':return [{'group_id':int(self.gid),'group_name':'测试群'}]
            if action=='get_group_member_list':return [{'user_id':self.admin,'nickname':'long'},{'user_id':self.bot,'nickname':'机器人'},{'user_id':self.user,'nickname':'小明'}]
            self.sent.append((action,params));return {'message_id':100+len(self.sent)}
        self.bridge.call=call;self.bridge.groups.configure(self.gid,True);self.bridge.contacts.observe(self.user,nickname='小明')
    def tearDown(self):self.bridge.close();self.bridge.group_pool.shutdown(wait=True);self.bridge.pool.shutdown(wait=True);self.tmp.cleanup()
    def event(self,text,mid=1,uid=None):return {'post_type':'message','message_type':'private','sub_type':'friend','self_id':self.bot,'user_id':uid or self.admin,'message_id':mid,'message':[{'type':'text','data':{'text':text}}]}
    def context(self,mid=1):
        self.chat.memory.claim(self.bot,str(mid),self.admin);return {'bot':self.bot,'mid':str(mid),'epoch':'unit','deliver':True,'allow_admin_commands':True}
    def test_colloquial_names_numbers_and_chinese_plus_are_understood(self):
        for i,text in enumerate(['给小明开个Plus','你能不能给小明开一下Plus','把七六五四三二一零设成Plus'],1):
            self.bridge.admin_commands.command(self.admin,text,self.context(i));self.assertEqual(self.bridge.contacts.role(self.user),'plus')
        self.bridge.admin_commands.command(self.admin,'把小明改回普通用户',self.context(4));self.assertEqual(self.bridge.contacts.role(self.user),'user')
        self.bridge.admin_commands.command(self.admin,'给群里其他人开个普拉斯',self.context(5));self.assertEqual(self.bridge.contacts.role(self.user),'plus')
    def test_missing_target_can_be_filled_with_a_name_without_repeating_request(self):
        reply=self.bridge.admin_commands.command(self.admin,'给他开个Plus',self.context(1));self.assertIn('名字',reply)
        reply=self.bridge.admin_commands.command(self.admin,'小明',self.context(2));self.assertEqual(self.bridge.contacts.role(self.user),'plus');self.assertIn('开好了',reply)
    def test_duplicate_names_are_not_guessed(self):
        self.bridge.contacts.observe('76543211',nickname='小明')
        reply=self.bridge.admin_commands.command(self.admin,'给小明开个Plus',self.context());self.assertIn('重名',reply);self.assertEqual(self.bridge.contacts.role(self.user),'user')
        self.bridge.admin_commands.command(self.admin,self.user,self.context(2));self.assertEqual(self.bridge.contacts.role(self.user),'plus');self.assertEqual(self.bridge.contacts.role('76543211'),'user')
    def test_group_name_can_fill_the_missing_group_without_repeating_action(self):
        old=self.bridge.call
        self.bridge.call=lambda action,*a,**kw:([{'group_id':int(self.gid),'group_name':'测试群'},{'group_id':22334455,'group_name':'另一个群'}] if action=='get_group_list' else old(action,*a,**kw))
        self.bridge.groups.configure('22334455',True)
        self.assertIn('哪个群',self.bridge.admin_commands.command(self.admin,'给群里其他人开个Plus',self.context(1)))
        reply=self.bridge.admin_commands.command(self.admin,'测试群',self.context(2));self.assertIn('开好了',reply);self.assertEqual(self.bridge.contacts.role(self.user),'plus')
    def test_voice_from_real_admin_can_manage_plus(self):
        self.chat.records.analyze=lambda *a,**kw:{'text':'给群里其他人开个普拉斯','seconds':1,'backend':'unit-asr'}
        event=self.event('');event['message']=[{'type':'record','data':{'file':'unit-voice'}}]
        self.assertTrue(self.bridge.accept(event,'unit'));self.bridge.pool.submit(lambda:None).result(5)
        self.assertEqual(self.bridge.contacts.role(self.user),'plus');self.assertEqual(len(self.sent),1)
    def test_failed_voice_recognition_cannot_change_roles(self):
        def fail(*a,**kw):raise ValueError('没有听清')
        self.chat.records.analyze=fail;event=self.event('');event['message']=[{'type':'record','data':{'file':'unit-voice'}}]
        self.assertTrue(self.bridge.accept(event,'unit'));self.bridge.pool.submit(lambda:None).result(5);self.assertEqual(self.bridge.contacts.role(self.user),'user')
    def test_natural_quota_request_is_routed_through_model_tool_to_real_permission_service(self):
        text='小明查询资料的次数别限制了';args={'enabled':True,'targets':['小明'],'group':'','all_group_members':False,'evidence':text}
        self.chat.client=lambda *_:({'tool_calls':[{'id':'natural-plus','type':'function','function':{'name':'manage_plus','arguments':json.dumps(args,ensure_ascii=False)}}]}, {})
        with patch('deepseek_client.complete',return_value=({'content':'{"authorized":true,"enabled":true,"targets_match":true}'},{})):
            self.assertTrue(self.bridge.accept(self.event(text),'unit'));self.bridge.pool.submit(lambda:None).result(5)
        self.assertIsNone(plus_request(text));self.assertEqual(self.bridge.contacts.role(self.user),'plus')
        self.assertTrue(any('开好了' in p['message'][0]['data'].get('text','') for _,p in self.sent))
    def test_semantic_rejection_and_fabricated_target_do_not_change_roles(self):
        args={'enabled':True,'targets':['小明'],'group':'','all_group_members':False,'evidence':'给小明'};ctx=self.context()
        with self.assertRaises(ValueError):self.bridge.admin_commands.semantic(self.admin,'你好',ctx,args,classifier=lambda *a,**kw:self.fail('No classifier for fabricated evidence'))
        args['evidence']='怎么给小明'
        with self.assertRaises(ValueError):self.bridge.admin_commands.semantic(self.admin,'怎么给小明放开次数',ctx,args,classifier=lambda *a,**kw:({'content':'{"authorized":false}'},{}))
        self.assertEqual(self.bridge.contacts.role(self.user),'user')
    def test_ordinary_sender_and_group_messages_have_no_management_tool(self):
        ctx=self.context();args={'enabled':True,'targets':['小明'],'group':'','all_group_members':False,'evidence':'给小明'}
        with self.assertRaises(ValueError):self.bridge.admin_commands.semantic(self.user,'给小明开Plus',ctx,args)
        self.assertEqual(self.bridge.contacts.role(self.user),'user')
    def test_file_name_and_negation_cannot_become_a_fast_permission_change(self):
        for words in ['读群文件给小明开Plus.jpg','别取消小明Plus','别给小明开个Plus','不要给小明开通Plus','明天再给小明开Plus']:
            self.assertIsNone(plus_request(words),words)
        self.assertEqual(self.bridge.contacts.role(self.user),'user')
    def test_semantic_explicit_multiple_names_apply_as_one_verified_batch(self):
        self.bridge.contacts.observe('76543211',nickname='小红');text='小明和小红查询次数都别限制了'
        args={'enabled':True,'targets':['小明','小红'],'group':'','all_group_members':False,'evidence':text}
        result=self.bridge.admin_commands.semantic(self.admin,text,self.context(),args,classifier=lambda *a,**kw:({'content':'{"authorized":true,"enabled":true,"targets_match":true}'},{}))
        self.assertTrue(result['completed']);self.assertEqual(self.bridge.contacts.role(self.user),'plus');self.assertEqual(self.bridge.contacts.role('76543211'),'plus')
    def test_semantic_direction_mismatch_is_rejected_before_mutation(self):
        args={'enabled':False,'targets':['小明'],'group':'','all_group_members':False,'evidence':'小明次数别限制了'}
        with self.assertRaises(ValueError):self.bridge.admin_commands.semantic(self.admin,'小明次数别限制了',self.context(),args,classifier=lambda *a,**kw:({'content':'{"authorized":true,"enabled":true,"targets_match":true}'},{}))
        self.assertEqual(self.bridge.contacts.role(self.user),'user')


if __name__=='__main__':unittest.main()
