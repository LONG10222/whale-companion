"""Restart-time recovery for this installation only; no QQ login or message sends."""
from pathlib import Path
import argparse,os,re,sys,time
ROOT=Path(__file__).resolve().parent.parent
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
from self_update import read,atomic,destination,reported_update_progress,updater_is_alive,active_tasks
from update_transaction import FileTransaction

def recover(root):
    root=Path(root).resolve()
    progress_file=root/'data/setup/update-progress.local.json'
    original=read(progress_file,{})
    progress=reported_update_progress(root,original,time.time())
    if progress.get('phase')!='recovery_required':return {'state':'not_needed'}
    if updater_is_alive(root,original.get('worker')) is not False:raise ValueError('升级进程状态尚不能确认，请稍后再试。')
    operation=original.get('operation','')
    if not re.fullmatch(r'\d+-v\d+\.\d+\.\d+',operation):raise ValueError('旧升级缺少可验证的事务记录，保留备份供人工检查。')
    backup=destination(root,'data/update-backups/'+operation)
    if not (backup/'file-transaction.private.json').is_file():raise ValueError('旧升级缺少文件事务，未自动覆盖。')
    from update_worker import Upgrade
    job=Upgrade(root)
    # Stop only idle application services. The independent QQ client stays alive.
    if active_tasks(root)!=0 or not job.voice_idle() or not job.engine_idle():raise ValueError('仍有本机任务运行，暂不恢复覆盖。')
    desired=read(root/'configs/runtime-control.local.json',{}).get('desired')
    for kind in ['host','api','voice','astrbot']:job.stop_owned(kind)
    FileTransaction(root,backup).recover()
    job.settings=read(root/'settings.local.json');job.backup=backup
    original_qq=read(backup/'private/qq.local.json',{})
    # A paused installation remains paused. A previously running installation
    # validates the prior version before reopening replies.
    if desired=='stopped':
        atomic(progress_file,{'phase':'recovered_paused','at':time.time(),'operation':operation,'message':'已校验恢复原版，保留暂停状态。'})
    else:
        job.id=operation
        job.status('validating','原版文件已恢复，正在检查服务。')
        job.start()
        atomic(progress_file,{'phase':'rolled_back','at':time.time(),'operation':operation,'message':'已校验恢复中断升级前的程序；数据库未回滚。'})
        if original_qq:job.set_replies(bool(original_qq.get('enabled')))
    (root/'data/setup/update-request.lock').unlink(missing_ok=True)
    return {'state':'recovered','database_restored':False,'qq_client_restarted':False}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True);options=parser.parse_args()
    if options.root.resolve()!=ROOT:raise SystemExit('Recovery root mismatch')
    # OS lock releases on a crash; the harmless lock file may remain on disk.
    guard=destination(ROOT,'data/setup/update-recovery.lock');guard.parent.mkdir(parents=True,exist_ok=True)
    with guard.open('a+b') as stream:
        if stream.tell()==0:stream.write(b'0');stream.flush()
        stream.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            print(recover(ROOT))
        except Exception as exc:
            print('Interrupted update retained for safe recovery: '+(str(exc)[:160] if isinstance(exc,ValueError) else type(exc).__name__))
            raise SystemExit(2)
