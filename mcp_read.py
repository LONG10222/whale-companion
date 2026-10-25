"""Public page reads use one pinned HTTPS boundary, including legacy MCP settings."""
from pathlib import Path
import json


def fetch(root,url):
    from agent_web import read_web_page
    config=Path(root)/'configs/web-tools.local.json'
    settings=json.loads(config.read_text(encoding='utf-8')) if config.exists() else {}
    requested=settings.get('reader','builtin')
    if requested not in {'builtin','mcp'}:raise ValueError('未知网页读取方式。')
    result={**read_web_page(url),'provider':'bounded public HTTPS reader with pinned addresses',
            'reader_requested':requested,'reader_effective':'builtin_pinned_https',
            'mcp_called':False,'tools_discovered':[],'available_tools':['read_page']}
    if requested=='mcp':
        result['safe_fallback']=True
        result['fallback_reason']='外部MCP网页客户端不能绑定已验证地址，本次由受控HTTPS读取器获取网页。'
    return result
