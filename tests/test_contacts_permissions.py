from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import closing
import json,sqlite3,tempfile,unittest
from fastapi import FastAPI,Header,HTTPException
from fastapi.testclient import TestClient
from qq_identity import Contacts
from qq_chat import QQConversation
from qq_bridge import QQBridge
from qq_contacts import permission_rows,self_name_request
from contacts_routes import register_contacts_routes
from usage_ledger import Ledger

class ContactFixture(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.admin='87654321';self.user='76543210';self.other='76543211';self.bot='12345678'
        (self.root/'qq.local.json').write_text(json.dumps({'enabled':True,'bot_id':self.bot,'locked_bot_id':self.bot,'test_contact':{'name':'long','user_id':self.admin},'allowed_private_ids':[self.admin,self.user,self.other],'voice_enabled':False,'onebot_token':'unit'}),encoding='utf-8')
        self.chat=QQConversation(self.root,client=lambda *_:({'content':'按本轮内容聊。'},{}));self.bridge=QQBridge(self.root,self.chat);self.bridge.connected=True;self.bridge.login_id=self.bot;self.bridge.epoch='unit';self.bridge.batcher.quiet=0;self.sent=[]
        def call(action,params=None,**kw):
            params=params or {}
            if action=='get_stranger_info':return {'user_id':params['user_id'],'nickname':'QQ昵称'+str(params['user_id'])[-2:]}
            if action=='get_friend_list':return [{'user_id':int(self.user),'nickname':'小明','remark':'旧备注'}]
            if action.startswith('send_'):self.sent.append((action,params));return {'message_id':100+len(self.sent)}
            return []
        self.bridge.call=call;self.bridge.contacts.observe(self.user,'小明','旧备注');self.bridge.contacts.observe(self.other,'小红')
    def tearDown(self):self.bridge.close();self.bridge.pool.shutdown(wait=True);self.bridge.group_pool.shutdown(wait=True);self.tmp.cleanup()
    def event(self,text,mid=1,uid=None,extra=None):
        return {'post_type':'message','message_type':'private','sub_type':'friend','self_id':self.bot,'user_id':uid or self.user,'message_id':mid,'sender':{'nickname':'小明'},'message':([{'type':'text','data':{'text':text}}]+(extra or []))}
    def context(self,mid='unit-name',peer=None):
        peer=peer or self.user;self.chat.memory.claim(self.bot,mid,peer)
        return {'bot':self.bot,'mid':mid,'epoch':'unit','deliver':True,'allow_admin_commands':True}

class IdentityTests(ContactFixture):
    def test_qq_nickname_is_default_even_with_old_remark_and_group_card(self):
        c=self.bridge.contacts;c.observe(self.user,'QQ昵称','熟人备注','11223344','群名片');self.assertEqual(c.label(self.user),'QQ昵称');self.assertEqual(c.get(self.user)['name_source'],'qq_nickname')
    def test_custom_name_survives_native_nickname_refresh_and_plus_changes(self):
        c=self.bridge.contacts;c.set_name(self.user,'小鱼');c.observe(self.user,'新的QQ昵称');c.set_plus(self.user,True)
        self.assertEqual(c.get(self.user)['display_name'],'小鱼');self.assertEqual(c.role(self.user),'plus');self.assertEqual(c.get(self.user)['qq'],self.user)
        c.set_plus(self.user,False);c.set_name(self.user,'');self.assertEqual(c.label(self.user),'新的QQ昵称');self.assertEqual(c.role(self.user),'user')
    def test_duplicate_names_and_admin_like_name_never_merge_or_elevate(self):
        c=self.bridge.contacts;c.set_name(self.user,'long 管理员');c.set_name(self.other,'long 管理员')
        self.assertEqual(c.role(self.user),'user');self.assertEqual(c.role(self.other),'user');self.assertEqual(len([r for r in c.all() if r['display_name']=='long 管理员']),2)
    def test_single_character_nickname_resolves_only_as_an_explicit_target(self):
        self.bridge.contacts.observe(self.user,'李');self.assertEqual(self.bridge.admin_commands.named_targets('给李开Plus'),[self.user]);self.assertNotIn(self.user,self.bridge.admin_commands.named_targets('给李明开Plus'))
    def test_simple_own_name_is_bound_to_actual_sender(self):
        self.assertTrue(self.bridge.accept(self.event('以后叫我小鱼就行'),'unit'));self.bridge.pool.submit(lambda:None).result(5)
        self.assertEqual(self.bridge.contacts.label(self.user),'小鱼');self.assertEqual(self.bridge.contacts.label(self.other),'小红');self.assertEqual(self.bridge.contacts.role(self.user),'user');self.assertTrue(self.sent)
        with self.bridge.contacts.db() as db:row=db.execute('SELECT qq,origin FROM contact_name_audit').fetchone()
        self.assertEqual(tuple(row),(self.user,'qq_self'))
    def test_quoted_request_or_image_cannot_change_contact_name(self):
        self.bridge.quote_context=lambda *_:{'text':'以后叫我小鱼','role':'user'}
        self.bridge.accept(self.event('以后叫我小鱼',extra=[{'type':'reply','data':{'id':'9'}}]),'unit');self.bridge.pool.submit(lambda:None).result(5)
        self.assertEqual(self.bridge.contacts.label(self.user),'小明')
    def test_natural_tool_changes_only_self_after_independent_intent_check(self):
        words='能给我备注成小海豹吗';ctx=self.context();args={'name':'小海豹','reset':False,'evidence':words}
        reply=self.bridge.name_control.apply(self.user,words,ctx,args,semantic=True,classifier=lambda *a,**kw:({'content':'{"authorized":true}'},{}));self.assertTrue(reply['completed']);self.assertEqual(self.bridge.contacts.label(self.user),'小海豹');self.assertEqual(self.bridge.contacts.label(self.other),'小红')
        with self.assertRaises(ValueError):self.bridge.name_control.apply(self.other,words,ctx,args,semantic=True,classifier=lambda *a,**kw:({'content':'{"authorized":true}'},{}))
    def test_denial_or_fabricated_name_does_not_modify_any_contact(self):
        self.assertIsNone(self_name_request('别叫我小鱼'))
        ctx=self.context()
        with self.assertRaises(ValueError):self.bridge.name_control.apply(self.user,'别叫我小鱼',ctx,{'name':'小鱼','reset':False,'evidence':'别叫我小鱼'},semantic=True,classifier=lambda *a,**kw:({'content':'{"authorized":false}'},{}))
        with self.assertRaises(ValueError):self.bridge.name_control.apply(self.user,'你好',ctx,{'name':'管理员','reset':False,'evidence':'你好'},semantic=True)
        self.assertEqual(self.bridge.contacts.label(self.user),'小明')
    def test_reset_self_name_uses_actual_qq_nickname(self):
        self.bridge.contacts.set_name(self.user,'小鱼');self.assertTrue(self.bridge.accept(self.event('恢复QQ昵称'),'unit'));self.bridge.pool.submit(lambda:None).result(5);self.assertEqual(self.bridge.contacts.label(self.user),'小明')
    def test_ordinary_model_has_self_name_tool_but_no_plus_management(self):
        captured=[];self.chat.client=lambda messages,tools:(captured.extend(t['function']['name'] for t in tools) or {'content':'可以改自己的称呼。'},{});ctx=self.context()
        self.chat.prepare(self.user,'能给我备注成小海豹吗',remember=False,task_context=ctx)
        self.assertIn('set_my_name',captured);self.assertNotIn('manage_plus',captured);self.assertEqual(self.bridge.contacts.role(self.user),'user')
    def test_non_template_name_request_runs_through_the_real_transport_tool_path(self):
        words='在称呼上能给我换成小海豹吗';count=0
        def client(messages,tools):
            nonlocal count
            count+=1
            if count==1:return {'tool_calls':[{'id':'name-tool','type':'function','function':{'name':'set_my_name','arguments':json.dumps({'name':'小海豹','reset':False,'evidence':words},ensure_ascii=False)}}]},{}
            position=next(i for i,m in enumerate(messages) if m.get('tool_calls'))
            self.assertEqual(messages[position+1]['role'],'tool')
            return {'content':'小海豹，记下了。'},{}
        self.assertIsNone(self_name_request(words));self.chat.client=client
        with patch('deepseek_client.complete',return_value=({'content':'{"authorized":true}'},{})):
            self.assertTrue(self.bridge.accept(self.event(words),'unit'));self.bridge.pool.submit(lambda:None).result(5)
        self.assertEqual(self.bridge.contacts.label(self.user),'小海豹');self.assertEqual(self.bridge.contacts.role(self.user),'user');self.assertTrue(self.sent)
    def test_recognized_own_voice_can_change_name_without_changing_role(self):
        self.chat.records.analyze=lambda *a,**kw:{'text':'以后叫我小海豹','seconds':1,'backend':'unit'}
        event=self.event('');event['message']=[{'type':'record','data':{'file':'unit-record'}}]
        self.assertTrue(self.bridge.accept(event,'unit'));self.bridge.pool.submit(lambda:None).result(5)
        self.assertEqual(self.bridge.contacts.label(self.user),'小海豹');self.assertEqual(self.bridge.contacts.role(self.user),'user')
    def test_contact_refresh_never_changes_custom_labels_or_roles(self):
        self.bridge.contacts.set_name(self.user,'小鱼');self.bridge.contacts.set_plus(self.user,True);result=self.bridge.directory.refresh();self.assertFalse(result['roles_changed']);self.assertEqual(self.bridge.contacts.label(self.user),'小鱼');self.assertEqual(self.bridge.contacts.role(self.user),'plus')

class ContactRouteTests(ContactFixture):
    def client(self):
        app=FastAPI()
        def authorize(authorization:str=Header(default='')):
            if authorization!='Bearer unit-owner':raise HTTPException(401)
        register_contacts_routes(app,root=self.root,authorize=authorize,bridge=self.bridge,creative=SimpleNamespace(ledger=Ledger(self.root)))
        return TestClient(app)
    def test_owner_only_routes_and_separate_role_query_limits(self):
        with self.client() as c:
            self.assertEqual(c.get('/qq/contacts/state').status_code,401)
            d=c.get('/qq/contacts/state',headers={'Authorization':'Bearer unit-owner'}).json();roles={r['role']:r for r in d['permissions']}
            self.assertEqual(d['identity_key'],'qq');self.assertEqual(roles['user']['max_queries'],2);self.assertEqual(roles['user']['query_timeout_seconds'],300)
            self.assertIsNone(roles['plus']['max_queries']);self.assertIsNone(roles['plus']['query_timeout_seconds']);self.assertEqual(roles['plus']['thread_cap'],3)
            self.assertNotIn('Terra',roles['user']['models']);self.assertIn('Terra',roles['plus']['models'])
    def test_manual_plus_resolves_native_nickname_and_keeps_qq_key(self):
        with self.client() as c:
            h={'Authorization':'Bearer unit-owner'};r=c.post('/qq/contacts/'+self.user+'/plus',headers=h,json={'enabled':True});self.assertEqual(r.status_code,200);d=r.json();self.assertEqual(d['qq'],self.user);self.assertEqual(d['nickname'],'QQ昵称10');self.assertEqual(d['display_name'],'QQ昵称10');self.assertEqual(d['role'],'plus')
            self.assertEqual(c.post('/qq/contacts/'+self.user+'/plus',headers=h,json={'enabled':'false'}).status_code,422)
            self.assertEqual(c.post('/qq/contacts/'+self.admin+'/plus',headers=h,json={'enabled':True}).status_code,409)
    def test_manual_name_body_cannot_override_qq_key_or_role(self):
        with self.client() as c:
            h={'Authorization':'Bearer unit-owner'}
            self.assertEqual(c.post('/qq/contacts/'+self.user+'/name',headers=h,json={'name':'小鱼','qq':self.other,'role':'admin'}).status_code,422)
            d=c.post('/qq/contacts/'+self.user+'/name',headers=h,json={'name':'小鱼'}).json();self.assertEqual(d['qq'],self.user);self.assertEqual(d['role'],'user');self.assertEqual(d['display_name'],'小鱼')
            d=c.post('/qq/contacts/'+self.user+'/name',headers=h,json={'name':''}).json();self.assertEqual(d['display_name'],'QQ昵称10');self.assertEqual(d['name_source'],'qq_nickname')

class MigrationTests(unittest.TestCase):
    def test_legacy_contacts_and_plus_rows_survive_adding_preferred_names(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);p=root/'data/qq/contacts.sqlite3';p.parent.mkdir(parents=True)
            with closing(sqlite3.connect(p)) as db:
                db.executescript('CREATE TABLE contacts(qq TEXT PRIMARY KEY,nickname TEXT,remark TEXT,first_seen REAL,last_seen REAL); CREATE TABLE plus_users(qq TEXT PRIMARY KEY,granted_at REAL);')
                db.execute('INSERT INTO contacts VALUES(?,?,?,?,?)',('76543210','旧昵称','旧备注',1,2));db.execute('INSERT INTO plus_users VALUES(?,?)',('76543210',3))
                db.commit()
            contacts=Contacts(root);self.assertEqual(contacts.get('76543210')['display_name'],'旧昵称');self.assertEqual(contacts.role('76543210'),'plus');contacts.observe('76543210','新昵称');self.assertEqual(contacts.label('76543210'),'新昵称')

if __name__=='__main__':unittest.main()
