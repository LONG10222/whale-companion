from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import hashlib,json,tempfile,time,unittest
from PIL import Image
import numpy as np
from image_ingest import ImageIngest
from qq_images import QQImages
from image_protection import protect_candidate
from dialogue_context import pending_photo_edit,intent_proof,valid_intent_proof

class ImageRecoveryTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.images=QQImages(self.root)
    def tearDown(self):self.tmp.cleanup()
    def test_large_chat_photo_uses_working_copy_and_keeps_original(self):
        source=self.root/'large.jpg';Image.new('RGB',(7680,4320),'blue').save(source);original=hashlib.sha256(source.read_bytes()).hexdigest()
        got=ImageIngest(self.root,self.images.prepare).ingest(source)
        self.assertEqual(got['original_metadata']['original_dimensions'],[7680,4320]);self.assertLess(Path(got['path']).stat().st_size,12*1024**2)
        with Image.open(got['path']) as im:self.assertLessEqual(max(im.size),4096)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),original)
        self.assertTrue((self.root/'cache/qq-original-images'/(original+'.source')).is_file())
    def test_byte_and_decode_limits_fail_with_specific_reasons(self):
        large=self.root/'oversize.jpg'
        with large.open('wb') as stream:stream.truncate(65*1024**2)
        with self.assertRaisesRegex(ValueError,'64MiB'):ImageIngest(self.root,self.images.prepare).ingest(large)
        unsupported=self.root/'source.heic';unsupported.write_bytes(b'\x00\x00\x00\x18ftypheic'+b'\0'*64)
        with self.assertRaisesRegex(ValueError,'HEIC'):ImageIngest(self.root,self.images.prepare).ingest(unsupported)
    def test_narrow_rotated_photo_is_bounded_and_group_gif_keeps_frames(self):
        source=self.root/'panorama.jpg';image=Image.new('RGB',(6000,1000),'blue');exif=Image.Exif();exif[274]=6;image.save(source,exif=exif)
        got=ImageIngest(self.root,self.images.prepare).ingest(source)
        with Image.open(got['path']) as im:self.assertLessEqual(max(im.size),4096);self.assertGreater(im.height,im.width)
        gif=self.root/'animated.gif';Image.new('RGB',(64,64),'red').save(gif,save_all=True,append_images=[Image.new('RGB',(64,64),'blue')],duration=100,loop=0)
        got=ImageIngest(self.root,self.images.prepare).ingest(gif,preserve_small=False)
        self.assertEqual(got['frames'],2)
    def test_failed_new_image_blocks_stale_sticker_then_new_photo_recovers(self):
        from qq_chat import ChatMemory
        memory=ChatMemory(self.root/'data/qq');peer='87654321'
        source=self.root/'small.png';Image.new('RGB',(64,64),'blue').save(source);pic=self.images.prepare(source.read_bytes())
        old=memory.add(peer,'user','牛惊');memory.remember_images(peer,old,[pic]);self.assertTrue(memory.latest_images(peer))
        memory.add(peer,'user','[对方发来一张图片，但本次无法取得原图：超过大小限制。]')
        self.assertEqual(memory.latest_images(peer),[])
        new=memory.add(peer,'user','新原图');memory.remember_images(peer,new,[pic]);self.assertTrue(memory.latest_images(peer))
    def test_service_path_can_resolve_installed_codex_without_interactive_path(self):
        from codex_rpc import resolve_codex
        binary=self.root/'OpenAI/Codex/bin/test-version/codex.exe';binary.parent.mkdir(parents=True);binary.write_bytes(b'fixture')
        with patch('codex_rpc.shutil.which',return_value=None),patch.dict('os.environ',{'LOCALAPPDATA':str(self.root)}):self.assertEqual(resolve_codex(),str(binary))
    def test_photo_confirmation_only_resumes_actual_unfinished_photo_request(self):
        photo='[收到图片]\n[本条QQ消息附图，资料]\n'+json.dumps([{'id':'a'*24,'image_kind':'photo'}])
        history=[('user','帮我修图，正常美皮瘦脸，再加天使光效'),('assistant','请补充图片'),('user',photo),('assistant','就是这张')]
        self.assertIn('美皮瘦脸',pending_photo_edit('OK啊',history,[]))
        self.assertFalse(pending_photo_edit('OK啊',history,[{'kind':'draw_picture','state':'completed','source_image_shas':['a'*64]}]))
        self.assertFalse(pending_photo_edit('好',[('assistant','我替你修图吧'),('user',photo)],[]))
        self.assertFalse(pending_photo_edit('不要修图',history,[]))
        self.assertFalse(pending_photo_edit('好',history+[('assistant','这次没修成，原图处理失败。')],[]))
        context={'mid':'source','_pending_photo_edit':True,'_current_image':True}
        proof=intent_proof('draw_picture','',peer='87654321',context=context,history=history,jobs=[],client=lambda *a,**k:({'content':'{"authorized":true,"continuation":true}'},{}))
        self.assertTrue(valid_intent_proof('draw_picture','','87654321',{**context,'_intent_proof':proof}))
    def test_actual_conversation_confirmation_calls_edit_and_keeps_source(self):
        from tests.test_image_edits import ImageEditTests
        case=ImageEditTests();case.setUp()
        try:
            photo='[收到图片]\n[本条QQ消息附图，资料]\n'+json.dumps([{'id':case.image['id'],'image_kind':'photo'}])
            case.chat.memory.add(case.peer,'user','帮我修图，正常美肤瘦脸，再加天使光效')
            mid=case.chat.memory.add(case.peer,'user',photo);case.chat.memory.remember_images(case.peer,mid,[case.image]);case.chat.memory.add(case.peer,'assistant','就是这张，准备按你的要求修。')
            case.chat.images.describe=lambda *_:[{'id':case.image['id'],'image_kind':'photo','description':'本人提供的角色照片'}]
            case.chat.client=lambda *_:({'tool_calls':[{'id':'confirmed-edit','type':'function','function':{'name':'draw_picture','arguments':json.dumps({'prompt':'按原图美肤瘦脸并加柔光'})}}]}, {})
            result=case.chat.prepare(case.peer,'OK啊',remember=False)
            case.service.pool.submit(lambda:None).result(5)
            self.assertFalse(result.get('silent'));self.assertTrue(result['tools'][0]['result'].get('accepted'))
            self.assertEqual(case.calls[0]['source_images'],[case.image['sha256']])
        finally:case.tearDown()

class RegionProtectionTests(unittest.TestCase):
    def test_protected_pixels_are_exact_and_transition_is_soft(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);src=folder/'source.png';candidate=folder/'candidate.png'
            Image.new('RGB',(800,600),(30,50,70)).save(src);Image.new('RGB',(800,600),(180,200,220)).save(candidate)
            plan={'protection':{'has_person':True,'mode':'subject_lock','subject_polygons':[[[300,200],[700,200],[700,1000],[300,1000]]],'identity_core_polygons':[[[430,300],[570,300],[570,450],[430,450]]],'face_box':[350,200,650,550]}}
            result=protect_candidate(src,candidate,plan,'加柔和光效',folder,1)
            self.assertEqual(result['protected_pixels_changed'],0);self.assertEqual(result['identity_core_pixels_changed'],0)
            with Image.open(result['path']) as im:actual=np.asarray(im.convert('RGB'))
            self.assertTrue(np.all(actual[300,400]==[30,50,70]));self.assertGreater(actual[300,20,0],30)
            self.assertLess(np.max(np.abs(np.diff(actual[300,:,0].astype(int)))) ,40)
    def test_missing_mask_does_not_silently_fall_back_to_full_redraw(self):
        from image_protection import spec
        for value in [{},{'protection':{'has_person':True,'mode':'none'}},{'protection':{'has_person':True,'mode':'subject_lock','subject_polygons':[[[0,0],[1,1],[2000,0]]],'identity_core_polygons':[]}}]:
            with self.assertRaises(ValueError):spec(value)
    def test_new_composition_keeps_previous_file_and_removal_never_becomes_translucent(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);source=folder/'source.png';candidate=folder/'candidate.png'
            Image.new('RGB',(800,600),'blue').save(source);Image.new('RGB',(800,600),'red').save(candidate)
            plan={'protection':{'has_person':True,'mode':'regions_only','subject_polygons':[[[300,100],[700,100],[700,1000],[300,1000]]],'identity_core_polygons':[[[400,200],[600,200],[600,400],[400,400]]],'edit_regions':[[[0,0],[250,0],[250,1000],[0,1000]]]}}
            first=protect_candidate(source,candidate,plan,'去路人，保留人物',folder,1);digest=hashlib.sha256(Path(first['path']).read_bytes()).hexdigest()
            second=protect_candidate(source,candidate,plan,'去路人，保留人物',folder,1,adjustments={'effect_strength':.2,'feather_scale':2})
            self.assertNotEqual(first['path'],second['path']);self.assertEqual(hashlib.sha256(Path(first['path']).read_bytes()).hexdigest(),digest)
            self.assertEqual(second['effect_strength'],1)
    def test_percent_coordinates_are_rejected_before_composition(self):
        from image_protection import spec
        with self.assertRaisesRegex(ValueError,'百分制'):spec({'protection':{'has_person':True,'mode':'subject_lock','subject_polygons':[[[0,0],[100,0],[100,100],[0,100]]],'identity_core_polygons':[[[10,10],[30,10],[30,30],[10,30]]]}})
    def test_local_removal_patch_matches_background_instead_of_leaving_a_person_shape(self):
        from PIL import ImageDraw
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);src=folder/'original.png';raw=folder/'generated.png'
            original=Image.new('RGB',(800,600),(30,30,30));ImageDraw.Draw(original).rectangle((40,250,120,490),fill=(3,3,3));original.save(src)
            Image.new('RGB',(800,600),(90,90,90)).save(raw)
            plan={'protection':{'has_person':True,'mode':'regions_only','subject_polygons':[[[400,100],[800,100],[800,1000],[400,1000]]],'identity_core_polygons':[[[500,200],[650,200],[650,400],[500,400]]],'edit_regions':[[[30,350],[180,350],[180,900],[30,900]]]}}
            result=protect_candidate(src,raw,plan,'去路人，其他不动，不加特效',folder,1)
            with Image.open(result['path']) as im:
                self.assertLess(max(abs(v-30) for v in im.getpixel((80,400))),3)
            self.assertEqual(result['translucent_removal_pixels'],0);self.assertTrue(any(r['applied'] for r in result['tone_matching']))

if __name__=='__main__':unittest.main()
