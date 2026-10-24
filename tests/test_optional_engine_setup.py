from pathlib import Path
from types import SimpleNamespace
import asyncio
import json
import tempfile
import time
import unittest
from unittest.mock import Mock,patch

from optional_engine_setup import install,environment_state
from scripts.beginner_setup import SetupEngine,ManagedProcess
from astrbot_sidecar import activity_state,tracked_request,confirm_pending


def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value),encoding='utf-8')


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        write(self.root/'data/setup/installation.local.json',{'root':str(self.root),'instance':'fixture'})
        self.python=self.root/'.venv/Scripts/python.exe';self.python.parent.mkdir(parents=True);self.python.touch()
        write(self.root/'settings.local.json',{'python':str(self.python),'api_token':'fixture'})
        for name in ['requirements-astrbot-build.lock','requirements-astrbot.lock']:
            (self.root/name).write_text('fixture-pinned-lock',encoding='utf-8')
        self.writer=patch('optional_engine_setup.write',write);self.writer.start()
    def tearDown(self):self.writer.stop();self.temp.cleanup()
    def runner(self,args,**kwargs):
        if '-m' in args and 'venv' in args:
            env=Path(args[-1]);(env/'Scripts').mkdir(parents=True);(env/'Scripts/python.exe').touch()
        if '-c' in args:
            code=args[-1]
            return SimpleNamespace(returncode=0,stdout='3.13\n' if 'version_info' in code else json.dumps({'astrbot':'4.29.1','psutil':'7.1.3'}))
        return SimpleNamespace(returncode=0)
    def test_install_isolated_environment_records_relative_paths_and_starts_legacy(self):
        result=install(self.root,runner=self.runner)
        self.assertTrue(result['installed'])
        cfg=json.loads((self.root/'configs/chat-engine.local.json').read_text())
        self.assertFalse(Path(cfg['python']).is_absolute());self.assertTrue(cfg['python'].startswith('.runtime/astrbot/'))
        self.assertEqual(cfg['profile'],'data/astrbot');self.assertEqual(cfg['mode'],'legacy')
        self.assertEqual(json.loads((self.root/'settings.local.json').read_text())['python'],str(self.python))
        self.assertNotIn(str(self.root),json.dumps(result))
        with patch('optional_engine_setup.subprocess.run') as run:
            install(self.root,runner=run);run.assert_not_called()
    def test_failed_dependency_install_keeps_previous_config(self):
        before={'python':'.runtime/old/Scripts/python.exe','profile':'data/old-profile','mode':'hybrid','enabled':True}
        write(self.root/'configs/chat-engine.local.json',before)
        def fail(args,**kwargs):
            if 'pip' in args:return SimpleNamespace(returncode=1)
            return self.runner(args,**kwargs)
        with self.assertRaises(RuntimeError):install(self.root,runner=fail)
        self.assertEqual(json.loads((self.root/'configs/chat-engine.local.json').read_text()),before)
        self.assertEqual(environment_state(self.root)['phase'],'failed')
    def test_unmanaged_root_and_external_profile_are_not_adopted(self):
        before={'python':str(self.python),'profile':str(self.root.parent/'external-profile')}
        write(self.root/'configs/chat-engine.local.json',before)
        with self.assertRaises(ValueError):install(self.root,runner=Mock())
        self.assertEqual(json.loads((self.root/'configs/chat-engine.local.json').read_text()),before)
        (self.root/'data/setup/installation.local.json').unlink()
        with self.assertRaises(ValueError):install(self.root,runner=Mock())


class ReloadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        self.python=self.root/'.runtime/test/Scripts/python.exe';self.python.parent.mkdir(parents=True);self.python.touch()
        self.path=self.root/'configs/chat-engine.local.json'
        self.cfg={'enabled':True,'mode':'legacy','python':str(self.python),'profile':'data/astrbot','port':18869}
        write(self.path,self.cfg);write(self.root/'settings.local.json',{'api_token':'fixture'})
        write(self.root/'agent.local.json',{'api_key':'local-fixture','model':'first-model'})
        self.engine=SetupEngine.__new__(SetupEngine);self.engine.root=self.root;self.engine.astrbot=None
        self.engine.astrbot_fingerprint='';self.engine.astrbot_state='disabled'
    def tearDown(self):self.temp.cleanup()
    def test_mode_switch_reuses_process_but_provider_changes_wait_for_inflight(self):
        worker=Mock();worker.ports=[18869];worker.tick.return_value='running';worker.owns.return_value=True
        proc=Mock();proc.parents.return_value=[]
        response=Mock();response.json.return_value={'busy':True,'pending':0}
        client=Mock();client.__enter__=Mock(return_value=client);client.__exit__=Mock(return_value=False);client.get.return_value=response
        with patch('scripts.beginner_setup.ManagedProcess',return_value=worker) as factory:
            self.engine.ensure_astrbot();self.assertEqual(factory.call_count,1)
            write(self.path,{**self.cfg,'mode':'hybrid'})
            with patch('psutil.process_iter',return_value=[proc]) as listing:
                self.engine.ensure_astrbot();listing.assert_not_called()
            self.assertEqual(factory.call_count,1)
            write(self.root/'agent.local.json',{'api_key':'local-fixture','model':'second-model'})
            with patch('psutil.process_iter',return_value=[proc]),patch('httpx.Client',return_value=client):
                self.engine.ensure_astrbot();proc.terminate.assert_not_called()
                self.assertEqual(self.engine.astrbot_state,'waiting_for_idle')
                response.json.return_value={'busy':False,'pending':0}
                self.engine.ensure_astrbot();proc.terminate.assert_called_once()
            self.assertEqual(factory.call_count,2)
    def test_venv_wrapper_and_base_child_require_same_full_argv_and_root(self):
        argv=[str(self.python),'-X','utf8',str(self.root/'astrbot_sidecar.py'),'--root',str(self.root),'--profile',str(self.root/'data/astrbot'),'--port','18869']
        service=ManagedProcess(self.root,'astrbot',argv,self.root,[18869])
        base=SimpleNamespace(exe=lambda:'C:/Python/python.exe',cwd=lambda:str(self.root),cmdline=lambda:argv)
        self.assertTrue(service.owns(base))
        for field in ['--profile','--port','--root']:
            wrong=argv.copy();wrong[wrong.index(field)+1]='unrelated'
            base.cmdline=lambda:wrong;self.assertFalse(service.owns(base))


class NativeActivityTests(unittest.TestCase):
    def test_health_prunes_expired_preview_without_hiding_active_request(self):
        pending={'expired':{'at':1},'fresh':{'at':150}}
        state=activity_state(pending,{'inflight':1},now=200)
        self.assertEqual(state['pending'],1);self.assertTrue(state['busy'])
    def test_request_counter_recovers_on_error(self):
        async def check():
            stats={}
            with self.assertRaises(ValueError):
                async with tracked_request(stats):
                    self.assertEqual(stats['inflight'],1);raise ValueError('fixture')
            self.assertEqual(stats['inflight'],0)
        asyncio.run(check())
    def test_confirm_database_await_remains_inflight_after_ticket_pop(self):
        async def check():
            entered=asyncio.Event();release=asyncio.Event();stats={'commits':0}
            pending={'ticket':{'at':0,'sid':'sid','umo':'umo','cid':'cid','before':[],'after':[{'role':'user','content':'hello'}]}}
            async def get(*args):
                entered.set();await release.wait();return SimpleNamespace(history='[]')
            update=Mock()
            async def save(*args,**kwargs):update(*args,**kwargs)
            manager=SimpleNamespace(get_conversation=get,update_conversation=save)
            task=asyncio.create_task(confirm_pending('ticket',pending,{},stats,manager,clock=lambda:1))
            await entered.wait();self.assertFalse(pending)
            self.assertTrue(activity_state(pending,stats,now=1)['busy'])
            release.set();self.assertTrue((await task)['committed'])
            self.assertEqual(stats['inflight'],0);self.assertEqual(stats['commits'],1);update.assert_called_once()
        asyncio.run(check())
