"""Exercise the resume gate with fake launchers, never the host's live services."""
from pathlib import Path
import json,os,subprocess,tempfile,unittest
from datetime import datetime,timezone

@unittest.skipUnless(os.name=='nt','Windows resume entry')
class ResumeTests(unittest.TestCase):
    def test_windows_default_powershell_parses_unicode_startup_scripts(self):
        root=Path(__file__).resolve().parents[1]
        code="$files=@('runtime-task.ps1','launch-whale.ps1','resume-whale.ps1');if(Test-Path 'scripts/bootstrap.ps1'){$files+=@('scripts/bootstrap.ps1')};foreach($f in $files){$t=$null;$e=$null;[System.Management.Automation.Language.Parser]::ParseFile((Join-Path (Get-Location) $f),[ref]$t,[ref]$e)|Out-Null;if($e.Count){Write-Output $f;exit 3}}"
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',code],cwd=root,capture_output=True,text=True,timeout=15,creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_pause_fresh_host_and_maintenance_block_relaunch(self):
        for state in ['paused','fresh','maintenance','stale']:
            with self.subTest(state=state),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);(root/'configs').mkdir();(root/'logs/runtime').mkdir(parents=True)
                (root/'configs/runtime-control.local.json').write_text(json.dumps({'desired':'stopped' if state=='paused' else 'running'}))
                (root/'configs/launcher.local.json').write_text('{}')
                (root/'launch-whale.ps1').write_text('param([switch]$NoOpen,[switch]$NoGui,[switch]$ResumeOnly)\nif(-not$ResumeOnly){throw "Must preserve resume-only intent"}\nSet-Content -LiteralPath (Join-Path $PSScriptRoot "launched.txt") -Value ready',encoding='utf-8')
                if state=='fresh':
                    (root/'logs/runtime/host-status.json').write_text(json.dumps({'state':'running','pid':os.getpid(),'checked_at':datetime.now(timezone.utc).isoformat()}))
                if state=='stale':
                    (root/'logs/runtime/host-status.json').write_text(json.dumps({'state':'running','pid':os.getpid(),'checked_at':'2020-01-01T00:00:00Z'}))
                if state=='maintenance':
                    (root/'data/setup').mkdir(parents=True);(root/'data/setup/update-progress.local.json').write_text('{"phase":"recovery_required"}')
                script=Path(__file__).resolve().parents[1]/'resume-whale.ps1'
                result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(script),'-Root',str(root)],capture_output=True,text=True,timeout=15,creationflags=subprocess.CREATE_NO_WINDOW)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual((root/'launched.txt').exists(),state=='stale')

if __name__=='__main__':unittest.main()
