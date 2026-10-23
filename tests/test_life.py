from pathlib import Path
from types import SimpleNamespace
from datetime import datetime
import json,tempfile,unittest
from unittest.mock import patch
from fastapi import FastAPI,Header,HTTPException
from fastapi.testclient import TestClient
from qq_chat import ChatMemory,QQConversation
from qq_bridge import QQBridge
from persona_store import PersonaStore,dania_card
from proactive_chat import ProactiveChat
from life_memory import LifeMemory,ZONE
from companion_life import CompanionLife
from life_routes import register_life_routes
from conversation_rhythm import guidance

def response(content):return {'content':json.dumps(content,ensure_ascii=False)},{}
def call(name,args):return {'content':None,'tool_calls':[{'id':'call1','type':'function','function':{'name':name,'arguments':json.dumps(args,ensure_ascii=False)}}]},{}

class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.peer='long';self.chat=ChatMemory(self.root/'data/qq');self.mem=self.chat.life
    def tearDown(self):self.tmp.cleanup()
    def add(self,text='我喜欢鸣潮，尤其喜欢探索地图。',kind='preference',topic='鸣潮',**kw):
        mid=self.chat.add(self.peer,'user',text)
        return self.mem.accept(self.peer,{'source_id':mid,'kind':kind,'topic':topic,'quote':text,'content':'用户喜欢原神（故意错误的提取）','explicit':True,**kw})
    def test_auto_content_is_original_not_model_hallucination(self):
        self.add();r=self.mem.retrieve(self.peer,'鸣潮地图')[0];self.assertIn('鸣潮',r['content']);self.assertNotIn('原神',r['content']);self.assertEqual(r['content'],r['quote'])
    def test_wrong_peer_assistant_or_forged_quote_cannot_enter(self):
        mid=self.chat.add('other','user','我喜欢原神。');item={'source_id':mid,'kind':'preference','topic':'原神','quote':'我喜欢原神。','content':'喜欢原神','explicit':True}
        self.assertIsNone(self.mem.accept(self.peer,item));mid=self.chat.add(self.peer,'assistant','我喜欢原神。');item['source_id']=mid;self.assertIsNone(self.mem.accept(self.peer,item))
        mid=self.chat.add(self.peer,'user','我喜欢鸣潮。');item['source_id']=mid;self.assertIsNone(self.mem.accept(self.peer,item))
    def test_third_party_and_hypothetical_are_pending(self):
        self.add('他说我喜欢鸣潮，但这是转发。');self.add('假设我喜欢鸣潮。',topic='假设')
        self.assertFalse(self.mem.retrieve(self.peer,'鸣潮'));self.assertTrue(all(x['status']=='pending' for x in self.mem.list_notes(self.peer)))
    def test_new_explicit_value_supersedes_old_and_is_reversible(self):
        first=self.add();last=self.add('我现在不喜欢鸣潮了。');rows=self.mem.list_notes(self.peer)
        self.assertEqual(rows[1]['status'],'superseded');self.assertEqual(rows[0]['status'],'active')
        self.mem.change(self.peer,last,'disabled');self.mem.change(self.peer,first,'active');self.assertIn('喜欢探索',self.mem.retrieve(self.peer,'鸣潮')[0]['content'])
    def test_stale_state_expires_and_no_duplicate_after_disable(self):
        with patch('qq_chat.time.time',return_value=1):nid=self.add('我最近很累，想早点睡。',kind='state',topic='疲惫')
        self.assertFalse(self.mem.retrieve(self.peer,'疲惫'));self.mem.change(self.peer,nid,'disabled')
        row=self.mem.list_notes(self.peer)[0];self.mem.accept(self.peer,{**row,'explicit':True});self.assertEqual(len(self.mem.list_notes(self.peer)),1)
    def test_old_context_is_indexed_and_no_other_peer_leaks(self):
        self.add()
        for _ in range(40):self.chat.add(self.peer,'user','今天随便聊点别的。')
        self.chat.add('other','user','我喜欢鸣潮里的秘密账号。')
        found=self.mem.recall(self.peer,'鸣潮探索地图');self.assertTrue(found['records']);self.assertNotIn('秘密账号',str(found))
        self.mem.sync_index(self.peer)
        with self.mem.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM chat_search WHERE peer=?',(self.peer,)).fetchone()[0],41)
    def test_style_correction_is_immediate_and_core_unchanged(self):
        before=self.chat.persona.load().model_dump();mid=self.chat.add(self.peer,'user','别每次都在结尾问问题。');self.mem.observe_correction(self.peer,mid)
        self.assertIn('结尾',str(self.mem.retrieve(self.peer,'你好')));self.assertEqual(before,self.chat.persona.load().model_dump())
    def test_browse_records_are_separate_from_user_facts(self):
        self.mem.add_episode(self.peer,'one','鸣潮更新','实际读到的公告。',[{'url':'https://example.com/a','quote':'公告原文证据'}])
        self.assertFalse(self.mem.list_notes(self.peer));self.assertTrue(self.mem.activities(self.peer,'鸣潮更新'));self.assertFalse(self.mem.activities('other'))
    def test_one_off_requests_are_not_preferences_or_future_plans(self):
        self.assertIsNone(self.add('唱一个小星星',kind='plan',topic='唱歌'))
        self.assertIsNone(self.add('要语音',topic='语音'))
        self.assertIsNone(self.add('帮我查一下鸣潮最新消息',kind='plan'))
    def test_explicit_nickname_updates_are_quoted_and_do_not_edit_card(self):
        before=self.chat.persona.load().model_dump();self.add('你叫鲸娘娅',kind='fact',topic='称呼')
        self.assertEqual(self.mem.list_notes(self.peer)[0]['status'],'active');self.assertEqual(before,self.chat.persona.load().model_dump())
    def test_recent_activity_question_recalls_actual_work(self):
        self.mem.add_episode(self.peer,'actual','DeepSeek工具调用','实际查阅的文档。',[{'url':'https://example.com','quote':'实际文档依据'}])
        self.assertIn('实际查阅的文档',self.mem.prompt(self.peer,'你最近在干嘛？')['content'])
        self.assertIsNone(self.mem.prompt('other','你最近在干嘛？'))

class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.peer='87654321';self.bot='12345678';self.now=datetime(2026,10,21,15,tzinfo=ZONE).timestamp()
        persona=PersonaStore(self.root);persona.save(dania_card());self.chat=SimpleNamespace(memory=ChatMemory(self.root/'data/qq',persona),persona=persona)
        self.bridge=QQBridge(self.root,self.chat);self.bridge.cfg.update(locked_bot_id=self.bot,test_contact={'name':'long','user_id':self.peer});self.bridge.configure(True,self.bot,[self.peer],True)
        self.bridge.connected=True;self.bridge.login_id=self.bot;self.bridge.epoch='x';self.sent=[];self.bridge.call=lambda *a:self.sent.append(a)
        with patch('qq_chat.time.time',return_value=self.now-3600):self.mid=self.chat.memory.add(self.peer,'user','我喜欢鸣潮地图探索。')
        self.proactive=ProactiveChat(self.root,self.bridge,lambda *_:response({}),lambda:self.now)
        self.service=CompanionLife(self.root,self.bridge,self.proactive,clock=lambda:self.now)
    def tearDown(self):self.service.close();self.proactive.close();self.bridge.pool.shutdown(wait=True);self.tmp.cleanup()
    def web_model(self):
        rows=[response({'action':'web','query':'鸣潮探索','reason':'读新内容'}),call('search_web',{'query':'鸣潮探索'}),call('read_page',{'url':'https://example.com/a'}),call('finish_activity',{'findings':[{'summary':'地图探索讨论','url':'https://example.com/a','quote':'这是实际读取的网页正文证据'}]})]
        return lambda *args:rows.pop(0)
    def wire(self):
        self.service.configure({'autonomy':True})
        self.service.client=self.web_model();self.service.search=lambda q:{'results':[{'url':'https://example.com/a'}]}
        self.service.reader=lambda url:{'ok':True,'text':'这是实际读取的网页正文证据，后面还有正文。','provider':'test-mcp'}
    def test_autonomous_plan_execution_records_source_never_sends(self):
        self.wire();r=self.service.activity_once();self.assertTrue(r['completed']);self.assertFalse(self.sent);self.assertEqual(len(self.service.memory.activities(self.peer)),1)
        with self.service.memory.db() as db:self.assertEqual(db.execute("SELECT state FROM jobs WHERE kind='activity'").fetchone()[0],'completed')
    def test_unread_url_or_invented_quote_does_not_become_activity(self):
        self.wire();self.service.reader=lambda url:{'ok':True,'text':'没有所需证据的页面'}
        with self.assertRaises((ValueError,IndexError)):self.service.activity_once()
        self.assertFalse(self.service.memory.activities(self.peer));self.assertFalse(self.sent)
    def test_no_actions_when_paused_busy_or_outside_scope(self):
        self.wire();self.service.configure({'enabled':False})
        with self.assertRaises(ValueError):self.service.activity_once()
        self.service.configure({'enabled':True})
        with patch('qq_chat.time.time',return_value=self.now):self.chat.memory.add(self.peer,'user','刚来的消息')
        with self.assertRaises(ValueError):self.service.activity_once()
        self.bridge.cfg['allowed_private_ids'].append('99999999')
        with self.assertRaises(ValueError):self.service.state()
        self.assertFalse(self.sent)
    def test_learning_cites_real_utterances_and_advances_cursor(self):
        self.service.client=lambda *_:response({'memories':[{'kind':'preference','topic':'鸣潮','content':'喜欢探索','quote':'我喜欢鸣潮地图探索。','source_id':self.mid,'explicit':True}]})
        result=self.service.learning_once();self.assertEqual(result['saved'],1);self.assertEqual(self.service.learning_once()['processed'],0)
        self.assertTrue(self.chat.memory.life.retrieve(self.peer,'鸣潮'));self.assertFalse(self.sent)
    def test_failed_learning_does_not_advance_and_retries_are_bounded(self):
        self.service.client=lambda *_:({'content':'invalid'}, {})
        with self.assertRaises(ValueError):self.service.learning_once()
        self.assertEqual(self.service.memory.cursor(self.peer,'learning'),0)
    def test_wait_and_daily_budget_survive_restart(self):
        self.service.configure({'autonomy':True})
        self.service.client=lambda *_:response({'action':'wait','query':'','reason':'无事'})
        self.service.activity_once();self.service.activity_once()
        with self.assertRaises(ValueError):self.service.activity_once()
        self.assertFalse(self.sent)
    def test_new_install_requires_opt_in_before_autonomous_browsing(self):
        self.assertFalse(self.service.config()['autonomy'])
        with self.assertRaises(ValueError):self.service.activity_once()
    def test_config_disable_cancels_before_next_tool(self):
        self.wire();original=self.service.client
        def client(*args):
            r=original(*args)
            if r[0].get('tool_calls'):self.service.configure({'enabled':False})
            return r
        self.service.client=client
        with self.assertRaises(ValueError):self.service.activity_once()
        self.assertFalse(self.service.memory.activities(self.peer));self.assertFalse(self.sent)
    def test_multiple_proposed_tools_are_replanned_before_any_execution(self):
        self.wire();normal=self.service.client;first=[True]
        def client(*args):
            if len(args)>1 and args[1] and first[0]:
                first[0]=False;a=call('search_web',{'query':'不该执行'})[0];a['tool_calls']*=2;return a,{}
            return normal(*args)
        plan=normal(None,None);self.service.client=lambda *_:plan
        original=self.service.web_activity
        def web(*args):self.service.client=client;return original(*args)
        self.service.web_activity=web
        self.assertTrue(self.service.activity_once()['completed']);self.assertFalse(self.sent)
    def test_routes_require_auth_and_previews_do_not_create_messages(self):
        app=FastAPI()
        def auth(authorization:str=Header(default='')):
            if authorization!='Bearer test':raise HTTPException(401)
        register_life_routes(app,root=self.root,authorize=auth,bridge=self.bridge,proactive=self.proactive)
        client=TestClient(app);self.assertEqual(client.get('/qq/life/state').status_code,401)
        h={'Authorization':'Bearer test'};self.assertEqual(client.get('/qq/life/state',headers=h).status_code,200)
        self.assertEqual(client.post('/qq/life/config',headers=h,json={'topics':['鸣潮'],'interval_minutes':1}).status_code,422)

class RhythmTests(unittest.TestCase):
    def test_excessive_questions_and_false_game_claims_are_addressed(self):
        p=guidance('你会操作吗？',[('assistant','打瓦我会一点。你会吗？'),('assistant','模拟器也行，你拉我吗？')])['content']
        self.assertIn('不再习惯性抛问题',p);self.assertIn('没有FPS游戏操作',p)
        self.assertIn('没有FPS游戏操作',guidance('你真会用模拟器陪我打瓦吗？',[])['content'])
    def test_preview_reads_memories_without_writing_a_chat_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            chat=QQConversation(Path(tmp),client=lambda *_:({'content':'接着聊。'},{}));chat.memory.add('long','user','我喜欢鸣潮地图探索。')
            self.assertEqual(chat.prepare('long','之前的地图',remember=False)['reply'],'接着聊。')
            with chat.memory.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM messages').fetchone()[0],1)

if __name__=='__main__':unittest.main()
