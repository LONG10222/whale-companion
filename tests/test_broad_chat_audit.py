from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import copy,json,tempfile,unittest
from PIL import Image
from dialogue_context import task_status_question,task_status_reply,comparison_models,resolve_creative_followup,reply_problem
from creative_tools import requested_tool
from chat_style import sticker_request
from qq_chat import QQConversation
from qq_bridge import QQBridge,QQActionRejected
from qq_creative import CreativeJobs
from qq_delivery import confirmed_history
from sticker_understanding import select_candidates,character_name

class HistoricalTurnTests(unittest.TestCase):
    def test_quoted_subject_wins_over_unrelated_completed_research(self):
        seen=[]
        def model(messages,tools):
            seen.append(copy.deepcopy(messages))
            return {'tool_calls':[{'type':'function','id':'query','function':{'name':'lookup_web','arguments':'{"query":"小星星 简谱"}'}}]},{}
        with tempfile.TemporaryDirectory() as td:
            chat=QQConversation(td,client=model)
            chat.memory.add('peer','user','查一下某人的公开资料')
            chat.creative=SimpleNamespace(prompt_context=lambda _:[{'kind':'lookup_web','state':'completed','task':'旧的无关查询'}],request=lambda *a,**k:{'accepted':True})
            r=chat.prepare('peer','你可以去网上找找详细信息，我是回复这个',remember=False,quoted_context={'text':'目前没有登记《小星星》的乐谱。'})
            self.assertIn('引用',r['reply']);self.assertIn('查',r['reply']);self.assertNotIn('唱不了',r['reply']);self.assertEqual(len(seen),1)
            self.assertIn('不能拿最近的其他话题代替',seen[0][0]['content']);self.assertEqual(seen[0][-1]['role'],'user')
    def test_model_questions_use_actual_completed_tasks(self):
        job={'kind':'draw_picture','model':'gpt-6.1-sol','effort':'medium','state':'completed','delivery':'sent'}
        with tempfile.TemporaryDirectory() as td:
            chat=QQConversation(td,client=lambda *_:self.fail('Status is a local database read'))
            chat.creative=SimpleNamespace(prompt_context=lambda _:[job])
            r=chat.prepare('peer','好看啊，这个是用什么模型什么推理做的啊？',remember=False)
            self.assertIn('gpt-6.1-sol',r['reply']);self.assertIn('medium',r['reply']);self.assertIn('底层绘图',r['reply']);self.assertFalse(r['attachments'])
    def test_previews_and_other_kinds_are_not_presented_as_delivered_images(self):
        rows=[{'kind':'draw_picture','model':'gpt-5.6-terra','effort':'low','state':'completed','delivery':'preview'},
              {'kind':'draw_picture','model':'gpt-6.1-sol','effort':'medium','state':'completed','delivery':'sent'}]
        reply=task_status_reply(rows,'那我的地球月球模型画的图呢？')
        self.assertIn('没找到',reply);self.assertIn('gpt-6.1-sol',reply);self.assertNotIn('程序',reply)
    def test_question_about_used_model_does_not_start_web_lookup(self):
        for text in ['用什么模型跑？什么推理强度？','查询跑完了吗？','那我的地球月球模型画的图呢？']:
            self.assertTrue(task_status_question(text));self.assertIsNone(requested_tool(text))
        self.assertFalse(task_status_question('就画图，然后告诉我用什么模型以及推理强度'))
        self.assertEqual(requested_tool('帮我画一张搜索流程图'),'draw_picture')
    def test_accepting_a_single_sticker_offer_is_an_actual_request(self):
        history=[('user','你可以发什么表情包？'),('assistant','我这儿有两套，想看我发一个不？')]
        request=sticker_request('可以啊',history);self.assertTrue(request['requested']);self.assertTrue(request['images_only']);self.assertEqual(request['count'],1)
    def test_comparison_keeps_the_subject_and_model_revisions(self):
        history=[('user','你可以画一下鸣潮的达妮娅吗？'),('assistant','提交了'),('user','试试地球和月球，我看看区别'),('user','就画图，然后告诉我用什么模型以及推理强度'),('user','不是发我了吗？应该要terra与luna的呀')]
        bound=resolve_creative_followup('画这两个',history)
        self.assertIn('达妮娅',bound);self.assertEqual(comparison_models(bound),['gpt-5.6-terra','gpt-6-luna'])
        self.assertFalse(comparison_models('不用terra，只用luna'))
        self.assertFalse(comparison_models('原要求：用terra和luna各画一张\n本轮修订：这次不用terra，只用luna'))
    def test_no_unsubmitted_comparison_promises(self):
        self.assertTrue(reply_problem('现在按terra和luna各画一张，画完一起发给你对比。','应该要terra和luna',[]))
    def test_real_word_count_correction_never_reopens_old_song_topic(self):
        with tempfile.TemporaryDirectory() as td:
            chat=QQConversation(td,client=lambda *_:self.fail('Count can be verified locally'))
            chat.memory.add('peer','user','唱小星星');chat.memory.add('peer','assistant','再说“唱小星星”这三个字。')
            r=chat.prepare('peer','呃呃呃，这个是4个字',remember=False)
            self.assertIn('4个字',r['reply']);self.assertNotIn('歌库',r['reply']);self.assertFalse(r['attachments'])
    def test_chat_model_question_is_not_last_drawing_status(self):
        self.assertFalse(task_status_question('你现在用的什么模型？'));self.assertFalse(task_status_question('你是什么模型'))
    def test_sticker_choice_cannot_invent_a_file_or_change_recipient(self):
        rows=[{'id':'safe','caption':'点头表示同意'}]
        result=select_candidates(rows,'可以啊',[],1,lambda *a,**kw:({'content':'{"ids":["C:/private.png","safe","safe"]}'},{}))
        self.assertEqual([r['id'] for r in result],['safe']);self.assertEqual(character_name('大肥鲸'),'DeepSeek娘')
    def test_unmatched_stickers_do_not_fall_back_to_random_picture(self):
        self.assertFalse(select_candidates([{'id':'x','caption':'在庆祝'}],'这个画错了',[],1,lambda *a,**kw:({'content':'{"ids":[]}'},{})))
    def test_partial_history_contains_only_confirmed_bubbles(self):
        r={'reply':'给你看看','attachments':[{'kind':'image','path':str(Path(tempfile.gettempdir())/'safe'/'x.png'),'caption':'没确认的图片'}]}
        h=confirmed_history(r,[[{'type':'text','data':{'text':'给你看看'}}]],True)
        self.assertIn('给你看看',h);self.assertIn('未确认',h);self.assertNotIn('没确认的图片',h);self.assertNotIn('已发送1张',h)
    def test_unclear_utterance_and_questions_do_not_become_permanent_facts(self):
        from qq_chat import ChatMemory
        for text in ['发语音我天天','你喜欢唱歌吗？','帮我查一位导师','好吧']:self.assertFalse(ChatMemory.user_note(text))
        for text in ['我喜欢多发表情包','以后多发图片，少解释','你叫鲸娘娅']:self.assertTrue(ChatMemory.user_note(text))
    def test_quote_relationship_is_retained_without_rewriting_raw_user_words(self):
        from qq_chat import ChatMemory
        with tempfile.TemporaryDirectory() as td:
            memory=ChatMemory(Path(td)/'data/qq');mid=memory.add('peer','user','我是回复这个，去查查')
            memory.remember_quote('peer',mid,{'text':'《小星星》的乐谱','role':'assistant','images':['not-a-readable-file']})
            self.assertEqual(memory.recent('peer')[-1][1],'我是回复这个，去查查')
            self.assertEqual(memory.quote('peer',mid),{'text':'《小星星》的乐谱','role':'assistant'})
            self.assertIn('小星星',str(memory.context('peer')));self.assertIsNone(memory.quote('other',mid))
            with self.assertRaises(ValueError):memory.remember_quote('other',mid,{'text':'伪造引用'})
    def test_partial_real_send_is_retained_and_missing_image_is_not_marked_sent(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);chat=QQConversation(root,client=lambda *_:({},{}));b=QQBridge(root,chat)
            b.configure(True,'12345678',['87654321'],False);b.connected=True;b.login_id='12345678';b.epoch='test';sent=[]
            chat.prepare=lambda *a,**kw:{'reply':'这张给你看看','attachments':[{'kind':'image','id':'known','path':str(root/'image.png'),'caption':'不应记为已发'}],'tools':[]}
            chat.agent_settings=lambda:{'image_layout':'separate'}
            def send(action,params,**kw):
                sent.append(params)
                if len(sent)==2:raise QQActionRejected('图片被拒绝')
                return {'message_id':len(sent)}
            b.call=send
            try:
                obj={'message_type':'private','sub_type':'friend','self_id':12345678,'user_id':87654321,'message_id':7,'message':[{'type':'text','data':{'text':'发图'}}]}
                self.assertTrue(b.accept(obj,'test'));b.pool.submit(lambda:None).result(4)
                h=str(chat.memory.recent('87654321'));self.assertIn('这张给你看看',h);self.assertIn('未确认',h);self.assertNotIn('不应记为已发',h)
                self.assertFalse(chat.memory.sticker_activity('87654321')['recent']);self.assertEqual(len(sent),3)
            finally:b.pool.shutdown(wait=True)

class ComparisonWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.chat=QQConversation(self.root,client=lambda *_:({'content':'好'},{}));self.b=QQBridge(self.root,self.chat)
        self.peer='87654321';self.b.cfg.update(locked_bot_id='12345678',test_contact={'name':'owner','user_id':self.peer});self.b.configure(True,'12345678',[self.peer],False)
        self.b.connected=True;self.b.login_id='12345678';self.b.epoch='test';self.calls=[]
        def run(jid,task,**kw):
            self.calls.append(kw);folder=self.root/jid;folder.mkdir(exist_ok=True);p=folder/'drawing.png';Image.new('RGB',(12,12),'blue' if len(self.calls)==1 else 'red').save(p)
            kw['progress'](thread_id='one-thread',directory=str(folder),model=kw['model'],effort=kw['effort'])
            return {'images':[str(p)],'thread_id':'one-thread','directory':str(folder),'model':kw['model'],'effort':kw['effort']}
        self.service=CreativeJobs(self.root,self.b,resources=lambda:{'ram_free_mib':16000,'gpu_free_mib':13000,'gpu_util_percent':0,'cpu_percent':0},workbench=SimpleNamespace(run=run))
    def tearDown(self):self.service.close();self.service.pool.shutdown(wait=True);self.b.pool.shutdown(wait=True);self.tmp.cleanup()
    def settle(self):self.service.pool.submit(lambda:None).result(5)
    def test_two_models_share_one_bounded_job_and_preserve_both_images(self):
        self.service.request(self.peer,'draw_picture',{'prompt':'同一只小鲸鱼'},'用terra与luna各画一张小鲸鱼');self.settle()
        rows=self.service.recent(self.peer);self.assertEqual(len(rows),1);self.assertEqual(rows[0]['state'],'completed')
        self.assertEqual([c['model'] for c in self.calls],['gpt-5.6-terra','gpt-6-luna']);self.assertEqual(self.calls[1]['thread_id'],'one-thread')
        images=rows[0]['result']['attachments'];self.assertEqual(len(images),2);self.assertNotEqual(Path(images[0]['path']).read_bytes(),Path(images[1]['path']).read_bytes())
        self.assertEqual(len(self.service.prompt_context(self.peer)[0]['variants']),2)
    def test_ordinary_user_cannot_smuggle_expensive_variant(self):
        self.b.cfg['allowed_private_ids'].append('99999999')
        with self.assertRaises(ValueError):self.service.request('99999999','draw_picture',{'prompt':'鲸鱼'},'用terra与luna各画一张鲸鱼')
        self.assertFalse(self.calls);self.assertFalse(self.service.recent(self.peer))
    def test_partial_model_failure_returns_the_image_that_did_succeed(self):
        original=self.service.workbench.run
        def run(*a,**kw):
            if kw['model']=='gpt-6-luna':raise RuntimeError('第二项真实失败')
            return original(*a,**kw)
        self.service.workbench.run=run
        self.service.request(self.peer,'draw_picture',{'prompt':'鲸鱼'},'用terra与luna各画一张鲸鱼');self.settle()
        result=self.service.recent(self.peer)[0]['result'];self.assertEqual(len(result['attachments']),1);self.assertIn('另一张未完成',result['text'])

if __name__=='__main__':unittest.main()
