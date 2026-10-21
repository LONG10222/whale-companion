"""Meaningful protocol and isolation checks; no account or API credentials used."""
from pathlib import Path
import json, tempfile, time, unittest
from unittest.mock import patch
from mobile_bridge import MobileBridge, MobileError, parse_screen, PACKAGE
from mobile_tools import execute
import mobile_agent


def screen_xml(nodes):
    return '<hierarchy>' + ''.join('<node package="'+PACKAGE+'" enabled="true" bounds="[0,0][200,100]" '+n+'/>' for n in nodes) + '</hierarchy>'


class MobileSafetyTests(unittest.TestCase):
    def test_search_post_title_is_distinguished_from_miniapp_title(self):
        xml = '<hierarchy><node resource-id="'+PACKAGE+':id/ll_container"><node><node package="'+PACKAGE+'" resource-id="'+PACKAGE+':id/tv_title" text="真实帖子标题" bounds="[0,0][200,100]"/><node package="'+PACKAGE+'" resource-id="'+PACKAGE+':id/tv_content" text="帖子正文预览" bounds="[0,100][200,200]"/></node></node><node package="'+PACKAGE+'" resource-id="'+PACKAGE+':id/tv_title" text="鸣潮角色绑定" bounds="[0,300][200,400]"/></hierarchy>'
        nodes,_,_ = parse_screen(xml)
        self.assertTrue(nodes[0]['post_card'])
        self.assertFalse(nodes[-1]['post_card'])

    def test_android_15_foreground_format(self):
        with tempfile.TemporaryDirectory() as td:
            b = MobileBridge(td)
            with patch.object(b, '_adb', return_value='    topResumedActivity=ActivityRecord{30534759 u0 com.max.xiaoheihe/.module.account.RegisterOrLoginActivityV2 t7}'):
                self.assertEqual(b.foreground(), PACKAGE)

    def test_login_and_password_never_become_readable_screen(self):
        nodes, texts, blocked = parse_screen(screen_xml([
            'text="验证码登录" resource-id="'+PACKAGE+':id/tv_title"',
            'text="123456" password="true" class="android.widget.EditText"']))
        self.assertEqual(blocked, 'login_required')
        self.assertNotIn('123456', texts)

    def test_publish_and_message_inputs_are_not_actionable(self):
        nodes, _, _ = parse_screen(screen_xml([
            'text="发布" resource-id="'+PACKAGE+':id/tv_publish" clickable="true"',
            'text="发送" resource-id="'+PACKAGE+':id/tv_send" clickable="true"',
            'text="" resource-id="'+PACKAGE+':id/et_comment" class="android.widget.EditText"',
            'text="搜索" resource-id="'+PACKAGE+':id/ib_search" clickable="true"']))
        self.assertEqual([n['role'] for n in nodes], ['text', 'text', 'text', 'search'])

    def test_model_cannot_select_other_app_or_shell(self):
        with tempfile.TemporaryDirectory() as td:
            b = MobileBridge(td)
            with self.assertRaises(ValueError):
                execute(b, 'shell', {'command': 'anything'})
            with self.assertRaises(Exception):
                execute(b, 'mobile_open', {'package': 'com.tencent.mm'})
            with self.assertRaises(MobileError):
                b.search('鸣潮; am start evil')

    def test_instance_identity_mismatch_blocks_before_adb(self):
        with tempfile.TemporaryDirectory() as td:
            b = MobileBridge(td)
            with patch.object(b, 'config', return_value={'mumu_cli': 'cli', 'instance': 1, 'created_timestamp': '123', 'name': 'test'}), \
                 patch.object(b, '_run', return_value=json.dumps({'index': 0, 'created_timestamp': 123, 'name': 'test'})):
                with self.assertRaises(MobileError):
                    b._serial()

    def test_login_is_blocked_before_screenshot(self):
        with tempfile.TemporaryDirectory() as td:
            b = MobileBridge(td)
            xml = screen_xml(['text="验证码登录" resource-id="'+PACKAGE+':id/tv_title"'])
            def adb(*args, **kwargs):
                if args[:2] == ('exec-out', 'screencap'):
                    self.fail('Login screenshot must never be captured')
                return xml if args[:2] == ('shell', 'cat') else ''
            with patch.object(b, 'foreground', return_value=PACKAGE), patch.object(b, 'hierarchy', return_value=xml), patch.object(b, '_adb', side_effect=adb):
                self.assertEqual(b.observe()['state'], 'login_required')
                self.assertEqual(list(b.cache.iterdir()), [])

    def test_animation_dump_success_does_not_generalize_to_command_failure(self):
        import subprocess
        with tempfile.TemporaryDirectory() as td:
            b = MobileBridge(td)
            args = ['adb', 'shell', 'uiautomator', 'dump', '/sdcard/companion-view.xml']
            r = subprocess.CompletedProcess(args, 139, b'UI hierchary dumped to: /sdcard/companion-view.xml', b'')
            with patch('mobile_bridge.subprocess.run', return_value=r):
                self.assertIn('dumped', b._run(args))
                with self.assertRaises(MobileError):
                    b._run(['adb', 'shell', 'am', 'start', 'anything'])

    def test_tap_rechecks_target_in_current_page(self):
        with tempfile.TemporaryDirectory() as td:
            b = MobileBridge(td)
            node = {'text':'', 'resource_id':'iv_home_search', 'class':'android.widget.ImageView', 'bounds':(600,40,660,100)}
            with patch.object(b,'foreground',return_value=PACKAGE), patch.object(b,'hierarchy',return_value=screen_xml(['text="发布" resource-id="'+PACKAGE+':id/tv_publish"'])), patch.object(b,'_adb') as adb:
                with self.assertRaises(MobileError):
                    b._tap(node)
                adb.assert_not_called()

    def test_stale_or_changed_node_is_not_clicked(self):
        with tempfile.TemporaryDirectory() as td:
            b = MobileBridge(td)
            b.last = {'snapshot_id': 'a'*32, 'captured_at': time.time()-100, 'nodes': []}
            with self.assertRaises(MobileError):
                b.navigate('a'*32, '1')
            b.last = {'snapshot_id': 'a'*32, 'captured_at': time.time(), 'xml_hash': 'old',
                      'nodes': [{'id': '1', 'enabled': True, 'role': 'post', 'resource_id': 'tv_title', 'class': 'TextView', 'text': 'original post', 'bounds': (0,0,100,100)}]}
            def changed():
                b.last = {'xml_hash': 'new', 'nodes': []}
                return {'ok': True}
            with patch.object(b, 'observe', side_effect=changed), patch.object(b, '_tap') as tap:
                with self.assertRaises(MobileError):
                    b.navigate('a'*32, '1')
                tap.assert_not_called()

    def test_draft_requires_current_reading_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            b = MobileBridge(td)
            with self.assertRaises(MobileError):
                b.save_draft('标题', '正文内容。'*10, ['a'*32])
            b.evidence = [{'snapshot_id': 'a'*32, 'title': '实际打开的帖子', 'text': '实际内容'}]
            out = b.save_draft('标题', '正文内容。'*10, ['a'*32])
            self.assertFalse(out['published'])
            self.assertEqual(len(list((Path(td)/'mobile-drafts').glob('*.json'))), 1)

    def test_image_post_records_real_page_evidence_even_with_short_text(self):
        import io
        from PIL import Image
        picture = io.BytesIO()
        Image.new('RGB', (100,200)).save(picture,format='PNG')
        with tempfile.TemporaryDirectory() as td:
            b = MobileBridge(td)
            b.pending_post = '实际打开的图片帖'
            xml = screen_xml(['text="正文" resource-id="'+PACKAGE+':id/tv_tab_title"',
                              'text="来说点什么吧" resource-id="'+PACKAGE+':id/et_editor" class="android.widget.EditText"'])
            with patch.object(b,'foreground',return_value=PACKAGE),patch.object(b,'hierarchy',return_value=xml),patch.object(b,'_adb',return_value=picture.getvalue()),patch.object(b,'config',return_value={}):
                out = b.observe()
            self.assertEqual(out['page_kind'],'post_detail')
            self.assertEqual(out['reading_sources'][0]['title'],b.pending_post)
            self.assertTrue(b.evidence[0]['image_sha256'])

    def test_cancel_after_model_reply_prevents_actions(self):
        with tempfile.TemporaryDirectory() as td:
            b = MobileBridge(td)
            stop = False
            def client(*args):
                nonlocal stop
                stop = True
                return {'tool_calls': [{'id': 'x', 'function': {'name': 'mobile_open', 'arguments': '{}'}}]}, {}
            with patch.object(b, 'open') as opening:
                out = mobile_agent.run('搜索', b, client=client, cancelled=lambda: stop)
                opening.assert_not_called()
            self.assertEqual(out['state'], 'cancelled')
            self.assertFalse(out['deepseek_api_called'])

    def test_parallel_action_plan_stops_before_executing(self):
        with tempfile.TemporaryDirectory() as td:
            b = MobileBridge(td)
            call = {'id': 'x', 'function': {'name': 'mobile_open', 'arguments': '{}'}}
            with patch.object(b, 'open') as opening:
                out = mobile_agent.run('搜索', b, client=lambda *args: ({'tool_calls': [call, call]}, {}))
                opening.assert_not_called()
            self.assertEqual(out['state'], 'invalid_plan')

    def test_post_body_image_survives_the_following_scroll(self):
        with tempfile.TemporaryDirectory() as td:
            b = MobileBridge(td)
            captured = []
            index = 0
            def client(messages, tools):
                nonlocal index
                captured.append(messages.copy())
                index += 1
                if index <= 4:
                    return {'tool_calls':[{'id':str(index),'function':{'name':'mobile_observe','arguments':'{}'}}]},{}
                return {'content':'结束协议测试'},{}
            screens = iter([{'ok':True,'snapshot_id':s*32} for s in 'abcd'])
            with patch('mobile_agent.execute',side_effect=lambda *args: next(screens)),patch.object(b,'image_part',side_effect=lambda sid:{'type':'image_url','image_url':{'url':sid}}):
                mobile_agent.run('测试',b,client=client)
            images = [m['content'][1]['image_url']['url'] for m in captured[-1] if m['role']=='user' and isinstance(m['content'],list)]
            self.assertEqual(images,['b'*32,'c'*32,'d'*32])

    def test_review_cannot_send_fabricated_sources_to_provider(self):
        with tempfile.TemporaryDirectory() as td:
            b=MobileBridge(td)
            args={'title':'标题','body':'正文内容。'*10,'source_snapshot_ids':['a'*32]}
            with patch('mobile_agent.deepseek_client.api_post') as api:
                review=mobile_agent.review_draft(b,args)
                self.assertFalse(review['passed'])
                api.assert_not_called()

    def test_review_rejection_is_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            b=MobileBridge(td)
            b.evidence=[{'snapshot_id':'a'*32,'title':'真实来源标题','text':'原文'}]
            args={'title':'标题','body':'正文内容。'*10,'source_snapshot_ids':['a'*32]}
            response={'choices':[{'message':{'content':json.dumps({'passed':False,'issues':['误读正文图片']})}}]}
            with patch('mobile_agent.deepseek_client.api_post',return_value=response):
                review=mobile_agent.review_draft(b,args)
            self.assertFalse(review['passed'])
            self.assertEqual(review['issues'],['误读正文图片'])


if __name__ == '__main__':
    unittest.main()
