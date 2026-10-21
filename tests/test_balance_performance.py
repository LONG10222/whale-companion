from pathlib import Path
from unittest.mock import patch
import tempfile,unittest,json
import numpy as np
from qq_chat import QQConversation
from qq_bridge import QQBridge
from balance_watch import BalanceWatch
from singing_performance import prepare_performance,steady_f0
from score_adapter import demo_request

class BalanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.chat=QQConversation(self.root,client=lambda *_:({'content':'x'},{}));self.bridge=QQBridge(self.root,self.chat)
        self.bridge.cfg.update(locked_bot_id='12345678',test_contact={'name':'owner','user_id':'87654321'});self.bridge.configure(True,'12345678',['87654321'],True);self.bridge.connected=True;self.bridge.login_id='12345678';self.now=1000;self.amount='6.26';self.sent=[]
        self.bridge.call=lambda *a,**kw:self.sent.append((a,kw)) or {'message_id':42}
        self.watch=BalanceWatch(self.root,self.bridge,fetch=lambda:{'is_available':float(self.amount)>0,'balance_infos':[{'currency':'CNY','total_balance':self.amount}],'key_fingerprint':'private-fingerprint'},clock=lambda:self.now)
    def tearDown(self):self.watch.close();self.bridge.pool.shutdown(wait=True);self.tmp.cleanup()
    def check(self,amount):self.now+=31;self.amount=amount;return self.watch.refresh(notify=True)
    def test_threshold_crossing_once_recovery_rearms_and_exhaustion_escalates(self):
        self.check('6.26');self.assertFalse(self.sent);self.check('.8');self.check('.7');self.assertEqual(len(self.sent),1)
        self.check('0');self.assertEqual(len(self.sent),2);self.check('2');self.check('.9');self.assertEqual(len(self.sent),3)
        self.assertTrue(all(a[0][1]['user_id']==87654321 for a in self.sent))
    def test_unknown_receipt_not_retried_while_still_low(self):
        def unknown(*a,**kw):self.sent.append(a);raise RuntimeError('no receipt')
        self.bridge.call=unknown;self.check('.5');self.check('.4');self.assertEqual(len(self.sent),1)
        with self.watch.db() as db:self.assertEqual(db.execute('SELECT state FROM alerts').fetchone()[0],'uncertain')
    def test_failed_query_preserves_old_balance_and_does_not_invent_zero(self):
        self.check('6.26');self.watch.fetch=lambda:(_ for _ in ()).throw(ValueError('timeout'));self.now+=31;r=self.watch.refresh(notify=True)
        self.assertEqual(r['balance']['balance_infos'][0]['total_balance'],'6.26');self.assertEqual(r['balance']['error'],'timeout');self.assertFalse(self.sent)
    def test_wrong_scope_paused_or_disabled_never_sends(self):
        self.bridge.configure(False,'12345678',['87654321'],True);self.check('.4');self.assertFalse(self.sent)
        self.bridge.configure(True,'12345678',['87654321'],True);self.watch.configure(False,'1',15);self.check('.3');self.assertFalse(self.sent)
        self.watch.configure(True,'1',15);self.bridge.cfg['allowed_private_ids'].append('99999999')
        with self.assertRaises(ValueError):self.check('.2')
        self.assertFalse(self.sent)
    def test_credentials_not_in_public_state_and_invalid_config_rejected(self):
        s=self.check('6.26');self.assertNotIn('private-fingerprint',json.dumps(s))
        for amount in ['NaN','-1','10001']:
            with self.assertRaises(ValueError):self.watch.configure(True,amount,15)
    def test_balance_chat_query_uses_no_language_model(self):
        self.chat.client=lambda *_:self.fail('balance should not call LLM')
        self.assertIn('6.26',self.chat.learning_command('87654321','查一下余额'))

class PerformanceTests(unittest.TestCase):
    def test_score_tempo_and_gap_controls_do_not_change_notes_or_repeat_scaling(self):
        payload=demo_request();payload.update(tempo=.9,rhythm='connected',phrase_gap=.2)
        out=prepare_performance(payload);self.assertEqual(out['phrases'][0]['notes'],payload['phrases'][0]['notes']);self.assertAlmostEqual(out['phrases'][0]['durations'][0],.65/.9,places=4)
        self.assertEqual(out['phrases'][0]['pre_gap'],.12);self.assertEqual(out['phrases'][0]['post_gap'],.2);self.assertEqual(out,prepare_performance(out));self.assertEqual(payload['phrases'][0]['durations'][0],.65)
    def test_pitch_stabilizer_preserves_unvoiced_frames_and_reduces_note_flutter(self):
        values=np.r_[np.zeros(18),440*2**((80*np.sin(np.arange(200)*.5))/1200),np.zeros(30)]
        phrases=[{'notes':['A4'],'durations':[2.0]}];new,metrics=steady_f0(values,2.48,phrases)
        self.assertTrue(np.array_equal(new==0,values==0));self.assertEqual(len(new),len(values));self.assertTrue(metrics['applied'])
        before=np.std(1200*np.log2(values[40:180]/440));after=np.std(1200*np.log2(new[40:180]/440));self.assertLess(after,before*.4)
    def test_two_distinct_notes_are_not_flattened_into_one_pitch(self):
        values=np.r_[np.zeros(18),np.full(100,440.),np.full(100,493.883),np.zeros(30)]
        new,_=steady_f0(values,2.48,[{'notes':['A4','B4'],'durations':[1,1]}]);self.assertGreater(float(np.mean(new[135:200])),float(np.mean(new[35:100]))*1.1)
