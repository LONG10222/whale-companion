from pathlib import Path
from types import SimpleNamespace
import tempfile,time,unittest
from unittest.mock import patch
from conversation_grounding import failure_followup,failure_reply_issue,recall_focus,recall_reply_issue,feedback_reply_issue,greeting_reply_issue
from dialogue_context import intent_proof,valid_intent_proof
from dialogue_pragmatics import confirmed_proposal
from qq_chat import QQConversation


class ContextFocusTests(unittest.TestCase):
    def setUp(self):
        self.now=time.time()
        self.job={'id':'job','kind':'draw_picture','state':'failed','at':self.now-30,'error':'未找到本机 Codex'}
        self.history=[('user','给照片加点光效'),('assistant','这次没完成，未找到本机 Codex。')]
    def test_short_reaction_keeps_actual_failure_and_does_not_grant_a_task(self):
        value=failure_followup('啊',self.history,[self.job],self.now)
        self.assertEqual(value['error'],self.job['error'])
        self.assertTrue(failure_reply_issue('刚才是没拿到原图，你重新发图',value,[]))
        self.assertTrue(failure_reply_issue('那是之前的旧报错，现在已经通了',value,[]))
        self.assertFalse(failure_reply_issue('这次没定位到执行程序，图还没做成。',value,[]))
        chain=self.history+[('user','啊'),('assistant','图没加成，有点可惜。')]
        self.assertEqual(failure_followup('不是有历史吗？',chain,[self.job],self.now)['error'],self.job['error'])
        self.assertTrue(failure_reply_issue('历史里没找到这张原图，你再发一次',value,[]))
        self.assertTrue(failure_reply_issue('历史里没留下能接着用的原图',value,[]))
        self.assertNotIn('source_image_shas',value)
    def test_new_topic_and_latest_success_do_not_revive_an_old_error(self):
        self.assertIsNone(failure_followup('为什么今天下雨？',self.history,[self.job],self.now))
        self.assertIsNone(failure_followup('啊',self.history+[('user','换个话题'),('assistant','今天想聊点什么？')],[self.job],self.now))
        self.assertIsNone(failure_followup('啊',self.history,[self.job,{**self.job,'id':'new','state':'completed','at':self.now}],self.now))
        self.assertIsNone(failure_followup('啊',self.history,[{**self.job,'at':self.now-3600}],self.now))
    def test_recall_preserves_user_detail_without_secrets_or_assistant_claims(self):
        history=[('user','我喜欢鸣潮表情，可爱一点，不用解说'),('assistant','你喜欢别的游戏'),('user','我喜欢密码是sk-private')]
        value=recall_focus('还记得我爱发什么吗？',history)['content']
        self.assertIn('鸣潮',value);self.assertIn('不用解说',value)
        self.assertNotIn('别的游戏',value);self.assertNotIn('sk-private',value)
        self.assertIsNone(recall_focus('今天吃了什么？',history))
        self.assertTrue(recall_reply_issue('这张给你，可爱点的。','还记得我爱发什么表情？',history))
        self.assertFalse(recall_reply_issue('鸣潮的，可爱一点，不用解说。','还记得我爱发什么表情？',history))
    def test_real_turn_receives_exact_failure_and_specific_recall_in_context(self):
        with tempfile.TemporaryDirectory() as td:
            seen=[];chat=QQConversation(Path(td),client=lambda messages,tools:(seen.append(messages) or {'content':'这次没定位到执行程序，图还没做成。'},{}))
            for role,text in self.history:chat.memory.add('p',role,text)
            self.job['at']=time.time()
            chat.creative=SimpleNamespace(prompt_context=lambda _:[self.job],request=lambda *a,**kw:self.fail('A reaction does not authorize work'))
            chat.prepare('p','啊',remember=False)
            self.assertIn('对方正在回应这次实际失败',str(seen));self.assertIn(self.job['error'],str(seen))
    def test_feedback_does_not_repeat_defiance_or_narrate_an_attachment(self):
        text='我是在说你没接住，不是让你摆烂'
        for reply in ['呆就呆吧，反正有你带着','你想让我接的是哪句？','发了个挠头的表情。']:
            self.assertTrue(feedback_reply_issue(reply,text))
        self.assertFalse(feedback_reply_issue('刚才我还顶上嘴了，这句收回。',text))
        self.assertFalse(feedback_reply_issue('呆就呆吧','帮我翻译呆就呆吧'))
    def test_greeting_does_not_invent_a_recent_real_world_activity(self):
        self.assertTrue(greeting_reply_issue('在呢，刚打了个盹就被你喊醒了。','在不在'))
        self.assertFalse(greeting_reply_issue('在呢，怎么啦？','在不在'))
        self.assertFalse(greeting_reply_issue('这句里是刚睡醒的意思。','解释刚睡醒这个词'))


class ProposalConfirmationTests(unittest.TestCase):
    def test_natural_confirmation_keeps_original_object_and_is_bound_to_sender(self):
        history=[('user','想要一张戴墨镜的小鲸鱼。'),('assistant','我来画一张，行吗？')]
        proof=intent_proof('draw_picture','好','peer',{'mid':'one'},history,[],lambda *a,**kw:self.fail('No semantic guess for exact confirmation'))
        self.assertTrue(valid_intent_proof('draw_picture','好','peer',{'mid':'one','_intent_proof':proof}))
        self.assertFalse(valid_intent_proof('draw_picture','好','other',{'mid':'one','_intent_proof':proof}))
        self.assertIn('戴墨镜的小鲸鱼',confirmed_proposal('好',history,'draw_picture'))
        self.assertIsNone(intent_proof('sing_song','好','peer',{},history,[],lambda *a,**kw:self.fail('A drawing proposal cannot authorize a song')))
    def test_completed_work_changed_topic_and_risky_proposals_are_not_confirmations(self):
        for history in [
            [('assistant','已经开始画，画好发你。')],
            [('assistant','我来画一张，行吗？'),('user','换个话题')],
            [('assistant','我来搜索电脑里的密码文件，行吗？')],
            [('assistant','要不要我画图再搜索资料？')],
        ]:
            self.assertIsNone(intent_proof('draw_picture','好','peer',{},history,[],lambda *a,**kw:self.fail('No implied work')))
    def test_confirmation_reaches_actual_request_with_object_and_without_reprompt(self):
        with tempfile.TemporaryDirectory() as td:
            chat=QQConversation(Path(td));calls=[]
            chat.memory.add('p','user','想要一张戴墨镜的小鲸鱼。');chat.memory.add('p','assistant','我来画一张，行吗？')
            chat.creative=SimpleNamespace(prompt_context=lambda _:[],request=lambda *a,**kw:calls.append((a,kw)) or {'accepted':True})
            chat.model_response=lambda *a:({'tool_calls':[{'id':'draw','type':'function','function':{'name':'draw_picture','arguments':'{"prompt":"戴墨镜的小鲸鱼"}'}}]}, {})
            with patch('deepseek_client.complete',side_effect=AssertionError('No classifier call')):result=chat.prepare('p','好',remember=False)
            self.assertEqual(len(calls),1);self.assertIn('戴墨镜的小鲸鱼',calls[0][0][3]);self.assertTrue(result['tools'][0]['result']['accepted'])


if __name__=='__main__':unittest.main()
