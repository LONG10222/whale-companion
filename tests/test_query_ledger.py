from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import closing
import json,os,sqlite3,sys,tempfile,threading,time,unittest
from fastapi import FastAPI,Header,HTTPException
from fastapi.testclient import TestClient
from tests.test_qq_roles_naming import RoleTests
from qq_identity import Contacts
from qq_research import PublicResearch,fallback,EMPTY,save
from usage_ledger import Ledger
from owned_query_process import QueryProcess

class QueryTests(RoleTests):
    def setUp(self):
        super().setUp();self.requests=[]
        def research(jid,peer,args,*a,**kw):self.requests.append((peer,args));return {'text':EMPTY,'attachments':[],'metadata':{'summary_mode':'no_reliable_result'}}
        self.service.research=SimpleNamespace(run=research)
    def query(self,peer=None):return self.service.request(peer or self.other,'lookup_web',{'query':'鸣潮官方公告'},'帮我查一下鸣潮官方公告')
    def test_rolling_window_two_queries_across_bot_changes_and_restart(self):
        self.query();self.settle();self.query();self.settle()
        with self.assertRaisesRegex(ValueError,'两次'):self.query()
        with self.service.db() as db:db.execute('UPDATE jobs SET bot=?',('old-bot',))
        with self.assertRaisesRegex(ValueError,'两次'):self.query()
        with self.service.db() as db:db.execute('UPDATE jobs SET at=at-901 WHERE id=(SELECT id FROM jobs ORDER BY at LIMIT 1)')
        self.query();self.settle();self.assertEqual(len(self.requests),3)
    def test_plus_is_only_manually_granted_and_exempts_query_limits_not_computer_access(self):
        self.service.contacts.observe(self.other,'Plus long 管理员')
        self.assertEqual(self.service.contacts.role(self.other),'user')
        self.service.contacts.set_plus(self.other,True)
        self.assertEqual(Contacts(self.root).role(self.other),'plus')
        for _ in range(4):self.query();self.settle()
        self.assertTrue(all(args['_query_timeout_seconds']==0 and args['model']=='gpt-6-luna' for _,args in self.requests))
        with self.assertRaises(ValueError):self.service.request(self.other,'delegate_codex',{'task':'写入本机文件','model':'gpt-6-luna','effort':'low','mode':'new'},'让Codex写入本机文件')
        self.service.contacts.set_plus(self.other,False)
        with self.assertRaisesRegex(ValueError,'两次'):self.query()
    def test_duplicate_real_origin_counts_once_and_rejected_queue_consumes_no_query(self):
        self.chat.memory.claim(self.bot,'real',self.other)
        context={'deliver':True,'bot':self.bot,'mid':'real','epoch':'test'}
        self.bridge.call=lambda *a,**kw:{'message_id':10}
        a=self.service.request(self.other,'lookup_web',{'query':'鸣潮官方公告'},'帮我查一下鸣潮官方公告',context)
        self.settle();b=self.service.request(self.other,'lookup_web',{'query':'鸣潮官方公告'},'帮我查一下鸣潮官方公告',context)
        self.assertEqual(a['job_id'],b['job_id'])
        with self.service.db() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM jobs WHERE kind='lookup_web'").fetchone()[0],1)
        self.assertEqual(len(self.requests),1)
    def test_ordinary_search_is_dispatched_without_extra_deepseek_model_call(self):
        self.chat.client=lambda *_:self.fail('Should route ordinary public search to Luna job')
        self.chat.memory.claim(self.bot,'q',self.other)
        result=self.chat.prepare(self.other,'帮我查一下鸣潮公告',task_context={'deliver':True,'bot':self.bot,'mid':'q','epoch':'test'})
        self.assertTrue(result['tools'][0]['result']['accepted']);self.assertEqual(result['tools'][0]['tool'],'lookup_web');self.settle()
    def test_plus_and_ledger_routes_require_local_owner_auth(self):
        from creative_routes import register_creative_routes
        app=FastAPI()
        def auth(authorization:str=Header(default='')):
            if authorization!='Bearer owner':raise HTTPException(401)
        with patch('creative_routes.CreativeJobs',return_value=self.service):register_creative_routes(app,root=self.root,authorize=auth,bridge=self.bridge)
        c=TestClient(app);url='/qq/creative/contacts/'+self.other+'/plus'
        self.assertEqual(c.post(url,json={'enabled':True}).status_code,401)
        self.assertEqual(c.get('/qq/creative/ledger').status_code,401)
        h={'Authorization':'Bearer owner'}
        self.assertEqual(c.post(url,json={'enabled':'true'},headers=h).status_code,422)
        self.assertEqual(c.post(url,json={'enabled':True},headers=h).json()['role'],'plus')
        self.assertEqual(c.get('/qq/creative/ledger',headers=h).json()['counts']['plus_changed'],1)
        self.assertEqual(c.post('/qq/creative/ledger/money',json={'direction':'income','amount':'10','currency':'CNY','note':'手动确认充值'},headers=h).status_code,200)

class ResearchBoundaryTests(unittest.TestCase):
    def test_deadline_kills_worker_and_returns_partial_sources_or_natural_empty_reply(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);clock=[298.];owned=[]
            class Proc:
                def __init__(self,args,*a):
                    self.closed=False;owned.append(self);folder=Path(args[-1]).parent
                    save(folder/'progress.json',{'query':'官方更新','sources':[{'title':'官方公告','url':'https://example.com/news','text':'本次官方更新增加了新的剧情任务与活动关卡，其他内容仍需要核对。'}]})
                def poll(self):return None
                def close(self):self.closed=True
            worker=PublicResearch(root,clock=lambda:clock[0],sleeper=lambda _:clock.__setitem__(0,clock[0]+1),process_factory=Proc)
            with patch('qq_research.WORKSPACE',root):result=worker.run('a'*32,'76543210',{'query':'官方更新','_query_timeout_seconds':300},0,'user')
            self.assertTrue(owned[0].closed);self.assertTrue(result['metadata']['timed_out']);self.assertIn('官方公告',result['text']);self.assertIn('还没核实',result['text'])
            self.assertEqual(fallback({'search_results':[{'snippet':'只有摘要，未读到原文'}]},True),EMPTY)
    def test_cancellation_exits_owned_worker_without_result_delivery(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);owned=[]
            class Proc:
                def __init__(self,*a):self.closed=False;owned.append(self)
                def poll(self):return None
                def close(self):self.closed=True
            worker=PublicResearch(root,process_factory=Proc)
            with patch('qq_research.WORKSPACE',root),self.assertRaisesRegex(RuntimeError,'停止'):worker.run('b'*32,'76543210',{'query':'公告','_query_timeout_seconds':0},time.time(),'plus',cancelled=lambda:True)
            self.assertTrue(owned[0].closed)
    def test_owned_process_tree_closes_real_child_processes(self):
        import psutil
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);pidfile=root/'child.json'
            code="import subprocess,sys,time,json;from pathlib import Path;p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);Path(sys.argv[1]).write_text(json.dumps(p.pid));time.sleep(30)"
            with (root/'worker.log').open('w') as log:
                p=QueryProcess([sys.executable,'-c',code,str(pidfile)],str(root),log)
                try:
                    end=time.monotonic()+5
                    while not pidfile.exists() and time.monotonic()<end:time.sleep(.05)
                    self.assertTrue(pidfile.exists());child=json.loads(pidfile.read_text());self.assertTrue(psutil.pid_exists(child))
                finally:p.close()
                end=time.monotonic()+3
                while psutil.pid_exists(child) and time.monotonic()<end:time.sleep(.05)
                self.assertFalse(psutil.pid_exists(child));self.assertIsNotNone(p.poll())

class LedgerTests(unittest.TestCase):
    def test_idempotent_token_updates_persist_and_export_without_secrets(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);ledger=Ledger(root)
            for total in [10,20]:ledger.record('codex_usage',event_key='one-turn',model='gpt-6-luna',state='observed',input_tokens=5,output_tokens=total-5,total_tokens=total,details={'api_key':'sk-PRIVATE','note':'Bearer secretvalue'})
            state=Ledger(root).state();self.assertEqual(state['usage'][0]['total_tokens'],20);self.assertEqual(state['counts']['codex_usage'],1)
            text=next(ledger.folder.glob('*.jsonl')).read_text(encoding='utf-8');self.assertNotIn('sk-PRIVATE',text);self.assertNotIn('secretvalue',text)
    def test_balance_deltas_are_not_fabricated_income_or_expense(self):
        with tempfile.TemporaryDirectory() as td:
            ledger=Ledger(Path(td))
            for amount in ['6','5','10']:ledger.observe_balance('account',[{'currency':'CNY','total_balance':amount}])
            state=ledger.state();self.assertEqual(state['confirmed_money'],[]);self.assertEqual(state['counts']['balance_change'],2)
            ledger.manual_money('income','5','CNY','用户确认充值')
            self.assertEqual(ledger.state()['confirmed_money'],[{'currency':'CNY','income':'5','expense':'0'}]);self.assertFalse(state['unknown_costs_are_zero'])
