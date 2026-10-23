from pathlib import Path
from unittest.mock import patch
import json,tempfile,unittest
from codex_workbench import CodexWorkbench
from codex_naming import NamingManager
from tests import test_qq_roles_naming as role_fixtures


class ProjectScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.calls=[];self.progress=[]
        self.worker=CodexWorkbench(self.root);self.worker.workspace=self.root/'tasks';self.worker.workspace.mkdir()
    def tearDown(self):self.tmp.cleanup()
    def rpc(self,read_project='bound-project',start_project='bound-project',name=''):
        calls=self.calls
        class RPC:
            def __init__(self,*_):self.name=name
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def request(self,method,params):
                calls.append((method,params))
                if method=='thread/read':return {'thread':{'id':'owned-thread','projectId':read_project,'name':self.name}}
                if method in {'thread/start','thread/resume'}:return {'thread':{'id':'owned-thread','projectId':start_project,'name':self.name},'model':params['model']}
                if method=='thread/name/set':self.name=params['name'];return {}
                if method=='turn/start':return {'turn':{'id':'owned-turn'}}
                return {}
            def next(self,*_):return {'method':'turn/completed','params':{'threadId':'owned-thread','turn':{'status':'completed'}}}
        return RPC
    def run_worker(self,rpc,thread=''):
        with patch('codex_workbench.CodexRPC',rpc),patch.object(self.worker,'ensure_project',return_value='bound-project'),patch('codex_workbench.NamingManager.suggest',return_value='long · 工作 · 整理资料'):
            return self.worker.run('a'*32,'整理资料',model='gpt-6-luna',effort='low',thread_id=thread,progress=lambda **kw:self.progress.append(kw))
    def test_foreign_thread_is_rejected_before_resume_or_any_turn(self):
        with self.assertRaisesRegex(ValueError,'不属于'):self.run_worker(self.rpc(read_project='another-project'),thread='owned-thread')
        self.assertEqual([m for m,_ in self.calls],['thread/read']);self.assertFalse(self.progress)
    def test_new_thread_wrong_project_is_not_named_or_run(self):
        with self.assertRaisesRegex(ValueError,'不属于'):self.run_worker(self.rpc(start_project='another-project'))
        self.assertNotIn('thread/name/set',[m for m,_ in self.calls]);self.assertNotIn('turn/start',[m for m,_ in self.calls])
    def test_new_thread_name_is_saved_and_read_back_before_work(self):
        result=self.run_worker(self.rpc(name='automatic default'))
        methods=[m for m,_ in self.calls]
        self.assertLess(methods.index('thread/name/set'),methods.index('thread/read'));self.assertLess(methods.index('thread/read'),methods.index('turn/start'))
        self.assertEqual(result['title'],'long · 工作 · 整理资料');self.assertEqual(result['project_id'],'bound-project')
        before_turn=next(p for p in self.progress if p.get('title'));self.assertEqual(before_turn['project_id'],'bound-project')
    def test_continue_keeps_real_canonical_name_in_result_and_record(self):
        result=self.run_worker(self.rpc(name='long · 工作 · 原有主题'),thread='owned-thread')
        self.assertEqual(result['title'],'long · 工作 · 原有主题');self.assertNotIn('thread/name/set',[m for m,_ in self.calls]);self.assertEqual(self.calls[0][0],'thread/read')
    def test_membership_change_during_resume_prevents_turn(self):
        with self.assertRaisesRegex(ValueError,'不属于'):self.run_worker(self.rpc(start_project='another-project'),thread='owned-thread')
        self.assertNotIn('turn/start',[m for m,_ in self.calls])
    def test_naming_manager_cannot_resume_another_project_thread(self):
        manager=NamingManager(self.root);manager.file.parent.mkdir();manager.file.write_text(json.dumps({'thread_id':'owned-thread','project_id':'bound-project'}),encoding='utf-8')
        rpc=self.rpc(read_project='another-project')()
        with patch('workspace_policy.WORKSPACE',self.root/'work'),self.assertRaisesRegex(ValueError,'不属于'):
            manager.suggest(rpc,'bound-project',self.root,'long','delegate_codex','整理资料',{})
        self.assertEqual([m for m,_ in self.calls],['thread/read'])


class PersistentThreadRecordTests(unittest.TestCase):
    def test_failed_work_keeps_verified_thread_name_project_and_usage_record(self):
        case=role_fixtures.RoleTests();case.setUp()
        try:
            def fail(jid,task,**kw):
                kw['progress'](thread_id='owned-thread',project_id='bound-project',title='long · 工作 · 测试主题',directory=str(case.root/jid),model=kw['model'],effort=kw['effort'])
                raise RuntimeError('unit task failure after binding')
            case.service.workbench.run=fail
            case.service.request(case.admin,'delegate_codex',{'task':'整理资料','mode':'new','model':'gpt-6-luna','effort':'low'},'让Codex整理资料');case.settle()
            row=case.service.recent(case.admin)[0]
            self.assertEqual(row['state'],'failed');self.assertEqual(row['title'],'long · 工作 · 测试主题');self.assertEqual(row['project_id'],'bound-project');self.assertEqual(row['thread_id'],'owned-thread')
            with case.service.ledger.db() as db:record=db.execute("SELECT details FROM events WHERE kind='codex_thread_bound'").fetchone()
            self.assertEqual(json.loads(record[0])['title'],row['title'])
        finally:case.tearDown()


if __name__=='__main__':unittest.main()
