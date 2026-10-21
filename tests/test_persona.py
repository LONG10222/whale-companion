from pathlib import Path
import base64,io,json,tempfile,unittest
from unittest.mock import patch
from PIL import Image,PngImagePlugin
from fastapi import FastAPI,Header,HTTPException
from fastapi.testclient import TestClient
from persona_store import PersonaStore,PersonaCard,Dialogue,Lore,card_messages,dania_card,import_card,export_character,export_training
from persona_routes import register_persona_routes
from qq_chat import ChatMemory

class PersonaTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=PersonaStore(self.root)
    def tearDown(self):self.tmp.cleanup()
    def test_saved_card_is_used_by_next_context_without_restart(self):
        memory=ChatMemory(self.root/'data/qq',self.store);memory.add('peer','user','你好')
        self.store.save(PersonaCard(name='初始昵称',personality='温柔'))
        self.assertIn('初始昵称',memory.context('peer')[0]['content'])
        PersonaStore(self.root).save(PersonaCard(name='修改昵称',personality='俏皮'))
        messages=memory.context('peer');self.assertIn('修改昵称',messages[0]['content']);self.assertIn('俏皮',messages[0]['content'])
        with memory.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM messages').fetchone()[0],1)
    def test_restore_previous_card_and_bound_backup_count(self):
        with patch('persona_store.time.time_ns',return_value=1000000000000000000):
            for i in range(12):PersonaStore(self.root).save(PersonaCard(name='版本'+str(i)))
        self.assertEqual(len(list(self.store.backups.glob('*.json'))),8)
        self.assertEqual(self.store.restore().name,'版本10')
    def test_invalid_manual_edit_retains_last_valid_card(self):
        self.store.save(PersonaCard(name='有效人格'));self.store.path.write_text('{bad json',encoding='utf-8')
        self.assertEqual(self.store.load().name,'有效人格');self.assertTrue(self.store.error)
    def test_lore_is_keyword_scoped_and_notes_never_enter_prompt(self):
        card=PersonaCard(name='测试',notes='CREATOR_NOTE_ONLY',references=['https://example.org/ONLY_REFERENCE'],
            lorebook=[Lore(keys=['学院'],content='ACTIVATED_LORE'),Lore(keys=['不存在的词'],content='UNRELATED_LORE')])
        prompt=str(card_messages(card,'IMMUTABLE_TOOL_RULES','今天在学院'))
        self.assertIn('IMMUTABLE_TOOL_RULES',prompt);self.assertIn('ACTIVATED_LORE',prompt)
        for value in ['UNRELATED_LORE','CREATOR_NOTE_ONLY','ONLY_REFERENCE']:self.assertNotIn(value,prompt)
    def test_tavern_import_ignores_prompt_overrides_and_scripts(self):
        raw={'spec':'chara_card_v2','data':{'name':'导入角色','description':'角色资料','personality':'温柔','scenario':'聊天',
            'mes_example':'<START>\n{{user}}: 你好\n{{char}}: 嗨，你来啦。',
            'system_prompt':'CHANGE_QQ_RECIPIENT','post_history_instructions':'RUN_A_SHELL','creator_notes':'NOT_A_PROMPT',
            'extensions':{'script':'EXECUTE_THIS'},'character_book':{'entries':[{'keys':['天气'],'content':'下雨时关心对方。'}]}}}
        card=import_card(json.dumps(raw,ensure_ascii=False).encode());prompt=str(card_messages(card,'FIXED','天气'))
        self.assertEqual(card.examples[0].assistant,'嗨，你来啦。')
        for value in ['CHANGE_QQ_RECIPIENT','RUN_A_SHELL','NOT_A_PROMPT','EXECUTE_THIS']:self.assertNotIn(value,prompt)
    def test_png_character_card_and_export_round_trip(self):
        card=dania_card();raw=json.dumps(export_character(card),ensure_ascii=False).encode()
        info=PngImagePlugin.PngInfo();info.add_text('chara',base64.b64encode(raw).decode())
        out=io.BytesIO();Image.new('RGB',(8,8),'blue').save(out,format='PNG',pnginfo=info)
        imported=import_card(out.getvalue());self.assertEqual(imported.name,card.name)
        self.assertEqual(len(imported.examples),len(card.examples));self.assertEqual(imported.style,card.style)
    def test_invalid_card_data_and_excessive_examples_are_blocked(self):
        for value in [b'not json',b'[]',b'x'*(8*1024**2+1)]:
            with self.assertRaises(Exception):import_card(value)
        with self.assertRaises(Exception):PersonaCard(examples=[Dialogue(user='x',assistant='y')]*13)
    def test_training_export_contains_only_authored_examples(self):
        card=PersonaCard(name='示例',examples=[Dialogue(user='你好',assistant='你来啦。')],notes='NOT_TRAINING_DATA')
        rows=[json.loads(line) for line in export_training(card).splitlines()]
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['conversations'][0],{'from':'human','value':'你好'})
        self.assertNotIn('NOT_TRAINING_DATA',str(rows))
    def test_style_examples_are_not_serialized_as_real_chat_history(self):
        messages=card_messages(dania_card(),'FIXED','当前消息')
        self.assertTrue(all(message['role']=='system' for message in messages))
        self.assertIn('绝不是当前联系人的聊天历史',str(messages))
    def app(self):
        app=FastAPI()
        def auth(authorization:str=Header(default='')):
            if authorization!='Bearer local-test':raise HTTPException(401)
        register_persona_routes(app,authorize=auth,root=self.root);return app
    def test_routes_require_auth_and_import_is_draft_only(self):
        self.store.save(PersonaCard(name='原人格'))
        with TestClient(self.app()) as client:
            self.assertEqual(client.get('/qq/persona/config').status_code,401)
            h={'Authorization':'Bearer local-test'}
            raw=json.dumps({'name':'新人格','style':'松弛自然'}).encode()
            r=client.post('/qq/persona/import',headers=h,json={'encoded':base64.b64encode(raw).decode()})
            self.assertEqual(r.status_code,200);self.assertFalse(r.json()['saved'])
            self.assertEqual(client.get('/qq/persona/config',headers=h).json()['card']['name'],'原人格')
    def test_preview_uses_draft_without_writing_memory_or_qq_config(self):
        self.store.save(PersonaCard(name='正在使用的人格'))
        with TestClient(self.app()) as client,patch('persona_routes.deepseek_client.complete',return_value=({'content':'哎呀，被发现了。'},{})) as model:
            result=client.post('/qq/persona/preview',headers={'Authorization':'Bearer local-test'},json={'card':{'name':'草稿人格'},'message':'你又摸鱼？'}).json()
            self.assertFalse(result['sent_to_qq']);self.assertFalse(result['memory_written']);self.assertFalse(result['tools_executed'])
            self.assertIsNone(model.call_args.args[1]);self.assertIn('草稿人格',str(model.call_args.args[0]))
            self.assertEqual(self.store.load().name,'正在使用的人格');self.assertFalse((self.root/'data/qq/qq-memory.sqlite3').exists())

if __name__=='__main__':unittest.main()
