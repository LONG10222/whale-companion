from pathlib import Path
import json,os,subprocess,sys,tempfile,threading,time,unittest
from unittest.mock import patch
from local_learning import PriorityResourceLock,LocalLearningAssistant,AssistantBusy,DEFAULT_POLICY,busy_reason,load_policy,owned_job,assistant_complete

class LocalLearningTests(unittest.TestCase):
    def test_audio_preempts_learning_and_idle_watchdog_does_not(self):
        lock=PriorityResourceLock();self.assertTrue(lock.acquire_low());self.assertFalse(lock.acquire_idle());self.assertFalse(lock.low_cancel.is_set())
        def learning():
            lock.low_cancel.wait(2);lock.release_low()
        worker=threading.Thread(target=learning);worker.start()
        self.assertTrue(lock.acquire(False));lock.release();worker.join(timeout=2)
        self.assertTrue(lock.low_cancel.is_set());self.assertFalse(lock.locked())
    def test_learning_does_not_preempt_existing_audio(self):
        lock=PriorityResourceLock();lock.acquire();self.assertFalse(lock.acquire_low());lock.release()
    def test_busy_gpu_low_memory_and_unknown_telemetry_defer(self):
        good={'gpu_free_mib':12000,'gpu_used_mib':3000,'gpu_util_percent':5,'ram_free_mib':10000,'cpu_percent':5}
        self.assertEqual(busy_reason(good,DEFAULT_POLICY),'')
        for overrides in [{'gpu_util_percent':80},{'gpu_free_mib':8000},{'ram_free_mib':7000},{'cpu_percent':70}]:
            self.assertTrue(busy_reason({**good,**overrides},DEFAULT_POLICY))
        self.assertTrue(busy_reason(None,DEFAULT_POLICY))
    def test_policy_cannot_expand_resource_budget_and_paused_never_calls_cloud(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'configs').mkdir();(root/'configs/local-learning.local.json').write_text(json.dumps({'mode':'paused','threads':16,'max_seconds':999,'min_free_ram_mib':0}),encoding='utf-8')
            policy=load_policy(root);self.assertEqual(policy['threads'],2);self.assertEqual(policy['max_seconds'],35);self.assertEqual(policy['min_free_ram_mib'],8192)
            with patch('local_learning.host_client') as host,patch('deepseek_client.complete') as cloud:
                with self.assertRaises(AssistantBusy):assistant_complete(root,[],{},'test')
                host.assert_not_called();cloud.assert_not_called()
    def test_busy_computer_does_not_launch_worker_or_unload_audio(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'configs').mkdir();model=root/'fake.gguf';exe=root/'fake.exe';model.touch();exe.touch()
            (root/'configs/local-learning.local.json').write_text(json.dumps({'mode':'local','model':str(model),'runtime':str(exe)}),encoding='utf-8')
            with patch('local_learning.resource_snapshot',return_value={'gpu_free_mib':12000,'gpu_used_mib':3000,'gpu_util_percent':90,'ram_free_mib':12000,'cpu_percent':0}),patch('local_learning.subprocess.Popen') as launch:
                unload=[];assistant=LocalLearningAssistant(root,PriorityResourceLock(),lambda:unload.append(True))
                with self.assertRaises(AssistantBusy):assistant.complete([{'role':'user','content':'test'}],{},'test')
                launch.assert_not_called();self.assertFalse(unload)
    @unittest.skipUnless(os.name=='nt','Windows owned process job')
    def test_closing_owned_job_terminates_only_our_child(self):
        p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(10)'],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            close=owned_job(p);close();p.wait(timeout=3);self.assertIsNotNone(p.returncode)
        finally:
            if p.poll() is None:p.kill();p.wait(timeout=3)

if __name__=='__main__':unittest.main()
