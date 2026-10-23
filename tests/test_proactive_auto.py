import json,unittest
from datetime import datetime
from types import SimpleNamespace
from tests import test_proactive as fixture
from proactive_chat import ProactiveChat,ZONE
from qq_bridge import QQActionRejected

class AutomaticTests(unittest.TestCase):
    def setUp(self):
        self.case=fixture.ProactiveTests();self.case.setUp();self.s=self.case.service
        self.news={'id':'real-source','title':'测试：新型望远镜开放观测','url':'https://www.chinanews.com.cn/test/story.shtml',
                   'publisher':'中新网','published_at':self.case.now-120,'date':'2026-10-21'}
        self.s.topics=SimpleNamespace(collect=lambda:{'items':[self.news],'feeds':[]},snapshot=lambda:{'cached_today_items':1,'feeds':[]})
    def tearDown(self):self.case.tearDown()
    def enable(self):self.s.configure(True,'automatic',True)
    def response(self,**values):
        data={'should_contact':True,'topic':'随意聊聊','message':'游戏背包里舍不得扔的东西，你真的用过几次？','reason':'自由想一个小问题。','source_ids':[],'topic_kind':'free','news_ids':[]}
        data.update(values);self.s.client=lambda *_:({'content':json.dumps(data,ensure_ascii=False)},{})
    def test_automatic_tick_selects_and_sends_once_to_fixed_owner(self):
        self.enable();self.response();self.s.tick();self.assertEqual(len(self.case.sent),1)
        action,params=self.case.sent[0];self.assertEqual(action,'send_private_msg');self.assertEqual(params['user_id'],int(self.case.peer))
        self.assertTrue(self.s.state()['automatic_send']);self.assertEqual(self.s.state()['deliveries'][0]['mode'],'automatic')
        self.s.tick();self.assertEqual(len(self.case.sent),1)
    def test_switch_from_preview_does_not_send_old_preview(self):
        old=self.case.draft();self.enable();self.response(message='今天换个新问题。');self.s.tick();self.assertFalse(self.case.sent)
        self.case.now+=61;self.s.tick()
        text=self.case.sent[0][1]['message'][0]['data']['text'];self.assertNotEqual(text,old['message']);self.assertEqual(text,'今天换个新问题。')
    def test_manual_draft_never_becomes_automatic(self):
        self.enable();self.response(message='手动预览的文字。');manual=self.s.generate(manual=True)
        self.assertEqual(manual['kind'],'manual');self.assertFalse(self.case.sent)
        self.response(message='定时自己选择的文字。');self.case.now+=61;self.s.tick()
        self.assertEqual(self.case.sent[0][1]['message'][0]['data']['text'],'定时自己选择的文字。')
    def test_saving_edits_converts_auto_candidate_to_manual(self):
        self.enable();draft=self.s.generate();self.s.edit(draft['id'],'手动改写。')
        with self.assertRaises(ValueError):self.s._send_in_bridge_queue(draft['id'],'手动改写。',True)
        self.assertFalse(self.case.sent)
    def test_pause_while_model_thinks_cancels_auto_draft(self):
        self.enable();original=self.s.client
        def changed(*args):
            result=original(*args);self.s.configure(False);return result
        self.s.client=changed;self.s.tick();self.assertFalse(self.case.sent)
        self.assertEqual(self.s.state()['drafts'][0]['state'],'stale')
    def test_incoming_while_generating_prevents_auto_send(self):
        self.enable();original=self.s.client
        def changed(*args):
            result=original(*args);self.s.incoming(self.case.inbound());return result
        self.s.client=changed;self.s.tick();self.assertFalse(self.case.sent)
    def test_opt_out_and_quiet_hours_still_block_automatic(self):
        self.enable();self.s.incoming(self.case.inbound('别主动找我'));self.case.now+=7201;self.s.tick();self.assertFalse(self.case.sent)
        self.s.resume_contact();self.case.now=datetime(2026,10,21,23,tzinfo=ZONE).timestamp();self.s.tick();self.assertFalse(self.case.sent)
    def test_unknown_receipt_is_not_retried_after_restart(self):
        self.enable()
        def unknown(*args,**kwargs):self.case.sent.append(args);raise RuntimeError('receipt missing')
        self.case.bridge.call=unknown;self.s.tick()
        other=ProactiveChat(self.case.root,self.case.bridge,self.s.client,lambda:self.case.now,topics=self.s.topics)
        try:other.tick();self.assertEqual(len(self.case.sent),1);self.assertEqual(other.state()['deliveries'][0]['status'],'uncertain')
        finally:other.close()
    def test_news_must_have_real_selected_source_and_link(self):
        self.enable();self.response(topic_kind='news',news_ids=['real-source'],message='看到条消息说有新望远镜开放观测，光听标题就有点想看星星。')
        self.s.tick();text=self.case.sent[0][1]['message'][0]['data']['text'];self.assertTrue(text.endswith('\n'+self.news['url']))
        self.assertEqual(self.s.state()['drafts'][0]['news_sources'][0]['id'],'real-source')
    def test_invented_news_id_is_not_delivered(self):
        self.enable();self.response(topic_kind='news',news_ids=['invented']);self.s.tick();self.assertFalse(self.case.sent)
        self.assertEqual(self.s.state()['drafts'][0]['state'],'skipped')
    def test_unattributed_latest_news_is_rejected(self):
        self.enable();self.response(message='最新消息，游戏出新角色了！');self.s.tick();self.assertFalse(self.case.sent)
    def test_news_is_checked_again_on_next_day(self):
        self.enable();self.response(topic_kind='news',news_ids=['real-source']);draft=self.s.generate()
        self.case.now=datetime(2026,10,22,10,tzinfo=ZONE).timestamp()
        with self.assertRaises(ValueError):self.s._send_in_bridge_queue(draft['id'],draft['message'],True)
        self.assertFalse(self.case.sent)
    def test_empty_news_pool_still_allows_free_topic(self):
        self.enable();self.s.topics=SimpleNamespace(collect=lambda:{'items':[],'feeds':[]},snapshot=lambda:{})
        self.response(message='想到个小问题，游戏里的钓鱼和打怪，你更容易沉迷哪个？');self.s.tick();self.assertEqual(len(self.case.sent),1)
    def test_news_toggle_and_mode_survive_service_reconstruction(self):
        self.s.configure(True,'automatic',False)
        other=ProactiveChat(self.case.root,self.case.bridge,self.s.client,lambda:self.case.now,topics=self.s.topics)
        try:self.assertEqual(other.settings()['mode'],'automatic');self.assertFalse(other.state()['news_enabled'])
        finally:other.close()
    def test_model_can_choose_not_to_contact_and_retry_is_bounded(self):
        self.enable();calls=[]
        def skip(*args):calls.append(1);return {'content':'{"should_contact":false,"message":"","reason":"这次先不打扰"}'},{}
        self.s.client=skip;self.s.tick();self.case.now+=61;self.s.tick();self.assertEqual(len(calls),1)
        self.case.now+=7200;self.s.tick();self.assertEqual(len(calls),2);self.assertFalse(self.case.sent)
        self.case.now+=7200;self.s.tick();self.case.now+=7200;self.s.tick();self.assertEqual(len(calls),3)
    def test_switch_to_preview_during_final_queue_check_cancels_send(self):
        self.enable();draft=self.s.generate();original=self.s.eligibility
        def changed(*args,**kwargs):
            if kwargs.get('ignore_delivery_id'):self.s.configure(True,'preview_only')
            return original(*args,**kwargs)
        self.s.eligibility=changed
        with self.assertRaises(QQActionRejected):self.s._send_in_bridge_queue(draft['id'],draft['message'],True)
        self.assertFalse(self.case.sent)
    def test_mode_configuration_requires_owner_and_rejects_extra_recipient(self):
        from fastapi import FastAPI,Header,HTTPException
        from fastapi.testclient import TestClient
        from proactive_routes import register_proactive_routes
        app=FastAPI()
        def auth(authorization:str=Header(default='')):
            if authorization!='Bearer own':raise HTTPException(401)
        register_proactive_routes(app,authorize=auth,root=self.case.root,bridge=self.case.bridge)
        h=TestClient(app);body={'enabled':True,'mode':'automatic','news_enabled':True}
        self.assertEqual(h.post('/qq/proactive/config',json=body).status_code,401)
        headers={'Authorization':'Bearer own'}
        self.assertEqual(h.post('/qq/proactive/config',headers=headers,json={**body,'peer':'99999999'}).status_code,422)
        self.assertEqual(h.post('/qq/proactive/config',headers=headers,json={**body,'mode':'anything'}).status_code,422)
        result=h.post('/qq/proactive/config',headers=headers,json=body)
        self.assertEqual(result.status_code,200);self.assertTrue(result.json()['automatic_send'])
    def test_generation_context_has_headlines_without_forcing_news(self):
        self.enable();self.s.generate(manual=True);messages=self.case.calls[0]
        data=json.loads(messages[-1]['content']);self.assertEqual(data['news'][0]['id'],'real-source')
        self.assertTrue(data['preview_only']);self.assertIn('不固定', ''.join(m['content'] for m in messages).replace('不局限','不固定'))

if __name__=='__main__':unittest.main()
