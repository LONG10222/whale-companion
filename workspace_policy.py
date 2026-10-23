"""Explicit per-turn read grants; database inputs are read-only source snapshots."""
from pathlib import Path
from contextlib import closing
import json,re,shutil,sqlite3

from runtime_paths import workspace
WORKSPACE=workspace()
ALIASES={'聊天记录':['聊天记录','聊天数据库','qq-memory'],'长期记忆':['长期记忆','记忆数据库','companion-life'],'人格反馈':['人格反馈','学习数据库','persona-learning'],
 '表情包目录':['表情包目录','表情数据库','表情包数据库'],'手机任务':['手机任务数据库','tasks.sqlite3'],'创作任务':['创作数据库','creative.sqlite3'],'自主活动':['自主活动数据库'],
 '主动话题':['主动话题','主动聊天数据库','proactive.sqlite3'],'授权记录':['授权记录','授权数据库','action-consent.sqlite3'],'梗学习':['梗学习','梗数据库','jargon.sqlite3'],'余额记录':['余额记录','余额数据库','balance.sqlite3'],'项目代码':['项目代码','机器人代码','程序代码','源代码'],'项目资料':['项目资料','项目文件','sources']}
FILES={'聊天记录':'qq-memory.sqlite3','长期记忆':'companion-life.sqlite3','人格反馈':'persona-learning.sqlite3','手机任务':'tasks.sqlite3','创作任务':'creative.sqlite3','自主活动':'companion-life.sqlite3','主动话题':'proactive.sqlite3','授权记录':'action-consent.sqlite3','梗学习':'jargon.sqlite3','余额记录':'balance.sqlite3'}
def validate_read_request(text,source):
    if source not in ALIASES:raise ValueError('不支持的资料来源。')
    from creative_tools import bot_repair_intent
    repair=source=='项目代码' and bot_repair_intent(text)
    if not repair and not re.search('读|看|查|检查|分析|整理|总结|使用|参考|导出',text):raise ValueError('读取资料须由你在本次明确提出。')
    all_db=source in FILES and bool(re.search('全部数据库|所有数据库|之前.*数据库',text))
    if not repair and not all_db and not any(a.casefold() in text.casefold() for a in ALIASES[source]):raise ValueError('本次来信没有要求读取“'+source+'”。')
    fragments=re.split('[，,。；;\n]',text)
    relevant=[f for f in fragments if any(a.casefold() in f.casefold() for a in ALIASES[source]) or all_db and re.search('全部数据库|所有数据库|之前.*数据库',f)]
    if relevant and all(re.search(r'(?:不要|不用|无需|无须|禁止|不许|不得|不能|别|不)(?:再|自行|自己|直接|擅自|主动)?(?:读取?|查看?|看|使用|访问|检查|分析)',f) for f in relevant):raise ValueError('本次明确要求不读取该资料。')

def prepare_inputs(root,project,folder,text,sources=None,paths=None):
    from safe_input_snapshots import Snapshot,private_name
    root=Path(root);project=Path(project);snapshot=None;exports=[]
    def ready():
        nonlocal snapshot
        if snapshot is None:snapshot=Snapshot(root,folder)
        return snapshot
    for source in sources or []:
        validate_read_request(text,source);store=ready()
        if source in FILES:
            original=root/'data/qq'/FILES[source]
            target=store.database(original,root,FILES[source]);exports.append(str(target))
        elif source=='项目资料':
            exports.append(str(store.tree(project/'sources','project')))
        elif source=='项目代码':
            target=store.directory/'project-code';target.mkdir(exist_ok=True)
            for original in root.iterdir():
                if original.is_file() and original.suffix in {'.py','.html','.js','.md','.ps1'}:
                    store.copy(original,root,Path('project-code')/original.name)
            for name in ['tests','tool-guides','configs']:
                if (root/name).exists():store.tree(root/name,Path('project-code')/name,{'.py','.html','.js','.md','.json','.txt'})
            exports.append(str(target))
        elif source=='表情包目录':
            target=store.copy(root/'assets/stickers/catalog.json',root,'sticker-index.json')
            if target:exports.append(str(target))
    for value in paths or []:
        if value.startswith(('\\\\','//')):raise ValueError('不读取网络共享，请选择本机的实际资料。')
        normalized=value.replace('\\','/').casefold()
        if normalized not in text.replace('\\','/').casefold():raise ValueError('只读路径必须出现在你本次的原话里。')
        if not re.search('读|看|检查|分析|使用|参考|总结',text):raise ValueError('本次未要求读取外部文件。')
        masked=text.replace('\\','/').casefold().replace(normalized,'__requested_local_path__')
        matching=[f for f in re.split('[，,。；;\n]',masked) if '__requested_local_path__' in f]
        if matching and all(re.search(r'(?:不要|不用|无需|无须|禁止|不许|不得|不能|别|不)(?:再|自行|自己|直接|擅自|主动)?(?:读取?|查看?|看|使用|访问|检查|分析)',f) for f in matching):raise ValueError('本次明确要求不读取该地址。')
        path=Path(value).absolute()
        if len(path.parts)<3 or not path.exists() or private_name(path):raise ValueError('请指定具体资料文件；凭据和密钥不交给工作任务。')
        store=ready();label=Path('external')/(str(len(exports))+'-'+path.name)
        if path.is_dir():target=store.tree(path,label)
        else:target=store.copy(path,path.parent,label)
        if target:exports.append(str(target))
    grants=[str(snapshot.directory)] if snapshot is not None else []
    return {'read_only_paths':grants,'input_copies':exports,'source_files_never_writable':True,
        'excluded_files':sorted(set(snapshot.excluded)) if snapshot else [],'host_owned_snapshot':True}

def permission_config(folder,read_paths=()):
    fs={':root':'deny',':minimal':'read',str(Path(folder).resolve()):'write'}
    runtime=(Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies')
    if runtime.exists():fs[str(runtime)]='read'
    for path in read_paths:fs[str(Path(path).resolve())]='read'
    private=Path(__file__).resolve().parent
    for path in [private/'settings.local.json',private/'agent.local.json',private/'qq.local.json',(Path.home()/'.codex/auth.json'),(Path.home()/'.codex/config.toml')]:fs[str(path)]='deny'
    return {'permissions':{'qq_task':{'extends':':workspace','description':'Only this task folder is writable. Outside data needs explicit read grants.','filesystem':fs,'network':{'enabled':False}}}}
