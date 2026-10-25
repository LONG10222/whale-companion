from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import io,json,subprocess,tempfile,unittest

from PIL import Image,PngImagePlugin
from qq_images import QQImages,requires_isolated_preview,PNG_SCAN_CHUNKS,SEMANTIC_VERSION
import animation_preview_worker as worker
import bounded_animation


def animation(format='GIF',count=140,metadata=False):
    frames=[Image.new('RGB',(16,16),(i%253,(i//253)*70,80)) for i in range(count)]
    options={}
    if metadata:
        info=PngImagePlugin.PngInfo();info.add_text('Comment','a'*9000);options['pnginfo']=info
    out=io.BytesIO();frames[0].save(out,format=format,save_all=True,append_images=frames[1:],duration=20,loop=0,**options)
    return out.getvalue()


class AnimationIngestionTests(unittest.TestCase):
    def test_many_gif_frames_use_isolated_bounded_prefix_and_keep_original(self):
        raw=animation(count=300)
        with tempfile.TemporaryDirectory() as td:
            result=QQImages(td).prepare(raw)
            self.assertEqual(result['frames'],300)
            self.assertEqual(result['decoded_frame_limit'],120)
            self.assertTrue(result['animation_truncated'])
            self.assertEqual(Path(result['path']).read_bytes(),raw)

    def test_valid_apng_after_long_png_metadata_uses_the_same_budget(self):
        raw=animation('PNG',metadata=True)
        self.assertGreater(raw.find(b'acTL'),8192)
        with tempfile.TemporaryDirectory() as td:
            result=QQImages(td).prepare(raw)
            self.assertEqual(result['frames'],140)
            self.assertTrue(result['animation_truncated'])
            self.assertEqual(result['decoded_frame_limit'],120)

    def test_static_png_text_does_not_misclassify_plain_acTL_as_a_chunk(self):
        info=PngImagePlugin.PngInfo();info.add_text('Comment','acTL'+'a'*9000)
        out=io.BytesIO();Image.new('RGB',(1000,10),'blue').save(out,format='PNG',pnginfo=info)
        with tempfile.TemporaryDirectory() as td,patch('bounded_animation.preview') as isolated:
            result=QQImages(td).prepare(out.getvalue())
            self.assertEqual(result['frames'],1);isolated.assert_not_called()

    def test_fragmented_or_bad_png_headers_never_use_parent_decoder(self):
        chunk=b'\0\0\0\0tEXt'+b'\0'*4
        fragmented=b'\x89PNG\r\n\x1a\n'+chunk*(PNG_SCAN_CHUNKS+1)+b'\0\0\0\0IDAT'+b'\0'*4
        malformed=b'\x89PNG\r\n\x1a\n'+b'\x7f\xff\xff\xfftEXt'+b'\0'*4
        for raw in [fragmented,malformed]:
            self.assertTrue(requires_isolated_preview(raw))
            with tempfile.TemporaryDirectory() as td,patch('bounded_animation.preview',side_effect=ValueError('bounded reject')),patch('qq_images.Image.open') as parent:
                with self.assertRaises(ValueError):QQImages(td).prepare(raw)
                parent.assert_not_called()

    def test_truncation_is_supplied_to_vision_and_old_semantic_cache_is_refreshed(self):
        with tempfile.TemporaryDirectory() as td:
            reader=QQImages(td);item=reader.prepare(animation())
            (reader.cache/(item['sha256']+'.json')).write_text(json.dumps({'id':item['id'],'semantic_version':SEMANTIC_VERSION-1,'description':'旧的完整动图解说','possible_meanings':[]}),encoding='utf-8')
            answer={'choices':[{'message':{'content':json.dumps({'items':[{'id':item['id'],'description':'当前提供的画面','possible_meanings':[]}]})}}]}
            with patch('qq_images.deepseek_client.api_post',return_value=answer) as api:
                result=reader.describe([item])
            self.assertEqual(api.call_count,1)
            metadata=json.loads(api.call_args.args[1]['messages'][0]['content'][1]['text'])
            self.assertTrue(metadata['animation_truncated'])
            self.assertEqual(result[0]['description'],'当前提供的画面')


class WorkerBudgetTests(unittest.TestCase):
    def test_pixel_budget_limits_large_frame_count_before_decode(self):
        visits=[]
        class FakeImage:
            width=4000;height=4000;format='GIF';n_frames=500
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def seek(self,n):visits.append(n)
        output=io.StringIO()
        with patch.object(worker.sys,'stdin',SimpleNamespace(buffer=io.BytesIO(b'fixture'))),patch.object(worker.sys,'stdout',output),patch.object(worker.Image,'open',return_value=FakeImage()),patch.object(worker.ImageOps,'exif_transpose',side_effect=lambda _:Image.new('RGBA',(8,8),'blue')):
            worker.main()
        result=json.loads(output.getvalue())
        self.assertEqual(result['decoded_frame_limit'],4)
        self.assertLess(max(visits),4)
        self.assertTrue(result['animation_truncated'])

    def test_single_frame_pixel_limit_is_checked_before_decoding(self):
        class FakeImage:
            width=5000;height=5000;format='GIF';n_frames=2
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def seek(self,n):raise AssertionError('Oversized image was decoded')
        with patch.object(worker.sys,'stdin',SimpleNamespace(buffer=io.BytesIO(b'fixture'))),patch.object(worker.Image,'open',return_value=FakeImage()),self.assertRaises(ValueError):
            worker.main()

    def test_timeout_stops_owned_worker_and_releases_slot(self):
        class Proc:
            returncode=None
            def communicate(self,*args,**kwargs):raise subprocess.TimeoutExpired('fixture',4)
            def poll(self):return self.returncode
            def terminate(self):self.returncode=-15
            def wait(self,**kwargs):return self.returncode
        process=Proc()
        with tempfile.TemporaryDirectory() as td,patch.object(bounded_animation.subprocess,'Popen',return_value=process),patch.object(bounded_animation,'MediaJob') as job:
            with self.assertRaisesRegex(ValueError,'处理时间'):bounded_animation.preview(b'GIF89a',td)
        self.assertEqual(process.returncode,-15);job.return_value.close.assert_called_once()
        acquired=[bounded_animation.SLOTS.acquire(blocking=False) for _ in range(2)]
        try:self.assertEqual(acquired,[True,True])
        finally:
            for held in acquired:
                if held:bounded_animation.SLOTS.release()
