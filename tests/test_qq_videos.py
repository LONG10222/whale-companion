from pathlib import Path
from unittest.mock import patch
import json,tempfile,unittest
import av,numpy as np
from PIL import Image
from qq_videos import QQVideos,video_segment
from qq_video_worker import decode_video,decode_request

class VideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.root=Path(cls.tmp.name);cls.clip=cls.root/'sequence.mp4'
        with av.open(str(cls.clip),'w') as output:
            stream=output.add_stream('mpeg4',rate=10);stream.width=96;stream.height=64;stream.pix_fmt='yuv420p'
            for i in range(60):
                color=['red','green','blue'][i//20];array=np.array(Image.new('RGB',(96,64),color))
                frame=av.VideoFrame.from_ndarray(array,format='rgb24');frame.pts=i
                for packet in stream.encode(frame):output.mux(packet)
            for packet in stream.encode():output.mux(packet)
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def test_frame_timeline_preserves_scene_order(self):
        result=decode_video(self.clip,self.root/'frames')
        self.assertAlmostEqual(result['duration_seconds'],6,places=1);self.assertFalse(result['has_audio'])
        self.assertGreaterEqual(len(result['frames']),3)
        first=Image.open(result['frames'][0]['path']);last=Image.open(result['frames'][-1]['path'])
        try:
            self.assertGreater(first.getpixel((30,30))[0],220);self.assertGreater(last.getpixel((30,30))[2],220)
        finally:first.close();last.close()
        self.assertEqual(sorted(f['seconds'] for f in result['frames']),[f['seconds'] for f in result['frames']])
    def test_truncation_reports_coverage_and_frame_limit(self):
        result=decode_video(self.clip,self.root/'short',limit=2,max_frames=3)
        self.assertTrue(result['truncated']);self.assertEqual(result['analyzed_seconds'],2)
        self.assertLessEqual(len(result['frames']),3);self.assertLess(result['frames'][-1]['seconds'],2.1)
    def test_video_file_attachment_detected_but_documents_not(self):
        self.assertTrue(video_segment({'type':'video','data':{}}))
        self.assertTrue(video_segment({'type':'file','data':{'name':'clip.MP4'}}))
        self.assertFalse(video_segment({'type':'file','data':{'name':'notes.pdf'}}))
    def test_size_limit_cleans_only_its_partial_file(self):
        with tempfile.TemporaryDirectory() as root:
            reader=QQVideos(root)
            with patch('qq_videos.MAX_VIDEO_BYTES',8):
                with self.assertRaises(ValueError):reader.store([b'1234',b'56789'])
            self.assertFalse(list(reader.cache.glob('*.part')))
    def test_file_rpc_cannot_read_outside_owned_qq_directories(self):
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root);(folder/'configs').mkdir();owned=folder/'qq';owned.mkdir()
            (folder/'configs/qq-client.local.json').write_text(json.dumps({'qq_data':str(owned),'napcat_root':str(owned)}),encoding='utf-8')
            private=folder/'private.mp4';private.write_bytes(b'not a QQ media file')
            reader=QQVideos(folder)
            with self.assertRaises(ValueError):reader.fetch({'data':{'file':'id'}},lambda *a,**kw:{'file':str(private)})
    def test_corrupt_video_does_not_return_fake_frames(self):
        invalid=self.root/'corrupt.mp4';invalid.write_bytes(b'not a video')
        with self.assertRaises(Exception):decode_video(invalid,self.root/'invalid')
    def test_auto_decode_falls_back_when_hardware_is_unavailable(self):
        cfg={'source':'video','folder':'frames','backend':'auto'}
        with patch('qq_video_worker.decode_video',side_effect=[RuntimeError('No supported device'),{'hardware_used':False,'decode_backend':'cpu'}]) as decoder:
            result=decode_request(cfg)
            self.assertTrue(result['automatic_cpu_fallback']);self.assertFalse(result['hardware_used'])
            self.assertEqual([call.args[-1] for call in decoder.call_args_list],['cuda','cpu'])
    def test_successful_hardware_decode_is_not_repeated_on_cpu(self):
        with patch('qq_video_worker.decode_video',return_value={'hardware_used':True,'decode_backend':'cuda'}) as decoder:
            result=decode_request({'source':'video','folder':'frames'})
            self.assertFalse(result['automatic_cpu_fallback']);self.assertTrue(result['hardware_used']);decoder.assert_called_once()

if __name__=='__main__':unittest.main()
