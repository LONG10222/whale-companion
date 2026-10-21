import json,tempfile,unittest
from unittest.mock import patch
from mobile_bridge import MobileBridge,MobileError

class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.bridge=MobileBridge(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def test_android_started_flag_does_not_skip_adb_and_boot_readiness(self):
        calls=[];states=['offline','device'];boots=['0','1']
        def run(args,**kw):
            calls.append(args)
            if args[-1]=='get-state':return states.pop(0)
            if args[-1]=='sys.boot_completed':return boots.pop(0)
            return ''
        with patch.object(self.bridge,'info',return_value={'is_android_started':True}),patch.object(self.bridge,'_serial',return_value='127.0.0.1:16384'),patch.object(self.bridge,'config',return_value={'adb':'adb'}),patch.object(self.bridge,'_run',side_effect=run),patch('mobile_bridge.time.sleep'):
            self.assertTrue(self.bridge.wait_ready()['adb_ready'])
        self.assertEqual(sum(a[-1]=='get-state' for a in calls),2)
        self.assertTrue(all('kill-server' not in a and 'reconnect' not in a for a in calls))
    def test_offline_reconnect_is_only_for_the_verified_instance(self):
        calls=[];failed=[False]
        def run(args,**kw):
            calls.append(args)
            if args[-1]=='get-state' and not failed[0]:failed[0]=True;raise MobileError('offline')
            if args[-1]=='get-state':return 'device'
            if args[-1]=='sys.boot_completed':return '1'
            return ''
        with patch.object(self.bridge,'info',return_value={'is_android_started':True}),patch.object(self.bridge,'_serial',return_value='127.0.0.1:16384'),patch.object(self.bridge,'config',return_value={'adb':'adb'}),patch.object(self.bridge,'_run',side_effect=run),patch('mobile_bridge.time.sleep'):
            self.bridge.wait_ready()
        self.assertIn(['adb','disconnect','127.0.0.1:16384'],calls)
        self.assertNotIn(['adb','disconnect'],calls)
    def test_cancel_and_identity_change_stop_before_adb(self):
        with patch.object(self.bridge,'_run') as run:
            with self.assertRaises(MobileError):self.bridge.wait_ready(lambda:True)
            run.assert_not_called()
        with patch.object(self.bridge,'info',side_effect=MobileError('测试手机身份发生变化')),patch.object(self.bridge,'_run') as run:
            with self.assertRaises(MobileError):self.bridge.wait_ready()
            run.assert_not_called()
    def test_shutdown_confirmation_precedes_success_and_cache_clear(self):
        self.bridge._ui_device=object();self.bridge.launched_here=True
        running=[{'is_process_started':True},{'is_process_started':True},{'is_process_started':False}]
        with patch.object(self.bridge,'info',side_effect=running),patch.object(self.bridge,'config',return_value={'mumu_cli':'manager','instance':1}),patch.object(self.bridge,'_run',return_value=json.dumps({'errcode':0})),patch('mobile_bridge.time.sleep') as sleep:
            self.assertFalse(self.bridge.stop()['running']);self.assertEqual(sleep.call_count,1)
        self.assertIsNone(self.bridge._ui_device);self.assertFalse(self.bridge.launched_here)

if __name__=='__main__':unittest.main()
