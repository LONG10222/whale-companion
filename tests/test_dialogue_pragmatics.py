from pathlib import Path
from types import SimpleNamespace
import json,tempfile,time,unittest
from unittest.mock import patch
from dialogue_pragmatics import closing_decision,vision_history,confirmed_proposal
from dialogue_context import resolve_creative_followup,intent_proof
from qq_chat import QQConversation
from qq_bridge import QQBridge


class PragmaticsTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def chat(self,reply='在呢。'):
        self.calls=[]
        def client(messages,tools):self.calls.append((messages,tools));return {'content':reply},{}
        return QQConversation(self.root,client=client)
    def picture(self):return {'image_url':{'url':'data:image/jpeg;base64,dGVzdA=='},'sticker_hint':True}
    def sticker(self,chat,act='tease',certainty='high'):
        chat.images.describe=lambda *_:[{'id':'a'*24,'image_kind':'meme','description':'卡通人物与省略号','text':'噢…','possible_meanings':['知道了','淡淡收尾'],
            'conversation_turn':{'meaning':'对上一段说明表示收到' if act=='end_topic' else '接着前一句打趣','response_act':act,'certainty':certainty,'reply_hint':'要不要我再给你解释一下？'}}]
    def test_acknowledgement_after_task_acceptance_does_not_generate_or_retry(self):
        c=self.chat();c.memory.add('peer','user','画一张鲸鱼');c.memory.add('peer','assistant','我按这张原图改，做好后发你。')
        c.creative=SimpleNamespace(prompt_context=lambda *_:self.fail('No task lookup for a closing acknowledgement'),request=lambda *a,**kw:self.fail('No new work'))
        result=c.prepare('peer','好')
        self.assertTrue(result['silent']);self.assertFalse(result['attachments']);self.assertFalse(result['tools']);self.assertFalse(self.calls)
        self.assertEqual(c.memory.recent('peer')[-1],('user','好'))
    def test_ack_does_not_become_a_semantic_tool_authorization(self):
        history=[('user','画一只鲸鱼'),('assistant','已经开始画，画好发你。')]
        self.assertIsNone(intent_proof('draw_picture','好','peer',{'mid':'one'},history,[],lambda *a,**kw:self.fail('No classifier needed')))
    def test_real_fresh_proposal_is_not_mistaken_for_signoff(self):
        history=[('user','我想看鲸鱼'),('assistant','要不要我帮你画一只鲸鱼？')]
        self.assertIsNone(closing_decision('好',history))
        proof=intent_proof('draw_picture','好','peer',{'mid':'one'},history,[],lambda *a,**kw:({'content':'{"authorized":true}'},{}))
        self.assertIsNotNone(proof)
    def test_confirmation_binds_nearest_proposal_instead_of_older_drawing_request(self):
        history=[('user','以前画过鲸鱼'),('assistant','已经画好。'),('user','换点别的'),('assistant','要不要我帮你画一只猫？')]
        result=confirmed_proposal('好',history,'draw_picture');self.assertIn('一只猫',result);self.assertNotIn('鲸鱼',result)
        self.assertIsNone(confirmed_proposal('好',history,'delegate_codex'))
    def test_plain_ack_never_confirms_a_computer_or_private_file_operation(self):
        history=[('user','你好'),('assistant','要不要我帮你整理电脑里的文件？')]
        self.assertIsNone(intent_proof('delegate_codex','好','peer',{'mid':'one'},history,[],lambda *a,**kw:self.fail('No authority expansion from a bare acknowledgement')))
    def test_question_and_explicit_continuation_are_not_silenced(self):
        for text in ['好？','好家伙','好看吗','好，继续修图','好，但是哪里不对']:
            self.assertIsNone(closing_decision(text,[('assistant','已经收到。')]),text)
    def test_short_ack_with_native_qq_face_can_end_without_an_extra_reaction(self):
        c=self.chat();c.memory.add('peer','assistant','我已经接到任务，做好后发你。')
        result=c.prepare('peer','好[QQ表情：微笑]',remember=False)
        self.assertTrue(result['silent']);self.assertFalse(self.calls)
    def test_goodnight_is_short_and_repeated_goodbye_does_not_loop(self):
        c=self.chat();result=c.prepare('peer','晚安');self.assertEqual(result['reply'],'晚安。');self.assertFalse(self.calls)
        c.memory.add('peer','assistant','晚安。');self.assertTrue(c.prepare('peer','晚安')['silent'])
    def test_explicit_signoff_does_not_offer_a_new_topic_or_sticker(self):
        c=self.chat('要不要再聊点别的？');c.memory.add('peer','assistant','已经处理好了。')
        result=c.prepare('peer','先这样吧');self.assertTrue(result['silent']);self.assertFalse(self.calls);self.assertFalse(result['attachments'])
    def test_acknowledgement_followed_by_explicit_signoff_is_still_a_signoff(self):
        for text in ['嗯嗯，先这样。','好，就这样吧','好的,不聊了']:
            c=self.chat('等你回来。');self.assertTrue(c.prepare('peer',text,remember=False)['silent']);self.assertFalse(self.calls)
        for text in ['好，继续修图','嗯嗯，先这样？','好，先这样，但是还想问个事']:
            self.assertIsNone(closing_decision(text,[('assistant','收到了。')]))
    def test_sticker_signoff_uses_context_and_does_not_invoke_reply_model(self):
        c=self.chat('再给你解释解释？');self.sticker(c,'end_topic');c.memory.add('peer','assistant','这是一段已经讲完的说明。')
        result=c.prepare('peer','',images=[self.picture()]);self.assertTrue(result['silent']);self.assertFalse(self.calls)
        self.assertEqual(len([r for r in c.memory.recent('peer') if r[0]=='assistant']),1)
    def test_plain_acknowledgement_sticker_does_not_extend_finished_explanation(self):
        c=self.chat('规矩立住了，接下来就看你怎么走。');self.sticker(c,'acknowledge','medium');c.memory.add('peer','assistant','规矩体是短剧里的一种用法，你学得挺快。')
        result=c.prepare('peer','',images=[self.picture()],remember=False)
        self.assertTrue(result['silent']);self.assertFalse(self.calls)
    def test_low_key_acknowledgement_variant_after_explanation_does_not_restart_it(self):
        history=[('assistant','这个梗的含义是表达先讲规矩、再安排事情。它通常用于戏剧化的场景，也可以用来表达某种强调。')]
        semantic={'response_act':'acknowledge','certainty':'medium','visible_text':'哦鲸鲸…'}
        self.assertTrue(closing_decision('',history,semantic=semantic,has_media=True))
        for changed in [{'response_act':'tease'},{'certainty':'low'},{'visible_text':'牛惊'}]:
            self.assertIsNone(closing_decision('',history,semantic=semantic|changed,has_media=True))
        self.assertIsNone(closing_decision('',[('assistant','这池子把你拉黑了。')],semantic=semantic,has_media=True))
    def test_same_sticker_can_continue_banter_and_keeps_real_pixels(self):
        c=self.chat('行行，我收回刚才那句嘴硬。');self.sticker(c,'tease');c.memory.add('peer','assistant','这次我肯定不会翻车。')
        result=c.prepare('peer','',images=[self.picture()],remember=False);self.assertFalse(result.get('silent',False))
        messages=self.calls[0][0];current=next(m for m in reversed(messages) if m['role']=='user')
        self.assertEqual(current['content'][1]['type'],'image_url');self.assertNotIn('要不要我再给你解释一下',str(messages))
    def test_uncertain_meme_is_not_silenced_by_a_low_confidence_guess(self):
        c=self.chat('这回我没接准。');self.sticker(c,'end_topic','low')
        self.assertFalse(c.prepare('peer','',images=[self.picture()],remember=False).get('silent',False));self.assertTrue(self.calls)
    def test_explicit_meaning_question_is_answered_even_if_picture_looks_like_signoff(self):
        c=self.chat('这张表情在这里更像“知道了，到这儿就行”。');self.sticker(c,'end_topic')
        result=c.prepare('peer','这张表情是什么意思？',images=[self.picture()],remember=False)
        self.assertIn('知道了',result['reply']);self.assertTrue(self.calls)
    def test_previous_image_words_and_meaning_survive_in_vision_context_as_evidence(self):
        old='[收到图片或表情]\n[本条QQ消息附图，识别资料]\n'+json.dumps([{'id':'a'*24,'image_kind':'meme','text':'GG','conversation_turn':{'meaning':'拿上一局失误开玩笑','response_act':'tease'}}],ensure_ascii=False)
        rows=vision_history([('user',old),('assistant','这次我可不嘴硬了。')])
        self.assertEqual(rows[0]['text'],'');self.assertEqual(rows[0]['image_evidence'][0]['visible_text'],'GG');self.assertIn('上一局',rows[0]['image_evidence'][0]['previous_context_meaning'])
    def test_new_photo_blocks_old_character_drawing_as_redraw_source(self):
        old='帮我画DeepSeek鲸娘与达妮娅的单人融合图'
        photo='[收到图片或表情]\n[本条QQ消息附图，识别资料]\n'+json.dumps([{'id':'a'*24,'image_kind':'photo','text':''}])
        history=[('user',old),('assistant','画好了。'),('user',photo)]
        value=resolve_creative_followup('重新画',history,[{'kind':'draw_picture','request_text':old}])
        self.assertIn('原图',value);self.assertNotIn('融合',value)
    def test_session_gap_excludes_old_task_from_active_context_without_deleting_history(self):
        c=self.chat();mid=c.memory.add('peer','user','昨天那张旧角色融合图')
        with c.memory.db() as db:db.execute('UPDATE messages SET at=? WHERE id=?',(time.time()-86400,mid))
        c.memory.add('peer','user','今天聊新的事情')
        self.assertNotIn('旧角色融合',str(c.memory.active_recent('peer',64)));self.assertIn('旧角色融合',str(c.memory.recent('peer',64)))
        self.assertNotIn('旧角色融合',str(c.memory.context('peer')))
    def test_old_job_is_not_injected_as_current_drawing_context(self):
        c=self.chat();old='昨天DeepSeek与达妮娅融合要求';seen=[]
        c.creative=SimpleNamespace(prompt_context=lambda *_:[{'kind':'draw_picture','state':'completed','at':time.time()-86400,'request_text':old}],request=lambda *a,**kw:{'accepted':True})
        c.memory.add('peer','user','今天想画一只猫')
        c.prepare('peer','你好',remember=False)
        self.assertNotIn(old,str(self.calls))
    def test_new_natural_drawing_request_is_not_forced_into_old_character_redraw(self):
        c=self.chat();c.native_client=True;old='画一张鲸娘和达妮娅的融合图';c.memory.add('peer','user',old);calls=[]
        c.creative=SimpleNamespace(prompt_context=lambda _:[],request=lambda *a,**kw:calls.append((a,kw)) or {'accepted':True})
        c.model_response=lambda *a:({'tool_calls':[{'id':'new-natural','type':'function','function':{'name':'draw_picture','arguments':'{"prompt":"一张小橘猫头像"}'}}]}, {})
        text='整一张能当头像的，主题是小橘猫'
        with patch('deepseek_client.complete',return_value=({'content':'{"authorized":true,"continuation":false}'},{})):c.prepare('peer',text,remember=False)
        self.assertEqual(calls[0][0][3],text);self.assertNotIn('融合',calls[0][0][3])
    def test_semantically_confirmed_continuation_keeps_actual_user_requirements(self):
        c=self.chat();c.native_client=True;old='帮我画一张小橘猫头像';c.memory.add('peer','user',old);calls=[]
        c.creative=SimpleNamespace(prompt_context=lambda _:[],request=lambda *a,**kw:calls.append((a,kw)) or {'accepted':True})
        c.model_response=lambda *a:({'tool_calls':[{'id':'continue-natural','type':'function','function':{'name':'draw_picture','arguments':'{"prompt":"让刚才的小橘猫更自然"}'}}]}, {})
        with patch('deepseek_client.complete',return_value=({'content':'{"authorized":true,"continuation":true}'},{})):c.prepare('peer','按刚才那张再调得自然一点',remember=False)
        self.assertIn(old,calls[0][0][3])
    def test_public_query_can_be_selected_without_search_keywords(self):
        c=self.chat();c.native_client=True;calls=[];text='打听一下鸣潮新角色的官方技能说明'
        from conversation_tools import external_intent
        self.assertFalse(external_intent(text))
        c.creative=SimpleNamespace(prompt_context=lambda _:[],request=lambda *a,**kw:calls.append((a,kw)) or {'accepted':True})
        def response(messages,tools,*_):
            self.assertIn('lookup_web',[t['function']['name'] for t in tools])
            return {'tool_calls':[{'id':'natural-research','type':'function','function':{'name':'lookup_web','arguments':'{"query":"鸣潮 新角色 官方技能"}'}}]},{}
        c.model_response=response
        with patch('deepseek_client.complete',return_value=({'content':'{"authorized":true,"continuation":false}'},{})):result=c.prepare('peer',text,remember=False)
        self.assertTrue(result['tools'][0]['result']['accepted']);self.assertEqual(calls[0][0][3],text)
    def test_unrequested_public_query_is_rejected_before_execution(self):
        c=self.chat();c.native_client=True;c.creative=SimpleNamespace(prompt_context=lambda _:[],request=lambda *a,**kw:self.fail('casual talk cannot authorize lookup'))
        responses=iter([({'tool_calls':[{'id':'unwanted-research','type':'function','function':{'name':'lookup_web','arguments':'{"query":"鸣潮"}'}}]},{}),({'content':'接着聊。'}, {})])
        c.model_response=lambda *_:next(responses)
        with patch('deepseek_client.complete',return_value=({'content':'{"authorized":false,"continuation":false}'},{})):result=c.prepare('peer','这梗还挺逗的',remember=False)
        self.assertFalse(result['tools'][0]['result'].get('accepted'));self.assertEqual(result['reply'],'接着聊。')
    def test_model_can_choose_silence_for_a_natural_signoff_not_in_word_list(self):
        c=self.chat();text='行啦，咱今天到这儿'
        c.client=lambda *_:({'tool_calls':[{'id':'quiet','type':'function','function':{'name':'end_chat_turn','arguments':json.dumps({'evidence':'今天到这儿','reason':'topic_end'},ensure_ascii=False)}}]}, {})
        result=c.prepare('peer',text,remember=False);self.assertTrue(result['silent']);self.assertEqual(result['closing_reason'],'model_topic_end')
    def test_silence_tool_is_not_offered_for_an_explicit_task_or_question(self):
        for text in ['帮我画一只鲸鱼','你为什么刚才不回复？']:
            c=self.chat('我在。');c.prepare('peer',text,remember=False)
            self.assertNotIn('end_chat_turn',[t['function']['name'] for t in self.calls[-1][1]])
    def test_valid_natural_closing_precedes_optional_sticker_in_same_batch(self):
        c=self.chat();text='收到啦，那就这样～'
        c.client=lambda *_:({'tool_calls':[
            {'id':'decoration','type':'function','function':{'name':'use_sticker','arguments':'{}'}},
            {'id':'quiet','type':'function','function':{'name':'end_chat_turn','arguments':json.dumps({'evidence':'那就这样','reason':'topic_end'},ensure_ascii=False)}}]}, {})
        c.prepare_stickers=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('closing should not prepare decoration'))
        result=c.prepare('peer',text,remember=False);self.assertTrue(result['silent']);self.assertEqual(result['tools'],[]);self.assertEqual(result['attachments'],[])


class SilentTransportTests(unittest.TestCase):
    def test_closing_is_handled_without_empty_message_failure_or_phantom_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);chat=QQConversation(root,client=lambda *_:({'content':'你好呀。'},{}));bridge=QQBridge(root,chat)
            admin='87654321';bot='12345678';bridge.cfg.update(locked_bot_id=bot,test_contact={'name':'long','user_id':admin});bridge.configure(True,bot,[admin],False)
            bridge.connected=True;bridge.login_id=bot;bridge.epoch='unit';bridge.batcher.quiet=0;sent=[]
            bridge.call=lambda action,params:sent.append((action,params)) or {'message_id':len(sent)}
            chat.memory.add(admin,'assistant','我开始处理，画好发你。')
            def event(mid,text):return {'post_type':'message','message_type':'private','sub_type':'friend','self_id':bot,'user_id':admin,'message_id':mid,'message':[{'type':'text','data':{'text':text}}]}
            try:
                self.assertTrue(bridge.accept(event(1,'好'),'unit'));bridge.pool.submit(lambda:None).result(5)
                with chat.memory.db() as db:self.assertEqual(db.execute('SELECT state FROM events WHERE mid=?',('1',)).fetchone()[0],'handled_silently')
                self.assertFalse(sent);self.assertFalse(any(e['kind']=='failure_notice' for e in bridge.events))
                self.assertEqual(len([r for r in chat.memory.recent(admin) if r[0]=='assistant']),1)
                self.assertTrue(bridge.accept(event(2,'你好'),'unit'));bridge.pool.submit(lambda:None).result(5);self.assertEqual(len(sent),1)
                with patch('usage_ledger.Ledger.record',side_effect=RuntimeError('unit audit unavailable')):
                    self.assertTrue(bridge.accept(event(3,'收到'),'unit'));bridge.pool.submit(lambda:None).result(5)
                self.assertEqual(len(sent),1);self.assertFalse(any(e['kind']=='failure_notice' for e in bridge.events))
            finally:bridge.close();bridge.group_pool.shutdown(wait=True);bridge.pool.shutdown(wait=True)


if __name__=='__main__':unittest.main()
