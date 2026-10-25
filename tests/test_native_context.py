from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import asyncio,copy,json,tempfile,time,unittest

from astrbot_sidecar import ConversationDrafts,confirm_pending,activity_state,tracked_request,bounded_turn
from chat_engine import ChatEngine,bounded_facts,NATIVE_TURN_TIMEOUT_SECONDS,NATIVE_CLIENT_TIMEOUT_SECONDS
from persona_store import PersonaCard,Dialogue,Lore
from qq_chat import QQConversation


class NativeContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        (self.root/'configs').mkdir()
        (self.root/'configs/chat-engine.local.json').write_text('{"enabled":true,"mode":"hybrid"}',encoding='utf-8')
        self.chat=QQConversation(self.root,client=lambda *a,**kw:({'content':'fake answer'},{}))
        self.engine=ChatEngine(self.root);self.engine.headers=lambda:{'Authorization':'Bearer test-only'}
    def tearDown(self):self.tmp.cleanup()
    def request(self,peer='123456789',text='今天有点累'):
        requests=[]
        class Client:
            def __init__(self,*a,**kw):pass
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def post(self,url,*,json):
                requests.append(json)
                return SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'reply':'fake answer','ticket':'a'*48})
        with patch('httpx.Client',Client):
            result=self.engine.converse(peer,text,self.chat,{'bot':'synthetic-bot'})
        self.assertIsNotNone(result)
        return requests[0]
    def test_disabled_persona_does_not_leak_style_examples_or_lore(self):
        self.chat.persona.save(PersonaCard(enabled=False,style='DISABLED_STYLE',
            examples=[Dialogue(user='x',assistant='DISABLED_EXAMPLE')],
            lorebook=[Lore(keys=[],content='DISABLED_LORE',constant=True)]))
        payload=self.request();encoded=json.dumps(payload,ensure_ascii=False)
        for value in ['DISABLED_STYLE','DISABLED_EXAMPLE','DISABLED_LORE']:self.assertNotIn(value,encoded)
        self.assertEqual(payload['persona'],{'enabled':False})
    def test_approved_learning_and_persistent_corrections_reach_only_their_peer(self):
        card=self.chat.persona.load()
        self.chat.learning.add_item('123456789',card,'preference',content='APPROVED_STYLE')
        self.chat.learning.add_item('123456789',card,'preference',content='PENDING_STYLE',status='pending')
        self.chat.learning.add_item('987654321',card,'preference',content='OTHER_PERSON_STYLE')
        mid=self.chat.memory.add('123456789','user','不要每次都追问')
        self.chat.memory.life.observe_correction('123456789',mid)
        with self.chat.memory.db() as db:db.execute('UPDATE messages SET at=0')
        payload=self.request();facts=payload['facts']
        self.assertIn('APPROVED_STYLE',facts);self.assertIn('不要每次都追问',facts)
        self.assertNotIn('PENDING_STYLE',facts);self.assertNotIn('OTHER_PERSON_STYLE',facts)
        self.assertEqual(payload['seed_history'],[])
        other=self.request('987654321')['facts'];self.assertNotIn('APPROVED_STYLE',other);self.assertNotIn('不要每次都追问',other)
    def test_recent_confirmed_legacy_turn_is_not_limited_to_last_six_rows(self):
        self.chat.memory.add('123456789','user','改成蓝色，这是已经确认的改动')
        for i in range(10):self.chat.memory.add('123456789','assistant' if i%2 else 'user','synthetic '+str(i))
        payload=self.request()
        self.assertIn('改成蓝色',json.dumps(payload['seed_history'],ensure_ascii=False))
    def test_same_member_has_separate_private_and_group_memories(self):
        card=self.chat.persona.load()
        scoped=[('123456789','PRIVATE_ONLY'),('group:22222:123456789','GROUP_A_ONLY'),('group:33333:123456789','GROUP_B_ONLY')]
        keys=[]
        for peer,value in scoped:self.chat.learning.add_item(peer,card,'preference',content=value)
        for peer,value in scoped:
            payload=self.request(peer);keys.append(payload['session_key'])
            self.assertIn(value,payload['facts'])
            for _,other in scoped:
                if other!=value:self.assertNotIn(other,payload['facts'])
        self.assertEqual(len(set(keys)),3)
    def test_evidence_budget_keeps_valid_complete_json_and_prioritizes_earlier_records(self):
        result=bounded_facts([{'approved':'keep'},{'large':'x'*300},{'small':'later'}],limit=80)
        self.assertEqual(json.loads(result),[{'approved':'keep'},{'small':'later'}])
    def test_transport_timeout_covers_server_deadline(self):
        timeouts=[]
        class Client:
            def __init__(self,*a,**kw):timeouts.append(kw['timeout'])
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def post(self,*a,**kw):return SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'reply':'fake answer','ticket':'a'*48})
        with patch('httpx.Client',Client):self.engine.converse('123456789','你好',self.chat,{'bot':'synthetic'})
        self.assertEqual(timeouts,[NATIVE_CLIENT_TIMEOUT_SECONDS])
        self.assertGreaterEqual(timeouts[0]-NATIVE_TURN_TIMEOUT_SECONDS,5)


class FakeManager:
    def __init__(self):
        self.stored={'a':[{'role':'assistant','content':'stale red'}],'b':[]};self.writes=[]
    async def get_conversation(self,origin,cid,create_if_not_exists=False):
        return SimpleNamespace(cid=cid,history=json.dumps(self.stored[cid]),token_usage=10)
    async def update_conversation(self,origin,cid=None,history=None,**kwargs):
        if history is not None:self.stored[cid]=copy.deepcopy(history);self.writes.append((origin,cid,copy.deepcopy(history)))


class NativeDraftTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager=FakeManager();self.drafts=ConversationDrafts(self.manager)
    async def test_existing_native_history_is_replaced_by_current_authoritative_context(self):
        old=await self.manager.get_conversation('peer-a','a');seed=[{'role':'user','content':'confirmed blue'}]
        async with self.drafts.turn('peer-a',old,seed) as draft:
            current=await self.manager.get_conversation('peer-a','a')
            self.assertEqual(json.loads(current.history),seed)
            self.assertEqual(current.token_usage,0)
            await self.manager.update_conversation('peer-a','a',history=seed+[{'role':'assistant','content':'unsent'}])
            self.assertIn('unsent',draft.history)
            self.assertNotIn('unsent',json.dumps(self.manager.stored))
        self.assertEqual(self.manager.writes,[])
    async def test_receipt_commits_once_and_preview_or_failure_does_not_commit(self):
        before=copy.deepcopy(self.manager.stored['a']);old=await self.manager.get_conversation('peer-a','a')
        async with self.drafts.turn('peer-a',old,[]) as draft:
            await self.manager.update_conversation('peer-a','a',history=[{'role':'assistant','content':'confirmed reply'}])
            after=json.loads(draft.history)
        self.assertEqual(self.manager.stored['a'],before)
        pending={'t':{'sid':'a','umo':'peer-a','cid':'a','before':before,'after':after,'at':time.monotonic()}}
        statistics={'commits':0,'inflight':0}
        self.assertTrue((await confirm_pending('t',pending,{},statistics,self.manager))['committed'])
        self.assertEqual(self.manager.stored['a'],after);self.assertEqual(len(self.manager.writes),1)
        self.assertFalse((await confirm_pending('t',pending,{},statistics,self.manager))['committed'])
    async def test_exception_and_cancellation_cannot_write_unconfirmed_history(self):
        old=await self.manager.get_conversation('peer-a','a');before=copy.deepcopy(self.manager.stored)
        with self.assertRaises(RuntimeError):
            async with self.drafts.turn('peer-a',old,[]):
                await self.manager.update_conversation('peer-a','a',history=[{'role':'assistant','content':'failed'}])
                raise RuntimeError('synthetic failure')
        ready=asyncio.Event()
        async def cancelled_turn():
            async with self.drafts.turn('peer-a',old,[]):
                await self.manager.update_conversation('peer-a','a',history=[{'role':'assistant','content':'cancelled'}])
                ready.set();await asyncio.Event().wait()
        task=asyncio.create_task(cancelled_turn());await ready.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(self.manager.stored,before);self.assertEqual(self.manager.writes,[])
    async def test_concurrent_peer_views_are_isolated_and_empty_context_removes_old_session(self):
        both=asyncio.Event();arrived=[]
        async def run(cid,content):
            old=await self.manager.get_conversation('peer-'+cid,cid)
            seed=[] if content is None else [{'role':'user','content':content}]
            async with self.drafts.turn('peer-'+cid,old,seed):
                arrived.append(cid)
                if len(arrived)==2:both.set()
                await both.wait()
                return json.loads((await self.manager.get_conversation('peer-'+cid,cid)).history)
        a,b=await asyncio.gather(run('a',None),run('b','other user'))
        self.assertEqual(a,[]);self.assertEqual(b,[{'role':'user','content':'other user'}]);self.assertEqual(self.manager.writes,[])
    async def test_busy_and_expiry_state_tracks_request_exit(self):
        state={'inflight':0};pending={'old':{'at':0},'recent':{'at':120}}
        async with tracked_request(state):
            result=activity_state(pending,state,now=121)
            self.assertTrue(result['busy']);self.assertEqual(result['pending'],1)
        self.assertFalse(activity_state(pending,state,now=121)['busy'])
    async def test_deadline_covers_waiting_for_capacity_and_session_lock(self):
        for blocked in ['capacity','session']:
            state={'inflight':0};gate=asyncio.Semaphore(0 if blocked=='capacity' else 1);lock=asyncio.Lock();entered=False
            if blocked=='session':await lock.acquire()
            with self.assertRaises(TimeoutError):
                async with bounded_turn(state,gate,lock,timeout=.02):entered=True
            self.assertFalse(entered);self.assertEqual(state['inflight'],0)
            if blocked=='session':lock.release()
    async def test_generation_timeout_and_suppressed_cancellation_cannot_extend_turn(self):
        state={'inflight':0};old=await self.manager.get_conversation('peer-a','a');before=copy.deepcopy(self.manager.stored)
        with self.assertRaises(TimeoutError):
            async with bounded_turn(state,asyncio.Semaphore(1),asyncio.Lock(),timeout=.02):
                async with self.drafts.turn('peer-a',old,[]):
                    await self.manager.update_conversation('peer-a','a',history=[{'role':'assistant','content':'not delivered'}])
                    try:await asyncio.sleep(10)
                    except asyncio.CancelledError:pass
        self.assertEqual(state['inflight'],0);self.assertEqual(self.manager.stored,before);self.assertEqual(self.manager.writes,[])


if __name__=='__main__':unittest.main()
