from pathlib import Path
from types import SimpleNamespace
import json,tempfile,unittest
from persona_store import PersonaStore,Dialogue,dania_card,card_messages
from persona_learning import persona_signature
from social_examples import ORIGINAL_EXAMPLES,select_examples,finish_style_prompt
from qq_chat import QQConversation,INSTRUCTIONS
from dialogue_context import pack_messages
from conversation_rhythm import playful_echo,glossary_like

class SocialExampleTests(unittest.TestCase):
    def test_examples_beyond_old_first_eight_are_selected_for_current_topic(self):
        card=dania_card();self.assertEqual(len(card.examples),12)
        chosen=select_examples(card.examples,'你还记得我之前说喜欢什么吗？')
        self.assertEqual(chosen[0],card.examples[-1]);self.assertEqual(len(chosen),6)
        self.assertEqual(select_examples(card.examples,'八乘七等于多少？')[0].assistant,'56。')
    def test_demo_reordering_preserves_current_user_and_tool_authority(self):
        messages=card_messages(dania_card(),'FIXED_AUTHORITY','今天加班了')+[{'role':'user','content':'THIS_ACTUAL_REQUEST'},{'role':'system','content':'LIVE_TOOL_RECEIPT'}]
        packed=pack_messages(finish_style_prompt(messages));self.assertEqual(packed[-1],{'role':'user','content':'THIS_ACTUAL_REQUEST'})
        system=packed[0]['content'];self.assertLess(system.index('LIVE_TOOL_RECEIPT'),system.index('以下JSON仅为说话风格示范'));self.assertIn('FIXED_AUTHORITY',system)
        self.assertIn('不表示工具已执行',system);self.assertTrue(all(m['role']!='assistant' for m in packed))
    def test_authored_example_change_does_not_invalidate_persona_bound_learning(self):
        card=dania_card();signature=persona_signature(card);card.examples=[Dialogue(user='人工编写的新例子',assistant='新表达')]
        self.assertEqual(signature,persona_signature(card))
    def test_recently_delivered_long_demo_is_not_injected_as_next_stock_reply(self):
        card=dania_card();used=card.examples[1].assistant
        chosen=select_examples(card.examples,'加班好累',avoid_replies=[used])
        self.assertNotIn(used,[r.assistant for r in chosen]);self.assertEqual(len(chosen),6)
    def test_optional_sticker_error_keeps_memory_answer_as_primary_task(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);captured=[];count=0
            def client(messages,tools):
                nonlocal count
                captured.append(messages);count+=1
                if count==1:return {'tool_calls':[{'id':'optional','type':'function','function':{'name':'use_sticker','arguments':json.dumps({'emotion':'开心','query':'可爱','theme':'鸣潮','character':''},ensure_ascii=False)}}]},{}
                self.assertIn('配图只是可选表达',str(messages));self.assertIn('可选配图没有准备成功',str(messages));return {'content':'鸣潮的，要可爱一点的。'},{}
            chat=QQConversation(root,client=client);chat.memory.add('peer','user','我喜欢鸣潮表情包，要可爱一点的。')
            chat.prepare_stickers=lambda *a,**kw:(_ for _ in ()).throw(ValueError('unit unavailable'))
            result=chat.prepare('peer','你还记得我喜欢什么表情吗？',remember=False);self.assertIn('鸣潮',result['reply']);self.assertFalse(result['attachments'])
    def test_current_preview_topic_drives_examples_and_learning_not_previous_topic(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);chat=QQConversation(root,client=lambda *_:({'content':'56。'},{}));chat.persona.save(dania_card());chat.memory.add('peer','user','你又在摸鱼？');seen=[]
            chat.memory.learning.prompt=lambda peer,text,card:(seen.append(text) or {'role':'system','content':'learned'},[])
            messages=chat.memory.context('peer','八乘七等于多少？');prompt=next(m['content'] for m in messages if '以下JSON仅为说话风格示范' in m.get('content',''))
            examples=json.loads(prompt.split('\n',1)[1]);self.assertEqual(examples[0]['assistant'],'56。');self.assertEqual(seen,['八乘七等于多少？'])
    def test_original_examples_do_not_teach_fake_task_success_or_repeated_catchphrases(self):
        values=[answer for _,answer in ORIGINAL_EXAMPLES]
        self.assertFalse(any('已经' in v or '已发送' in v or '……' in v or '今天已经够努力' in v for v in values))
        self.assertNotIn('没有接入唱歌工具',INSTRUCTIONS);self.assertNotIn('发到当前获准的私聊',INSTRUCTIONS)
    def test_reordered_recent_meme_phrase_is_still_a_social_echo_not_a_lesson(self):
        rows=[('assistant','有个“规矩体”，常说先把规矩立住，路才越走越宽。')]
        self.assertTrue(playful_echo('规矩先立住，路才宽',rows));self.assertTrue(glossary_like('嗯，你学得还挺快。'))
        for text in ['这是什么梗？','帮我查规矩体的出处','给我画一张规矩体图片']:self.assertFalse(playful_echo(text,rows))
    def test_plain_greeting_does_not_inject_clock_to_guess_sleep_habits(self):
        with tempfile.TemporaryDirectory() as td:
            chat=QQConversation(Path(td),client=lambda *_:({'content':'在呢。'},{}));self.assertNotIn('时间背景',str(chat.memory.context('peer','在吗？')))
            self.assertIn('时间背景',str(chat.memory.context('peer','现在几点？')))
    def test_explicit_plain_result_has_no_optional_sticker_or_qq_face(self):
        with tempfile.TemporaryDirectory() as td:
            captured=[]
            def client(messages,tools):captured.extend(t['function']['name'] for t in tools);return {'content':'56。'},{}
            chat=QQConversation(Path(td),client=client)
            chat.prepare_stickers=lambda *a,**kw:self.fail('No decorative picture for a plain result')
            result=chat.prepare('peer','7乘8，直接给个数。',remember=False)
            self.assertFalse(result['attachments']);self.assertFalse(result['tools']);self.assertNotIn('use_sticker',captured);self.assertNotIn('use_qq_face',captured)
            from chat_style import plain_answer_only
            self.assertFalse(plain_answer_only('画图，只要结果'));self.assertFalse(plain_answer_only('发一张数字表情包'))
    def test_live_conversation_keeps_selected_style_at_end_of_system_context(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);captured=[]
            chat=QQConversation(root,client=lambda messages,tools:(captured.append(messages) or {'content':'56。'},{}));chat.persona.save(dania_card());chat.creative=SimpleNamespace(prompt_context=lambda _:[],request=lambda *a,**kw:self.fail('No task for arithmetic'))
            result=chat.prepare('peer','八乘七等于多少？',remember=False);self.assertEqual(result['reply'],'56。')
            system=captured[0][0]['content'];self.assertLess(system.index('本轮实际状态'),system.index('以下JSON仅为说话风格示范'))
            self.assertNotIn('管理员真实私聊已经接通群授权',system)

if __name__=='__main__':unittest.main()
