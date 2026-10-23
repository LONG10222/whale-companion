from pathlib import Path
from types import SimpleNamespace
import io,json,tempfile,unittest
from PIL import Image
from qq_bridge import QQBridge
from qq_chat import QQConversation
from qq_creative import CreativeJobs
from creative_tools import requested_tool
from draw_references import prepare_user_references


class ImageEditTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.peer='87654321';self.bot='12345678'
        self.chat=QQConversation(self.root,client=lambda *_:({'content':'好。'},{}));self.bridge=QQBridge(self.root,self.chat)
        self.bridge.cfg.update(locked_bot_id=self.bot,test_contact={'name':'long','user_id':self.peer});self.bridge.configure(True,self.bot,[self.peer],False)
        self.bridge.connected=True;self.bridge.login_id=self.bot;self.bridge.epoch='unit';self.calls=[]
        output=io.BytesIO();Image.new('RGB',(64,64),'blue').save(output,format='PNG');self.image=self.chat.images.prepare(output.getvalue())
        mid=self.chat.memory.add(self.peer,'user','测试原图');self.chat.memory.remember_images(self.peer,mid,[self.image])
        self.folder=self.root/'work';self.folder.mkdir()
        def run(jid,task,**kw):
            self.calls.append(kw);folder=self.folder/jid;folder.mkdir(exist_ok=True);file=folder/'generated.png';Image.new('RGB',(64,64),'cyan').save(file)
            return {'text':'生成成功','images':[str(file)],'thread_id':jid,'directory':str(folder),'model':kw['model'],'effort':kw['effort']}
        self.service=CreativeJobs(self.root,self.bridge,resources=lambda:{'ram_free_mib':16000,'gpu_free_mib':14000,'gpu_util_percent':0,'cpu_percent':0},workbench=SimpleNamespace(workspace=self.folder,run=run))
        self.args={'prompt':'给这张图加蓝色特效','model':'gpt-6-luna','effort':'low'}
    def tearDown(self):
        self.service.close();self.service.pool.shutdown(wait=True);self.bridge.group_pool.shutdown(wait=True);self.bridge.pool.shutdown(wait=True);self.tmp.cleanup()
    def test_edit_intent_is_distinct_from_negation_and_explanation(self):
        for text in ['帮我修图','给这张图加特效','P图，换背景']:self.assertEqual(requested_tool(text),'draw_picture')
        for text in ['不要修图','修图是什么意思','如何修图','给语音加特效']:self.assertIsNone(requested_tool(text))
    def test_reference_copy_preserves_source_pixels_and_stays_in_task_directory(self):
        before=Path(self.image['path']).read_bytes();refs=prepare_user_references(self.root,self.folder,[self.image['sha256']]);target=Path(refs[0]['path'])
        self.assertEqual(target.parent,self.folder.resolve());self.assertEqual(Path(self.image['path']).read_bytes(),before)
        with Image.open(target) as got:self.assertEqual(got.convert('RGB').getpixel((10,10)),(0,0,255))
    def test_hashes_cannot_name_arbitrary_files(self):
        for refs in [['../../secret'],['0'*64],[],['a'*64]*4]:
            with self.assertRaises(ValueError):prepare_user_references(self.root,self.folder,refs)
    def test_edit_requires_a_received_original_before_starting_any_job(self):
        with self.assertRaisesRegex(ValueError,'原图'):self.service.request(self.peer,'draw_picture',self.args,'帮我修图加特效')
        self.assertFalse(self.calls)
    def test_own_image_is_passed_to_worker_without_broadening_file_access(self):
        ctx={'source_image_shas':[self.image['sha256']]};r=self.service.request(self.peer,'draw_picture',self.args,'帮我修图加特效',context=ctx)
        self.service.pool.submit(lambda:None).result(5);self.assertTrue(r['accepted']);self.assertEqual(self.calls[0]['source_images'],[self.image['sha256']])
        self.assertEqual(self.calls[0]['read_paths'],[]);self.assertEqual(self.calls[0]['read_sources'],[])
    def test_other_peers_image_is_rejected_before_worker_execution(self):
        with self.chat.memory.db() as db:db.execute('UPDATE image_messages SET peer=?',('76543210',))
        with self.assertRaisesRegex(ValueError,'其他人'):self.service.request(self.peer,'draw_picture',self.args,'帮我修图加特效',context={'source_image_shas':[self.image['sha256']]})
        self.assertFalse(self.calls)
    def test_tampered_cache_cannot_be_used_as_original(self):
        Path(self.image['path']).write_bytes(b'corrupted-original')
        with self.assertRaises(ValueError):self.service.request(self.peer,'draw_picture',self.args,'帮我修图加特效',context={'source_image_shas':[self.image['sha256']]})
        self.assertFalse(self.calls)
    def test_conversation_routes_edit_with_its_received_photo_to_real_job_queue(self):
        self.chat.creative=self.service
        self.chat.images.describe=lambda *_:[]
        self.chat.client=lambda *_:({'content':'','tool_calls':[{'id':'edit-unit-call','type':'function','function':{'name':'draw_picture','arguments':json.dumps(self.args)}}]}, {})
        result=self.chat.prepare(self.peer,'帮我修图加蓝色特效',images=[self.image],remember=True)
        self.service.pool.submit(lambda:None).result(5)
        self.assertTrue(any(t['tool']=='draw_picture' and t['result'].get('accepted') for t in result['tools']))
        self.assertEqual(self.calls[0]['source_images'],[self.image['sha256']])


if __name__=='__main__':unittest.main()
