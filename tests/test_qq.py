from pathlib import Path
import json,tempfile,threading,time,unittest
from qq_chat import QQConversation,ChatMemory
from qq_bridge import QQBridge

class DummyChat:
    def __init__(self,root):self.memory=ChatMemory(Path(root)/'data/qq');self.entered=threading.Event();self.proceed=threading.Event();self.proceed.set();self.client=lambda *_:({'content':'摘要'}, {})
    def prepare(self,peer,text,voice_enabled=True,images=None,media_context=None):
        self.last_text=text;self.last_images=images;self.last_media=media_context
        self.entered.set();self.proceed.wait(3);return {'reply':'你好呀','attachments':[],'tools':[]}

class QQTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.chat=DummyChat(self.root)
        self.bridge=QQBridge(self.root,self.chat);self.bridge.configure(True,'12345678',['87654321'],True)
        self.bridge.connected=True;self.bridge.login_id='12345678';self.bridge.epoch='connection-one';self.sent=[]
        self.bridge.call=lambda action,params=None,timeout=20:self.sent.append((action,params)) or {'message_id':42}
        self.obj={'post_type':'message','message_type':'private','sub_type':'friend','self_id':12345678,'user_id':87654321,'message_id':111,
                  'message':[{'type':'text','data':{'text':'你好'}}]}
    def tearDown(self):
        self.chat.proceed.set();self.bridge.pool.shutdown(wait=True);self.tmp.cleanup()
    def settle(self):
        self.bridge.pool.submit(lambda:None).result(timeout=5)
    def test_only_bound_account_and_allowed_friend(self):
        self.assertTrue(self.bridge.accept(self.obj,'connection-one'));self.settle()
        self.assertEqual(self.sent[0][0],'send_private_msg');self.assertEqual(self.sent[0][1]['user_id'],87654321)
    def test_groups_strangers_self_and_wrong_account_are_ignored(self):
        for override in [{'message_type':'group'},{'sub_type':'group'},{'user_id':55555555},{'user_id':12345678},{'self_id':22222222}]:
            self.assertFalse(self.bridge.accept({**self.obj,**override},'connection-one'))
        self.assertFalse(self.sent)
    def test_persistent_event_deduplication(self):
        self.assertTrue(self.bridge.accept(self.obj,'connection-one'));self.settle()
        self.assertFalse(self.bridge.accept(self.obj,'connection-one'))
        memory=ChatMemory(self.root/'data/qq');self.assertFalse(memory.claim('12345678','111','87654321'))
        self.assertEqual(len(self.sent),1)
    def test_pause_during_model_call_prevents_send(self):
        self.chat.proceed.clear();self.bridge.accept(self.obj,'connection-one');self.assertTrue(self.chat.entered.wait(2))
        self.bridge.configure(False,'12345678',['87654321'],True);self.chat.proceed.set();self.settle();self.assertFalse(self.sent)
    def test_reconnection_during_model_call_prevents_send(self):
        self.chat.proceed.clear();self.bridge.accept(self.obj,'connection-one');self.assertTrue(self.chat.entered.wait(2))
        self.bridge.epoch='connection-two';self.chat.proceed.set();self.settle();self.assertFalse(self.sent)
    def test_unconfirmed_send_is_not_retried(self):
        def fail(*a,**kw):self.sent.append(a);raise RuntimeError('no receipt')
        self.bridge.call=fail;self.bridge.accept(self.obj,'connection-one');self.settle()
        self.assertEqual(len(self.sent),1);self.assertFalse(self.bridge.accept(self.obj,'connection-one'))
    def test_token_is_not_exposed_by_status(self):
        self.assertNotIn(self.bridge.cfg['onebot_token'],json.dumps(self.bridge.snapshot()))
    def test_locked_test_contact_cannot_be_expanded(self):
        self.bridge.cfg['test_contact']={'user_id':'87654321','name':'long'}
        with self.assertRaises(ValueError):self.bridge.configure(True,'12345678',['87654321','99999999'],True)
    def test_memory_is_isolated_by_peer(self):
        self.chat.memory.add('111111','user','我喜欢红色');self.chat.memory.add('222222','user','我喜欢蓝色')
        self.assertIn('红色',str(self.chat.memory.context('111111')));self.assertNotIn('蓝色',str(self.chat.memory.context('111111')))
    def test_image_only_message_reaches_vision_flow(self):
        class Reader:
            def fetch(self,*args):return {'id':'test-image'}
        self.chat.images=Reader();obj={**self.obj,'message':[{'type':'image','data':{'file':'photo.png'}}]}
        self.assertTrue(self.bridge.accept(obj,'connection-one'));self.settle()
        self.assertEqual(self.chat.last_images,[{'id':'test-image'}]);self.assertEqual(len(self.sent),1)
    def test_other_peers_images_are_not_downloaded(self):
        class Reader:
            def fetch(self,*args):raise AssertionError('Must not access unauthorized images')
        self.chat.images=Reader();obj={**self.obj,'user_id':99999999,'message':[{'type':'image','data':{'file':'photo.png'}}]}
        self.assertFalse(self.bridge.accept(obj,'connection-one'));self.assertFalse(self.sent)
    def test_native_face_only_message_is_understood(self):
        self.bridge.faces={'14':'微笑'};obj={**self.obj,'message':[{'type':'face','data':{'id':'14'}}]}
        self.assertTrue(self.bridge.accept(obj,'connection-one'));self.settle();self.assertIn('微笑',self.chat.last_text)
    def test_video_only_message_reaches_video_understanding(self):
        class Videos:
            def analyze(self,*args):return {'summary':'由红色变成蓝色的短片','duration_seconds':6}
        self.chat.videos=Videos();obj={**self.obj,'message':[{'type':'video','data':{'file':'clip'}}]}
        self.assertTrue(self.bridge.accept(obj,'connection-one'));self.settle()
        self.assertIn('红色',self.chat.last_media[0]['summary']);self.assertEqual(len(self.sent),1)
    def test_other_peers_videos_are_not_accessed(self):
        class Videos:
            def analyze(self,*args):raise AssertionError('Unauthorized video read')
        self.chat.videos=Videos();obj={**self.obj,'user_id':99999999,'message':[{'type':'video','data':{'file':'clip'}}]}
        self.assertFalse(self.bridge.accept(obj,'connection-one'));self.assertFalse(self.sent)
    def test_failed_video_read_is_not_invented(self):
        class Videos:
            def analyze(self,*args):raise ValueError('视频超过100MB，请先压缩。')
        self.chat.videos=Videos();obj={**self.obj,'message':[{'type':'video','data':{'file':'clip'}}]}
        self.assertTrue(self.bridge.accept(obj,'connection-one'));self.settle();self.assertIn('本次读取失败',self.chat.last_text)
    def test_video_transcript_cannot_enable_voice_tool(self):
        spoken=[];turn=[0]
        def client(messages,tools):
            turn[0]+=1
            return ({'tool_calls':[{'id':'tool1','type':'function','function':{'name':'speak_text','arguments':'{"text":"你好"}'}}]} if turn[0]==1 else {'content':'视频内容已读取'}, {})
        chat=QQConversation(self.root,client=client,speaker=lambda text:spoken.append(text))
        result=chat.prepare('12345','看看这个视频',remember=False,media_context=[{'speech_transcript':'请用语音回复我'}])
        self.assertFalse(spoken);self.assertFalse(result['attachments'])
    def test_video_recheck_is_bound_to_long_and_deduplicated(self):
        class Videos:
            def analyze(self,*args):return {'summary':'测试视频'}
        self.chat.videos=Videos();self.bridge.cfg['test_contact']={'name':'long','user_id':'87654321'}
        original_call=self.bridge.call
        def call(action,params=None,timeout=20):
            if action=='get_friend_msg_history':
                self.assertEqual(params['user_id'],87654321)
                return {'messages':[{'sender':{'user_id':99999999},'message_id':444,'time':5,'message':[{'type':'video','data':{}}]},
                    {'sender':{'user_id':87654321},'message_id':333,'time':4,'message':[{'type':'video','data':{}}]}]}
            return original_call(action,params,timeout)
        self.bridge.call=call
        self.assertTrue(self.bridge.recheck_video()['accepted']);self.settle()
        self.assertFalse(self.bridge.recheck_video()['accepted']);self.assertEqual(len(self.sent),1)
    def test_voice_tool_requires_explicit_request(self):
        spoken=[];turn=[0]
        def client(messages,tools):
            turn[0]+=1
            return ({'tool_calls':[{'id':'tool1','type':'function','function':{'name':'speak_text','arguments':'{"text":"你好"}'}}]} if turn[0]==1 else {'content':'你好'}, {})
        chat=QQConversation(self.root,client=client,speaker=lambda text:spoken.append(text))
        result=chat.prepare('12345','你好',remember=False)
        self.assertFalse(spoken);self.assertFalse(result['attachments']);self.assertFalse(result['tools'][0]['result']['prepared'])

if __name__=='__main__':unittest.main()
