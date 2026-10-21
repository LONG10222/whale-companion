from pathlib import Path
import hashlib,io,json,tempfile,unittest
from PIL import Image
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sticker_library import StickerLibrary,PUBLIC_SOURCE,CATALOG_SOURCE,PUBLIC_SOURCES
from sticker_routes import register_sticker_routes


class StickerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.folder=self.root/'assets/stickers';(self.folder/'files').mkdir(parents=True)
        self.rows=[]
        for n,(caption,emotions,allowed) in enumerate([('开心的小猫',['开心'],True),('无语的熊猫',['无语'],True),('未标注的图片',[],False)]):
            b=io.BytesIO();Image.new('RGB',(300,100),(n*50,80,120)).save(b,format='PNG');data=b.getvalue();digest=hashlib.sha256(data).hexdigest()
            file='files/'+digest+'.png';(self.folder/file).write_bytes(data)
            self.rows.append({'id':digest[:24],'sha256':digest,'file':file,'pack':'猫猫','packs':['猫猫'],'pack_code':'010','title':'原文件名',
                'caption':caption,'emotions':emotions,'auto_select':allowed,'annotation_status':'model_captioned' if allowed else 'not_captioned',
                'animated':False,'frames':1,'bytes':len(data),'width':300,'height':100})
        self.data={'source':PUBLIC_SOURCE,'images':self.rows,'packs':[{'name':'猫猫'}],'stats':{'images':3}}
        self.write();self.library=StickerLibrary(self.root)

    def write(self):
        (self.folder/'catalog.json').write_text(json.dumps(self.data,ensure_ascii=False),encoding='utf-8')

    def tearDown(self):self.tmp.cleanup()

    def test_chinese_caption_and_emotion_search(self):
        r=self.library.search(query='无语',ready_only=True)
        self.assertEqual(r['total'],1)
        self.assertEqual(r['items'][0]['id'],self.rows[1]['id'])

    def test_automatic_candidates_exclude_unlabeled_images(self):
        self.assertEqual(self.library.search(ready_only=False)['total'],3)
        self.assertEqual(self.library.search(ready_only=True)['total'],2)
        out=self.library.choose('开心')
        self.assertEqual(out['total'],1);self.assertFalse(out['sent'])

    def test_path_traversal_and_unknown_ids_are_rejected(self):
        for value in ['../settings.local.json','a'*24+'/.','x'*24]:
            with self.assertRaises(ValueError):self.library.file(value)
        with self.assertRaises(FileNotFoundError):self.library.file('f'*24)

    def test_catalog_cannot_point_to_other_local_files(self):
        self.rows[0]['file']='../../settings.local.json';self.write()
        with self.assertRaises(ValueError):self.library.file(self.rows[0]['id'])

    def test_personal_collection_is_not_exposed_by_public_gallery(self):
        self.data['source']='personal-upload';self.write()
        with self.assertRaises(ValueError):self.library.load()

    def test_thumbnail_does_not_modify_original(self):
        source=self.library.file(self.rows[0]['id']);before=source.read_bytes()
        thumb=self.library.thumbnail(self.rows[0]['id'])
        with Image.open(thumb) as image:
            self.assertEqual(image.format,'WEBP');self.assertLessEqual(max(image.size),240)
        self.assertEqual(source.read_bytes(),before)

    def test_large_page_limits_are_blocked(self):
        with self.assertRaises(ValueError):self.library.search(limit=5000)
        with self.assertRaises(ValueError):self.library.search(page=0)

    def test_cache_cleanup_preserves_originals_and_unknown_files(self):
        self.library.thumbs.mkdir(parents=True)
        files=[]
        for name in ['a'*24,'b'*24,'c'*24]:
            p=self.library.thumbs/(name+'.webp');p.write_bytes(b'x'*800);files.append(p)
        unknown=self.library.thumbs/'my-personal-image.webp';unknown.write_bytes(b'keep')
        self.library.prune_thumbnails(files[-1],max_bytes=1000)
        self.assertTrue(files[-1].exists());self.assertTrue(unknown.exists())
        self.assertTrue(self.library.file(self.rows[0]['id']).exists())
        self.assertEqual(sum(p.stat().st_size for p in files if p.exists()),800)

    def test_http_gallery_returns_public_metadata_and_image(self):
        app=FastAPI();register_sticker_routes(app,root=self.root)
        with TestClient(app) as client:
            out=client.get('/stickers/api/list?q=开心').json()
            self.assertEqual(out['total'],1);self.assertNotIn('file',out['items'][0])
            response=client.get(out['items'][0]['image_url'])
            self.assertEqual(response.status_code,200);self.assertEqual(response.headers['content-type'],'image/png')
            self.assertEqual(client.get('/stickers/images/not-an-id').status_code,404)
            self.assertEqual(client.get('/stickers/api/list?limit=999').status_code,422)

    def mixed(self):
        self.data['source']=CATALOG_SOURCE
        self.data['sources']=[{'id':sid,'url':PUBLIC_SOURCES[sid]} for sid in ['deepseek_gengjing','wuwa_user_download','chinesebqb']]
        for row,sid,theme,role in zip(self.rows,['deepseek_gengjing','wuwa_user_download','chinesebqb'],['DeepSeek','鸣潮','其他'],['DeepSeek娘','达妮娅','']):
            row.update(source_id=sid,themes=[theme],characters=[role] if role else [],topics=['晚安'] if theme=='鸣潮' else [])
        self.rows[1].update(annotation_status='source_labeled',animated=True,frames=4)
        self.write()

    def test_mixed_public_sources_and_featured_themes(self):
        self.mixed()
        out=self.library.search(theme='featured')
        self.assertEqual(out['total'],2)
        self.assertEqual([x['themes'][0] for x in out['items']],['DeepSeek','鸣潮'])
        self.assertEqual(self.library.status()['themes']['鸣潮']['images'],1)
        roles={r['name']:r for r in self.library.status()['characters']}
        self.assertEqual(roles['达妮娅']['themes'],['鸣潮'])

    def test_unknown_source_cannot_be_registered(self):
        self.mixed();self.data['sources'].append({'id':'unknown-source'});self.write()
        with self.assertRaises(ValueError):self.library.load()

    def test_unregistered_image_source_is_blocked(self):
        self.mixed();self.rows[0]['source_id']='private';self.write()
        with self.assertRaises(ValueError):self.library.load()

    def test_role_alias_topic_and_animation_filters(self):
        self.mixed()
        self.assertEqual(self.library.search(query='达尼娅')['total'],1)
        out=self.library.search(theme='鸣潮',character='达妮娅',topic='晚安',kind='animated',ready_only=True)
        self.assertEqual(out['total'],1)
        self.assertEqual(self.library.search(theme='鸣潮',kind='static')['total'],0)
        self.assertEqual(self.library.search(theme='其他',ready_only=True)['total'],0)

    def test_automatic_choice_prioritizes_requested_themes(self):
        self.mixed();self.rows[2].update(auto_select=True,annotation_status='model_captioned',emotions=['开心']);self.write()
        self.assertEqual(self.library.choose('开心')['items'][0]['themes'],['DeepSeek'])
        self.assertEqual(self.library.choose('开心')['total'],1)

    def test_http_new_filters_and_metadata(self):
        self.mixed();app=FastAPI();register_sticker_routes(app,root=self.root)
        with TestClient(app) as client:
            out=client.get('/stickers/api/list',params={'theme':'鸣潮','character':'达妮娅','topic':'晚安','kind':'animated','ready_only':'true'}).json()
            self.assertEqual(out['total'],1)
            self.assertEqual(out['items'][0]['file_extension'],'.png')
            self.assertEqual(client.get('/stickers/api/list?kind=video').status_code,422)


if __name__=='__main__':unittest.main()
