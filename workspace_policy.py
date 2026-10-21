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
    if not re.search('读|看|查|检查|分析|整理|总结|使用|参考|导出',text):raise ValueError('读取资料须由你在本次明确提出。')
    all_db=source in FILES and bool(re.search('全部数据库|所有数据库|之前.*数据库',text))
    if not all_db and not any(a.casefold() in text.casefold() for a in ALIASES[source]):raise ValueError('本次来信没有要求读取“'+source+'”。')
    fragments=re.split('[，,。；;\n]',text)
    relevant=[f for f in fragments if any(a.casefold() in f.casefold() for a in ALIASES[source]) or all_db and re.search('全部数据库|所有数据库|之前.*数据库',f)]
    if relevant and all(re.search(r'(?:不要|不用|无需|无须|禁止|不许|不得|不能|别|不)(?:再|自行|自己|直接|擅自|主动)?(?:读取?|查看?|看|使用|访问|检查|分析)',f) for f in relevant):raise ValueError('本次明确要求不读取该资料。')

def prepare_inputs(root,project,folder,text,sources=None,paths=None):
    root=Path(root);folder=Path(folder).resolve();inputs=folder/'inputs';grants=[];exports=[]
    for source in sources or []:
        validate_read_request(text,source);inputs.mkdir(exist_ok=True)
        if source in FILES:
            original=root/'data/qq'/FILES[source];target=inputs/FILES[source]
            if not original.exists():raise ValueError('该数据库还没有建立。')
            with closing(sqlite3.connect(original.resolve().as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(target)) as dst:src.backup(dst)
            exports.append(str(target))
        elif source=='项目资料':
            base=Path(project)/'sources';amount=0
            for original in base.rglob('*'):
                if not original.is_file() or original.is_symlink():continue
                amount+=original.stat().st_size
                if original.stat().st_size>5*1024**2 or amount>30*1024**2:raise ValueError('项目资料较大，请指定本次需要的文件。')
                target=inputs/'project'/original.relative_to(base);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(original,target)
            exports.append(str(inputs/'project'))
        elif source=='项目代码':
            target=inputs/'project-code';target.mkdir(exist_ok=True)
            # Only application sources and public metadata; no credentials, data, weights or caches.
            for original in root.iterdir():
                if original.is_file() and original.suffix in {'.py','.html','.js','.md','.ps1'}:shutil.copy2(original,target/original.name)
            for name in ['tests','tool-guides','configs']:
                base=root/name
                if not base.exists():continue
                for original in base.rglob('*'):
                    if not original.is_file() or original.is_symlink() or '__pycache__' in original.parts or '.local.' in original.name:continue
                    if original.suffix not in {'.py','.html','.js','.md','.json','.txt'} or original.stat().st_size>2*1024**2:continue
                    dest=target/name/original.relative_to(base);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(original,dest)
            exports.append(str(target))
        elif source=='表情包目录':
            target=inputs/'sticker-index.json';shutil.copy2(root/'assets/stickers/catalog.json',target);exports.append(str(target))
    for value in paths or []:
        if value.replace('\\','/').casefold() not in text.replace('\\','/').casefold():raise ValueError('只读路径必须出现在你本次的原话里。')
        if not re.search('读|看|检查|分析|使用|参考|总结',text):raise ValueError('本次未要求读取外部文件。')
        matching=[f for f in re.split('[，,。；;\n]',text) if value.replace('\\','/').casefold() in f.replace('\\','/').casefold()]
        if matching and all(re.search(r'(?:不要|不用|无需|无须|禁止|不许|不得|不能|别|不)(?:再|自行|自己|直接|擅自|主动)?(?:读取?|查看?|看|使用|访问|检查|分析)',f) for f in matching):raise ValueError('本次明确要求不读取该地址。')
        path=Path(value).resolve()
        if len(path.parts)<3 or not path.exists() or path.is_symlink():raise ValueError('请指定实际文件或具体资料目录。')
        if path.name.casefold() in {'auth.json','settings.local.json','agent.local.json','qq.local.json','config.toml'} or path.suffix.casefold() in {'.pem','.key','.pfx','.env'}:raise ValueError('凭据和密钥文件不提供给工作任务。')
        grants.append(str(path))
    return {'read_only_paths':grants,'input_copies':exports,'source_files_never_writable':True}

def permission_config(folder,read_paths=()):
    fs={':root':'deny',':minimal':'read',str(Path(folder).resolve()):'write'}
    runtime=(Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies')
    if runtime.exists():fs[str(runtime)]='read'
    for path in read_paths:fs[str(Path(path).resolve())]='read'
    private=Path(__file__).resolve().parent
    for path in [private/'settings.local.json',private/'agent.local.json',private/'qq.local.json',(Path.home()/'.codex/auth.json'),(Path.home()/'.codex/config.toml')]:fs[str(path)]='deny'
    return {'permissions':{'qq_task':{'extends':':workspace','description':'Only this task folder is writable. Outside data needs explicit read grants.','filesystem':fs,'network':{'enabled':False}}}}
