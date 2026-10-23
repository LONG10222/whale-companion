import json,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from bilibili_sharing import BilibiliSharing,wants_bilibili
from qq_chat import ChatMemory,QQConversation
from persona_store import PersonaStore
from fastapi import FastAPI,HTTPException,Header
from fastapi.testclient import TestClient

BV='BV1d8pU6xEZX';OWNER='87654321';OTHER='98765432';BOT='12345678'

class SharingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        (self.root/'configs').mkdir();(self.root/'qq.local.json').write_text(json.dumps({'test_contact':{'name':'owner','user_id':OWNER}}),encoding='utf-8')
        (self.root/'configs/bilibili.local.json').write_text(json.dumps({'enabled':True,'auto_read_enabled':True,'auto_like':False,'home':str(self.root/'bilibili-data')}),encoding='utf-8')
        self.s=BilibiliSharing(self.root);self.memory=ChatMemory(self.root/'data/qq',PersonaStore(self.root))
        self.row={'at':__import__('datetime').datetime.now().astimezone().isoformat(),'items':[{'bvid':BV,'status':'selected','title':'公开教程','summary':'使用字幕判断视频价值，先配置好关键词。','take':'有具体的配置说明。','reason':'与话题相关。','evidence':'先配置好关键词','published':1,'duration_seconds':157}]}
        self.source=self.s.remember(self.row)[0]
    def tearDown(self):self.tmp.cleanup()
    def context(self,mid='22'):
        self.memory.claim(BOT,mid,OWNER)
        return {'deliver':True,'bot':BOT,'mid':mid,'memory_peer':OWNER,'group_id':'','allow_admin_commands':True}
    def test_natural_requests_and_negation(self):
        for text in ['帮我去B站看看有什么有意思的','B站有没有好看的游戏视频，推荐一个','看看 https://www.bilibili.com/video/'+BV]:self.assertTrue(wants_bilibili(text))
        for text in ['我喜欢看B站','不要去刷B站了','能接入B站吗']:self.assertFalse(wants_bilibili(text))
    def test_number_identity_private_scope_and_request_proof(self):
        for peer in [OTHER,'group:12345678:'+OWNER]:
            with self.assertRaises(ValueError):self.s.read(peer,'AI',raw_text='帮我刷B站')
        with self.assertRaises(ValueError):self.s.read(OWNER,'AI',raw_text='帮我刷B站',context={'deliver':True,'bot':BOT,'mid':'missing'})
        out=self.s.read(OWNER,'AI',BV,raw_text='帮我看看这个B站视频',context=self.context())
        self.assertTrue(out['reused']);self.assertEqual(out['items'][0]['url'],'https://www.bilibili.com/video/'+BV+'/')
    def test_sources_cannot_supply_new_link_or_fake_video(self):
        self.assertFalse(self.s.valid_sources([self.source|{'url':'https://evil.test/'}]))
        self.assertFalse(self.s.valid_sources([self.source|{'bvid':'BV1111111111'}]))
    def test_followup_needs_real_video_context_not_a_fixed_command(self):
        history=[('user','帮我去B站看看'),('assistant','这个挺有意思。\n'+self.source['url'])]
        self.assertTrue(self.s.requested(OWNER,'再找一条',history))
        self.assertFalse(self.s.requested(OWNER,'再找一条',[('user','画只猫'),('assistant','画好了')]))
    def test_actual_shared_source_is_deduplicated(self):
        self.s.mark_shared(OWNER,[self.source],42)
        self.assertFalse(self.s.topics(OWNER));self.assertEqual(len(self.s.topics(OWNER,include_shared=True)),1)
    def test_no_comment_or_coins_before_exact_confirmation(self):
        post=[]
        class FakeClient:
            def __init__(self,*a,**k):pass
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def request(self,url,params=None):return {'bvid':BV,'aid':123,'money':10}
            def post_action(self,action,fields):post.append((action,fields));return {'rpid':7}
        self.s.social.client_factory=FakeClient
        self.memory.add(OWNER,'user','给这条视频评论一句')
        proposal=self.s.social.propose(OWNER,BV,'comment','关键词先选好，确实能省不少时间。',1,raw_text='给这条视频评论一句',context=self.context())
        self.assertFalse(post)
        self.memory.add(OWNER,'assistant',proposal['review_text'])
        result=self.s.social.confirm_chat(OWNER,'这句发吧',self.context('23'))
        self.assertTrue(result['executed']);self.assertEqual(len(post),1)
        self.assertEqual(post[0][1]['message'],proposal['comment'])
        with self.assertRaises(ValueError):self.s.social.confirm_local(OWNER,proposal['proposal_id'])
        self.assertEqual(len(post),1)
    def test_unrelated_chat_or_expired_proposal_cannot_be_confirmed_by_good(self):
        self.memory.add(OWNER,'user','给这条投币')
        p=self.s.social.propose(OWNER,BV,'coin','',1,raw_text='给这条投币',context=self.context())
        self.memory.add(OWNER,'assistant',p['review_text']);self.memory.add(OWNER,'user','今天好累')
        self.assertIsNone(self.s.social.confirm_chat(OWNER,'好',self.context('23')))
        self.assertIsNone(self.s.social.confirm_chat(OTHER,'确认',self.context('24')))
    def test_coin_amount_change_needs_a_new_proposal(self):
        self.memory.add(OWNER,'user','投一枚币')
        p=self.s.social.propose(OWNER,BV,'coin','',1,raw_text='投一枚币',context=self.context())
        self.memory.add(OWNER,'assistant',p['review_text'])
        with self.assertRaises(ValueError):self.s.social.confirm_chat(OWNER,'投二枚',self.context('23'))
    def test_public_comment_rejects_private_data(self):
        for text in ['账号123456789',('sk-' + 'a' * 24),'owner快来看','我的验证码是123456','https://evil.test']:
            with self.assertRaises(ValueError):self.s.social.propose(OWNER,BV,'comment',text,1,local=True)
    def test_like_does_not_coin_and_is_exact_once(self):
        posts=[]
        class Client:
            def __init__(self,*a,**k):pass
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def request(self,url,params=None):return 0 if 'has/like' in url else {'bvid':BV,'aid':3}
            def post_action(self,action,fields):posts.append((action,fields));return {}
        self.s.social.client_factory=Client;self.s.configure(enabled=True,auto_read_enabled=True,auto_like=True)
        self.s.social.auto_like(OWNER,self.source);self.s.social.auto_like(OWNER,self.source)
        self.assertEqual(posts,[('like',{'bvid':BV,'like':1})])
    def test_uncertain_coin_response_is_not_retried(self):
        attempts=[]
        class Client:
            def __init__(self,*a,**k):pass
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def request(self,url,params=None):return {'money':10,'bvid':BV,'aid':3}
            def post_action(self,*a):attempts.append(a);raise RuntimeError('lost reply')
        self.s.social.client_factory=Client
        p=self.s.social.propose(OWNER,BV,'coin','',1,local=True)
        with self.assertRaises(ValueError):self.s.social.confirm_local(OWNER,p['proposal_id'])
        with self.assertRaises(ValueError):self.s.social.confirm_local(OWNER,p['proposal_id'])
        self.assertEqual(len(attempts),1)
    def test_autonomous_read_reserves_only_one_round_per_day(self):
        calls=[]
        row=self.row|{'id':'real-test-read','state':'completed','elapsed_seconds':1}
        self.s.reader_factory=lambda *a:SimpleNamespace(run=lambda *args,**kwargs:calls.append(kwargs) or row)
        self.s.read(OWNER,'游戏科普',autonomous=True)
        with self.assertRaises(ValueError):self.s.read(OWNER,'音乐',autonomous=True)
        self.assertEqual(len(calls),1);self.assertEqual(calls[0]['max_reviews'],1)
    def test_manual_like_needs_current_target_and_real_owner_request(self):
        self.memory.add(OWNER,'user','帮我看看B站');self.memory.add(OWNER,'assistant','这条。\n'+self.source['url'])
        with patch.object(self.s.social,'execute',return_value={'executed':True,'message':'点好赞啦。'}) as execute:
            out=self.s.social.requested_like(OWNER,BV,'给这条点赞',self.context())
            self.assertTrue(out['executed']);self.assertEqual(execute.call_count,1)
        with self.assertRaises(ValueError):self.s.social.requested_like(OWNER,BV,'不要给它点赞',self.context('23'))
    def test_chat_calls_actual_tool_and_uses_its_source(self):
        calls=[]
        def model(messages,tools,**kwargs):
            calls.append((messages,tools))
            if len(calls)==1:return {'role':'assistant','content':None,'tool_calls':[{'id':'b1','type':'function','function':{'name':'browse_bilibili','arguments':json.dumps({'query':'AI','video':BV})}}]},{}
            return {'content':'这个会按字幕挑内容，能少踩点标题党的坑。\nhttps://www.bilibili.com/video/'+BV+'/'},{}
        chat=QQConversation(self.root,client=model);chat.bilibili=self.s
        result=chat.prepare(OWNER,'帮我看看这个B站视频 '+BV,remember=False)
        self.assertTrue(any(t['tool']=='browse_bilibili' for t in result['tools']))
        self.assertIn(BV,result['reply'])

class ProactiveVideoTests(unittest.TestCase):
    def setUp(self):
        from tests.test_proactive import ProactiveTests
        self.fx=ProactiveTests();self.fx.setUp();self.s=self.fx.service
        self.video={'id':'v1','bvid':BV,'url':'https://www.bilibili.com/video/'+BV+'/','title':'测试视频','summary':'按字幕筛选视频。','read_at':self.fx.now,'evidence':'按字幕筛选视频'}
        self.reads=[];self.shared=[]
        self.s.bilibili=SimpleNamespace(topics=lambda *a,**k:[self.video],status=lambda:{'automatic_reads_today':0,'reviewed_videos':1},
            config=lambda:{'enabled':True,'auto_read_enabled':True},valid_sources=lambda rows:[self.video] if rows and rows[0].get('id')=='v1' else [],
            mark_shared=lambda *a:self.shared.append(a),read=lambda *a,**k:self.reads.append((a,k)) or {'ok':True,'items':[self.video]})
        self.s.configure(True,'automatic',False,True)
    def tearDown(self):
        self.fx.tearDown();self.fx.doCleanups()
    def response(self,**update):
        data={'should_contact':True,'topic':'有意思的视频','message':'这个会按字幕挑内容，倒是省得我被标题骗进去。','reason':'来自真实字幕。','source_ids':[],'topic_kind':'video','video_ids':['v1']}
        data.update(update);return {'content':json.dumps(data,ensure_ascii=False)},{}
    def test_video_source_is_appended_and_existing_daily_limit_still_applies(self):
        self.s.client=lambda *a:self.response();self.s.tick();self.s.tick()
        self.assertEqual(len(self.fx.sent),1);self.assertEqual(len(self.shared),1)
        self.assertTrue(self.fx.sent[0][1]['message'][0]['data']['text'].endswith('\n'+self.video['url']))
    def test_invented_source_and_invented_link_not_delivered(self):
        for update in [{'video_ids':['fake']},{'message':'快看 https://evil.test/'}]:
            self.fx.now+=61;self.s.client=lambda *a:self.response(**update);self.s.tick()
        self.assertFalse(self.fx.sent)
    def test_model_may_read_once_then_choose_real_video(self):
        count=[]
        def model(messages,tools):
            count.append(tools)
            if tools:return {'role':'assistant','content':None,'tool_calls':[{'id':'read-1','type':'function','function':{'name':'browse_bilibili','arguments':'{"query":"游戏科普","video":""}'}}]},{}
            return self.response()
        self.s.client=model;self.s.tick();self.assertEqual(len(self.reads),1);self.assertEqual(len(self.fx.sent),1)
        self.assertTrue(count[0]);self.assertIsNone(count[-1])

class BiliRouteTests(SharingTests):
    def setUp(self):
        super().setUp()
        from bilibili_routes import register_bilibili_routes
        self.app=FastAPI()
        def auth(authorization:str=Header(default='')):
            if authorization!='Bearer local-owner':raise HTTPException(401)
        self.bridge=SimpleNamespace(chat=SimpleNamespace(),pool=None)
        self.proactive=SimpleNamespace(settings=lambda:{'bilibili_enabled':False,'enabled':True},configure=lambda *a,**k:None)
        self.service=register_bilibili_routes(self.app,root=self.root,authorize=auth,bridge=self.bridge,proactive=self.proactive)
        self.service.remember(self.row);self.client=TestClient(self.app);self.headers={'Authorization':'Bearer local-owner'}
    def tearDown(self):self.service.close();self.client.close();super().tearDown()
    def test_management_requires_auth_and_never_accepts_recipient_or_auto_coin_mode(self):
        self.assertEqual(self.client.get('/qq/bilibili/api/state').status_code,401)
        body={'enabled':True,'auto_read_enabled':True,'proactive_enabled':True,'coin_mode':'automatic'}
        self.assertEqual(self.client.post('/qq/bilibili/config',headers=self.headers,json=body).status_code,422)
        body={'bvid':BV,'action':'coin','amount':1,'recipient':OTHER}
        self.assertEqual(self.client.post('/qq/bilibili/social/propose',headers=self.headers,json=body).status_code,422)
    def test_local_proposal_does_not_execute_comment_or_coin(self):
        body={'bvid':BV,'action':'comment','comment':'按字幕挑视频，比只看标题靠谱多了。','amount':1}
        r=self.client.post('/qq/bilibili/social/propose',headers=self.headers,json=body)
        self.assertEqual(r.status_code,200);self.assertFalse(r.json()['sent']);self.assertIn(body['comment'],r.json()['review_text'])
        with self.service.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM social_actions').fetchone()[0],0)

if __name__=='__main__':unittest.main()
