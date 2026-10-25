import hashlib,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from update_transaction import FileTransaction,database_state

class TransactionTests(unittest.TestCase):
    def fixture(self,folder):
        root=Path(folder);backup=root/'data/update-backups/100-v0.4.0';stage=root/'data/update-staging/new'
        for parent in [backup/'application',stage,root/'data/qq']:parent.mkdir(parents=True)
        for name in ['phone_server.py','voice_server.py']:
            (root/name).write_text('old '+name);(backup/'application'/name).write_text('old '+name);(stage/name).write_text('new '+name)
        (root/'data/qq/memory.db').write_bytes(b'unchanged private fixture')
        transaction=FileTransaction(root,backup)
        transaction.prepare([{'name':n,'existed':True} for n in ['phone_server.py','voice_server.py']],stage)
        return root,backup,stage,transaction
    def test_half_switched_process_crash_is_recovered_idempotently(self):
        with tempfile.TemporaryDirectory() as folder:
            root,backup,stage,transaction=self.fixture(folder)
            # A real child exits without exception handlers or cleanup, midway
            # through the same file replacement boundaries as the updater.
            code="import os,pathlib; r=pathlib.Path(__import__('sys').argv[1]); (r/'phone_server.py').write_text('new phone_server.py'); os._exit(37)"
            self.assertEqual(subprocess.run([sys.executable,'-c',code,str(root)]).returncode,37)
            before=database_state(root)
            self.assertTrue(FileTransaction(root,backup).recover())
            self.assertEqual((root/'phone_server.py').read_text(),'old phone_server.py')
            self.assertEqual((root/'voice_server.py').read_text(),'old voice_server.py')
            self.assertEqual(database_state(root),before)
            self.assertFalse(FileTransaction(root,backup).recover())
    def test_changed_database_or_application_blocks_all_restoration(self):
        for database in [True,False]:
            with self.subTest(database=database),tempfile.TemporaryDirectory() as folder:
                root,backup,stage,transaction=self.fixture(folder)
                (root/'phone_server.py').write_text('new phone_server.py')
                (root/('data/qq/memory.db' if database else 'voice_server.py')).write_text('new user data')
                with self.assertRaises(ValueError):transaction.recover()
                self.assertEqual((root/'phone_server.py').read_text(),'new phone_server.py')
    def test_private_settings_intent_handles_crash_before_or_after_write(self):
        for applied in [False,True]:
            with self.subTest(applied=applied),tempfile.TemporaryDirectory() as folder:
                root,backup,stage,transaction=self.fixture(folder)
                (root/'settings.local.json').write_bytes(b'old private fixture')
                transaction.private_intent('settings.local.json',b'new private fixture')
                if applied:(root/'settings.local.json').write_bytes(b'new private fixture')
                self.assertTrue(transaction.recover())
                self.assertEqual((root/'settings.local.json').read_bytes(),b'old private fixture')
    def test_tampered_backup_and_link_never_restore(self):
        with tempfile.TemporaryDirectory() as folder:
            root,backup,stage,transaction=self.fixture(folder)
            (backup/'application/voice_server.py').write_text('tampered')
            with self.assertRaises(ValueError):transaction.recover()
            self.assertEqual((root/'phone_server.py').read_text(),'old phone_server.py')
        with tempfile.TemporaryDirectory() as folder:
            root,backup,stage,transaction=self.fixture(folder)
            os.link(root/'phone_server.py',root/'another-file')
            with self.assertRaises(ValueError):transaction.recover()
    def test_completed_transaction_cannot_be_rolled_back(self):
        with tempfile.TemporaryDirectory() as folder:
            root,backup,stage,transaction=self.fixture(folder);transaction.finish('succeeded')
            with self.assertRaises(ValueError):transaction.recover()

    def test_independent_qq_client_data_is_not_a_rollback_target_or_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            root,backup,stage,transaction=self.fixture(folder)
            folder=root/'data/qq-client';folder.mkdir()
            qq=folder/'client.db';qq.write_bytes(b'new independent QQ state')
            (root/'phone_server.py').write_text('new phone_server.py')
            self.assertTrue(transaction.recover())
            self.assertEqual(qq.read_bytes(),b'new independent QQ state')
            self.assertEqual((root/'phone_server.py').read_text(),'old phone_server.py')

if __name__=='__main__':unittest.main()
