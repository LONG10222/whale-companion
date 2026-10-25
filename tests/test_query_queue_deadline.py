from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json,sqlite3,threading,time,unittest

from tests import test_qq_roles_naming as role_fixture
from qq_creative import CreativeJobs
from qq_research import PublicResearch,EMPTY,save


class QueuedQueryDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.fixture=role_fixture.RoleTests();self.fixture.setUp()
        self.service=self.fixture.service;self.gate=threading.Event();started=threading.Event()
        def blocked():started.set();self.gate.wait(20)
        self.service.pool.submit(blocked);self.assertTrue(started.wait(2))
        self.clock=time.time();self.service.deadline_clock=lambda:self.clock
        self.research_calls=[];self.sent=[]
        def research(*args,**kwargs):
            self.research_calls.append(args)
            return {'text':'可验证的示例结果','attachments':[],'metadata':{}}
        self.service.research=SimpleNamespace(run=research)
        self.fixture.bridge.call=lambda action,params,**kw:self.sent.append(params) or {'message_id':len(self.sent)}

    def tearDown(self):
        self.gate.set();self.fixture.tearDown()

    def row(self,jid):
        with self.service.db() as db:return dict(db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone())

    def request(self,deliver=True):
        fixture=self.fixture;context=None
        if deliver:
            fixture.chat.memory.claim(fixture.bot,'deadline-origin',fixture.other)
            context={'deliver':True,'bot':fixture.bot,'mid':'deadline-origin','epoch':'test'}
        result=self.service.request(fixture.other,'lookup_web',{'query':'官方示例公告'},'帮我查一下官方示例公告',context)
        if deliver:
            with fixture.chat.memory.db() as db:db.execute("UPDATE events SET state='sent' WHERE bot=? AND mid='deadline-origin'",(fixture.bot,))
        return result['job_id']

    def until(self,predicate):
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            if predicate():return
            time.sleep(.02)
        self.fail('Expected task transition did not complete')

    def release(self):
        self.gate.set();self.service.pool.submit(lambda:None).result(timeout=10)

    def test_watchdog_finishes_queued_query_before_work_executor_is_available(self):
        jid=self.request();accepted=self.row(jid)['at']
        with self.service.db() as db:db.execute('UPDATE jobs SET updated=? WHERE id=?',(accepted+290,jid))
        self.clock=accepted+301
        self.until(lambda:self.row(jid)['delivery']=='sent')
        self.assertFalse(self.gate.is_set());self.assertFalse(self.research_calls)
        result=json.loads(self.row(jid)['result'])
        self.assertEqual(result['text'],EMPTY);self.assertTrue(result['metadata']['queued_timeout'])
        self.release();self.assertFalse(self.research_calls);self.assertEqual(len(self.sent),1)

    def test_competing_expiry_checks_and_late_worker_do_not_duplicate_execution_or_notification(self):
        jid=self.request();self.clock=self.row(jid)['at']+301
        with ThreadPoolExecutor(max_workers=2) as checks:
            futures=[checks.submit(self.service.expire_queued_queries) for _ in range(2)]
            for future in futures:future.result(timeout=5)
        self.until(lambda:self.row(jid)['delivery']=='sent')
        self.assertEqual(self.service.expire_queued_queries(),[])
        self.release();self.assertFalse(self.research_calls);self.assertEqual(len(self.sent),1)
        with self.service.ledger.db() as db:
            count=db.execute("SELECT count(*) FROM events WHERE kind='task_finished' AND job_id=?",(jid,)).fetchone()[0]
        self.assertEqual(count,1)

    def test_uncertain_timeout_notification_never_restarts_research_or_retries_send(self):
        def uncertain(*args,**kwargs):self.sent.append('attempt');raise RuntimeError('synthetic unknown receipt')
        self.fixture.bridge.call=uncertain
        jid=self.request();self.clock=self.row(jid)['at']+301
        self.service.expire_queued_queries();self.until(lambda:self.row(jid)['delivery']=='uncertain')
        self.service.expire_queued_queries();self.release()
        self.assertFalse(self.service.deliver(jid)['sent'])
        self.assertFalse(self.research_calls);self.assertEqual(self.sent,['attempt'])

    def test_plus_query_keeps_exemption_while_it_is_waiting(self):
        self.service.contacts.set_plus(self.fixture.other,True)
        jid=self.request(deliver=False);self.clock=self.row(jid)['at']+901
        self.assertEqual(self.service.expire_queued_queries(),[])
        self.assertEqual(self.row(jid)['state'],'accepted')
        self.release();self.assertEqual(len(self.research_calls),1)
        self.assertEqual(self.row(jid)['state'],'completed')

    def test_cancelled_queued_query_does_not_send_a_timeout_response_or_start_model(self):
        jid=self.request();self.service.cancel(self.fixture.other)
        self.clock=self.row(jid)['at']+301;self.service.expire_queued_queries()
        self.release()
        self.assertEqual(self.row(jid)['state'],'cancelled')
        self.assertEqual(self.row(jid)['delivery'],'suppressed')
        self.assertFalse(self.research_calls);self.assertFalse(self.sent)

    def test_real_short_write_lock_cannot_drop_an_expired_query_claim(self):
        jid=self.request(deliver=False);self.clock=self.row(jid)['at']+301
        # Isolate the work executor from the monitor so it cannot hide a lost claim.
        self.service.stop.set();self.service.deadline_thread.join();self.service.stop.clear()
        with self.service.db() as other_connection:
            other_connection.execute('BEGIN IMMEDIATE')
            # This is the old pre-claim scan's real SQLite contention failure.
            with self.assertRaises(sqlite3.OperationalError):self.service.expire_queued_queries(jid)
            self.gate.set();time.sleep(.25)
            self.assertFalse(self.research_calls)
        self.release()
        row=self.row(jid)
        self.assertEqual(row['state'],'completed')
        self.assertTrue(json.loads(row['result'])['metadata']['queued_timeout'])
        self.assertFalse(self.research_calls)

    def test_real_short_write_lock_does_not_drop_a_normal_query(self):
        jid=self.request(deliver=False)
        with self.service.db() as other_connection:
            other_connection.execute('BEGIN IMMEDIATE')
            self.gate.set();time.sleep(.25)
            self.assertFalse(self.research_calls)
        self.release()
        self.assertEqual(self.row(jid)['state'],'completed')
        self.assertEqual(len(self.research_calls),1)

    def test_unexpected_claim_failure_is_recorded_and_delivered_once(self):
        jid=self.request()
        with patch.object(self.service,'_claim_work',side_effect=RuntimeError('synthetic claim failure')):
            self.release()
        self.until(lambda:self.row(jid)['delivery']=='sent')
        self.assertEqual(self.row(jid)['state'],'failed')
        self.assertFalse(self.research_calls);self.assertEqual(len(self.sent),1)

    def test_running_query_keeps_its_partial_sources_at_the_existing_deadline(self):
        root=self.fixture.root;clock=[time.time()+299];processes=[]
        class Process:
            def __init__(self,args,*unused):
                self.closed=False;processes.append(self)
                save(Path(args[-1]).parent/'progress.json',{'query':'官方示例公告','sources':[{
                    'title':'官方示例公告','url':'https://example.com/news','text':'这份官方示例公告包含可确认的测试说明，其他没有读到的内容仍需核实。'}]})
            def poll(self):return None
            def close(self):self.closed=True
        self.service.research=PublicResearch(root,clock=lambda:clock[0],sleeper=lambda _:clock.__setitem__(0,clock[0]+1),process_factory=Process)
        with patch('qq_research.WORKSPACE',root/'isolated-query'):
            jid=self.request(deliver=False);self.release()
        result=json.loads(self.row(jid)['result'])
        self.assertTrue(processes[0].closed)
        self.assertTrue(result['metadata']['timed_out']);self.assertTrue(result['metadata']['partial'])
        self.assertIn('官方示例公告',result['text'])

    def test_close_joins_watchdog_and_restart_does_not_reset_or_resume_old_request(self):
        jid=self.request(deliver=False);accepted=self.row(jid)['at'];old=self.service
        old.close();self.assertFalse(old.deadline_thread.is_alive())
        self.gate.set();old.pool.shutdown(wait=True)
        with patch.object(old,'db',side_effect=AssertionError('Closed monitor touched storage')):
            self.assertEqual(old.expire_queued_queries(),[])
        replacement=CreativeJobs(self.fixture.root,self.fixture.bridge,resources=old.resources,workbench=old.workbench)
        self.fixture.service=replacement;self.service=replacement
        replacement.deadline_clock=lambda:accepted+1000
        self.assertEqual(replacement.expire_queued_queries(),[])
        self.assertEqual(self.row(jid)['state'],'interrupted');self.assertEqual(self.row(jid)['at'],accepted)
        self.assertFalse(self.research_calls)
