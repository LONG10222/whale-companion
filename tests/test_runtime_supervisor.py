"""Recovery boundaries: never restart living QQ or claim someone else's port."""
import json, os, sqlite3, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import Mock, patch
from runtime_supervisor import Service, Supervisor, RetryBudget, atomic_json, active_work, InstanceLock

class FakeProcess:
    def __init__(self,pid,exe,args,cwd): self.pid=pid; self._exe=exe; self.args=args; self.folder=cwd
    def exe(self): return self._exe
    def name(self): return Path(self._exe).name
    def cwd(self): return str(self.folder)
    def cmdline(self): return self.args

class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        atomic_json(self.root/'settings.local.json',{'python':sys.executable,'api_token':'private-do-not-log-me'})
        self.now=1000; self.procs=[]
        self.s=Supervisor(self.root,clock=lambda:self.now,processes=lambda:self.procs)
        self.s.listeners=Mock(return_value={}); self.s.probe=Mock(return_value='ready'); self.s.spawn=Mock()
    def tearDown(self):
        for handler in self.s.log.handlers[:]: handler.close(); self.s.log.removeHandler(handler)
        self.temp.cleanup()
    def api(self,pid=101):
        return FakeProcess(pid,'C:/Python/python.exe',['python.exe','-m','uvicorn','phone_server:app','--port','18766'],self.root)
    def voice(self,pid=102):
        return FakeProcess(pid,'C:/Python/python.exe',['python.exe',str(self.root/'voice_server.py')],self.root)
    def test_adopts_and_skips_healthy_services(self):
        self.procs=[self.api(),self.voice()]; self.s.listeners.return_value={18766:101,18765:102}
        state=self.s.tick(); self.s.spawn.assert_not_called()
        self.assertEqual(state['services']['companion']['state'],'ready')
    def test_port_conflict_never_spawns_or_terminates(self):
        self.s.services=self.s.services[1:]; self.s.listeners.return_value={18766:999}
        state=self.s.tick(); self.s.spawn.assert_not_called()
        self.assertEqual(state['services']['companion']['state'],'port_conflict')
    def test_owned_process_with_foreign_port_is_conflict(self):
        self.procs=[self.api()]; self.s.services=self.s.services[1:]; self.s.listeners.return_value={18766:999}
        self.assertEqual(self.s.tick()['services']['companion']['state'],'port_conflict')
        self.s.spawn.assert_not_called()
    def test_live_but_unhealthy_is_not_killed_or_duplicated(self):
        self.procs=[self.api()]; self.s.services=self.s.services[1:]; self.s.probe.return_value='unhealthy'
        self.assertEqual(self.s.tick()['services']['companion']['state'],'unhealthy')
        self.s.spawn.assert_not_called()
    def test_missing_process_recovers_after_real_disappearance(self):
        self.procs=[self.api()]; self.s.services=self.s.services[1:]; self.s.tick()
        self.procs=[]; state=self.s.tick(); self.s.spawn.assert_called_once()
        self.assertEqual(state['services']['companion']['state'],'starting')
        self.now+=5; self.s.tick(); self.s.spawn.assert_called_once()
    def test_repeated_start_failures_have_bounded_retries(self):
        self.s.services=self.s.services[1:]; self.s.spawn.side_effect=OSError('private-do-not-log-me')
        for offset in [0,61,122,183,244,400]:
            self.now=1000+offset; self.s.tick()
        self.assertEqual(self.s.spawn.call_count,3)
        self.assertNotIn('private-do-not-log-me',(self.root/'logs/runtime/events.jsonl').read_text())
    def test_daily_qq_is_never_owned(self):
        service=Service('napcat',['boot.exe'],self.root,client_root=self.root/'qq-client')
        self.assertFalse(service.owns(FakeProcess(4,'C:/Program Files/Tencent/QQNT/QQ.exe',[],self.root)))
        self.assertFalse(service.owns(FakeProcess(5,str(self.root/'qq-client-extra/QQ.exe'),[],self.root)))
        self.assertTrue(service.owns(FakeProcess(6,str(self.root/'qq-client/QQ.exe'),[],self.root)))
    def test_python_in_other_installation_is_not_owned(self):
        p=self.api(); p.folder=self.root/'other'; self.assertFalse(self.s.services[1].owns(p))
    def test_live_qq_does_not_use_login_state_as_restart_trigger(self):
        client=self.root/'qq-client'; service=Service('napcat',['boot.exe'],client,client_root=client)
        self.s.services=[service]; self.procs=[FakeProcess(7,str(client/'QQ.exe'),[],client)]
        self.assertEqual(self.s.tick()['services']['napcat']['state'],'running')
        self.s.spawn.assert_not_called(); self.s.probe.assert_not_called()
    def test_boot_exit_does_not_immediately_spawn_duplicate(self):
        service=self.s.services[1]; service.started_at=self.now-10
        self.s.services=[service]; self.s.tick(); self.s.spawn.assert_not_called()
    def test_pause_stops_monitor_without_terminating_services(self):
        atomic_json(self.s.control_path,{'desired':'stopped','stop_services':False})
        self.s.tick=Mock(); self.s.stop_services=Mock(); self.s.run()
        self.s.tick.assert_not_called(); self.s.stop_services.assert_not_called()
        self.assertEqual(json.loads(self.s.state_path.read_text())['state'],'stopped')
    def test_stop_services_waits_for_busy_work(self):
        with patch('runtime_supervisor.active_work',return_value=1): self.assertFalse(self.s.stop_services())
        with patch('runtime_supervisor.active_work',return_value=None): self.assertFalse(self.s.stop_services())
    def test_readonly_work_check_does_not_create_database(self):
        self.assertEqual(active_work(self.root),0); self.assertFalse((self.root/'data/qq').exists())
    def test_invalid_work_db_is_not_considered_idle(self):
        folder=self.root/'data/qq'; folder.mkdir(parents=True); (folder/'creative.sqlite3').write_text('invalid')
        self.assertIsNone(active_work(self.root))
    def test_phone_task_also_defers_deliberate_shutdown(self):
        folder=self.root/'data/qq'; folder.mkdir(parents=True)
        db=sqlite3.connect(folder/'tasks.sqlite3')
        try:
            db.execute('CREATE TABLE tasks(state TEXT)'); db.execute("INSERT INTO tasks VALUES('running')"); db.commit()
        finally: db.close()
        self.assertEqual(active_work(self.root),1)
    def test_crash_restart_preserves_start_budget(self):
        self.s.services=self.s.services[1:]; self.s.tick()
        other=Supervisor(self.root,clock=lambda:self.now,processes=lambda:[])
        try: self.assertEqual(other.services[1].retry.attempts,[self.now])
        finally:
            for handler in other.log.handlers[:]:handler.close();other.log.removeHandler(handler)
    def test_budget_window_expires_but_backoff_remains(self):
        budget=RetryBudget()
        for now in [1000,1061,1122]: budget.started(now)
        self.assertGreaterEqual(budget.wait(1183),717)
        self.assertEqual(budget.wait(2000),0)
    @unittest.skipUnless(os.name=='nt','Windows named mutex')
    def test_duplicate_supervisor_has_same_installation_lock(self):
        a=InstanceLock(self.root); b=InstanceLock(self.root)
        try: self.assertTrue(a.acquire()); self.assertFalse(b.acquire())
        finally: a.close(); b.close()

if __name__=='__main__': unittest.main()
