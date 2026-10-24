from pathlib import Path
from types import SimpleNamespace
import json,tempfile,unittest
from unittest.mock import Mock,patch
from chat_engine import eligible,settings,ChatEngine
from qq_chat import QQConversation
from runtime_supervisor import Service
from fastapi import FastAPI,Header,HTTPException
from fastapi.testclient import TestClient
from chat_engine_routes import register_engine_routes
from qq_bridge import QQBridge,QQActionRejected


class EligibilityTests(unittest.TestCase):
    def test_casual_dialogue_is_eligible_but_actions_keep_existing_executor(self):
        self.assertTrue(eligible('钱包：我谢谢你',[]))
        for text in ['帮我画一张小鲸鱼','给我唱一首春晓','用语音读一下','帮我查今天新闻','开通Plus','给这个B站视频投币','查余额','发个表情','还记得我喜欢什么表情吗','你太像客服了','你最近在忙什么','看看我刚发的图片']:
            self.assertFalse(eligible(text,[]),text)
        self.assertFalse(eligible('这张呢',[],has_media=True))
        self.assertFalse(eligible('确认',[],approval=True))
    def test_task_confirmation_is_not_diverted_to_casual_kernel(self):
        history=[('user','我想要一张小鲸鱼'),('assistant','我来画一张，行吗？')]
        self.assertFalse(eligible('好',history))
    def test_absent_and_invalid_configuration_are_safe_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertFalse(settings(td)['enabled'])
            folder=Path(td)/'configs';folder.mkdir();(folder/'chat-engine.local.json').write_text('{"enabled":true,"port":"https://evil"}')
            self.assertEqual(settings(td)['port'],18769)
            for value in [[],{'enabled':True,'mode':[]},None]:
                (folder/'chat-engine.local.json').write_text(json.dumps(value))
                self.assertEqual(settings(td)['mode'],'legacy')

    def test_source_offer_does_not_expose_paths_or_credential_bearing_urls(self):
        from chat_engine import source_information
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'configs').mkdir()
            for value in [{'source_url':'https://example.test/code?access_token=private-fixture'},
                          {'source_url':'https://user:private-fixture@example.test/code'},
                          {'source_url':'https://[invalid'},[]]:
                (root/'configs/chat-engine.local.json').write_text(json.dumps(value))
                info=source_information(root);self.assertNotIn('private-fixture',json.dumps(info))
                self.assertNotIn(str(root),json.dumps(info));self.assertTrue(info['source_url'].endswith('/v0.3.0'))


class HybridConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.chat=QQConversation(self.root,client=lambda *a,**k:({'content':'原入口回复'},{}))
        self.native=SimpleNamespace(config=lambda:{'enabled':True,'mode':'hybrid'},converse=Mock(return_value={'reply':'钱包先替你捏把汗。','attachments':[],'tools':[],'native_ticket':'a'*48}))
        self.chat.native_engine=self.native
    def tearDown(self):self.temp.cleanup()
    def test_casual_native_reply_keeps_current_peer_and_logs_user_only_once(self):
        self.chat.memory.life.observe_correction=Mock();self.chat.learning.observe_style=Mock()
        result=self.chat.prepare('p','钱包：我谢谢你')
        self.assertIn('钱包',result['reply']);self.native.converse.assert_called_once()
        self.assertEqual(self.native.converse.call_args.args[0],'p')
        self.assertEqual(self.chat.memory.recent('p'),[('user','钱包：我谢谢你')])
        self.chat.memory.life.observe_correction.assert_called_once_with('p',1)
        self.assertEqual(self.chat.learning.observe_style.call_args.args[:3],('p',1,'钱包：我谢谢你'))
    def test_existing_closing_guard_runs_before_native_kernel(self):
        self.chat.memory.add('p','user','我去吃饭了，先这样');self.chat.memory.add('p','assistant','去吧。')
        result=self.chat.prepare('p','好');self.assertTrue(result['silent'])
        self.native.converse.assert_not_called()
    def test_installed_standby_kernel_does_not_divert_legacy_mode(self):
        self.native.config=lambda:{'enabled':True,'mode':'legacy'}
        result=self.chat.prepare('p','今天有点困')
        self.assertEqual(result['reply'],'原入口回复');self.native.converse.assert_not_called()
        self.assertEqual(sum(r=='user' for r,_ in self.chat.memory.recent('p')),1)
    def test_native_failure_falls_back_without_duplicate_user_history(self):
        self.native.converse.return_value=None
        result=self.chat.prepare('p','今天有点困')
        self.assertEqual(result['reply'],'原入口回复')
        self.assertEqual(sum(r=='user' for r,_ in self.chat.memory.recent('p')),1)
    def test_optional_kernel_packaging_error_cannot_break_the_old_executor(self):
        self.native.converse.side_effect=ValueError('optional metadata is unavailable')
        result=self.chat.prepare('p','今天有点困')
        self.assertEqual(result['reply'],'原入口回复')
    def test_native_silent_result_does_not_decorate_with_stickers(self):
        self.native.converse.return_value={'reply':'','attachments':[],'tools':[],'silent':True,'dialogue_act':'closing','native_ticket':'a'*48}
        result=self.chat.prepare('p','嗯嗯懂啦')
        self.assertTrue(result['silent']);self.assertFalse(result['attachments'])
    def test_recent_real_failure_stays_in_the_existing_factual_handler(self):
        import time
        self.chat.memory.add('p','user','给照片修图');self.chat.memory.add('p','assistant','原图保护方案未通过检查')
        self.chat.creative=SimpleNamespace(prompt_context=lambda _:[{'id':'failed','kind':'draw_picture','state':'needs_review','at':time.time()-30,'delivery':'sent','error':'','result_summary':'原图保护方案未通过检查'}])
        self.chat.prepare('p','为什么啊')
        self.native.converse.assert_not_called()


class NativeProcessOwnershipTests(unittest.TestCase):
    def test_only_configured_script_profile_port_and_root_are_owned(self):
        root=Path.cwd();profile=root/'test-profile'
        argv=['python.exe',str(root/'astrbot_sidecar.py'),'--root',str(root),'--profile',str(profile),'--port','18769']
        service=Service('astrbot',argv,root,18769)
        process=SimpleNamespace(cwd=lambda:str(root),exe=lambda:'C:/Python/python.exe',cmdline=lambda:argv)
        self.assertTrue(service.owns(process))
        other=argv.copy();other[-1]='18767';process.cmdline=lambda:other;self.assertFalse(service.owns(process))
        other=argv.copy();other[other.index('--profile')+1]=str(root/'other-profile');process.cmdline=lambda:other;self.assertFalse(service.owns(process))
    def test_three_transport_failures_open_a_temporary_circuit(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'configs').mkdir();(root/'configs/chat-engine.local.json').write_text('{"enabled":true,"mode":"hybrid"}')
            (root/'settings.local.json').write_text('{"api_token":"local-test"}')
            engine=ChatEngine(root)
            chat=SimpleNamespace(memory=SimpleNamespace(recent=lambda *a:[],profile=lambda *a:[]),creative=None,
                persona=SimpleNamespace(load=lambda:SimpleNamespace(model_dump=lambda:{})),contacts=SimpleNamespace(label=lambda x:'测试',role=lambda x:'user'))
            with patch('httpx.Client',side_effect=RuntimeError('down')):
                for _ in range(3):self.assertIsNone(engine.converse('12345','在吗',chat,{'bot':'test-bot'}))
            self.assertGreater(engine.blocked_until,0)


class LocalControlsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);(self.root/'configs').mkdir()
        self.path=self.root/'configs/chat-engine.local.json';self.path.write_text('{"enabled":true,"mode":"legacy","python":"owned-runtime","profile":"owned-profile"}')
        self.ready=True
        def status():return {'mode':json.loads(self.path.read_text())['mode'],'enabled':True,'kernel':{'ok':self.ready}}
        bridge=SimpleNamespace(chat=SimpleNamespace(native_engine=SimpleNamespace(status=status)),snapshot=lambda:{'connected':True,'account_matches':True})
        def authorize(authorization:str=Header(default='')):
            if authorization!='Bearer local-test':raise HTTPException(401)
        app=FastAPI();register_engine_routes(app,root=self.root,bridge=bridge,authorize=authorize)
        self.client=TestClient(app);self.headers={'Authorization':'Bearer local-test'}
    def tearDown(self):self.client.close();self.temp.cleanup()
    def test_config_requires_local_auth_and_never_accepts_executable_paths(self):
        self.assertEqual(self.client.post('/qq/engine/config',json={'mode':'hybrid'}).status_code,401)
        self.assertEqual(self.client.post('/qq/engine/config',headers=self.headers,json={'mode':'hybrid','python':'bad'}).status_code,422)
        self.assertEqual(json.loads(self.path.read_text())['python'],'owned-runtime')
    def test_ready_mode_switch_and_rollback_preserve_runtime_paths(self):
        for mode in ['hybrid','legacy']:
            response=self.client.post('/qq/engine/config',headers=self.headers,json={'mode':mode})
            self.assertEqual(response.status_code,200);self.assertEqual(response.json()['mode'],mode)
        self.assertEqual(json.loads(self.path.read_text())['profile'],'owned-profile')
    def test_not_ready_kernel_cannot_become_the_active_mode(self):
        self.ready=False
        self.assertEqual(self.client.post('/qq/engine/config',headers=self.headers,json={'mode':'hybrid'}).status_code,409)
        self.assertEqual(json.loads(self.path.read_text())['mode'],'legacy')


class ReceiptDispatchTests(unittest.TestCase):
    def test_silent_turn_without_a_text_receipt_does_not_commit_native_history(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);chat=QQConversation(root,client=lambda *a:({'content':'ok'},{}));bridge=QQBridge(root,chat)
            bridge.configure(True,'12345678',['87654321'],False);bridge.connected=True;bridge.login_id='12345678';bridge.epoch='test'
            confirm=Mock(return_value=True);chat.native_engine=SimpleNamespace(confirm=confirm)
            chat.prepare=lambda *a,**kw:{'reply':'','attachments':[],'tools':[],'silent':True,'dialogue_act':'closing','native_ticket':'a'*48}
            bridge.call=Mock()
            try:
                event={'message_type':'private','sub_type':'friend','self_id':12345678,'user_id':87654321,'message_id':8,'message':[{'type':'text','data':{'text':'嗯嗯'}}]}
                self.assertTrue(bridge.accept(event,'test'));bridge.pool.submit(lambda:None).result(5)
                bridge.call.assert_not_called();confirm.assert_not_called()
            finally:bridge.pool.shutdown(wait=True)
    def test_native_history_commits_only_after_a_confirmed_text_packet(self):
        for fail_on in [0,1,2]:
            with self.subTest(fail_on=fail_on),tempfile.TemporaryDirectory() as td:
                root=Path(td);chat=QQConversation(root,client=lambda *a:({'content':'ok'},{}));bridge=QQBridge(root,chat)
                bridge.configure(True,'12345678',['87654321'],False);bridge.connected=True;bridge.login_id='12345678';bridge.epoch='test'
                confirm=Mock(return_value=True);chat.native_engine=SimpleNamespace(confirm=confirm)
                chat.agent_settings=lambda:{'image_layout':'separate'}
                chat.prepare=lambda *a,**kw:{'reply':'这句已接住','attachments':[{'kind':'image','id':'reaction','path':str(root/'reaction.png')}],'tools':[],'native_ticket':'a'*48}
                calls=[]
                def send(action,params,**kw):
                    calls.append(params)
                    if fail_on and len(calls)==fail_on:raise QQActionRejected('test refusal')
                    return {'message_id':len(calls)}
                bridge.call=send
                try:
                    event={'message_type':'private','sub_type':'friend','self_id':12345678,'user_id':87654321,'message_id':7,'message':[{'type':'text','data':{'text':'聊一句'}}]}
                    self.assertTrue(bridge.accept(event,'test'));bridge.pool.submit(lambda:None).result(5)
                    if fail_on==1:confirm.assert_not_called()
                    else:confirm.assert_called_once_with('a'*48)
                finally:bridge.pool.shutdown(wait=True)


if __name__=='__main__':unittest.main()
