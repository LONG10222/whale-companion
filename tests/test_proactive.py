from pathlib import Path
from types import SimpleNamespace
from datetime import datetime
from unittest.mock import patch
from contextlib import closing
import json,sqlite3,tempfile,unittest
from fastapi import FastAPI,Header,HTTPException
from fastapi.testclient import TestClient
from qq_chat import ChatMemory
from qq_bridge import QQBridge,QQActionRejected
from persona_store import PersonaStore,dania_card
from proactive_chat import ProactiveChat,ZONE
from proactive_routes import register_proactive_routes

class ProactiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.now=datetime(2026,10,21,10,tzinfo=ZONE).timestamp()
        self.peer='87654321';self.bot='12345678';self.persona=PersonaStore(self.root);self.persona.save(dania_card())
        self.memory=ChatMemory(self.root/'data/qq',self.persona)
        with patch('qq_chat.time.time',return_value=self.now-10800):
            self.memory.add(self.peer,'user','今天想玩鸣潮，跑一跑地图。');self.memory.add(self.peer,'assistant','慢慢逛也挺好。')
        self.chat=SimpleNamespace(memory=self.memory,persona=self.persona)
        self.bridge=QQBridge(self.root,self.chat);self.bridge.cfg.update(locked_bot_id=self.bot,test_contact={'name':'owner','user_id':self.peer})
        self.bridge.configure(True,self.bot,[self.peer],True);self.bridge.connected=True;self.bridge.login_id=self.bot;self.bridge.epoch='test'
        self.sent=[];self.calls=[]
        self.bridge.call=lambda action,params=None,**kwargs:self.sent.append((action,params)) or {'message_id':42}
        def model(messages,tools):
            self.calls.append(messages)
            return {'content':json.dumps({'should_contact':True,'topic':'鸣潮探索','message':'鸣潮那张地图，逛着逛着是不是又走偏了？','reason':'接着之前聊过的探索话题。','source_ids':[1]})},{}
        self.service=ProactiveChat(self.root,self.bridge,model,lambda:self.now);self.bridge.proactive=self.service
        self.message_clock=patch('qq_chat.time.time',side_effect=lambda:self.now);self.message_clock.start()
    def tearDown(self):
        self.message_clock.stop()
        self.service.close();self.bridge.pool.shutdown(wait=True);self.tmp.cleanup()
    def draft(self):return self.service.generate(manual=True)
    def inbound(self,text='你好',mid=10,**overrides):
        return {'post_type':'message','self_id':self.bot,'user_id':self.peer,'message_id':mid,'time':self.now,
            'message_type':'private','sub_type':'friend','message':[{'type':'text','data':{'text':text}}],**overrides}
    def test_timer_only_prepares_once_and_never_sends(self):
        self.service.tick();self.assertEqual(len(self.calls),1);self.assertEqual(self.service.state()['drafts'][0]['state'],'draft')
        self.now+=120;self.service.tick();self.assertEqual(len(self.calls),1);self.assertFalse(self.sent)
        self.assertFalse(self.service.state()['automatic_send'])
    def test_manual_send_is_exact_once_and_fixed_peer(self):
        d=self.draft();r=self.service.send_manual(d['id'],d['message']);self.assertTrue(r['receipt_received'])
        self.assertEqual(self.sent,[('send_private_msg',{'user_id':int(self.peer),'message':[{'type':'text','data':{'text':d['message']}}]})])
        with self.assertRaises(ValueError):self.service.send_manual(d['id'],d['message'])
        self.assertEqual(len(self.sent),1)
    def test_quiet_hours_prevent_auto_generation_and_manual_send(self):
        self.now=datetime(2026,10,21,23,tzinfo=ZONE).timestamp();self.service.tick();self.assertFalse(self.calls)
        d=self.draft()
        with self.assertRaises(ValueError):self.service.send_manual(d['id'],d['message'])
        self.assertFalse(self.sent)
    def test_idle_is_measured_from_both_directions(self):
        with self.memory.db() as db:db.execute("UPDATE messages SET at=? WHERE role='assistant'",(self.now-60,))
        self.service.tick();self.assertFalse(self.calls);self.assertFalse(self.service.eligibility()['allowed'])
    def test_no_history_no_auto_contact(self):
        with self.memory.db() as db:db.execute('DELETE FROM messages')
        self.service.tick();self.assertFalse(self.calls)
    def test_wrong_bot_or_contact_scope_cannot_send(self):
        for key,value in [('allowed_private_ids',[self.peer,'99999999']),('locked_bot_id','99999999')]:
            old=self.bridge.cfg[key];self.bridge.cfg[key]=value
            with self.assertRaises(ValueError):self.draft()
            self.bridge.cfg[key]=old
        self.bridge.login_id='99999999';self.assertFalse(self.service.eligibility()['allowed']);self.assertFalse(self.sent)
    def test_message_while_model_generates_invalidates_draft(self):
        original=self.service.client
        def model(*args):
            result=original(*args);self.service.incoming(self.inbound());return result
        self.service.client=model;d=self.draft();self.assertEqual(d['state'],'stale');self.assertFalse(self.sent)
    def test_new_inbound_even_unsupported_file_invalidates_and_delays(self):
        d=self.draft();event=self.inbound(message=[{'type':'file','data':{'file':'notes.txt'}}])
        self.assertFalse(self.bridge.accept(event,'test'));self.assertEqual(self.service.state()['drafts'][0]['state'],'stale')
        self.assertEqual(self.service.eligibility()['idle_minutes'],0)
        with self.assertRaises(ValueError):self.service.send_manual(d['id'],d['message'])
    def test_inbound_dedup_and_unauthorized_peer(self):
        self.service.incoming(self.inbound());token=self.service.context_token(self.peer,self.persona.load())
        self.service.incoming(self.inbound());self.assertEqual(token,self.service.context_token(self.peer,self.persona.load()))
        self.bridge.accept(self.inbound(user_id='99999999',mid=20),'test');self.assertEqual(token,self.service.context_token(self.peer,self.persona.load()))
    def test_opt_out_and_today_snooze(self):
        for mid,text in enumerate(['别主动找我','今天别找我'],20):
            self.service.incoming(self.inbound(text,mid=mid));self.assertTrue(self.service.eligibility()['contact_opt_out'])
            self.service.resume_contact();self.assertFalse(self.service.eligibility()['contact_opt_out'])
    def test_daily_limit_and_no_followup_without_reply(self):
        d=self.draft();self.service.send_manual(d['id'],d['message']);self.assertEqual(self.service.eligibility()['daily_attempts'],1)
        self.now+=86400;elig=self.service.eligibility();self.assertEqual(elig['daily_attempts'],0);self.assertTrue(elig['awaiting_reply'])
        self.service.incoming(self.inbound(mid=30));self.now+=7201;self.assertTrue(self.service.eligibility()['allowed'])
    def test_unknown_receipt_blocks_resend_across_restart(self):
        d=self.draft()
        def unknown(*args,**kwargs):self.sent.append(args);raise RuntimeError('no receipt')
        self.bridge.call=unknown
        with self.assertRaises(RuntimeError):self.service.send_manual(d['id'],d['message'])
        restarted=ProactiveChat(self.root,self.bridge,self.service.client,lambda:self.now)
        self.assertEqual(restarted.state()['deliveries'][0]['status'],'uncertain');restarted.tick()
        with self.assertRaises(ValueError):restarted.send_manual(d['id'],d['message'])
        self.assertEqual(len(self.sent),1)
    def test_definite_rejection_does_not_reuse_failed_draft(self):
        d=self.draft();self.bridge.call=lambda *args,**kwargs:(_ for _ in ()).throw(QQActionRejected('rejected'))
        with self.assertRaises(QQActionRejected):self.service.send_manual(d['id'],d['message'])
        self.assertEqual(self.service.state()['deliveries'][0]['status'],'failed')
        with self.assertRaises(ValueError):self.service.send_manual(d['id'],d['message'])
    def test_unsaved_edits_and_changed_persona_are_rejected(self):
        d=self.draft()
        with self.assertRaises(ValueError):self.service.send_manual(d['id'],'未保存的内容')
        self.service.edit(d['id'],'保存后的内容');self.persona.save(self.persona.load().model_copy(update={'style':'更简短'}))
        with self.assertRaises(ValueError):self.service.send_manual(d['id'],'保存后的内容')
        self.assertFalse(self.sent)
    def test_pause_stales_existing_draft(self):
        self.draft();self.service.configure(False);self.assertEqual(self.service.state()['drafts'][0]['state'],'stale');self.service.tick();self.assertEqual(len(self.calls),1)
    def test_second_check_prevents_crossing_quiet_hour(self):
        d=self.draft();original=self.service.eligibility;count=[0]
        def check(*args,**kwargs):
            count[0]+=1
            if kwargs.get('ignore_delivery_id'):self.now=datetime(2026,10,21,22,tzinfo=ZONE).timestamp()
            return original(*args,**kwargs)
        self.service.eligibility=check
        with self.assertRaises(QQActionRejected):self.service.send_manual(d['id'],d['message'])
        self.assertFalse(self.sent)
    def test_known_receipt_survives_memory_write_failure(self):
        d=self.draft()
        with patch.object(self.memory,'add',side_effect=RuntimeError('disk error')):result=self.service.send_manual(d['id'],d['message'])
        self.assertFalse(result['memory_saved']);self.assertEqual(self.service.state()['deliveries'][0]['status'],'sent')
    def test_proactive_openers_are_not_reply_training_examples(self):
        d=self.draft();self.service.send_manual(d['id'],d['message']);target=self.memory.learning.target(self.peer)
        self.assertEqual(target['origin'],'proactive');self.assertFalse(target['text_only']);self.assertIn('主动开场',target['question'])
        self.memory.learning.feedback(self.peer,target['id'],'like','','',self.persona.load())
        self.assertEqual(self.memory.learning.export(self.peer,self.persona.load(),'sft'),'')
    def test_model_can_skip_or_fail_without_sending(self):
        self.service.client=lambda *_:({'content':'{"should_contact":false,"message":"","topic":"","reason":"今天没有自然的话题","source_ids":[]}'},{})
        self.assertEqual(self.draft()['state'],'skipped');self.assertFalse(self.sent)
        self.now+=61;self.service.client=lambda *_:({'content':'not json'}, {})
        with self.assertRaises(ValueError):self.draft()
        self.assertFalse(self.sent)
    def test_expired_and_busy_normal_chat_cannot_send(self):
        d=self.draft();self.now+=21601
        with self.assertRaises(ValueError):self.service.send_manual(d['id'],d['message'])
        self.now=datetime(2026,10,22,10,tzinfo=ZONE).timestamp();d=self.draft()
        with self.memory.db() as db:db.execute('INSERT INTO events VALUES(?,?,?,?,?)',(self.bot,'pending',self.peer,'accepted',self.now))
        with self.assertRaises(ValueError):self.service.send_manual(d['id'],d['message'])
    def test_routes_require_token_and_cannot_override_recipient(self):
        app=FastAPI()
        def auth(authorization:str=Header(default='')):
            if authorization!='Bearer own':raise HTTPException(401)
        register_proactive_routes(app,authorize=auth,root=self.root,bridge=self.bridge)
        client=TestClient(app);self.assertEqual(client.get('/qq/proactive/state').status_code,401)
        headers={'Authorization':'Bearer own'};r=client.get('/qq/proactive/state',headers=headers)
        self.assertEqual(r.status_code,200);self.assertNotIn(self.bridge.cfg['onebot_token'],r.text)
        r=client.post('/qq/proactive/send',headers=headers,json={'draft_id':'a'*32,'approved_text':'hi','peer':'99999999'})
        self.assertEqual(r.status_code,422);self.assertFalse(self.sent)

class LegacyOriginTest(unittest.TestCase):
    def test_existing_database_is_migrated_without_losing_chat(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);data=root/'data/qq';data.mkdir(parents=True)
            with closing(sqlite3.connect(data/'qq-memory.sqlite3')) as db:
                db.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY,peer TEXT,role TEXT,content TEXT,at REAL)')
                db.execute("INSERT INTO messages VALUES(1,'p','user','原来的聊天',1)")
                db.commit()
            m=ChatMemory(data)
            with m.db() as db:self.assertEqual(db.execute('SELECT content,origin FROM messages').fetchone(),('原来的聊天','chat'))

if __name__=='__main__':unittest.main()
