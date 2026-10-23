from pathlib import Path
from unittest.mock import patch
import io,json,tempfile,unittest
import httpx
from PIL import Image
from qq_chat import QQConversation
from qq_bridge import QQBridge
from types import SimpleNamespace


class GroupFileImageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.admin='87654321';self.bot='12345678';self.gid='11223344';self.other='22334455';self.calls=[]
        self.chat=QQConversation(self.root,client=lambda *_:({'content':'看到照片了。'},{}));self.bridge=QQBridge(self.root,self.chat)
        self.bridge.cfg.update(locked_bot_id=self.bot,test_contact={'name':'long','user_id':self.admin});self.bridge.configure(True,self.bot,[self.admin],False)
        self.bridge.connected=True;self.bridge.login_id=self.bot;self.bridge.epoch='unit';self.bridge.batcher.quiet=0
        self.photo={'file_id':'image-unit','file_name':'photo.JPG','file_size':35*1024**2,'busid':1,'upload_time':20};self.files=[self.photo,{'file_id':'app-unit','file_name':'setup.apk','file_size':100}]
        self.folders=[];self.sent=[]
        def call(action,params=None,**kw):
            self.calls.append((action,params))
            if action=='get_group_list':return [{'group_id':int(self.gid),'group_name':'测试群'},{'group_id':int(self.other),'group_name':'另一个群'}]
            if action=='get_group_root_files':return {'files':list(self.files),'folders':self.folders}
            if action=='get_group_files_by_folder':return {'files':[self.photo]}
            if action=='get_group_file_url':return {'url':'https://download.ftn.qq.com/unit-image'}
            self.sent.append((action,params));return {'message_id':100+len(self.sent)}
        self.bridge.call=call;self.bridge.groups.configure(self.gid,True);self.reader=self.bridge.group_files
        self.original=self.root/'unit-photo.jpg';Image.new('RGB',(64,64),'blue').save(self.original);self.reader.download=lambda _:self.original
    def tearDown(self):self.bridge.close();self.bridge.group_pool.shutdown(wait=True);self.bridge.pool.shutdown(wait=True);self.tmp.cleanup()
    def event(self,text='看下群文件的照片'):
        return {'post_type':'message','message_type':'group','sub_type':'normal','self_id':self.bot,'user_id':self.admin,'group_id':self.gid,'message_id':1,'message':[{'type':'at','data':{'qq':self.bot}},{'type':'text','data':{'text':text}}]}
    def test_authorized_group_photo_is_read_and_non_images_are_never_downloaded(self):
        result=self.reader.select(self.event(),'看看我传到群文件里的照片')
        self.assertEqual(len(result['images']),1);self.assertEqual(result['context']['files'][0]['name'],'photo.JPG')
        self.assertEqual([p['file_id'] for a,p in self.calls if a=='get_group_file_url'],['image-unit'])
    def test_group_cannot_read_other_group_and_ordinary_private_user_cannot_browse_groups(self):
        self.bridge.groups.configure(self.other,True)
        with self.assertRaises(ValueError):self.reader.select(self.event(),'读另一个群照片',group='另一个群')
        event={'self_id':self.bot,'message_type':'private','user_id':'76543210'}
        with self.assertRaises(ValueError):self.reader.select(event,'读群文件',group='测试群')
    def test_multiple_pictures_ask_for_target_without_download(self):
        self.files.append(self.photo|{'file_id':'second','file_name':'second.png','upload_time':10})
        self.reader.download=lambda *_:self.fail('Do not guess a target')
        result=self.reader.select(self.event(),'看看群文件照片');self.assertFalse(result['images']);self.assertIn('哪张',result['reply'])
    def test_unknown_explicit_file_name_does_not_fall_back_to_an_unrelated_photo(self):
        self.reader.download=lambda *_:self.fail('Do not substitute another photo')
        result=self.reader.select(self.event(),'读取群文件other.jpg');self.assertFalse(result['images']);self.assertIn('没找到',result['reply'])
    def test_latest_or_named_picture_selects_one_actual_file(self):
        self.files.append(self.photo|{'file_id':'second','file_name':'second.png','upload_time':10})
        self.reader.select(self.event(),'看看刚传到群文件的那张')
        self.assertEqual([p['file_id'] for a,p in self.calls if a=='get_group_file_url'],['image-unit'])
    def test_explicit_folder_uses_qq_folder_id_not_a_local_path(self):
        self.files=[];self.folders=[{'folder_id':'known-folder','folder_name':'照片集'}]
        self.assertTrue(self.reader.select(self.event(),'看看群文件照片集里的图')['images'])
        self.assertTrue(any(a=='get_group_files_by_folder' and p['folder_id']=='known-folder' for a,p in self.calls))
    def test_oversized_file_and_fake_image_are_rejected_without_execution(self):
        with self.assertRaises(ValueError):self.reader.fetch(self.gid,self.photo|{'file_size':65*1024**2})
        self.assertFalse(any(a=='get_group_file_url' for a,_ in self.calls))
        self.original.write_bytes(b'not an image or executable to run')
        with self.assertRaises(Exception):self.reader.fetch(self.gid,self.photo)
    def test_large_resolution_is_reduced_and_original_not_modified(self):
        Image.new('RGB',(6000,4000),'blue').save(self.original);before=self.original.read_bytes();picture=self.reader.prepare(self.original)
        with Image.open(picture['path']) as got:self.assertLessEqual(max(got.size),4096);self.assertLessEqual(got.width*got.height,20_000_000)
        self.assertEqual(self.original.read_bytes(),before)
    def test_non_qq_download_url_is_rejected(self):
        old=self.bridge.call;self.bridge.call=lambda action,params=None,**kw:({'url':'http://127.0.0.1/private'} if action=='get_group_file_url' else old(action,params,**kw))
        self.reader.download=lambda *_:self.fail('No SSRF')
        with self.assertRaises(ValueError):self.reader.fetch(self.gid,self.photo)
    def test_group_file_request_uses_same_image_pipeline_and_keeps_real_user_words(self):
        captured=[]
        self.chat.prepare=lambda peer,text,**kw:captured.append((peer,text,kw)) or {'reply':'看到了。','attachments':[],'tools':[]}
        self.assertTrue(self.bridge.accept(self.event('我发到群文件里了'),'unit'));self.bridge.group_pool.submit(lambda:None).result(6)
        self.assertEqual(len(captured),1);self.assertTrue(captured[0][2]['images']);self.assertIn('file_context',captured[0][2]);self.assertEqual(captured[0][1],'我发到群文件里了')
    def test_natural_model_tool_can_select_file_without_fixed_group_file_phrase(self):
        peer='group:'+self.gid+':'+self.admin;text='就看刚才传进去那张';args={'group':'','file_name':'photo.JPG','latest':True,'evidence':text}
        replies=iter([{'tool_calls':[{'id':'natural-file','type':'function','function':{'name':'read_group_images','arguments':json.dumps(args,ensure_ascii=False)}}]},{'content':'看到这张照片了。'}])
        self.chat.client=lambda *_:(next(replies),{});self.chat.images.describe=lambda *_:[{'image_kind':'photo','id':'unit','description':'测试照片','text':''}]
        event=self.event(text)
        with patch('deepseek_client.complete',return_value=({'content':'{"authorized":true}'},{})):
            self.assertTrue(self.bridge.accept(event,'unit'));self.bridge.group_pool.submit(lambda:None).result(6)
        self.assertTrue(self.chat.memory.latest_images(peer));self.assertTrue(self.sent)
    def test_edit_request_receives_real_catalog_without_downloading(self):
        captured=[];self.reader.download=lambda *_:self.fail('Catalog must not download')
        self.chat.prepare=lambda peer,text,**kw:captured.append(kw) or {'reply':'先看群文件。','attachments':[],'tools':[]}
        self.assertTrue(self.bridge.accept(self.event('修图，保留主体人物，去路人，添加特效，合适的都可以'),'unit'))
        self.bridge.group_pool.submit(lambda:None).result(6)
        catalog=captured[0]['file_catalog'];self.assertEqual(catalog['image_count'],1)
        self.assertEqual(catalog['images'][0]['name'],'photo.JPG');self.assertFalse(catalog['pixels_read'])
        self.assertNotIn('setup.apk',json.dumps(catalog))
    def tool(self,name,args,identifier):
        return {'tool_calls':[{'id':identifier,'type':'function','function':{'name':name,'arguments':json.dumps(args,ensure_ascii=False)}}]}
    def run_model(self,outputs,text):
        replies=iter(outputs);self.chat.client=lambda *_:(next(replies),{})
        self.chat.images.describe=lambda *_:[{'id':'unit','image_kind':'photo','description':'实际测试照片','text':''}]
        with patch('deepseek_client.complete',return_value=({'content':'{"authorized":true}'},{})):
            self.assertTrue(self.bridge.accept(self.event(text),'unit'));self.bridge.group_pool.submit(lambda:None).result(8)
    def test_paraphrased_evidence_can_be_corrected_without_duplicate_block(self):
        text='修图，保留主体人物，去路人，添加特效，合适的都可以'
        args={'group':'','file_name':'','latest':False,'evidence':'原图在群文件'}
        self.run_model([self.tool('read_group_images',args,'bad'),self.tool('read_group_images',{k:v for k,v in args.items() if k!='evidence'},'fixed'),{'content':'已经读到照片。'}],text)
        peer='group:'+self.gid+':'+self.admin;self.assertTrue(self.chat.memory.latest_images(peer))
        self.assertEqual(sum(a=='get_group_file_url' for a,p in self.calls),1)
    def test_identical_read_reuses_existing_result_without_second_download(self):
        args={'group':'','file_name':'','latest':False}
        self.run_model([self.tool('read_group_images',args,'one'),self.tool('read_group_images',args,'again'),{'content':'照片读到了。'}],'看看群文件里那张照片')
        self.assertEqual(sum(a=='get_group_file_url' for a,p in self.calls),1)
    def test_premature_edit_then_read_can_retry_same_edit_with_real_source(self):
        captured=[]
        self.chat.creative=SimpleNamespace(prompt_context=lambda peer:[],request=lambda peer,kind,args,text,**kw:captured.append((peer,kind,args,text,kw)) or {'accepted':True,'job_id':'unit-job'})
        draw={'prompt':'保留主体人物，去掉路人，添加美观特效','model':'gpt-6-luna','effort':'medium'}
        read={'group':'','file_name':'','latest':False}
        self.run_model([self.tool('draw_picture',draw,'early'),self.tool('read_group_images',read,'read'),self.tool('draw_picture',draw,'ready')],'修图，保留主体人物，去路人，添加特效，合适的都可以')
        self.assertEqual(len(captured),1);ctx=captured[0][4]['context'];self.assertTrue(ctx['source_image_shas'])
        self.assertEqual(ctx['source_image_shas'],self.chat.memory.latest_images(captured[0][0]))
        self.assertEqual(captured[0][3],'修图，保留主体人物，去路人，添加特效，合适的都可以')
    def test_ambiguous_catalog_still_requires_selection_before_edit(self):
        self.files.append(self.photo|{'file_id':'another','file_name':'other.JPG'})
        self.reader.download=lambda *_:self.fail('Ambiguous files must not be guessed')
        args={'group':'','file_name':'','latest':False}
        self.run_model([self.tool('read_group_images',args,'choose'),{'content':'群文件有photo.JPG和other.JPG，要修哪张？'}],'修图，加合适的特效')
        self.assertFalse(self.chat.memory.latest_images('group:'+self.gid+':'+self.admin))


if __name__=='__main__':unittest.main()
