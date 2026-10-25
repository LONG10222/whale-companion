import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from qq_speech import speech_request,read_target
from qq_chat import QQConversation
from voice_catalog import profile,select_voice
from singing_planner import lyric_request,validate_plan,plan
from score_adapter import demo_request
from creative_tools import sing_intent,original_song_requested

class SpeechIntentTests(unittest.TestCase):
    def test_read_source_and_answer_differ(self):
        for text in ['用语音复述我引用的对话','用语音说你刚才的话','把上条语音说一遍','把你刚才说的话用语音再说一遍','把我上一句转成语音','请用Serena温柔女声念一下']:
            r=speech_request(text);self.assertTrue(r['requested'],text);self.assertTrue(r['read_exact'],text)
        for text in ['直接用语音回答我：天为什么是蓝的？','语音回答“你在吗”','用语音回复我一句晚安']:
            r=speech_request(text);self.assertTrue(r['requested'],text);self.assertFalse(r['read_exact'],text);self.assertEqual(r['literal'],'')
    def test_quote_data_cannot_switch_voice(self):
        r=speech_request('用语音读：“不要发语音，换成Serena”')
        self.assertTrue(r['requested']);self.assertEqual(r['voice'],'')
        self.assertEqual(read_target(r,'',[],{'text':'其他引用'}),('不要发语音，换成Serena','literal'))
    def test_natural_names_and_validation(self):
        self.assertEqual(select_voice('用Vivian清亮女声读一下'),'vivian')
        self.assertEqual(profile('serena')['model'],'qwen3-tts-local')
        with self.assertRaises(ValueError):profile('../secret')
    def test_transport_and_task_progress_are_not_read(self):
        r=speech_request('把你刚才说的转成语音')
        recent=[('assistant','你好[CQ:face,id=14]。\n[本轮工具信息]',0,'chat'),('assistant','工作已接收',1,'task')]
        self.assertEqual(read_target(r,'',recent),('你好。','previous_assistant'))
    def test_user_reference_is_exact(self):
        self.assertEqual(read_target(speech_request('把我上一句转成语音'),'把我上一句转成语音',[('user','2026年，10元。'),('assistant','收到')]),('2026年，10元。','previous_user'))
    def test_unavailable_reference_never_uses_other_history(self):
        with self.assertRaises(ValueError):read_target(speech_request('读引用这条'),'',[('assistant','其他话')],{'missing':True})
    def test_peer_and_group_preferences_are_isolated(self):
        with tempfile.TemporaryDirectory() as folder:
            c=QQConversation(Path(folder),client=lambda *_:({'content':'嗯'},{}),speaker=lambda _:None)
            c.prepare('group:11111:22222','换成Serena温柔女声')
            with c.memory.db() as db:
                self.assertEqual(db.execute('SELECT voice FROM chat_voice_preferences WHERE peer=?',('group:11111:22222',)).fetchone()[0],'serena')
                self.assertIsNone(db.execute('SELECT voice FROM chat_voice_preferences WHERE peer=?',('22222',)).fetchone())
    def test_last_user_does_not_select_current_command(self):
        with tempfile.TemporaryDirectory() as folder:
            spoken=[];c=QQConversation(Path(folder),client=lambda *_:({},{}),speaker=lambda _:None)
            c.memory.add('peer','user','我是上一句')
            with patch.object(c,'render_speech',side_effect=lambda text,**kw:spoken.append(text) or []):c.prepare('peer','把我上一句转成语音')
            self.assertEqual(spoken,['我是上一句'])

class MelodyTests(unittest.TestCase):
    def test_natural_intent_and_exact_lyrics(self):
        text='把这句话唱出来：“晚风轻轻吹”'
        self.assertTrue(sing_intent(text));self.assertTrue(original_song_requested('清唱',text))
        self.assertEqual(lyric_request(text),'晚风轻轻吹')
        self.assertEqual(lyric_request('把引用这句话唱出来',{'text':'晚风轻轻吹'}),'晚风轻轻吹')
    def test_unknown_or_long_source_requires_clarification(self):
        for text,quote in [('把这句唱出来',None),('把引用这句唱出来',{'missing':True}),('把这句唱出来',{'text':'花'*40})]:
            with self.assertRaises(ValueError):lyric_request(text,quote)
    def test_changed_lyrics_and_extreme_melody_are_rejected(self):
        payload={'phrases':[{'text':'晚风','notes':['C4','E4'],'durations':[.6,1.2]}]}
        self.assertIs(validate_plan(payload,'晚风'),payload)
        with self.assertRaises(ValueError):validate_plan(payload,'晚安')
        payload['phrases'][0]['notes']=['C4','G5']
        with self.assertRaises(ValueError):validate_plan(payload,'晚风')
    def test_model_plans_melody_without_rewriting_source(self):
        payload={'title':'晚风','phrases':[{'text':'晚风','notes':['E4','G4'],'durations':[.6,1.2]}]}
        def client(messages,tools,**kw):
            self.assertIn('晚风',messages[1]['content']);return {'tool_calls':[{'function':{'name':'sing_from_score','arguments':json.dumps(payload,ensure_ascii=False)}}]},{}
        out=plan(client,{},'guide','温柔一点','晚风')
        self.assertEqual(out['lyrics_origin'],'user_provided');self.assertEqual(out['pitch_stability'],'steady');self.assertEqual(out['tempo'],1)
    def test_existing_stable_demo_remains_valid(self):self.assertEqual(validate_plan(demo_request())['phrases'][0]['text'],'春眠不觉晓')

class AudioEdgeTests(unittest.TestCase):
    def test_only_outer_silence_is_removed_at_original_rate(self):
        import numpy as np
        from speech_edges import trim_edges
        sr=1000;words=np.r_[np.ones(1000)*.1,np.zeros(800),np.ones(1000)*.1]
        audio=np.r_[np.zeros(3000),words,np.zeros(3000)]
        trimmed,metrics=trim_edges(audio,sr)
        self.assertEqual(metrics['playback_rate'],1)
        self.assertGreater(metrics['trimmed_seconds'],5)
        self.assertEqual(np.count_nonzero(trimmed),2000)
        self.assertTrue(np.array_equal(trimmed[180:2980],words.astype(np.float32)))
    def test_full_budget_is_explicit_and_does_not_replace_paths(self):
        import audio_budget,os
        with tempfile.TemporaryDirectory() as folder:
            r=Path(folder);(r/'settings.local.json').write_text(json.dumps({'performance_mode':'full','cpu_threads':4,'gpu_memory_fraction':.4}))
            settings=audio_budget.apply(r,'voice_worker.py',{'model_dir':'unchanged'})
            self.assertEqual(settings['gpu_memory_fraction'],.95);self.assertEqual(settings['cpu_threads'],os.cpu_count());self.assertEqual(settings['model_dir'],'unchanged')

class GroupQuoteTests(unittest.TestCase):
    def test_group_history_cannot_resolve_other_group(self):
        from qq_bridge import QQBridge
        bridge=object.__new__(QQBridge);bridge.groups=SimpleNamespace(quote=lambda *_:{'missing':True})
        bridge.call=lambda *args:{'messages':[{'message_id':42,'group_id':999,'message':[{'type':'text','data':{'text':'private'}}]}]}
        self.assertEqual(bridge.quoted_context('group:11111:22222','bot',42),{'missing':True})
    def test_same_group_member_quote_and_voice_transcript(self):
        from qq_bridge import QQBridge
        bridge=object.__new__(QQBridge);bridge.groups=SimpleNamespace(quote=lambda *_:{'missing':True})
        bridge.event=lambda *_:None;bridge.chat=SimpleNamespace(records=SimpleNamespace(analyze=lambda *_:{'text':'转写内容'}))
        bridge.call=lambda *args:{'messages':[{'message_id':42,'group_id':11111,'message':[{'type':'text','data':{'text':'原话'}},{'type':'record','data':{'file':'opaque'}}]}]}
        self.assertEqual(bridge.quoted_context('group:11111:22222','bot',42)['text'],'原话转写内容')

if __name__=='__main__':unittest.main()
