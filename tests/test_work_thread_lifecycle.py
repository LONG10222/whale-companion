from pathlib import Path
from unittest.mock import patch
import json,time,unittest
from work_thread_lifecycle import reclaim,count,archived

class ThreadLifecycleTests(unittest.TestCase):
    def setUp(self):
        from tests.test_image_edits import ImageEditTests
        self.case=ImageEditTests();self.case.setUp();self.service=self.case.service;self.calls=[]
        self.service.workbench.archive_thread=lambda tid,directory,**kw:self.calls.append(tid) or {'thread_id':tid,'archived':True,'title':'测试 · 旧任务'}
        self.service.workbench.restore_thread=lambda tid,directory:{'restored':True}
    def tearDown(self):self.case.tearDown()
    def add(self,tid,*,age=7200,state='completed',delivery='sent',peer=None):
        folder=self.case.folder/tid;folder.mkdir(exist_ok=True);(folder/'keep.txt').write_text('history output')
        with self.service.db() as db:db.execute('INSERT INTO jobs(id,peer,bot,mid,epoch,kind,args,state,at,updated,deliver,delivery,result,error,thread_id,directory) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(tid,peer or self.case.peer,self.case.bot,'','','draw_picture','{}',state,time.time()-age,time.time()-age,0,delivery,'','',tid,str(folder)))
    def test_only_oldest_completed_owned_idle_thread_is_archived_and_files_survive(self):
        self.add('oldest',age=9000);self.add('newer',age=7000);self.add('busy',age=10000,state='running');self.add('other',age=11000,peer='76543210')
        with self.service.db() as db:
            reclaim(self.service,db,self.case.peer,self.case.bot);self.assertEqual(self.calls,['oldest']);self.assertTrue(archived(db,self.case.peer,self.case.bot,'oldest'))
            self.assertEqual(count(db,self.case.peer,self.case.bot),2)
        self.assertEqual((self.case.folder/'oldest/keep.txt').read_text(),'history output')
        self.assertEqual(self.service.restore_work_thread('oldest')['restored'],True)
        with self.service.db() as db:self.assertEqual(count(db,self.case.peer,self.case.bot),3)
    def test_pending_receipt_recent_thread_and_other_owner_are_never_reclaimed(self):
        self.add('receipt',delivery='uncertain');self.add('fresh',age=10);self.add('other',peer='76543210')
        with self.service.db() as db:
            with self.assertRaises(ValueError):reclaim(self.service,db,self.case.peer,self.case.bot)
        self.assertFalse(self.calls)
    def test_queued_new_work_reserves_a_slot_before_native_thread_exists(self):
        self.add('pending',age=10,state='accepted',delivery='pending')
        with self.service.db() as db:
            db.execute("UPDATE jobs SET thread_id='' WHERE id='pending'")
            self.assertEqual(count(db,self.case.peer,self.case.bot),1)

if __name__=='__main__':unittest.main()
