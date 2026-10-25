"""Durable file rollback after a lost updater; never roll back newer chat data."""
from pathlib import Path
import hashlib,json,os,time
from self_update import destination,application_path

PRIVATE_FILES={'settings.local.json','data/setup/python-environment.local.json','.runtime/dependencies.sha256'}

def digest(path):
    path=Path(path)
    if not path.exists():return None
    if not path.is_file() or path.stat().st_nlink!=1:raise ValueError('升级路径不是独立文件，保留现场。')
    return hashlib.sha256(path.read_bytes()).hexdigest()

def durable(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.journal-tmp')
    with temporary.open('w',encoding='utf-8') as out:
        json.dump(value,out,ensure_ascii=False,indent=2);out.flush();os.fsync(out.fileno())
    os.replace(temporary,path)

def database_state(root):
    root=Path(root);data=root/'data';result={}
    if not data.is_dir():return result
    for path in data.rglob('*'):
        relative=path.relative_to(root).as_posix()
        if any(p in {'update-backups','update-staging','read-snapshots','account-backups'} for p in path.parts):continue
        if path.is_file() and (path.suffix in {'.sqlite','.sqlite3','.db'} or path.name.endswith(('.sqlite-wal','.sqlite3-wal','.db-wal'))):
            result[relative]=digest(destination(root,relative))
    return result

class FileTransaction:
    def __init__(self,root,backup):
        self.root=Path(root).resolve();self.backup=Path(backup).resolve()
        relative=self.backup.relative_to(self.root).as_posix()
        if not relative.startswith('data/update-backups/'):raise ValueError('恢复备份位置不属于本安装。')
        destination(self.root,relative)
        self.path=destination(self.backup,'file-transaction.private.json')
        self.data=None

    def prepare(self,changed,stage):
        rows=[]
        for item in changed:
            name=item['name'];application_path(name)
            original=destination(self.root,name);saved=destination(self.backup/'application',name)
            before=digest(original)
            if before!=digest(saved):raise ValueError('升级前文件与备份不一致，保留原版。')
            rows.append({'name':name,'before':before,'after':digest(destination(stage,name)),'backup':'application/'+name,'kind':'application'})
        self.data={'version':1,'root':str(self.root),'state':'switching','at':time.time(),'files':rows,'databases':database_state(self.root)}
        durable(self.path,self.data)

    def private_intent(self,name,content):
        if name not in PRIVATE_FILES:raise ValueError('该文件不在升级配置范围内。')
        path=destination(self.root,name);saved=destination(self.backup/'transaction-private',name)
        before=digest(path)
        if before is not None:
            saved.parent.mkdir(parents=True,exist_ok=True)
            with saved.open('wb') as out:out.write(path.read_bytes());out.flush();os.fsync(out.fileno())
        self.data['files'].append({'name':name,'before':before,'after':hashlib.sha256(content).hexdigest(),'backup':'transaction-private/'+name,'kind':'private'})
        durable(self.path,self.data)

    def finish(self,state):
        if self.data is not None:self.data['state']=state;durable(self.path,self.data)

    def recover(self):
        data=json.loads(self.path.read_text('utf-8'))
        if data.get('version')!=1 or data.get('root')!=str(self.root):raise ValueError('恢复记录不属于本安装。')
        if data.get('state')=='recovered':return False
        if data.get('state')!='switching':raise ValueError('这个升级事务已经完成，不能覆盖恢复。')
        if database_state(self.root)!=data.get('databases'):raise ValueError('升级后数据库已变化，已保留新数据；需要人工检查，未回滚覆盖。')
        planned=[]
        # Validate ALL targets and backup hashes before restoring any file. After
        # a second power loss, old/new hash matching makes restoration idempotent.
        for row in data['files']:
            name=row['name']
            if row['kind']=='application':application_path(name)
            elif name not in PRIVATE_FILES:raise ValueError('恢复记录包含越界配置。')
            path=destination(self.root,name);saved=destination(self.backup,row['backup'])
            if digest(path) not in {row['before'],row['after']}:raise ValueError('升级后文件已被修改，保留当前内容。')
            if digest(saved)!=row['before']:raise ValueError('备份校验失败，未恢复文件。')
            planned.append((path,saved,row['before']))
        for path,saved,before in reversed(planned):
            if digest(path)==before:continue
            if before is None:path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True,exist_ok=True)
                temporary=path.with_name(path.name+'.recover-tmp')
                with temporary.open('wb') as out:out.write(saved.read_bytes());out.flush();os.fsync(out.fileno())
                os.replace(temporary,path)
        data['state']='recovered';durable(self.path,data);self.data=data
        return True
