"""Fresh-directory optional installer smoke test; no QQ or cloud-model requests."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time

SOURCE=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(SOURCE))

import httpx
import psutil
from optional_engine_setup import OptionalEngineInstaller
from scripts.beginner_setup import SetupEngine,write_json


def owned(worker):
    return [p for p in psutil.process_iter() if worker is not None and worker.owns(p)]


def stop(worker):
    processes=owned(worker)
    for process in sorted(processes,key=lambda p:len(p.parents()),reverse=True):
        try:
            if worker.owns(process):process.terminate();process.wait(timeout=15)
        except psutil.NoSuchProcess:pass


def health(port,token):
    with httpx.Client(trust_env=False,timeout=2) as client:
        response=client.get(f'http://127.0.0.1:{port}/health',headers={'Authorization':'Bearer '+token})
        response.raise_for_status();return response.json()


def wait_ready(engine,port,token,timeout=100):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        engine.ensure_astrbot()
        try:
            result=health(port,token)
            if result.get('ok'):return result
        except httpx.HTTPError:pass
        time.sleep(1)
    raise RuntimeError('Isolated native kernel did not become healthy')


def verify():
    temp_base=Path(tempfile.gettempdir()).resolve()
    temp=tempfile.TemporaryDirectory(prefix='whale-astrbot-qa-',suffix=' 新安装')
    root=Path(temp.name).resolve()
    assert root.parent==temp_base and root.name.startswith('whale-astrbot-qa-')
    engine=None;installer=None
    try:
        import importlib.util
        spec=importlib.util.spec_from_file_location('whale_public_scan',SOURCE/'scripts/scan_public.py')
        scanner=importlib.util.module_from_spec(spec);spec.loader.exec_module(scanner)
        for source in scanner.files():
            target=root/source.relative_to(SOURCE);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
        # Base dependencies are supplied by the CI's verified application environment.
        # The optional child environment itself must not inherit any site packages.
        subprocess.run([sys.executable,'-m','venv','--system-site-packages',str(root/'.venv')],check=True)
        python=root/'.venv/Scripts/python.exe';token=secrets.token_urlsafe(32)
        write_json(root/'data/setup/installation.local.json',{'root':str(root),'instance':'optional-ci-smoke','completed':False})
        write_json(root/'settings.local.json',{'python':str(python),'api_token':token})
        write_json(root/'agent.local.json',{'api_key':'isolated-no-cloud-call','model':'deepseek-chat'})
        before=(root/'settings.local.json').read_bytes()
        installer=OptionalEngineInstaller(root);installer.start()
        deadline=time.monotonic()+1500;last=''
        while time.monotonic()<deadline:
            state=installer.state()
            if state['phase']!=last:
                print('Optional install:',state['phase'],flush=True);last=state['phase']
            if state['installed']:break
            if state['phase'] in {'failed','interrupted'}:raise RuntimeError(state['message'])
            time.sleep(2)
        else:raise RuntimeError('Optional installation deadline exceeded')
        if installer.process:installer.process.wait(timeout=30)
        assert (root/'settings.local.json').read_bytes()==before
        cfg_path=root/'configs/chat-engine.local.json';cfg=json.loads(cfg_path.read_text(encoding='utf-8'))
        assert cfg['mode']=='legacy' and not Path(cfg['python']).is_absolute()
        env=root/Path(cfg['python']).parent.parent/'pyvenv.cfg'
        assert 'include-system-site-packages = false' in env.read_text(encoding='utf-8')
        stamp=(root/'data/setup/astrbot-environment.local.json').read_bytes()
        installer.start();assert (root/'data/setup/astrbot-environment.local.json').read_bytes()==stamp
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        cfg['port']=port;write_json(cfg_path,cfg)
        engine=SetupEngine.__new__(SetupEngine);engine.root=root;engine.astrbot=None
        engine.astrbot_fingerprint='';engine.astrbot_state='disabled'
        initial=wait_ready(engine,port,token)
        pids={p.pid for p in owned(engine.astrbot)};assert pids
        with httpx.Client(trust_env=False,timeout=3) as client:
            assert client.get(f'http://127.0.0.1:{port}/health').status_code==401
            source=client.get(f'http://127.0.0.1:{port}/source').json()
            assert source['optional_kernel_license']=='AGPL-3.0-or-later'
        cfg['mode']='hybrid';write_json(cfg_path,cfg);engine.ensure_astrbot()
        assert {p.pid for p in owned(engine.astrbot)}==pids
        write_json(root/'agent.local.json',{'api_key':'isolated-no-cloud-call','model':'isolated-reload-no-cloud-call'})
        engine.ensure_astrbot();reloaded=wait_ready(engine,port,token)
        new_pids={p.pid for p in owned(engine.astrbot)}
        assert new_pids and not pids.intersection(new_pids)
        assert not any(psutil.pid_exists(pid) for pid in pids)
        assert initial['model_calls']==0 and reloaded['model_calls']==0
        assert initial['qq_transport'] is False and initial['computer_tools'] is False
        assert set(initial['allow_tools'])=={'end_chat_turn','route_whale_action'}
        from chat_engine import ChatEngine
        assert ChatEngine(root).status()['kernel']['ok']
        result={'installed':True,'relative_paths':True,'default_mode':'legacy','base_settings_unchanged':True,
                'separate_site_packages':True,'repeat_install_reused':True,'real_native_health':True,
                'mode_switch_kept_process':True,'provider_reload_replaced_all_owned_python_pids':True,
                'unauthenticated_health':401,'cloud_model_calls':0,'qq_started':False,
                'extra_ram_mib':initial['ram_mib'],'version':initial['version']}
        return result
    finally:
        if engine is not None:stop(engine.astrbot)
        if installer and installer.process and installer.process.poll() is None:
            installer.process.wait(timeout=30)
        assert root.parent==temp_base and root.name.startswith('whale-astrbot-qa-')
        temp.cleanup()


if __name__=='__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(verify(),ensure_ascii=False,indent=2))
