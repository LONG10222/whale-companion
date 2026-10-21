from pathlib import Path
import json,tempfile,unittest
from chat_style import sticker_request
from qq_chat import QQConversation
from qq_bridge import QQBridge,QQActionRejected
from persona_learning import LearningStore

def sticker_call():
    return {'tool_calls':[{'id':'sticker-1','type':'function','function':{'name':'use_sticker',
        'arguments':json.dumps({'emotion':'开心','query':'可爱','theme':'鸣潮','character':'','count':3},ensure_ascii=False)}}]}

class Library:
    def __init__(self,root):self.root=root
    def search(self,**kwargs):return {'total':5,'items':[{'id':str(i),'bytes':100,'caption':'可爱表情'+str(i)} for i in range(5)]}
    def file(self,item_id):return self.root/(item_id+'.png')

class ChatStyleTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def chat(self,client):
        chat=QQConversation(self.root,client=client);chat.library=Library(self.root);return chat
    def test_multiple_stickers_are_distinct_and_have_no_narration_or_extra_model_call(self):
        calls=[]
        def model(messages,tools):calls.append(messages);return sticker_call(),{}
        chat=self.chat(model);result=chat.prepare('peer','多发几张可爱的鸣潮表情包',remember=False)
        self.assertEqual(len(result['attachments']),3);self.assertEqual(result['reply'],'');self.assertEqual(len(calls),1)
        self.assertEqual(len({a['id'] for a in result['attachments']}),3)
        self.assertNotIn('description',result['tools'][0]['result']);self.assertEqual(result['tools'][0]['result']['prepared_count'],3)
    def test_followup_one_sticker_uses_sent_image_context(self):
        chat=self.chat(lambda *_:(sticker_call(),{}))
        chat.memory.add('peer','assistant','[已发送3张表情]')
        result=chat.prepare('peer','再发一张',remember=False)
        self.assertEqual(len(result['attachments']),1);self.assertEqual(result['reply'],'')
    def test_attachment_events_are_not_assistant_dialogue_examples(self):
        chat=self.chat(lambda *_:({'content':'嘿嘿'},{}))
        chat.memory.add('peer','user','发一些表情包');chat.memory.add('peer','assistant','[已发送3张表情]')
        context=chat.memory.context('peer')
        self.assertFalse(any(r['role']=='assistant' and '[已发送' in r['content'] for r in context))
        self.assertTrue(any(r['role']=='system' and '历史附件事件' in r['content'] for r in context))
    def test_explicit_no_explanation_also_covers_a_spontaneous_sticker(self):
        chat=self.chat(lambda *_:(sticker_call(),{}))
        result=chat.prepare('peer','啊，能不能不要解释啊？',remember=False)
        self.assertEqual(len(result['attachments']),1);self.assertEqual(result['reply'],'')
    def test_accepting_offer_and_count_limit(self):
        history=[('user','发一些表情包'),('assistant','下次再给你翻几张别的。')]
        self.assertEqual(sticker_request('我看看',history)['count'],3)
        self.assertTrue(sticker_request('我看看',history)['requested'])
        self.assertTrue(sticker_request('给我发三张',history)['requested'])
        self.assertEqual(sticker_request('发九张表情包')['count'],3)
        self.assertFalse(sticker_request('你可以发什么表情包')['requested'])
        self.assertTrue(sticker_request('不要发表情包')['forbid'])
        self.assertTrue(sticker_request('别解释，只发表情包')['images_only'])
    def test_model_cannot_claim_sticker_delivery_without_preparing_it(self):
        calls=[]
        def model(messages,tools):calls.append(messages);return {'content':'发啦，就是刚才那张。'},{}
        result=self.chat(model).prepare('peer','再发一张表情包',remember=False)
        self.assertEqual(len(calls),2);self.assertFalse(result['attachments']);self.assertEqual(result['reply'],'刚才这条没发出去。')
    def test_explicit_no_sticker_request_is_enforced(self):
        calls=[]
        def model(*_):calls.append(1);return (sticker_call() if len(calls)==1 else {'content':'好。'}),{}
        result=self.chat(model).prepare('peer','不要发表情包',remember=False)
        self.assertFalse(result['attachments']);self.assertFalse(result['tools'][0]['result']['prepared'])
    def test_prepare_failure_is_acknowledged_once_and_does_not_claim_success(self):
        chat=self.chat(lambda *_:(_ for _ in ()).throw(RuntimeError('model unavailable')))
        bridge=QQBridge(self.root,chat);bridge.configure(True,'12345678',['87654321'],True)
        bridge.connected=True;bridge.login_id='12345678';bridge.epoch='test';sent=[]
        bridge.call=lambda action,params,**kw:sent.append(params) or {'message_id':42}
        event={'message_type':'private','sub_type':'friend','self_id':12345678,'user_id':87654321,'message_id':1,
            'message':[{'type':'text','data':{'text':'再发一张表情包'}}]}
        try:
            self.assertTrue(bridge.accept(event,'test'));bridge.pool.submit(lambda:None).result(timeout=3)
            self.assertEqual(len(sent),1);self.assertIn('没处理好',sent[0]['message'][0]['data']['text'])
            self.assertTrue(chat.memory.last_delivery_failed('87654321'))
        finally:bridge.pool.shutdown(wait=True)
    def test_image_only_delivery_is_recorded_as_images_and_excluded_from_language_training(self):
        chat=self.chat(lambda *_:(sticker_call(),{}));bridge=QQBridge(self.root,chat)
        bridge.configure(True,'12345678',['87654321'],True);bridge.connected=True;bridge.login_id='12345678';bridge.epoch='test'
        sent=[];bridge.call=lambda action,params,**kw:sent.append(params) or {'message_id':42}
        event={'message_type':'private','sub_type':'friend','self_id':12345678,'user_id':87654321,'message_id':1,
            'message':[{'type':'text','data':{'text':'多发几张表情包'}}]}
        try:
            self.assertTrue(bridge.accept(event,'test'));bridge.pool.submit(lambda:None).result(timeout=3)
            self.assertEqual(len(sent[0]['message']),3);self.assertTrue(all(m['type']=='image' for m in sent[0]['message']))
            target=chat.learning.target('87654321');self.assertTrue(target['answer'].startswith('[已发送3张表情]'));self.assertFalse(target['text_only'])
        finally:bridge.pool.shutdown(wait=True)
    def test_definite_rejection_sends_notice_instead_of_retrying_original_payload(self):
        chat=self.chat(lambda *_:(sticker_call(),{}));bridge=QQBridge(self.root,chat)
        bridge.configure(True,'12345678',['87654321'],True);bridge.connected=True;bridge.login_id='12345678';bridge.epoch='test'
        sent=[]
        def send(action,params,**kw):
            sent.append(params)
            if len(sent)==1:raise QQActionRejected('QQ rejected image')
            return {'message_id':42}
        bridge.call=send
        event={'message_type':'private','sub_type':'friend','self_id':12345678,'user_id':87654321,'message_id':1,
            'message':[{'type':'text','data':{'text':'发一张表情包'}}]}
        try:
            self.assertTrue(bridge.accept(event,'test'));bridge.pool.submit(lambda:None).result(timeout=3)
            self.assertEqual(len(sent),2);self.assertEqual(sent[0]['message'][0]['type'],'image');self.assertEqual(sent[1]['message'][0]['type'],'text')
        finally:bridge.pool.shutdown(wait=True)

if __name__=='__main__':unittest.main()
