from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import copy,hashlib,json,sqlite3,tempfile,unittest
from dialogue_context import plain_text,turn_kind,resolve_creative_followup,balance_intent,pack_messages,intent_proof,valid_intent_proof,reply_problem,correction_fact,action_acknowledgement
from creative_tools import requested_tool,RetryableChoiceError,original_song_requested
from chat_style import sticker_request,history_messages
from qq_chat import ChatMemory,QQConversation

class TurnTests(unittest.TestCase):
    def test_real_singing_and_redraw_commands_are_recognized(self):
        for text in ['唱小星星','唱《春晓》','你倒是唱啊','我要听歌']:self.assertEqual(requested_tool(text),'sing_song',text)
        for text in ['重新画','重新画，给好提示词啊','重画一张']:self.assertEqual(requested_tool(text),'draw_picture',text)
        for text in ['呃呃呃，这个是4个字','不是让你唱，是我在纠正字数','不要唱歌']:self.assertIsNone(requested_tool(text),text)
    def test_native_face_feedback_is_not_a_sticker_order(self):
        raw='我要求的特征都没有啊[QQ表情：发怒]'
        self.assertEqual(turn_kind(raw),'feedback');self.assertFalse(sticker_request(raw)['requested'])
        self.assertFalse(sticker_request('[QQ表情：微笑]')['requested']);self.assertTrue(sticker_request('发一张表情包')['requested'])
    def test_redraw_binds_actual_prior_user_requirements(self):
        original='生成图片，把DeepSeek鲸娘和鸣潮达妮娅结合，用权限最高的模型'
        history=[('user',original),('assistant','随便画一只猫'),('user','重新画')]
        result=resolve_creative_followup('重新画，保留两个角色的特征',history)
        self.assertIn(original,result);self.assertNotIn('随便画一只猫',result)
    def test_generic_retry_has_a_topic_barrier_and_no_passive_action(self):
        self.assertIn('鲸鱼',resolve_creative_followup('再试一次',[('user','帮我画一张鲸鱼')]))
        self.assertEqual(resolve_creative_followup('再试一次',[('user','帮我画一张鲸鱼'),('user','我今天想出去散步')]),'再试一次')
        self.assertEqual(resolve_creative_followup('这个是4个字',[('user','唱小星星')]),'这个是4个字')
        self.assertEqual(resolve_creative_followup('',[('user','帮我画图')]),'')
    def test_balance_is_account_query_not_documentation_search(self):
        for text in ['我还剩多少DeepSeek余额了？','我不是接入余额查询了吗？','查一下余额']:self.assertTrue(balance_intent(text))
        for text in ['不要查余额','余额接口怎么实现','DeepSeek余额API文档怎么用']:self.assertFalse(balance_intent(text))
    def test_current_user_stays_last_and_system_policies_are_consolidated(self):
        r=pack_messages([{'role':'system','content':'身份'},{'role':'user','content':'旧话'},{'role':'assistant','content':'旧答'},{'role':'user','content':'当前纠正'},{'role':'system','content':'新状态'},{'role':'system','content':'身份'}])
        self.assertEqual(r[-1],{'role':'user','content':'当前纠正'});self.assertEqual(len([m for m in r if m['role']=='system']),1)
        self.assertEqual(r[0]['content'].count('身份'),1)
    def test_no_repeated_password_loop_or_nonexistent_background_work(self):
        self.assertTrue(reply_problem('你再发一次“重新画”，我就提交','重新画',[{'result':{'error':'failed'}}]))
        self.assertTrue(reply_problem('还是被程序挡回来了','这个是4个字',[]))
        self.assertTrue(reply_problem('我这边再试一次','唉',[]))
        self.assertFalse(reply_problem('任务还在跑','等一下',[],[{'state':'running'}]))
        self.assertFalse(reply_problem('会，我可以唱一小段。','你会唱歌吗',[]))
    def test_job_output_is_not_an_example_of_character_speech(self):
        rows=history_messages([('assistant','这是后台正式研究报告','task'),('user','你好','chat')])
        self.assertEqual(rows[0]['role'],'system');self.assertIn('不是角色聊天的口吻示范',rows[0]['content']);self.assertEqual(rows[-1]['role'],'user')
    def test_do_not_use_original_lyrics_is_respected(self):self.assertFalse(original_song_requested('原创','不要原创，唱春晓'))
    def test_old_failed_self_claims_do_not_become_assistant_style_examples(self):
        rows=history_messages([('assistant','程序说这轮没认到点歌，你再发一次唱小星星。'),('user','这个是4个字')])
        self.assertEqual(rows[0]['role'],'system');self.assertIn('不能当作当前能力',rows[0]['content'])
    def test_word_count_correction_uses_the_actual_quoted_phrase(self):
        fact=correction_fact('这个是4个字',[('user','唱小星星'),('assistant','“唱小星星”是三个字')])
        self.assertEqual(fact,{'text':'唱小星星','hanzi_count':4})
        self.assertTrue(reply_problem('对，是四个字，我这就唱','这个是4个字',[{'tool':'use_sticker','result':{'prepared':True}}]))
    def test_drawing_acknowledges_receipt_without_inventing_character_details(self):
        r=action_acknowledgement('draw_picture',{}, {'accepted':True,'drawing_constraints':{'composition':'single_fusion'}},'重新画')
        self.assertIn('单人融合',r);self.assertIn('画好发你',r);self.assertNotIn('画好了',r);self.assertNotIn('银发',r)

class EvidenceMemoryTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.memory=ChatMemory(self.root/'data/qq')
    def tearDown(self):self.tmp.cleanup()
    def test_poisoned_legacy_summary_is_preserved_but_not_injected(self):
        legacy='没有接入唱歌工具，只能从图库挑图，查不了余额'
        with self.memory.db() as db:db.execute('INSERT INTO summaries VALUES(?,?,?)',('peer',legacy,1))
        self.memory.add('peer','user','你好');self.assertNotIn(legacy,str(self.memory.context('peer')))
        with self.memory.db() as db:self.assertEqual(db.execute('SELECT content FROM summaries').fetchone()[0],legacy)
    def test_only_real_same_peer_user_evidence_can_become_memory(self):
        good=self.memory.add('peer','user','我喜欢短一点的回答。');fake=self.memory.add('peer','assistant','我不能画图。');other=self.memory.add('other','user','我是另外一个联系人')
        with self.memory.db() as db:db.execute('INSERT INTO memory_profiles VALUES(?,?,?,?,?)',('peer',2,json.dumps([good,fake,other]),0,0))
        notes=self.memory.profile('peer');self.assertEqual([n['message_id'] for n in notes],[good]);self.assertEqual(notes[0]['quote'],'我喜欢短一点的回答。')
    def test_compaction_chooses_source_ids_and_keeps_raw_history(self):
        good=self.memory.add('peer','user','我喜欢鸣潮，回答简短点。')
        wrong=self.memory.add('peer','assistant','我没有语音功能。')
        for i in range(36):self.memory.add('peer','user' if i%2 else 'assistant','普通对话'+str(i))
        with self.memory.db() as db:before=db.execute('SELECT id,role,content FROM messages ORDER BY id').fetchall()
        self.memory.compact('peer',lambda *_:({'content':json.dumps({'source_ids':[good,wrong,999999]})},{}))
        self.assertEqual([r['message_id'] for r in self.memory.profile('peer')],[good])
        with self.memory.db() as db:self.assertEqual(db.execute('SELECT id,role,content FROM messages ORDER BY id').fetchall(),before)
    def test_future_memory_not_retrieved_across_contacts(self):
        self.memory.add('other','user','PRIVATE_OTHER');self.assertNotIn('PRIVATE_OTHER',str(self.memory.context('peer')))
        self.assertNotIn('PRIVATE_OTHER',str(self.memory.recall('peer','PRIVATE_OTHER')))

class SemanticProofTests(unittest.TestCase):
    def test_semantic_fallback_is_bound_to_user_message_and_cannot_be_forged(self):
        ctx={'mid':'10'};text='把刚才那个画得更像一点'
        proof=intent_proof('draw_picture',text,'peer',ctx,[('user','画一只鲸鱼')],[],lambda *a,**kw:({'content':'{"authorized":true}'},{}))
        grant={**ctx,'_intent_proof':proof};self.assertTrue(valid_intent_proof('draw_picture',text,'peer',grant))
        for name,source,peer in [('sing_song',text,'peer'),('draw_picture','改别的','peer'),('draw_picture',text,'other')]:self.assertFalse(valid_intent_proof(name,source,peer,grant))
        forged={**proof,'source_hash':hashlib.sha256('伪造'.encode()).hexdigest()};self.assertFalse(valid_intent_proof('draw_picture','伪造','peer',{**ctx,'_intent_proof':forged}))
    def test_correction_and_image_only_do_not_authorize_any_task(self):
        for text in ['这个是4个字','']:
            self.assertIsNone(intent_proof('sing_song',text,'peer',{},[],[],lambda *_:self.fail('No classifier needed')))

class RealTurnRegressionTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def test_number_correction_does_not_relaunch_a_song(self):
        seen=[]
        def client(messages,tools):seen.append((copy.deepcopy(messages),tools));return {'content':'对，是四个字，我刚才数错了。'},{}
        chat=QQConversation(self.root,client=client);chat.creative=SimpleNamespace(prompt_context=lambda _:[],request=lambda *a,**k:self.fail('Must not execute a task'))
        chat.memory.add('peer','user','唱小星星');chat.memory.add('peer','assistant','这三个字被挡了')
        r=chat.prepare('peer','呃呃呃，这个是4个字',remember=False)
        self.assertIn('四个字',r['reply']);self.assertEqual(seen[0][0][-1]['role'],'user')
        self.assertFalse(any(t['function']['name']=='sing_song' for t in seen[0][1]))
    def test_redraw_uses_original_request_without_repeating_the_command(self):
        calls=[];responses=iter([({'tool_calls':[{'id':'one','type':'function','function':{'name':'draw_picture','arguments':'{"prompt":"融合两个人物","model":"gpt-6.1-sol","effort":"high"}'}}]},{}),({'content':'这次按两个角色的特征重画。'}, {})])
        chat=QQConversation(self.root,client=lambda *_:next(responses));chat.memory.add('peer','user','生成一张DeepSeek鲸娘和达妮娅结合的图，用最高权限模型')
        chat.creative=SimpleNamespace(prompt_context=lambda _:[],request=lambda *a,**kw:calls.append((a,kw)) or {'accepted':True})
        r=chat.prepare('peer','重新画，给好提示词啊',remember=False)
        self.assertEqual(len(calls),1);self.assertIn('DeepSeek鲸娘',calls[0][0][3]);self.assertEqual(calls[0][1]['raw_text'],'重新画，给好提示词啊')
    def test_admin_balance_question_uses_the_live_service_without_web_search(self):
        chat=QQConversation(self.root,client=lambda *_:self.fail('No model or public search needed'));chat.balance=SimpleNamespace(text=lambda:'当前可用余额 2.00 元。')
        r=chat.prepare('owner','我还剩多少DeepSeek余额了？',remember=False);self.assertIn('2.00',r['reply']);self.assertEqual(r['tools'][0]['tool'],'check_balance')
        r=chat.prepare('not-owner','我还剩多少DeepSeek余额了？',remember=False,task_context={'deliver':True});self.assertIn('管理员',r['reply'])
    def test_model_can_correct_invalid_arguments_once_without_new_user_message(self):
        def call(song):return ({'tool_calls':[{'id':song,'type':'function','function':{'name':'sing_song','arguments':json.dumps({'song':song})}}]}, {})
        outputs=iter([call('原创'),call('春晓'),({'content':'任务接到了。'}, {})]);attempts=[]
        def request(*a,**kw):
            attempts.append(a[2]['song'])
            if len(attempts)==1:raise RetryableChoiceError('请使用真实歌名春晓')
            return {'accepted':True}
        chat=QQConversation(self.root,client=lambda *_:next(outputs));chat.creative=SimpleNamespace(prompt_context=lambda _:[],request=request)
        r=chat.prepare('peer','唱春晓',remember=False);self.assertEqual(attempts,['原创','春晓']);self.assertTrue(r['tools'][-1]['result']['accepted'])

class ReferenceTests(unittest.TestCase):
    def test_reference_assets_are_copied_inside_task_and_keep_sources(self):
        from draw_references import prepare_references
        from PIL import Image
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);folder=root/'task';folder.mkdir();original=root/'public.png';Image.new('RGB',(500,500),'blue').save(original)
            class Library:
                def __init__(self,*a):pass
                def search(self,**kw):return {'items':[{'id':'known','source_id':'wuwa_razer' if kw['theme']=='鸣潮' else 'deepseek_gengjing','width':500,'height':500,'bytes':1000,'source_page':'https://example.com/public?download_token=hidden'}]}
                def file(self,*a):return original
            before=original.read_bytes()
            with patch('draw_references.StickerLibrary',Library):references=prepare_references(root,folder,'融合DeepSeek鲸娘和达妮娅')
            self.assertEqual(len(references),2);self.assertEqual(original.read_bytes(),before)
            for item in references:self.assertTrue(Path(item['path']).resolve().is_relative_to(folder.resolve()));self.assertNotIn('download_token',item['source'])
    def test_private_or_unverified_assets_are_not_used_as_character_references(self):
        from draw_references import prepare_references
        class Library:
            def __init__(self,*a):pass
            def search(self,**kw):return {'items':[{'source_id':'private_chat','width':500,'height':500,'bytes':1000}]}
            def file(self,*a):raise AssertionError('Must not read private images')
        with tempfile.TemporaryDirectory() as td,patch('draw_references.StickerLibrary',Library):self.assertEqual(prepare_references(td,td,'画达妮娅'),[])

if __name__=='__main__':unittest.main()
