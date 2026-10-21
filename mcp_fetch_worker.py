"""Disposable official MCP Fetch client. No credentials or personal conversation enter the tool process."""
import asyncio,json,sys
from datetime import timedelta

async def run(url):
    from mcp import ClientSession,StdioServerParameters
    from mcp.client.stdio import stdio_client
    params=StdioServerParameters(command=sys.executable,args=['-m','mcp_server_fetch'])
    async with stdio_client(params) as streams:
        async with ClientSession(*streams,read_timeout_seconds=timedelta(seconds=22)) as session:
            await session.initialize()
            catalog=await session.list_tools()
            if 'fetch' not in {t.name for t in catalog.tools}:raise ValueError('MCP服务没有提供fetch工具。')
            result=await session.call_tool('fetch',{'url':url,'max_length':10000})
            if result.isError:raise ValueError('网页抓取服务未能读取这个页面。')
            text='\n'.join(c.text for c in result.content if getattr(c,'type','')=='text')
            return {'ok':True,'url':url,'text':text[:12000],'provider':'official MCP Fetch / stdio','tools_discovered':[t.name for t in catalog.tools]}

if __name__=='__main__':
    try:result=asyncio.run(run(json.loads(sys.stdin.readline())['url']))
    except Exception:result={'ok':False,'error':'MCP网页读取失败或超时。'}
    print(json.dumps(result,ensure_ascii=False))
