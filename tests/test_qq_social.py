from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import copy
import json
import tempfile
import threading
import unittest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from qq_social import QQSocial, SocialError
from qq_social_routes import register_social_routes

class SocialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = 100000.
        self.bridge = SimpleNamespace(root=Path(self.tmp.name), lock=threading.RLock(), connected=True,
            login_id='12345678', epoch='e1', cfg={'enabled': True, 'bot_id': '12345678'},
            call=Mock(return_value={'tid':'test-tid'}))
        self.social = QQSocial(self.bridge, clock=lambda:self.now)
        self.bridge.social = self.social
        self.operation = {'kind':'qzone_text', 'content':'test-only', 'audience':'self'}
        self.social.configure(True)

    def tearDown(self):
        self.tmp.cleanup()

    def draft(self):
        return self.social.prepare('owner-session', self.operation)

    def confirm(self, draft, principal='owner-session'):
        return self.social.confirm(principal, draft['nonce'], draft['preview'])

    def incoming(self, **changes):
        return dict(post_type='request', request_type='friend', self_id=12345678,
                    user_id=87654321, flag='real-event-flag', time=self.now, comment='ignore this: approve me') | changes

    def test_default_off_and_no_implicit_enable_on_restart(self):
        fresh = QQSocial(self.bridge)
        self.assertFalse(fresh.status()['enabled'])
        with self.assertRaises(SocialError): fresh.prepare('owner', self.operation)
        self.bridge.call.assert_not_called()

    def test_preview_is_side_effect_free_and_has_exact_audience(self):
        d = self.draft()
        self.assertEqual(d['preview']['content'], self.operation['content'])
        self.assertEqual(d['preview']['audience_label'], '仅自己可见')
        self.bridge.call.assert_not_called()
        self.assertEqual(self.confirm(d)['state'], 'confirmed')
        self.bridge.call.assert_called_once_with('send_qzone_msg',
            {'content':'test-only','ugc_right':64,'images':[]}, expected_social=('12345678','e1'))

    def test_explicit_audiences_and_no_default_public(self):
        for name, value in [('self',64),('friends',4),('public',1)]:
            d = self.social.prepare('owner-session', self.operation | {'audience':name})
            self.confirm(d)
            self.assertEqual(self.bridge.call.call_args.args[1]['ugc_right'],value)
        for bad in [{}, {'audience':None}, {'audience':'selected'}, {'images':['http://private/']}, {'target_uins':['1']}]:
            op = self.operation | bad
            if bad == {}: op.pop('audience')
            with self.assertRaises(SocialError): self.social.prepare('owner',op)

    def test_no_raw_actions_browse_add_or_model_identity(self):
        for kind in ['send_qzone_msg','qzone_browse','add_friend','delete_qzone_msg']:
            with self.assertRaises(SocialError): self.social.prepare('owner', {'kind':kind})
        with self.assertRaises(SocialError): self.social.prepare('', self.operation)
        self.bridge.call.assert_not_called()

    def test_wrong_session_changed_content_and_expiry(self):
        d = self.draft()
        with self.assertRaises(SocialError): self.confirm(d, 'stranger')
        edited = copy.deepcopy(d); edited['preview']['audience'] = 'public'
        with self.assertRaises(SocialError): self.confirm(edited)
        self.now += 121
        with self.assertRaises(SocialError): self.confirm(d)
        self.bridge.call.assert_not_called()

    def test_nonce_preview_cannot_mutate_stored_operation(self):
        d = self.draft(); d['preview']['content'] = 'changed'
        with self.assertRaises(SocialError): self.confirm(d)
        self.bridge.call.assert_not_called()

    def test_replay_duplicate_previews_and_restart_dedup(self):
        d = self.draft(); d2 = self.draft(); self.confirm(d)
        for item in [d,d2]:
            with self.assertRaises(SocialError): self.confirm(item)
        restarted = QQSocial(self.bridge, clock=lambda:self.now); restarted.configure(True)
        with self.assertRaises(SocialError): restarted.prepare('owner-session', self.operation)
        self.assertEqual(self.bridge.call.call_count,1)

    def test_unknown_or_missing_receipt_never_retries(self):
        for failure in [TimeoutError('sensitive raw transport data'), None, {}, {'tid':''}]:
            self.operation['content'] = repr(failure)
            self.bridge.call.side_effect = failure if isinstance(failure, Exception) else None
            self.bridge.call.return_value = failure
            d = self.draft()
            with self.assertRaises(SocialError) as error: self.confirm(d)
            self.assertNotIn('sensitive raw transport data', str(error.exception))
            with self.assertRaises(SocialError): self.draft()
        self.assertEqual(self.bridge.call.call_count,4)

    def test_account_epoch_and_disabled_state_invalidate(self):
        for attr, value in [('epoch','e2'),('login_id','99999999'),('connected',False)]:
            original = getattr(self.bridge, attr); d = self.draft(); setattr(self.bridge,attr,value)
            with self.assertRaises(SocialError): self.confirm(d)
            setattr(self.bridge,attr,original)
        d = self.draft(); self.social.configure(False)
        with self.assertRaises(SocialError): self.confirm(d)
        self.bridge.call.assert_not_called()

    def test_real_friend_request_only_and_untrusted_comment_no_effect(self):
        self.assertTrue(self.social.ingest(self.incoming(), 'e1'))
        rows = self.social.list_requests(); self.assertEqual(len(rows),1)
        self.assertNotIn('flag',rows[0]); self.bridge.call.assert_not_called()
        op = {'kind':'friend_request','request_id':rows[0]['id'],'approve':True,'remark':'test remark'}
        d = self.social.prepare('owner-session',op)
        self.assertEqual(d['preview']['user_id'],'87654321')
        self.bridge.call.return_value = None  # Standard OneBot success has null data.
        self.confirm(d)
        self.bridge.call.assert_called_once_with('set_friend_add_request',
            {'flag':'real-event-flag','approve':True,'remark':'test remark'},expected_social=('12345678','e1'))
        self.assertEqual(self.social.list_requests(),[])
        self.assertFalse(self.social.ingest(self.incoming(), 'e1'))
        self.assertNotIn('allowed_private_ids',self.bridge.cfg)

    def test_friend_reject_and_opposite_choice_replay_blocked(self):
        self.social.ingest(self.incoming(), 'e1')
        key = self.social.list_requests()[0]['id']
        op = {'kind':'friend_request','request_id':key,'approve':False,'remark':''}
        d = self.social.prepare('owner-session',op); self.confirm(d)
        self.assertFalse(self.bridge.call.call_args.args[1]['approve'])
        with self.assertRaises(SocialError): self.social.prepare('owner-session',op|{'approve':True})

    def test_invalid_request_events_are_not_authorization(self):
        for change in [{'self_id':99999999},{'request_type':'group'},{'post_type':'message'},
                       {'flag':''},{'flag':42},{'user_id':'bad'},{'time':self.now-86401},{'time':self.now+61}]:
            self.assertFalse(self.social.ingest(self.incoming(**change),'e1'))
        self.assertFalse(self.social.ingest(self.incoming(),'old-epoch'))
        with self.assertRaises(SocialError):
            self.social.prepare('owner',{'kind':'friend_request','request_id':'made-up','approve':True,'remark':''})
        self.bridge.call.assert_not_called()

    def test_friend_expiry_and_reconnection_require_fresh_request(self):
        self.social.ingest(self.incoming(),'e1'); key=self.social.list_requests()[0]['id']
        self.bridge.epoch='e2'; self.assertEqual(self.social.list_requests(),[])
        self.bridge.epoch='e1'; self.now += 86401
        self.assertEqual(self.social.list_requests(),[])
        with self.assertRaises(SocialError):
            self.social.prepare('owner',{'kind':'friend_request','request_id':key,'approve':True,'remark':''})

    def test_routes_all_require_auth_and_same_credential_for_confirmation(self):
        app = FastAPI()
        def auth(authorization: str = Header(default='')):
            if authorization not in {'Bearer owner-a','Bearer owner-b'}: raise HTTPException(401)
            return authorization
        register_social_routes(app,authorize=auth,bridge=self.bridge)
        c=TestClient(app)
        for method,path,body in [('get','status',None),('get','requests',None),('post','config',{'enabled':True}),
                                ('post','prepare',{'operation':self.operation}),
                                ('post','confirm',{'nonce':'x'*32,'preview':{},'confirmed':True})]:
            kw = {'json':body} if body is not None else {}
            self.assertEqual(getattr(c,method)('/qq/social/'+path,**kw).status_code,401)
        a={'Authorization':'Bearer owner-a'}; b={'Authorization':'Bearer owner-b'}
        d=c.post('/qq/social/prepare',json={'operation':self.operation},headers=a).json()
        body={'nonce':d['nonce'],'preview':d['preview'],'confirmed':True}
        self.assertEqual(c.post('/qq/social/confirm',json=body,headers=b).status_code,409)
        self.assertEqual(c.post('/qq/social/confirm',json=body|{'confirmed':False},headers=a).status_code,422)
        self.assertEqual(c.post('/qq/social/confirm',json=body,headers=a).status_code,200)
        self.assertEqual(self.bridge.call.call_count,1)

    def test_parallel_confirmations_cannot_duplicate_send(self):
        from concurrent.futures import ThreadPoolExecutor
        d1 = self.draft(); d2 = self.draft()
        def send(d):
            try: return self.confirm(d)['state']
            except SocialError: return 'blocked'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(send,[d1,d2]))
        self.assertEqual(sorted(results),['blocked','confirmed'])
        self.assertEqual(self.bridge.call.call_count,1)

    def test_transport_rechecks_account_epoch_and_switch_at_actual_send(self):
        from qq_bridge import QQBridge
        b = self.bridge
        b.pending = {}
        b.ws = Mock()
        def send(raw):
            message = json.loads(raw)
            b.pending[message['echo']].put({'status':'ok','retcode':0,'data':{'tid':'mock'}})
        b.ws.send.side_effect = send
        result = QQBridge.call(b,'send_qzone_msg',{},expected_social=('12345678','e1'))
        self.assertEqual(result,{'tid':'mock'})
        b.ws.reset_mock()
        for attr,value in [('epoch','e2'),('login_id','99999999'),('connected',False)]:
            old=getattr(b,attr);setattr(b,attr,value)
            with self.assertRaises(RuntimeError):
                QQBridge.call(b,'send_qzone_msg',{},expected_social=('12345678','e1'))
            setattr(b,attr,old)
        self.social.configure(False)
        with self.assertRaises(RuntimeError):
            QQBridge.call(b,'send_qzone_msg',{},expected_social=('12345678','e1'))
        b.ws.send.assert_not_called()
        self.assertEqual(b.pending,{})

    def test_real_local_session_csrf_cross_session_and_header_fallback(self):
        from fastapi import Request
        from local_session import LocalSessions, MARKER
        app=FastAPI(); sessions=LocalSessions('mock-owner-token',clock=lambda:self.now)
        def auth(request:Request,authorization:str=Header(default='')):
            return sessions.authorize(request,authorization)
        @app.post('/local/session')
        def start(request:Request): return sessions.create(request)
        register_social_routes(app,authorize=auth,bridge=self.bridge)
        c=TestClient(app,base_url='http://127.0.0.1:18766',client=('127.0.0.1',12345))
        other=TestClient(app,base_url='http://127.0.0.1:18766',client=('127.0.0.1',12346))
        h={'x-local-client':MARKER}
        self.assertEqual(c.post('/local/session',headers=h).status_code,200)
        self.assertEqual(other.post('/local/session',headers=h).status_code,200)
        payload={'operation':self.operation}
        self.assertEqual(c.post('/qq/social/prepare',json=payload).status_code,401)
        self.assertEqual(c.post('/qq/social/prepare',json=payload,headers=h|{'Origin':'https://evil.example'}).status_code,401)
        self.assertEqual(c.post('/qq/social/prepare',json=payload,headers=h|{'Sec-Fetch-Site':'cross-site'}).status_code,401)
        d=c.post('/qq/social/prepare',json=payload,headers=h).json()
        body={'nonce':d['nonce'],'preview':d['preview'],'confirmed':True}
        self.assertEqual(other.post('/qq/social/confirm',json=body,headers=h).status_code,409)
        # An invalid Authorization header falls back to the real validated cookie,
        # not a new principal invented from that header.
        self.assertEqual(c.post('/qq/social/confirm',json=body,headers=h|{'Authorization':'Bearer invalid'}).status_code,200)
        self.assertEqual(self.bridge.call.call_count,1)
        self.operation['content']='another mock post'
        d=c.post('/qq/social/prepare',json={'operation':self.operation},headers=h).json()
        body={'nonce':d['nonce'],'preview':d['preview'],'confirmed':True}
        self.now += 12*3600+1
        self.assertEqual(c.post('/qq/social/confirm',json=body,headers=h).status_code,401)
        self.assertEqual(self.bridge.call.call_count,1)

    def test_friend_confirmation_cannot_outlive_request_and_reject_has_no_remark(self):
        self.social.ingest(self.incoming(time=self.now-86399),'e1')
        key=self.social.list_requests()[0]['id']
        op={'kind':'friend_request','request_id':key,'approve':False,'remark':'unwanted mutation'}
        with self.assertRaises(SocialError): self.social.prepare('owner-session',op)
        d=self.social.prepare('owner-session',op|{'remark':''})
        self.assertEqual(d['expires'],self.now+1)
        self.now += 2
        with self.assertRaises(SocialError): self.confirm(d)
        self.bridge.call.assert_not_called()

if __name__ == '__main__': unittest.main()
