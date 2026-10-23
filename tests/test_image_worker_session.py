from pathlib import Path
from unittest.mock import patch
import json,tempfile,unittest
from PIL import Image
from codex_workbench import CodexWorkbench
from qq_images import QQImages

class WorkerSessionTests(unittest.TestCase):
    def test_inspection_interrupts_unexpected_image_generation_before_accepting_a_result(self):
        for event_name in ['item/started','item/completed']:
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);source=root/'source.png';Image.new('RGB',(64,64),'blue').save(source);sha=QQImages(root).prepare(source.read_bytes())['sha256'];calls=[]
                class RPC:
                    def __init__(self,*args):self.named=''
                    def __enter__(self):return self
                    def __exit__(self,*args):pass
                    def request(self,method,p,**kwargs):
                        calls.append(method)
                        if method=='thread/read':return {'thread':{'id':'owned','projectId':'project','name':self.named}}
                        if method=='thread/name/set':self.named=p['name'];return {}
                        if method=='thread/start':return {'thread':{'id':'owned','projectId':'project'},'model':p['model']}
                        if method=='turn/start':return {'turn':{'id':'turn'}}
                        return {}
                    def next(self,*args):return {'method':event_name,'params':{'threadId':'owned','turnId':'turn','item':{'type':'imageGeneration','id':'unexpected'}}}
                worker=CodexWorkbench(root);worker.workspace=root/'tasks';worker.workspace.mkdir()
                with patch('codex_workbench.CodexRPC',RPC),patch.object(worker,'ensure_project',return_value='project'):
                    with worker.session(),self.assertRaises(RuntimeError) as error:
                        worker.run('a'*32,'只做JSON检查',model='gpt-6-luna',effort='high',inspection=True,image_workflow=True,kind='draw_picture',source_images=[sha],output_schema={'type':'object'})
                self.assertTrue(error.exception.no_retry);self.assertEqual(error.exception.image_tool_calls_observed,1);self.assertIn('turn/interrupt',calls)
    def test_request_keeps_notifications_for_the_turn_consumer(self):
        from codex_rpc import CodexRPC
        rpc=CodexRPC(Path('.'));rpc.send=lambda *_:None
        event={'method':'thread/tokenUsage/updated','params':{'threadId':'owned'}}
        rpc.events.put(event);rpc.events.put({'id':1,'result':{'ok':True}})
        self.assertEqual(rpc.request('probe',{}),{'ok':True});self.assertEqual(rpc.next(),event)
    def test_read_generate_read_share_one_stable_catalog_without_resuming_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source.png';Image.new('RGB',(64,64),'blue').save(source)
            sha=QQImages(root).prepare(source.read_bytes())['sha256'];calls=[];opened=[]
            class RPC:
                def __init__(self,*args):self.events=[];self.named='';self.loaded=False;opened.append(args)
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def request(self,method,p):
                    calls.append((method,p))
                    if method=='thread/read':return {'thread':{'id':'owned','projectId':'project','name':self.named}}
                    if method=='thread/start':self.loaded=True;return {'thread':{'id':'owned','projectId':'project'},'model':p['model']}
                    if method=='thread/resume':raise AssertionError('A live writer must not be resumed between phases')
                    if method=='thread/name/set':self.named=p['name'];return {}
                    if method=='turn/start':
                        tid='turn-'+str(len(calls));items=[]
                        if 'outputSchema' in p:items=[{'method':'item/completed','params':{'threadId':'owned','turnId':tid,'item':{'type':'agentMessage','phase':'final_answer','text':'{"ok":true}'}}}]
                        else:
                            output=Path(p['cwd'])/'real-test-output.png';Image.new('RGB',(64,64),'red').save(output)
                            items=[{'method':'item/started','params':{'threadId':'owned','turnId':tid,'item':{'type':'imageGeneration','id':'native-image'}}},{'method':'item/completed','params':{'threadId':'owned','turnId':tid,'item':{'type':'imageGeneration','id':'native-image','savedPath':str(output)}}}]
                        self.events=items+[{'method':'turn/completed','params':{'threadId':'owned','turnId':tid,'turn':{'status':'completed'}}}]
                        return {'turn':{'id':tid}}
                    return {}
                def next(self,*args):return self.events.pop(0)
            worker=CodexWorkbench(root);worker.workspace=root/'tasks';worker.workspace.mkdir();tid=''
            schema={'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok'],'additionalProperties':False}
            with patch('codex_workbench.CodexRPC',RPC),patch.object(worker,'ensure_project',return_value='project'):
                with worker.session():
                    for inspection,model in [(True,'gpt-6-luna'),(False,'gpt-6.1-sol'),(True,'gpt-5.6-terra')]:
                        result=worker.run('a'*32,'只检查JSON' if inspection else '编辑原图',model=model,effort='high',thread_id=tid,source_images=[sha],image=not inspection,inspection=inspection,image_workflow=True,kind='draw_picture',output_schema=schema if inspection else None)
                        tid=result['thread_id']
            self.assertEqual(len(opened),1);self.assertTrue(opened[0][1]['features.image_generation'])
            starts=[p for method,p in calls if method=='thread/start'];self.assertEqual(len(starts),1)
            self.assertTrue(starts[0]['config']['features.image_generation']);self.assertFalse(starts[0]['config']['features.shell_tool'])
            turns=[p for method,p in calls if method=='turn/start'];self.assertEqual([p['model'] for p in turns],['gpt-6-luna','gpt-6.1-sol','gpt-5.6-terra'])
            self.assertTrue(all(p['environments']==[] and p['serviceTier']=='default' for p in turns))

if __name__=='__main__':unittest.main()
