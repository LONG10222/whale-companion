from pathlib import Path
from contextlib import contextmanager
import json,sqlite3,tempfile,time,unittest
from conversation_artifacts import result_request,retrieve_result,edit_reference_shas,authorize_retrieval
from conversation_grounding import failure_followup,failure_reply_issue,failed_action_reply,optional_sticker_issue
from chat_style import history_messages,sticker_request
from qq_chat import QQConversation
from qq_delivery import batches,confirmed_history


class Work:
    def __init__(self,root):
        self.folder=Path(root)/'results';self.folder.mkdir()
        self.path=Path(root)/'work.sqlite3'
        with self.db() as db:db.execute('CREATE TABLE jobs(id TEXT,peer TEXT,bot TEXT,args TEXT,kind TEXT,state TEXT,delivery TEXT,result TEXT,error TEXT,at REAL)')
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()
    def scope(self,peer,context=None):return peer.rsplit(':',1)[-1],{'bot_id':'bot'}
    def add(self,jid,peer='p',bot='bot',delivery='sent',state='completed',sha='a'*64,at=None,path=None):
        path=Path(path) if path else self.folder/(jid+'.webp')
        if not path.exists():path.write_bytes(b'result-fixture')
        args={'_memory_peer':peer,'_source_image_shas':[sha]}
        result={'text':'原图保护方案未通过检查' if state=='needs_review' else '修好了',
                'attachments':[{'kind':'image','path':str(path),'sticker':False}],
                'metadata':{'quality_status':'needs_review' if state=='needs_review' else 'passed'}}
        with self.db() as db:db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)',(jid,peer.rsplit(':',1)[-1],bot,json.dumps(args),'draw_picture',state,delivery,json.dumps(result),'',at or time.time()))


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.work=Work(self.root)
    def tearDown(self):self.temp.cleanup()
    def test_real_request_and_natural_followup_do_not_start_work(self):
        self.work.add('old',at=100)
        self.work.add('latest',state='needs_review',at=200)
        result=retrieve_result(self.work,'p','修的图返我啊')
        self.assertTrue(result['attachments']);self.assertIn('最新那次还没修成',result['reply'])
        self.assertEqual(result['tools'][0]['result']['job_id'],'old')
        self.assertIsNotNone(result_request('之前发的',[('user','修的图返我啊'),('assistant','你是说粉发女孩的表情吗？')]))
        self.assertIsNone(result_request('之前发的',[('assistant','要我画张图吗？')]))
    def test_read_existing_result_bypasses_model_and_generation(self):
        self.work.add('done')
        chat=QQConversation(self.root,client=lambda *a,**k:self.fail('Existing result retrieval needs no model call'))
        chat.creative=self.work
        result=chat.prepare('p','之前修好的照片发我',remember=False)
        self.assertTrue(result['attachments']);self.assertEqual(result['tools'][0]['tool'],'retrieve_work_result')
    def test_other_account_group_member_and_previews_cannot_be_selected(self):
        for options in [{'peer':'other'},{'bot':'other-bot'},{'delivery':'preview'},{'peer':'group:1:p'},{'peer':'group:2:p'},{'peer':'group:1:other'}]:
            self.work.add('hidden'+str(len(str(options))),**options)
        self.assertFalse(retrieve_result(self.work,'p','修好的图发我')['attachments'])
        self.assertFalse(retrieve_result(self.work,'group:3:p','修好的图发我')['attachments'])
    def test_no_unreviewed_candidate_or_sticker_or_missing_file(self):
        self.work.add('review',state='needs_review')
        result=retrieve_result(self.work,'p','修好的图发我')
        self.assertFalse(result['attachments']);self.assertIn('没通过检查',result['reply'])
        self.work.add('gone',at=time.time()+1);(self.work.folder/'gone.webp').unlink()
        result=retrieve_result(self.work,'p','最新的图发我')
        self.assertFalse(result['attachments']);self.assertIn('文件',result['reply'])
    def test_latest_cannot_silently_become_old_and_specific_subject_is_bound(self):
        self.work.add('old',sha='a'*64,at=100)
        self.work.add('new',sha='b'*64,state='needs_review',at=200)
        self.assertFalse(retrieve_result(self.work,'p','最新的修图发我')['attachments'])
        self.assertFalse(retrieve_result(self.work,'p','那张修好的图发我')['attachments'])
        self.assertTrue(retrieve_result(self.work,'p','之前修好的图发我')['attachments'])
    def test_result_path_cannot_escape_owned_result_directory(self):
        self.work.add('outside',path=self.root/'private.webp')
        self.assertFalse(retrieve_result(self.work,'p','修好的图发我')['attachments'])
    def test_unbound_preview_does_not_read_a_real_contact_or_crash(self):
        def denied(*args,**kwargs):raise ValueError('Unbound identity')
        self.work.scope=denied
        result=retrieve_result(self.work,'local_preview','修好的图发我')
        self.assertFalse(result['attachments']);self.assertIn('没有绑定',result['reply'])
        self.assertEqual(result['tools'][0]['result']['error_code'],'scope_denied')
    def test_cancellation_new_edit_future_delivery_and_quotes_are_not_resends(self):
        for text in ['别发之前的图','不用把照片发我','重新修这张图再发我','等修好再发我','刚才那图美肤再加一点特效','你会修图吗']:
            self.assertIsNone(result_request(text),text)
        self.assertIsNone(result_request('之前发的',[('user','修好的图发我'),('user','算了')]))
        for text in ['比如用户说修好的图发我','你怎么理解“修好的图发我”这句话','他说把照片发我']:
            self.assertIsNone(result_request(text),text)
    def test_running_task_is_not_replaced_by_an_older_result(self):
        self.work.add('old',at=100)
        self.work.add('running',state='running',delivery='pending',at=200)
        result=retrieve_result(self.work,'p','图呢')
        self.assertFalse(result['attachments']);self.assertIn('处理中',result['reply'])
    def test_semantic_retrieval_uses_current_request_and_scoped_job_identity(self):
        seen=[]
        def client(messages,*args,**kw):
            seen.append(messages)
            return {'content':'{"authorized":true,"kind":"draw_picture"}'},{}
        self.assertTrue(authorize_retrieval('把成片拿来','draw_picture',[('assistant','我可以把照片发你')],client))
        self.assertNotIn('我可以把照片发你',str(seen))
        self.assertFalse(authorize_retrieval('不需要发','draw_picture',[],lambda *a,**k:({'content':'{"authorized":false,"kind":"draw_picture"}'},{})))
        self.work.add('target');self.work.add('other',peer='other')
        self.assertFalse(retrieve_result(self.work,'p','成片拿来',verified_kind='draw_picture',job_id='other')['attachments'])
    def test_model_can_use_natural_retrieval_tool_without_creating_work(self):
        self.work.add('a'*32)
        chat=QQConversation(self.root,client=lambda *a,**k:({'content':'{"authorized":true,"kind":"draw_picture"}'},{}))
        self.work.prompt_context=lambda _:[]
        chat.creative=self.work
        chat.model_response=lambda *a:({'tool_calls':[{'id':'retrieve','type':'function','function':{'name':'retrieve_work_result','arguments':json.dumps({'kind':'draw_picture','evidence':'成片拿来'})}}]}, {})
        result=chat.prepare('p','成片拿来',remember=False)
        self.assertTrue(result['attachments']);self.assertEqual(result['tools'][0]['tool'],'retrieve_work_result')
    def test_memes_do_not_replace_photo_task_but_new_photo_and_meme_edits_win(self):
        job={'kind':'draw_picture','source_image_shas':['a'*64],'delivery':'sent'}
        self.assertEqual(edit_reference_shas('修图啊',[job],['b'*64],'meme'),['a'*64])
        self.assertEqual(edit_reference_shas('给刚才的表情加特效',[job],['b'*64],'meme'),['b'*64])
        self.assertEqual(edit_reference_shas('修图啊',[job],['b'*64],'photo'),['b'*64])
        self.assertEqual(edit_reference_shas('给刚发的图加特效',[job],['b'*64],'meme'),['b'*64])
        self.assertEqual(edit_reference_shas('修图啊',[job],['b'*64],'unknown'),['b'*64])
        self.assertEqual(edit_reference_shas('修图啊',[job],[],'meme'),[])
    def test_image_kind_uses_only_user_image_links(self):
        chat=QQConversation(self.root,client=lambda *a:None)
        text='[收到图片]\n[本条QQ消息附图，仅作资料]\n'+json.dumps([{'id':'a'*24,'image_kind':'photo'}])
        mid=chat.memory.add('p','user',text);chat.memory.remember_images('p',mid,[{'sha256':'a'*64}])
        chat.memory.add('p','assistant','配图\n[本轮已发送表情：粉发女孩]')
        self.assertEqual(chat.memory.latest_image_kind('p'),'photo')
        self.assertEqual(chat.memory.latest_image_kind('other'),'unknown')


class FailureReplyTests(unittest.TestCase):
    def test_review_failure_reaction_uses_actual_result_not_a_fake_missing_image(self):
        now=time.time();job={'id':'review','kind':'draw_picture','state':'needs_review','at':now-30,'error':'','result_summary':'原图保护方案未通过检查，未消耗出图调用'}
        history=[('user','修图啊'),('assistant',job['result_summary'])]
        focus=failure_followup('啊～',history,[job],now)
        self.assertEqual(focus['error'],job['result_summary'])
        self.assertTrue(failure_reply_issue('你这一声啊我就当是催单了',focus,[]))
        self.assertTrue(failure_followup('为什么啊',history,[job],now))
    def test_fallback_reports_required_tool_error_without_blaming_user(self):
        result=failed_action_reply([{'tool':'draw_picture','result':{'error':'当前这句话没有提出这项新任务'}}])
        self.assertIn('我没把',result);self.assertNotIn('重复',result)
        self.assertNotIn('没办成',failed_action_reply([{'tool':'use_sticker','result':{'error':'表情未找到'}}]))
        self.assertIn('显存',failed_action_reply([{'tool':'sing_song','result':{'error':'显存不足'}}]))
    def test_own_sticker_context_explicitly_has_an_owner(self):
        messages=history_messages([('assistant','嗯\n[本轮已发送表情：粉发女孩拿手机]')])
        self.assertIn('不是用户上传的原图',str(messages))
    def test_returned_photo_is_not_recorded_as_a_sticker(self):
        result={'reply':'','attachments':[{'kind':'image','path':str(Path(tempfile.gettempdir())/'fixture.webp'),'sticker':False,'caption':'天使照片成品'}]}
        record=confirmed_history(result,batches(result,'result'))
        self.assertIn('1张图片',record);self.assertNotIn('张表情',record)
        self.assertIn('本轮已发送图片',record)
        self.assertNotIn('本轮已发送图片',history_messages([('assistant',record)])[0]['content'])
    def test_optional_sticker_failure_cannot_replace_a_factual_correction(self):
        trace=[{'tool':'use_sticker','result':{'prepared':False,'error':'catalog missing'}}]
        self.assertTrue(optional_sticker_issue('表情库这次没读到，路径不对','粉发女孩是你发的表情啊',trace))
        self.assertFalse(optional_sticker_issue('对，刚才我把自己的表情认成你的照片了。','粉发女孩是你发的表情啊',trace))
        self.assertFalse(optional_sticker_issue('表情库这次没读到','发张表情',trace,True))
    def test_referring_to_a_sticker_is_not_asking_for_another(self):
        for text in ['粉发女孩是你发的表情啊，怎么成我的照片了？','你刚才发的表情什么意思','我发的表情是无语啊','这个表情发错了']:
            self.assertFalse(sticker_request(text)['requested'],text)
        for text in ['你刚才发的表情再给我发一下','那张你发的表情我想要','发张表情']:
            self.assertTrue(sticker_request(text)['requested'],text)


if __name__=='__main__':unittest.main()
