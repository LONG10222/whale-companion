from pathlib import Path
import json,tempfile,time,unittest
from unittest.mock import patch
from fastapi import FastAPI,Header,HTTPException
from fastapi.testclient import TestClient
from persona_learning import LearningStore,persona_signature,valid_memory_candidate
from persona_store import PersonaStore,dania_card
from qq_chat import ChatMemory,QQConversation
from qq_bridge import QQBridge
from learning_routes import register_learning_routes

class LearningTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.card=dania_card();self.personas=PersonaStore(self.root);self.personas.save(self.card)
        self.memory=ChatMemory(self.root/'data/qq',self.personas);self.store=self.memory.learning;self.peer='87654321'
        self.memory.add(self.peer,'user','今天好累。');self.mid=self.memory.add(self.peer,'assistant','建议合理安排作息。')
        (self.root/'qq.local.json').write_text(json.dumps({'test_contact':{'name':'owner','user_id':self.peer}}),encoding='utf-8')
        (self.root/'configs/local-learning.local.json').write_text('{"mode":"cloud"}',encoding='utf-8')
    def tearDown(self):self.tmp.cleanup()
    def feedback(self,rating='rewrite',correction='先歇一会儿，今天已经够忙了。'):
        return self.store.feedback(self.peer,self.mid,rating,'少一点客服语气',correction,self.card)
    def test_feedback_is_used_next_turn_and_does_not_modify_core(self):
        before=self.personas.path.read_bytes();self.feedback();self.memory.add(self.peer,'user','我今天很累，事情好多。')
        context=str(self.memory.context(self.peer));self.assertIn('先歇一会儿',context)
        self.assertEqual(before,self.personas.path.read_bytes())
    def test_pending_disabled_expired_and_other_peer_are_excluded(self):
        for status in ['pending','approved']:
            row=self.store.add_item(self.peer,self.card,'preference',content='candidate '+status,status=status)
            if status=='approved':self.store.change_item(self.peer,row,'disabled')
        self.store.add_item(self.peer,self.card,'state',content='expired',expires=time.time()-1)
        self.store.add_item('99999999',self.card,'preference',content='OTHER_PEER')
        self.assertEqual(self.store.retrieve(self.peer,'任何消息',self.card),[])
    def test_undo_feedback_removes_learning_without_erasing_chat(self):
        self.feedback();self.store.undo(self.peer)
        self.assertFalse(self.store.retrieve(self.peer,'今天好累',self.card))
        self.assertFalse(self.store.retrieve(self.peer,'正常聊就行',self.card))
        self.assertEqual(len(self.store.recent(self.peer)),1)
        self.assertEqual(self.store.export(self.peer,self.card,'dpo'),'')
    def test_replacing_feedback_and_undo_restores_old_rating(self):
        self.store.feedback(self.peer,self.mid,'like','','',self.card);self.feedback();self.store.undo(self.peer)
        rows=self.store.list_feedback(self.peer);self.assertEqual([r['rating'] for r in rows if r['active']],['like'])
        examples=self.store.retrieve(self.peer,'今天好累',self.card);self.assertEqual(examples[0]['answer'],'建议合理安排作息。')
    def test_persona_change_suppresses_old_expressions_but_not_user_facts(self):
        self.feedback();self.store.add_item(self.peer,self.card,'fact',content='喜欢鸣潮',keywords=['鸣潮'])
        new=self.card.model_copy(update={'personality':'新角色的性格'})
        selected=self.store.retrieve(self.peer,'今天好累，聊聊鸣潮',new)
        self.assertFalse(any(r['kind']=='expression' for r in selected));self.assertTrue(any(r['kind']=='fact' for r in selected))
    def test_only_same_peer_assistant_messages_can_receive_feedback(self):
        uid=self.memory.add(self.peer,'user','普通用户消息')
        for peer,mid in [('99999999',self.mid),(self.peer,uid),(self.peer,9999)]:
            with self.assertRaises(ValueError):self.store.feedback(peer,mid,'like','','',self.card)
    def test_export_has_reviewed_pairs_and_stops_after_disable(self):
        self.feedback();sft=json.loads(self.store.export(self.peer,self.card,'sft'));dpo=json.loads(self.store.export(self.peer,self.card,'dpo'))
        self.assertEqual(sft['conversations'][1]['value'],dpo['chosen']['value']);self.assertNotIn('peer',dpo)
        row=self.store.list_items(self.peer,self.card)[0];self.store.change_item(self.peer,row['id'],'disabled')
        self.assertEqual(self.store.export(self.peer,self.card,'sft'),'');self.assertEqual(self.store.export(self.peer,self.card,'dpo'),'')
    def test_edit_undo_and_expiry(self):
        row=self.store.add_item(self.peer,self.card,'preference',content='旧偏好')
        self.store.edit_item(self.peer,row,self.card,'新偏好','','','通用',[],0)
        self.assertIn('新偏好',str(self.store.retrieve(self.peer,'你好',self.card)));self.store.undo(self.peer)
        self.assertIn('旧偏好',str(self.store.retrieve(self.peer,'你好',self.card)))
    def test_keyword_preferences_do_not_appear_on_unrelated_topics(self):
        self.store.add_item(self.peer,self.card,'preference',content='正常语速',keywords=['语音','语速'])
        self.assertFalse(self.store.retrieve(self.peer,'今天好累',self.card))
        self.assertTrue(self.store.retrieve(self.peer,'你发语音的时候',self.card))
    def test_third_party_and_disowned_preferences_are_not_profile_candidates(self):
        source='视频里的那个人说他喜欢猫，这是他说的，不是我的偏好。'
        self.assertFalse(valid_memory_candidate({'source_id':1,'kind':'preference','content':'喜欢猫','quote':source},{1:source}))
        own='我喜欢轻松自然的聊天。'
        self.assertTrue(valid_memory_candidate({'source_id':1,'kind':'preference','content':'轻松自然的聊天','quote':own},{1:own}))
    def test_qq_commands_are_explicit_and_quote_must_belong_to_peer(self):
        self.assertIsNone(self.store.command(self.peer,'学习 Python',self.card))
        self.assertIsNone(self.store.command(self.peer,'这句挺好的',self.card))
        self.store.delivery(self.peer,'remote-42',self.mid)
        self.store.command(self.peer,'反馈 改写：先缓一缓吧。',self.card,'remote-42')
        self.assertIn('先缓一缓吧',str(self.store.retrieve(self.peer,'今天好累',self.card)))
        with self.assertRaises(ValueError):self.store.command(self.peer,'反馈 喜欢',self.card,'not-our-message')
    def app(self):
        app=FastAPI()
        def auth(authorization:str=Header(default='')):
            if authorization!='Bearer local-test':raise HTTPException(401)
        register_learning_routes(app,authorize=auth,root=self.root);return app
    def test_data_and_mutation_routes_are_authenticated_and_scoped(self):
        with TestClient(self.app()) as client:
            self.assertEqual(client.get('/qq/learning/state').status_code,401)
            self.assertEqual(client.post('/qq/learning/undo',json={}).status_code,401)
            self.assertEqual(client.get('/qq/learning/export').status_code,401)
            h={'Authorization':'Bearer local-test'}
            state=client.get('/qq/learning/state',headers=h).json();self.assertEqual(state['contact'],'owner');self.assertEqual(len(state['turns']),1)
            wrong=self.memory.add('99999999','assistant','不能评价我')
            self.assertEqual(client.post('/qq/learning/feedback',headers=h,json={'message_id':wrong,'rating':'like'}).status_code,400)
    def test_candidate_extraction_is_pending_and_requires_literal_user_evidence(self):
        result={'content':json.dumps({'items':[{'kind':'preference','content':'明确觉得今天累','source_id':1,'quote':'今天好累','keywords':['累']},
            {'kind':'fact','content':'捏造的关系','source_id':1,'quote':'用户没说过的话','keywords':[]}]},ensure_ascii=False)}
        with TestClient(self.app()) as client,patch('learning_routes.deepseek_client.complete',return_value=(result,{})):
            r=client.post('/qq/learning/extract',headers={'Authorization':'Bearer local-test'},json={})
            self.assertEqual(r.status_code,200);self.assertEqual(r.json()['candidates'],1)
            self.assertEqual(self.store.list_items(self.peer,self.card)[0]['status'],'pending')
            self.assertEqual(self.store.retrieve(self.peer,'今天好累',self.card),[])
    def test_preview_and_evaluation_do_not_write_chat_or_invoke_tools(self):
        self.feedback();before=self.personas.path.read_bytes()
        with TestClient(self.app()) as client,patch('learning_routes.deepseek_client.complete',return_value=({'content':'嗯，先歇会儿。'},{})) as model:
            h={'Authorization':'Bearer local-test'}
            preview=client.post('/qq/learning/preview',headers=h,json={'message':'今天好累'}).json()
            self.assertFalse(preview['sent_to_qq']);self.assertFalse(preview['memory_written']);self.assertTrue(preview['selected'])
            report=client.post('/qq/learning/evaluate',headers=h,json={'full':True}).json()
            self.assertEqual(len(report['cases']),6);self.assertIsNone(report['automatic_persona_score'])
            self.assertTrue(all(call.args[1] is None for call in model.call_args_list))
        self.assertEqual(before,self.personas.path.read_bytes());self.assertEqual(len(self.store.recent(self.peer)),1)
    def test_assistant_suggestions_and_dataset_audit_are_drafts_only(self):
        self.feedback();before=self.personas.path.read_bytes();initial=len(self.store.list_items(self.peer,self.card))
        h={'Authorization':'Bearer local-test'}
        with TestClient(self.app()) as client,patch('learning_routes.assistant_complete',return_value={'data':{'issues':['太正式'],'suggestions':['先歇会儿。']},'metrics':{'source':'local'}}):
            self.assertEqual(client.post('/qq/learning/suggest',json={'message_id':self.mid}).status_code,401)
            result=client.post('/qq/learning/suggest',headers=h,json={'message_id':self.mid,'reason':'太客服'}).json()
            self.assertFalse(result['saved']);self.assertEqual(result['data']['suggestions'],['先歇会儿。'])
        with TestClient(self.app()) as client,patch('learning_routes.assistant_complete',return_value={'data':{'entries':[]},'metrics':{'source':'local'}}):
            result=client.post('/qq/learning/curate',headers=h,json={}).json();self.assertFalse(result['modified'])
        self.assertEqual(before,self.personas.path.read_bytes());self.assertEqual(initial,len(self.store.list_items(self.peer,self.card)))
    def test_qq_control_reply_does_not_become_next_feedback_target(self):
        chat=QQConversation(self.root,client=lambda *_:({'content':'不应调用模型'},{}))
        bridge=QQBridge(self.root,chat);bridge.cfg.update(enabled=True,bot_id='12345678',allowed_private_ids=[self.peer])
        bridge.connected=True;bridge.login_id='12345678';bridge.epoch='same';sent=[]
        bridge.call=lambda action,params,**kw:sent.append(params) or {'message_id':42}
        event={'post_type':'message','message_type':'private','sub_type':'friend','self_id':'12345678','user_id':self.peer,'message_id':123,
            'message':[{'type':'text','data':{'text':'反馈 喜欢'}}]}
        try:
            self.assertTrue(bridge.accept(event,'same'));bridge.pool.submit(lambda:None).result(timeout=3)
            self.assertEqual(len(sent),1);self.assertEqual(self.store.target(self.peer)['id'],self.mid)
            self.assertEqual(self.store.list_feedback(self.peer)[0]['rating'],'like')
        finally:bridge.pool.shutdown(wait=True)

if __name__=='__main__':unittest.main()
