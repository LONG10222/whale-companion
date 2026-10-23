"""Exercise the real Windows host with a disposable child, never the live QQ."""
import json,os,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from runtime_supervisor import atomic_json

@unittest.skipUnless(os.name=='nt','Windows background host')
class HostTests(unittest.TestCase):
    def test_failed_child_restarts_then_respects_explicit_stop(self):
        with tempfile.TemporaryDirectory(prefix='whale-host-test-') as temp:
            root=Path(temp)
            atomic_json(root/'settings.local.json',{'python':sys.executable})
            atomic_json(root/'configs/runtime-control.local.json',{'desired':'running'})
            (root/'runtime_supervisor.py').write_text('''from pathlib import Path
import json,time,sys
r=Path(__file__).resolve().parent
f=r/'run-count.txt'; n=int(f.read_text())+1 if f.exists() else 1; f.write_text(str(n))
if n==1: sys.exit(7)
while json.loads((r/'configs/runtime-control.local.json').read_text(encoding='utf-8-sig'))['desired']!='stopped': time.sleep(.1)
''',encoding='utf-8')
            host=Path(__file__).resolve().parents[1]/'runtime-host.ps1'
            child=subprocess.Popen(['powershell.exe','-NoProfile','-NonInteractive','-WindowStyle','Hidden',
                '-ExecutionPolicy','Bypass','-File',str(host),'-Root',str(root)],cwd=root,
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                deadline=time.monotonic()+30
                while time.monotonic()<deadline:
                    count=root/'run-count.txt'
                    if count.exists() and count.read_text()=='2':break
                    self.assertIsNone(child.poll(),'Host exited unexpectedly');time.sleep(.25)
                self.assertEqual(count.read_text(),'2','Failed child did not restart')
                atomic_json(root/'configs/runtime-control.local.json',{'desired':'stopped'})
                self.assertEqual(child.wait(timeout=15),0)
                events=[json.loads(line) for line in (root/'logs/runtime/host-events.jsonl').read_text(encoding='utf-8-sig').splitlines()]
                exits=[e['values']['exit_code'] for e in events if e['event']=='supervisor_exited']
                self.assertEqual(exits,[7,0]);self.assertEqual(count.read_text(),'2')
                self.assertEqual(json.loads((root/'logs/runtime/host-status.json').read_text(encoding='utf-8-sig'))['state'],'stopped')
            finally:
                atomic_json(root/'configs/runtime-control.local.json',{'desired':'stopped'})
                try: child.wait(timeout=15)
                except subprocess.TimeoutExpired:child.terminate();child.wait(timeout=5)

if __name__=='__main__':unittest.main()
