from pathlib import Path
import base64,io,json,tempfile,unittest
from PIL import Image
from qq_images import QQImages,qq_url,SEMANTIC_VERSION
from unittest.mock import patch

class ImageTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.reader=QQImages(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def image(self,color):
        out=io.BytesIO();Image.new('RGB',(40,30),color).save(out,format='PNG');return out.getvalue()
    def test_only_qq_image_hosts_and_standard_ports(self):
        self.assertTrue(qq_url('https://multimedia.nt.qq.com.cn/download?appid=1407'))
        self.assertTrue(qq_url('https://gchat.qpic.cn/test.jpg'))
        for url in ['http://127.0.0.1/a','file:///C:/secret.jpg','https://qq.com.evil.example/a','https://u:p@qq.com/a','https://qq.com:9999/a']:
            self.assertFalse(qq_url(url),url)
    def test_static_decode_preserves_original_bytes(self):
        data=self.image('red');item=self.reader.prepare(data)
        self.assertEqual(Path(item['path']).read_bytes(),data);self.assertEqual(item['frames'],1)
    def test_animated_sticker_samples_real_frames(self):
        out=io.BytesIO();frames=[Image.new('RGB',(30,30),color) for color in ['red','green','blue']]
        frames[0].save(out,format='GIF',save_all=True,append_images=frames[1:],duration=100,loop=0)
        item=self.reader.prepare(out.getvalue());self.assertEqual(item['frames'],3);self.assertEqual(item['sampled_frames'],3)
        data=base64.b64decode(item['image_url']['url'].split(',',1)[1])
        with Image.open(io.BytesIO(data)) as im:
            self.assertEqual(im.size,(90,30));self.assertGreater(im.getpixel((15,15))[0],200);self.assertGreater(im.getpixel((75,15))[2],200)
    def test_cached_descriptions_preserve_attachment_order(self):
        items=[self.reader.prepare(self.image(c)) for c in ['red','blue']]
        for i,item in enumerate(items):
            (self.reader.cache/(item['sha256']+'.json')).write_text(json.dumps({'id':item['id'],'description':str(i),'semantic_version':SEMANTIC_VERSION,'possible_meanings':[],'image_kind':'unknown'}),encoding='utf-8')
        self.assertEqual([r['description'] for r in self.reader.describe(items[::-1])],['1','0'])
    def test_non_image_payload_is_rejected(self):
        with self.assertRaises(Exception):self.reader.prepare(b'<html>not an image</html>')
    def test_old_visual_only_cache_is_refreshed_once_and_semantics_are_cached(self):
        image=self.reader.prepare(self.image('blue'));file=self.reader.cache/(image['sha256']+'.json')
        file.write_text(json.dumps({'id':image['id'],'description':'蓝色背景'}),encoding='utf-8')
        response={'choices':[{'message':{'content':json.dumps({'items':[{'id':image['id'],'image_kind':'meme','description':'卡通人物沉默','text':'……',
            'emotions':['无语'],'possible_meanings':['沉默或无语，需结合对话'],'uncertainty':'不代表用户真实心情'}]},ensure_ascii=False)}}]}
        with patch('qq_images.deepseek_client.api_post',return_value=response) as api:
            first=self.reader.describe([image]);second=self.reader.describe([image]);self.assertEqual(api.call_count,1)
        self.assertEqual(first,second);self.assertEqual(first[0]['image_kind'],'meme');self.assertIn('无语',first[0]['possible_meanings'][0])
        self.assertEqual(json.loads(file.read_text(encoding='utf-8'))['semantic_version'],SEMANTIC_VERSION)
    def test_corrupted_semantic_cache_is_recovered_without_reading_other_files(self):
        image=self.reader.prepare(self.image('red'));(self.reader.cache/(image['sha256']+'.json')).write_text('{broken',encoding='utf-8')
        response={'choices':[{'message':{'content':json.dumps({'items':[{'id':image['id'],'description':'红色图','image_kind':'unknown','possible_meanings':[]}]})}}]}
        with patch('qq_images.deepseek_client.api_post',return_value=response):self.assertEqual(self.reader.describe([image])[0]['description'],'红色图')
    def test_contextual_meaning_is_scoped_and_never_written_to_intrinsic_image_cache(self):
        image=self.reader.prepare(self.image('red'))
        def response(meaning):return {'choices':[{'message':{'content':json.dumps({'items':[{'id':image['id'],'description':'沉默表情','image_kind':'meme','possible_meanings':['沉默']}],
            'conversation':{'meaning':meaning,'response_act':'acknowledge','certainty':'medium','reply_hint':'好。'}})}}]}
        with patch('qq_images.deepseek_client.api_post',side_effect=[response('别讲太长'),response('抽卡真无语'),response('别讲太长')]) as api:
            a=self.reader.interpret([image],[('user','随便聊聊')],'','87654321');again=self.reader.interpret([image],[('user','随便聊聊')],'','87654321')
            b=self.reader.interpret([image],[('user','抽卡又歪了')],'','87654321');other=self.reader.interpret([image],[('user','随便聊聊')],'','99999999')
            self.assertEqual(api.call_count,3);self.assertEqual(a,again);self.assertNotEqual(a[0]['conversation_turn']['meaning'],b[0]['conversation_turn']['meaning'])
            self.assertNotIn('87654321',json.dumps(api.call_args_list[0].args[1],ensure_ascii=False))
        intrinsic=json.loads((self.reader.cache/(image['sha256']+'.json')).read_text(encoding='utf-8'))
        self.assertNotIn('conversation_turn',intrinsic);self.assertNotIn('别讲太长',json.dumps(intrinsic,ensure_ascii=False))
