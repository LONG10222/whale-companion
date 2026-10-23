"""One owned, bounded MCP worker per read; shuts down after each use."""
from pathlib import Path
import json,os,subprocess

def fetch(root,url):
    from agent_web import validate_public_url,read_web_page
    validate_public_url(url)
    root=Path(root)
    config=root/'configs/web-tools.local.json'
    settings=json.loads(config.read_text(encoding='utf-8')) if config.exists() else {}
    if settings.get('reader','builtin')=='builtin':
        return {**read_web_page(url),'provider':'bounded public HTTPS reader','tools_discovered':['read_page']}
    if settings.get('reader')!='mcp':raise ValueError('未知网页读取方式。')
    python=root/'.autonomy-runtime'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if not python.exists():raise ValueError('MCP网页工具尚未安装。')
    process=subprocess.Popen([str(python),'-X','utf8',str(root/'mcp_fetch_worker.py')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        text=True,encoding='utf-8',cwd=root,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        output,_=process.communicate(json.dumps({'url':url})+'\n',timeout=32)
        result=json.loads(output)
        if not result.get('ok'):raise ValueError(result.get('error','MCP读取失败。'))
        return result
    finally:
        if process.poll() is None:
            import psutil
            try:children=psutil.Process(process.pid).children(recursive=True)
            except psutil.Error:children=[]
            for child in reversed(children):
                try:child.kill()
                except psutil.Error:pass
            process.kill();process.communicate()
