"""Recoverable work-thread slots. QQ identities and contexts remain isolated."""
import json,time

ARCHIVED="NOT EXISTS(SELECT 1 FROM work_thread_archives a WHERE a.peer=jobs.peer AND a.bot=jobs.bot AND a.thread_id=jobs.thread_id AND a.state='archived')"

def count(db,peer,bot):
    active=db.execute("SELECT count(DISTINCT thread_id) FROM jobs WHERE peer=? AND bot=? AND thread_id!='' AND "+ARCHIVED,(peer,bot)).fetchone()[0]
    reserved=db.execute("SELECT count(*) FROM jobs WHERE peer=? AND bot=? AND state IN ('accepted','running') AND kind!='sing_song' AND thread_id='' AND COALESCE(json_extract(args,'$.thread_id'),'')=''",(peer,bot)).fetchone()[0]
    return active+reserved

def archived(db,peer,bot,tid):
    return bool(db.execute("SELECT 1 FROM work_thread_archives WHERE peer=? AND bot=? AND thread_id=? AND state='archived'",(peer,bot,tid)).fetchone())

def reclaim(service,db,peer,bot,*,exclude=()):
    if not hasattr(service.workbench,'archive_thread'):raise ValueError('工作对话已满，当前工作台不支持可恢复归档。')
    rows=db.execute("SELECT thread_id,min(directory) AS directory,max(updated) AS used FROM jobs WHERE peer=? AND bot=? AND thread_id!='' AND "+ARCHIVED+" GROUP BY thread_id HAVING max(CASE WHEN state NOT IN ('completed','cancelled') OR delivery IN ('pending','sending','uncertain') THEN 1 ELSE 0 END)=0 ORDER BY used ASC",(peer,bot)).fetchall()
    for row in rows:
        if row['thread_id'] in exclude or time.time()-row['used']<1800:continue
        # No task may be queued/running against the same persisted thread.
        busy=db.execute("SELECT 1 FROM jobs WHERE state IN ('accepted','running') AND (thread_id=? OR json_extract(args,'$.thread_id')=?)",(row['thread_id'],row['thread_id'])).fetchone()
        if busy:continue
        try:proof=service.workbench.archive_thread(row['thread_id'],row['directory'],idle_seconds=1800)
        except Exception as exc:
            service.ledger.record('thread_archive_skipped',peer=peer,state='skipped',details={'thread_id':row['thread_id'],'reason':str(exc)[:160]});continue
        if proof.get('archived') is not True:continue
        db.execute("INSERT INTO work_thread_archives(peer,bot,thread_id,directory,title,state,at) VALUES(?,?,?,?,?,'archived',?) ON CONFLICT(peer,bot,thread_id) DO UPDATE SET state='archived',at=excluded.at,title=excluded.title",(peer,bot,row['thread_id'],row['directory'],proof.get('title',''),time.time()))
        service.ledger.record('work_thread_archived',peer=peer,state='archived',details={**proof,'history_and_files_preserved':True,'reason':'oldest completed idle owned work thread; release one active slot'})
        return proof
    raise ValueError('工作对话已满，暂时没有已完成且空闲足够久的本人旧任务可归档；先等当前任务结束。')
