from pathlib import Path
from unittest.mock import patch
from contextlib import closing
import hashlib,io,json,sqlite3,tempfile,time,unittest
from PIL import Image
from qq_chat import QQConversation
from qq_bridge import QQBridge
from sticker_library import StickerLibrary,PUBLIC_SOURCE
from sticker_policy import sticker_opportunity
from chat_style import sticker_request

class Reactions:
    def __init__(self,root):self.root=root;self.queries=[]
    def reaction(self,emotion,**kwargs):
        self.queries.append(emotion);recent=kwargs.get('recent',[])
        number=next((str(i) for i in range(6) if str(i) not in recent),'5')
        return {'id':number,'caption':emotion+'的鸣潮表情','bytes':100}
    def file(self,item_id):return self.root/(item_id+'.png')
    def search(self,**kwargs):return {'total':6,'items':[{'id':str(i),'bytes':100,'caption':'可爱表情'} for i in range(6)]}

class SpontaneousStickerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'configs').mkdir()
        self.config('frequent');self.chat=QQConversation(self.root,client=lambda *_:({'content':'哈哈，确实有点好笑。'},{}));self.chat.library=Reactions(self.root)
    def tearDown(self):self.tmp.cleanup()
    def config(self,mode):
        (self.root/'configs/agent-tools.local.json').write_text(json.dumps({'sticker_mode':mode}),encoding='utf-8')
    def test_casual_reply_adds_image_without_explicit_image_request(self):
        r=self.chat.prepare('peer','哈哈哈你真的很会贫',remember=False)
        self.assertEqual(len(r['attachments']),1);self.assertEqual(r['reply'],'哈哈，确实有点好笑。');self.assertTrue(r['tools'][0]['automatic'])
        self.assertEqual(self.chat.library.queries,['调皮'])
    def test_repeated_previews_do_not_consume_delivery_cooldown_or_quota(self):
        for _ in range(2):self.assertEqual(len(self.chat.prepare('peer','哈哈哈',remember=False)['attachments']),1)
        self.assertEqual(self.chat.memory.sticker_activity('peer'),{'last_at':0,'plain_turns':0,'recent':[]})
    def test_no_image_and_serious_topics_suppress_automatic_reaction(self):
        for text in ['这条只发文字','别配表情包','不要加图','我要认真说这件事','朋友住院了，我很难过','这个API报错怎么处理']:
            r=self.chat.prepare('peer',text,remember=False);self.assertFalse(r['attachments'],text)
    def test_requested_only_mode_blocks_unasked_images(self):
        self.config('requested');self.assertFalse(self.chat.prepare('peer','哈哈哈',remember=False)['attachments'])
    def test_recent_successful_sticker_prevents_immediate_next_one(self):
        self.chat.memory.sticker_sent('peer','0')
        self.assertFalse(self.chat.prepare('peer','哈哈哈',remember=False)['attachments'])
    def test_model_can_choose_image_for_an_image_only_message(self):
        answers=iter([({'tool_calls':[{'id':'s','type':'function','function':{'name':'use_sticker','arguments':'{"emotion":"调皮","query":"","theme":"鸣潮","character":""}'}}]},{}),({'content':'哈哈，这张太会了。'}, {})])
        self.chat.client=lambda *_:next(answers);self.chat.images.describe=lambda _:['一张搞笑的鸣潮表情']
        r=self.chat.prepare('peer','',images=[{}],remember=False);self.assertEqual(len(r['attachments']),1)
    def test_unavailable_matching_image_keeps_normal_text(self):
        self.chat.library.reaction=lambda *a,**kw:None;r=self.chat.prepare('peer','哈哈哈',remember=False)
        self.assertFalse(r['attachments']);self.assertEqual(r['reply'],'哈哈，确实有点好笑。')
    def test_serious_visual_content_does_not_get_an_automatic_joke(self):
        self.chat.images.describe=lambda _:['事故现场，有人受伤']
        self.assertFalse(self.chat.prepare('peer','看看这张',images=[{}],remember=False)['attachments'])
    def test_full_qq_delivery_alternates_images_and_text_and_records_real_success(self):
        b=QQBridge(self.root,self.chat);b.configure(True,'12345678',['87654321'],False);b.connected=True;b.login_id='12345678';b.epoch='one';sent=[]
        b.call=lambda action,params,**kw:sent.append(params) or {'message_id':len(sent)}
        image_rounds=[]
        try:
            for i in range(5):
                before=len(sent)
                with patch('time.time',return_value=1000+i*60):
                    obj={'message_type':'private','sub_type':'friend','self_id':12345678,'user_id':87654321,'message_id':i+1,'message':[{'type':'text','data':{'text':'哈哈哈好有意思'}}]}
                    self.assertTrue(b.accept(obj,'one'));b.pool.submit(lambda:None).result(4)
                if any(m['type']=='image' for r in sent[before:] for m in r['message']):image_rounds.append(i)
                if len(sent)-before==2:
                    self.assertEqual(sent[before]['message'][0]['type'],'text');self.assertEqual(sent[before+1]['message'][0]['type'],'image')
            self.assertEqual(image_rounds,[0,2,4]);self.assertEqual(self.chat.memory.sticker_activity('87654321')['recent'],['2','1','0'])
        finally:b.pool.shutdown(wait=True)
    def test_failed_or_unknown_qq_send_does_not_record_sticker_as_sent(self):
        b=QQBridge(self.root,self.chat);b.configure(True,'12345678',['87654321'],False);b.connected=True;b.login_id='12345678';b.epoch='one'
        b.call=lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('unknown receipt'))
        try:
            b.accept({'message_type':'private','sub_type':'friend','self_id':12345678,'user_id':87654321,'message_id':1,'message':[{'type':'text','data':{'text':'哈哈哈哈'}}]},'one')
            b.pool.submit(lambda:None).result(4);self.assertFalse(self.chat.memory.sticker_activity('87654321')['last_at'])
        finally:b.pool.shutdown(wait=True)
    def test_explicit_request_bypasses_spontaneous_cooldown(self):
        self.chat.memory.sticker_sent('peer','0')
        self.chat.client=lambda *_:({'tool_calls':[{'id':'s','type':'function','function':{'name':'use_sticker','arguments':'{"emotion":"开心","query":"","theme":"鸣潮","character":""}'}}]}, {})
        self.assertEqual(len(self.chat.prepare('peer','发一张鸣潮表情包',remember=False)['attachments']),1)
    def test_other_peers_sticker_does_not_block_this_friend(self):
        self.chat.memory.sticker_sent('other','0');self.assertEqual(len(self.chat.prepare('peer','哈哈哈',remember=False)['attachments']),1)

class ReactionSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);folder=self.root/'assets/stickers';(folder/'files').mkdir(parents=True);self.rows=[]
        for n,(caption,tags,status) in enumerate([('普通开心',['开心'],'source_labeled'),('举手欢呼',['开心'],'model_captioned'),('生气骂人',['开心','生气'],'model_captioned'),('未识别',['开心'],'not_captioned')]):
            b=io.BytesIO();Image.new('RGB',(16,16),(n*30,80,90)).save(b,format='PNG');raw=b.getvalue();h=hashlib.sha256(raw).hexdigest();(folder/'files'/(h+'.png')).write_bytes(raw)
            self.rows.append({'id':h[:24],'sha256':h,'file':'files/'+h+'.png','title':caption,'caption':caption,'emotions':tags,'themes':['鸣潮'],'characters':[],
                'auto_select':True,'annotation_status':status,'bytes':len(raw),'packs':['测试'],'pack_code':'1','animated':False})
        (folder/'catalog.json').write_text(json.dumps({'source':PUBLIC_SOURCE,'images':self.rows}),encoding='utf-8');self.lib=StickerLibrary(self.root)
    def tearDown(self):self.tmp.cleanup()
    def test_only_matching_annotated_images_and_caption_quality_preferred(self):
        r=self.lib.reaction('开心');self.assertEqual(r['id'],self.rows[1]['id'])
        self.assertIsNone(self.lib.reaction('安慰'))
    def test_recent_image_is_avoided_and_wrong_emotion_is_never_random_fallback(self):
        self.assertEqual(self.lib.reaction('开心',recent=[self.rows[1]['id']])['id'],self.rows[0]['id'])
        self.assertIsNone(self.lib.reaction('问候',query='晚安 睡觉',require_query=True))
    def test_backend_search_never_consumes_gpu_or_calls_model(self):
        with patch('deepseek_client.complete',side_effect=AssertionError('no model')):self.assertTrue(self.lib.reaction('开心'))

if __name__=='__main__':unittest.main()
