"""Replay the failed owner conversation through isolated SQLite and fake Bili I/O."""
import json,time,unittest
from unittest.mock import patch
from tests import test_bilibili_sharing as sharing_fixtures
from bilibili_batches import requested_kinds,status_question

DRAFT='关键词先选好，确实能省不少时间。'
BV=sharing_fixtures.BV;OWNER=sharing_fixtures.OWNER;OTHER=sharing_fixtures.OTHER

class BatchTests(unittest.TestCase):
    def setUp(self):
        sharing_fixtures.SharingTests.setUp(self);self.posts=[];self.mid=100;self.failure='';self.balance=10
        fx=self
        class Client:
            def __init__(self,*a,**k):pass
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def request(self,url,params=None):
                if 'has/like' in url:return 0
                if 'nav' in url:return {'money':fx.balance}
                return {'bvid':(params or {}).get('bvid',BV),'aid':42}
            def post_action(self,kind,fields):
                fx.posts.append((kind,fields))
                if fx.failure==kind:raise RuntimeError('lost POST receipt')
                return {'rpid':9} if kind=='comment' else {}
        self.s.social.client_factory=Client
        self.drafter=patch.object(self.s.social,'draft_comment',return_value=DRAFT);self.drafter.start();self.addCleanup(self.drafter.stop)
    def tearDown(self):sharing_fixtures.SharingTests.tearDown(self)
    def context(self):
        self.mid+=1;return sharing_fixtures.SharingTests.context(self,str(self.mid))
    def request(self,text,action='comment',comment=DRAFT):
        self.memory.add(OWNER,'user',text)
        result=self.s.social.propose(OWNER,BV,action,comment,1,raw_text=text,context=self.context())
        self.memory.add(OWNER,'assistant',result['review_text']);return result
    def follow(self,text):
        # QQ's early confirmation hook runs before this message enters history.
        result=self.s.social.confirm_chat(OWNER,text,self.context())
        self.memory.add(OWNER,'user',text)
        if result:self.memory.add(OWNER,'assistant',result['message'])
        return result
    def automatic(self):self.s.configure(enabled=True,auto_read_enabled=True,auto_like=True,auto_comment=True,auto_coin=True)
    def states(self,result):return {r['action']:r['state'] for r in result['items']}
    def test_actual_failed_sequence_preserves_target_and_each_draft(self):
        first=self.request('找一个你喜欢的B站视频，把投币点赞并评论，把评论返给我')
        self.assertEqual([k for k,_ in self.posts],['like'])
        self.assertIn(DRAFT,first['review_text']);self.assertIn('投1枚币',first['review_text'])
        before=self.s.social.batches.current(OWNER)['id']
        state=self.follow('投币了吗？点赞了吗？')
        self.assertEqual(self.states(state),{'like':'confirmed','coin':'pending','comment':'pending'})
        self.assertEqual(self.s.social.batches.current(OWNER)['id'],before)
        coin=self.follow('直接投币')
        self.assertEqual([k for k,_ in self.posts],['like','coin']);self.assertEqual(self.states(coin)['comment'],'pending')
        self.assertEqual(self.states(self.follow('评论了吗？'))['comment'],'pending')
        done=self.follow('确认')
        self.assertEqual([k for k,_ in self.posts],['like','coin','comment'])
        self.assertIn(DRAFT,done['message']);self.assertEqual(self.states(done)['comment'],'confirmed')
        self.follow('投币并评论');self.assertEqual(len(self.posts),3)
        self.follow('算了');self.assertEqual(len(self.posts),3)
    def test_auto_batch_executes_all_three_and_reports_receipts(self):
        self.automatic();out=self.request('给这条投币点赞并评论')
        self.assertEqual([k for k,_ in self.posts],['like','coin','comment'])
        self.assertEqual(set(self.states(out).values()),{'confirmed'});self.assertFalse(out['prepared'])
        self.assertIn(DRAFT,out['message']);self.assertNotIn('确认吗',out['message'])
    def test_status_never_creates_replaces_or_sends(self):
        p=self.request('投币并评论')
        for text in ['投币了吗？点赞了吗？','评论发出了吗？','投币结果怎么样','评论成功了吗？']:
            self.assertTrue(self.follow(text)['status_only'])
        self.assertFalse(self.posts)
        self.assertEqual(self.s.social.batches.current(OWNER)['id'],p['batch_id'])
        with self.s.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM proposals').fetchone()[0],2)
    def test_plain_confirmation_after_status_handles_both_pending_operations(self):
        self.request('投币并评论');self.follow('投币了吗？')
        self.follow('确认');self.assertEqual([k for k,_ in self.posts],['coin','comment'])
    def test_good_after_status_can_end_conversation_without_approving(self):
        self.request('投币并评论');self.follow('投币了吗？')
        self.assertIsNone(self.follow('好'));self.assertFalse(self.posts)
    def test_discussing_another_video_cannot_confirm_the_previous_target(self):
        self.request('评论一句');self.memory.add(OWNER,'user','BV1111111111 的评论怎么删？')
        self.assertIsNone(self.follow('确认'));self.assertFalse(self.posts)
    def test_cancelled_batch_not_revived_by_new_global_permission_or_share(self):
        self.request('投币并评论');self.follow('算了');self.automatic()
        self.s.social.engagement_decision=lambda s:{'coin':True,'comment':True,'text':DRAFT}
        self.s.mark_shared(OWNER,[self.source],88)
        self.follow('确认');self.assertFalse(self.posts)
        self.assertEqual(self.s.social.batches.current(OWNER)['state'],'cancelled')
    def test_unrelated_topic_blocks_ambiguous_good(self):
        self.request('评论一句');self.memory.add(OWNER,'user','今天好累')
        self.assertIsNone(self.follow('好'));self.assertFalse(self.posts)
    def test_comment_and_coin_proposals_can_coexist_on_local_page(self):
        first=self.s.social.propose(OWNER,BV,'comment',DRAFT,local=True)
        second=self.s.social.propose(OWNER,BV,'coin',local=True)
        self.assertEqual(first['batch_id'],second['batch_id'])
        self.s.social.confirm_local(OWNER,first['proposal_id'])
        self.s.social.confirm_local(OWNER,second['proposal_id'])
        self.assertEqual([k for k,_ in self.posts],['comment','coin'])
    def test_local_preview_stays_preview_in_automatic_mode(self):
        self.automatic();p=self.s.social.propose(OWNER,BV,'coin',local=True)
        self.assertFalse(self.posts);self.assertTrue(p['prepared'])
    def test_partial_failure_does_not_lose_other_actions_or_retry_uncertain_post(self):
        self.automatic();self.failure='coin';out=self.request('点赞投币并评论')
        self.assertEqual([k for k,_ in self.posts],['like','coin','comment'])
        self.assertEqual(self.states(out),{'like':'confirmed','coin':'uncertain','comment':'confirmed'})
        self.assertIn('不重复',out['message'])
        self.follow('确认');self.assertEqual(len(self.posts),3)
        with self.assertRaises(ValueError):self.s.social.confirm_local(OWNER,out['proposal_id'])
    def test_daily_quota_reached_does_not_stop_other_kind(self):
        self.automatic();now=self.s.clock()
        with self.s.db() as db:db.execute("INSERT INTO social_actions VALUES('earlier',?,'BV1111111111','coin',2,?,'confirmed','{}')",(OWNER,now))
        out=self.request('投币点赞评论')
        self.assertEqual([k for k,_ in self.posts],['like','comment'])
        self.assertEqual(self.states(out)['coin'],'failed');self.assertIn('上限',out['message'])
    def test_insufficient_balance_never_sends_coin_and_still_comments(self):
        self.automatic();self.balance=0;out=self.request('投币并评论')
        self.assertEqual([k for k,_ in self.posts],['comment'])
        self.assertIn('余额不足',out['message'])
    def test_draft_failure_does_not_block_coin(self):
        self.automatic();self.s.social.draft_comment=lambda s:(_ for _ in ()).throw(ValueError('no model'))
        out=self.request('投币并评论',comment='')
        self.assertEqual([k for k,_ in self.posts],['coin']);self.assertEqual(self.states(out)['comment'],'draft_failed')
    def test_confirmed_network_ack_stays_confirmed_if_accounting_fails(self):
        with patch('usage_ledger.Ledger.record',side_effect=RuntimeError('ledger busy')):
            r=self.s.social.execute(OWNER,BV,'coin',1,'',ident='receipt-test')
        self.assertTrue(r['executed']);self.assertEqual(self.s.social.action_state('receipt-test'),'confirmed')
    def test_different_task_ids_do_not_repeat_coin_or_comment_on_same_video(self):
        for kind in ['coin','comment']:
            one=self.s.social.execute(OWNER,BV,kind,1 if kind=='coin' else 0,DRAFT if kind=='comment' else '',ident='one'+kind)
            two=self.s.social.execute(OWNER,BV,kind,1 if kind=='coin' else 0,DRAFT if kind=='comment' else '',ident='two'+kind)
            self.assertTrue(one['executed']);self.assertTrue(two['duplicate'])
        self.assertEqual([k for k,_ in self.posts],['coin','comment'])
    def test_automatic_after_share_source_only_decision_is_deduplicated(self):
        self.automatic();calls=[]
        self.s.social.engagement_decision=lambda source:calls.append(source) or {'coin':True,'comment':True,'text':DRAFT}
        self.s.mark_shared(OWNER,[self.source],88);self.s.mark_shared(OWNER,[self.source],89)
        self.assertEqual(len(calls),1);self.assertEqual([k for k,_ in self.posts],['like','coin','comment'])
    def test_model_is_allowed_to_skip_autonomous_coin_and_comment(self):
        self.automatic();self.s.social.engagement_decision=lambda s:{'coin':False,'comment':False,'text':''}
        self.s.mark_shared(OWNER,[self.source],88)
        self.assertEqual([k for k,_ in self.posts],['like'])
    def test_public_only_model_input_has_no_private_conversation(self):
        self.memory.add(OWNER,'user','我的私聊秘密，不应该发到评论决策里')
        seen=[]
        with patch('deepseek_client.complete',side_effect=lambda messages,*a,**k:seen.append(messages) or ({'content':'{"coin":false,"comment":false,"text":""}'},{})):
            self.s.social.engagement_decision(self.source)
        payload=json.loads(seen[0][1]['content'])
        self.assertEqual(set(payload),{'title','summary','evidence'});self.assertNotIn('私聊秘密',str(seen))
    def test_status_of_automatic_share_without_proposal_uses_real_receipts(self):
        self.automatic();self.s.social.engagement_decision=lambda s:{'coin':True,'comment':True,'text':DRAFT}
        self.s.mark_shared(OWNER,[self.source],88)
        out=self.follow('投币了吗？评论了吗？')
        self.assertEqual(set(self.states(out).values()),{'confirmed'});self.assertIn(DRAFT,out['message'])
    def test_status_does_not_quote_new_draft_as_an_old_published_comment(self):
        self.s.social.execute(OWNER,BV,'comment',0,DRAFT,ident='old-comment')
        newer='这条字幕的说明还挺详细的。';self.request('评论一句',comment=newer)
        out=self.follow('评论了吗？')
        self.assertIn(DRAFT,out['message']);self.assertNotIn(newer,out['message'])
    def test_real_origin_required_even_when_global_mode_is_automatic(self):
        self.automatic()
        for peer,context in [(OTHER,self.context()),(OWNER,{'deliver':True,'mid':'forged'})]:
            with self.assertRaises(ValueError):self.s.social.propose(peer,BV,'coin',raw_text='投币',context=context)
        self.assertFalse(self.posts)
    def test_negative_clause_and_status_are_distinct_from_an_action(self):
        self.assertEqual(requested_kinds('不要投币，只点赞'),['like'])
        self.assertEqual(requested_kinds('只点赞不投币'),['like'])
        self.assertEqual(requested_kinds('不要点赞投币评论'),[])
        self.assertEqual(requested_kinds('不需要点赞投币评论'),[])
        self.assertEqual(requested_kinds('分别点赞投币评论'),['like','coin','comment'])
        self.assertEqual(requested_kinds('别给这条投币，评论一句就好'),['comment'])
        self.assertTrue(status_question('点赞了吗？'));self.assertFalse(status_question('帮我投币并评论'))
        self.assertTrue(status_question('评论还没发'));self.assertTrue(status_question('还没有投币吧'))
    def test_legacy_sibling_drafts_migrate_and_later_cancellation_is_honored(self):
        self.memory.add(OWNER,'user','投币并评论');source_id=self.s.social.latest(OWNER)[0];created=time.time()-.1
        with self.s.db() as db:
            for ident,kind,state in [('a'*32,'comment','replaced'),('b'*32,'coin','pending')]:
                db.execute('INSERT INTO proposals(id,peer,bvid,kind,text,amount,created,expires,state,source_local) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (ident,OWNER,BV,kind,DRAFT if kind=='comment' else '',1 if kind=='coin' else 0,created,created+600,state,source_id))
        self.memory.add(OWNER,'user','算了');self.s.social.batches.import_legacy();batch=self.s.social.batches.current(OWNER)
        self.assertEqual(batch['state'],'cancelled');self.assertEqual({r['state'] for r in self.s.social.batches.items(batch['id'])},{'cancelled'})
        self.automatic();self.s.social.auto_after_share(OWNER,self.source);self.assertFalse(self.posts)
    def test_whitespace_reference_does_not_hide_current_video_target(self):
        self.memory.add(OWNER,'assistant','就是这条。\n'+self.source['url'])
        result=self.s.social.requested_like(OWNER,BV,'给这条点赞',self.context(),reference='\n')
        self.assertTrue(result['executed']);self.assertEqual([k for k,_ in self.posts],['like'])
    def test_polite_natural_approval_uses_displayed_draft(self):
        self.request('评论一句');result=self.follow('请直接发吧')
        self.assertTrue(result['executed']);self.assertIn(DRAFT,result['message'])
    def test_capability_question_cannot_mutate_even_if_model_requests_tool(self):
        self.automatic()
        with self.assertRaises(ValueError):self.request('你能不能评论？')
        with self.assertRaises(ValueError):self.s.social.requested_like(OWNER,BV,'你能不能点赞？',self.context())
        self.assertFalse(self.posts)
    def test_unknown_video_status_does_not_reuse_another_video_receipt(self):
        self.s.social.execute(OWNER,BV,'like',0,'',ident='old-like')
        result=self.follow('BV1111111111 点赞了吗？')
        self.assertEqual(result['bvid'],'BV1111111111');self.assertFalse(result['items'])
        self.assertNotIn('赞已经点上',result['message'])

class RoutePolicyTests(unittest.TestCase):
    def setUp(self):
        self.fx=sharing_fixtures.BiliRouteTests();self.fx.setUp()
        for key in ['client','headers','service']:setattr(self,key,getattr(self.fx,key))
    def tearDown(self):self.fx.tearDown()
    def test_all_three_switches_can_be_saved_without_executing_an_action(self):
        body={'enabled':True,'auto_read_enabled':True,'proactive_enabled':True,'auto_like':True,'auto_comment':True,'auto_coin':True}
        r=self.client.post('/qq/bilibili/config',headers=self.headers,json=body);self.assertEqual(r.status_code,200)
        policy=self.client.get('/qq/bilibili/api/state',headers=self.headers).json()['sharing']['social_policy']
        self.assertTrue(policy['auto_like']);self.assertEqual(policy['comment_mode'],'automatic');self.assertEqual(policy['coin_mode'],'automatic')
        with self.service.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM social_actions').fetchone()[0],0)
    def test_page_explains_independent_policy_and_preview_behavior(self):
        r=self.client.get('/qq/bilibili');self.assertEqual(r.status_code,200)
        for text in ['bili-comment-auto','bili-coin-auto','预览','每日最多2枚']:self.assertIn(text,r.text)

if __name__=='__main__':unittest.main()
