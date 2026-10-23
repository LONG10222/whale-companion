from pathlib import Path
from types import SimpleNamespace
import json,tempfile,unittest
from PIL import Image
from image_workflow import ImageWorkflow,image_choice,routes,clean_review,parse_object
from qq_images import QQImages
from codex_rpc import validate_choice

def assessment(passed=True,decision=None):
    return {'decision':decision or ('pass' if passed else 'refine'),
        'checks':[{'criterion':key,'status':'pass' if passed else 'fail','evidence':'对照原图，主体与具体细节具有明确的可见依据。'} for key in ['identity','requested_changes','artifacts']],
        'critical_failures':[],'fixes':[] if passed else ['修正人物衣服边缘的明显瑕疵'],'summary':'逐项对照后的判断。'}

class FakeImageWorkbench:
    def __init__(self,root,reviews=None):
        self.workspace=root/'tasks';self.workspace.mkdir();self.calls=[];self.reviews=list(reviews or [assessment()]);self.outputs=0
        self.plan=None
    def inspect_images(self):pass
    def run(self,jid,prompt,**kw):
        self.calls.append((prompt,kw));folder=Path(kw['directory']);folder.mkdir(exist_ok=True)
        if kw.get('inspection'):
            data={'requested_changes':['移除背景路人'],'preserve':['人物花束'],'style':'自然光感','needs_clarification':False,'protection':{'has_person':False}} if '为修图整理' in prompt else self.reviews.pop(0)
            if '为修图整理' in prompt and self.plan is not None:data=self.plan
            text=data if isinstance(data,str) else json.dumps(data,ensure_ascii=False);images=[]
        else:
            self.outputs+=1;path=folder/f'v{self.outputs}.png';Image.new('RGB',(600,900),'red' if self.outputs==1 else 'green').save(path)
            images=[str(path)];text='实际图片结果'
        kw['progress'](thread_id='one-project-thread',project_id='bound-project',title='测试 · 修图',directory=str(folder),model=kw['model'],effort=kw['effort'])
        return {'text':text,'images':images,'thread_id':'one-project-thread','project_id':'bound-project','directory':str(folder),'model':kw['model'],'effort':kw['effort'],'elapsed_seconds':1}

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        path=self.root/'source.png';Image.new('RGB',(600,900),'blue').save(path)
        self.source=QQImages(self.root).prepare(path.read_bytes());self.worker=FakeImageWorkbench(self.root)
    def tearDown(self):self.temp.cleanup()
    def run_workflow(self,**kw):
        request=kw.pop('request','保留主体人物，去掉路人，适度增加美观特效')
        return ImageWorkflow(self.root,self.worker).run('a'*32,request,[self.source['sha256']],**kw)
    def test_admin_uses_sol_xhigh_and_stops_when_first_version_passes(self):
        result=self.run_workflow();self.assertEqual(result['quality_status'],'passed');self.assertEqual(len(result['images']),1)
        calls=[k for _,k in self.worker.calls];self.assertEqual(calls[1]['model'],'gpt-6.1-sol');self.assertEqual(calls[1]['effort'],'xhigh')
        self.assertEqual(calls[2]['model'],'gpt-5.6-terra');self.assertEqual(self.worker.outputs,1)
        self.assertEqual(result['model'],'gpt-6.1-sol');self.assertEqual(result['last_stage_model'],'gpt-5.6-terra')
        self.assertEqual({k['thread_id'] for k in calls[1:]},{'one-project-thread'})
    def test_concrete_defect_triggers_one_lower_cost_refinement(self):
        self.worker.reviews=[assessment(False),assessment()];result=self.run_workflow()
        self.assertEqual(result['workflow']['selected_version'],2);self.assertEqual(self.worker.outputs,2)
        generation=[k for _,k in self.worker.calls if not k['inspection']]
        self.assertEqual(generation[1]['model'],'gpt-5.6-terra');self.assertEqual(generation[1]['reference_roles'],['identity_reference','edit_target'])
        self.assertEqual(len(result['workflow']['versions']),2)
    def test_no_passed_version_means_no_automatic_image(self):
        self.worker.reviews=[assessment(False),assessment(False,decision='reject')];result=self.run_workflow()
        self.assertEqual(result['quality_status'],'needs_review');self.assertEqual(result['images'],[]);self.assertIsNone(result['workflow']['selected_version'])
        self.assertEqual(self.worker.outputs,2)
    def test_review_failure_never_gets_treated_as_pass(self):
        self.worker.reviews=['not json'];result=self.run_workflow()
        self.assertEqual(result['quality_status'],'needs_review');self.assertEqual(result['images'],[]);self.assertEqual(self.worker.outputs,1)
    def test_plus_and_user_model_bounds_apply_to_every_stage(self):
        for role,expected in [('plus',{'gpt-5.6-terra','gpt-6-luna'}),('user',{'gpt-6-luna'})]:
            route=routes(role);self.assertTrue({r['model'] for r in route.values()}<=expected)
        with self.assertRaises(ValueError):image_choice('plus','gpt-6.1-sol','high')
        with self.assertRaises(ValueError):image_choice('user','gpt-5.6-terra','low')
        with self.assertRaises(ValueError):image_choice('admin','gpt-6-astra','low')
        with self.assertRaises(ValueError):image_choice('admin','gpt-6.1-sol','max')
    def test_general_work_still_cannot_use_xhigh(self):
        with self.assertRaises(ValueError):validate_choice('gpt-6.1-sol','xhigh')
        validate_choice('gpt-6.1-sol','xhigh',image_workflow=True)
    def test_empty_source_and_outside_directory_are_rejected(self):
        with self.assertRaises(ValueError):ImageWorkflow(self.root,self.worker).run('a'*32,'修图',[])
        with self.assertRaises(ValueError):self.run_workflow(directory=str(self.root/'outside'))
    def test_cancel_before_inference_calls_no_model(self):
        with self.assertRaises(RuntimeError):self.run_workflow(cancelled=lambda:True)
        self.assertFalse(self.worker.calls)
    def test_read_only_source_is_unchanged_and_versions_are_kept(self):
        original=Path(self.source['path']).read_bytes();self.worker.reviews=[assessment(False),assessment()]
        result=self.run_workflow();self.assertEqual(Path(self.source['path']).read_bytes(),original)
        self.assertTrue(all(Path(v['path']).is_file() for v in result['workflow']['versions']))
    def test_model_total_score_cannot_replace_evidence(self):
        self.assertFalse(clean_review({'decision':'pass','score':100})['passed'])
        self.assertFalse(clean_review({'decision':'pass','checks':[{'criterion':'identity','status':'pass','evidence':'好'}]})['passed'])
    def test_structural_aspect_change_is_rejected_before_visual_pass(self):
        output={'images':[str(self.root/'wrong.png')],'model':'gpt-6.1-sol','effort':'xhigh'}
        Image.new('RGB',(900,600),'red').save(output['images'][0])
        workflow=ImageWorkflow(self.root,self.worker);result=workflow.candidate(output,self.root,self.source['sha256'],(600,900),1)
        self.assertFalse(result['structural']['passed'])
    def test_candidate_normalizes_task_directory_before_containment_check(self):
        alias=self.root/'alias';alias.mkdir()
        output={'images':[str(self.root/'canonical.png')]}
        Image.new('RGB',(600,900),'red').save(output['images'][0])
        result=ImageWorkflow(self.root,self.worker).candidate(output,alias/'..',self.source['sha256'],(600,900),1)
        self.assertTrue(result['structural']['passed'])
    def test_normalized_task_directory_still_rejects_external_candidate(self):
        folder=self.root/'allowed';folder.mkdir()
        outside=self.root/'outside.png';Image.new('RGB',(600,900),'red').save(outside)
        with self.assertRaisesRegex(ValueError,'不在本任务目录'):
            ImageWorkflow(self.root,self.worker).candidate({'images':[str(outside)]},folder,self.source['sha256'],(600,900),1)
    def test_extra_observed_image_call_consumes_remaining_revision_budget(self):
        original=self.worker.run
        def run(*a,**kw):
            result=original(*a,**kw)
            if not kw['inspection']:result['image_tool_calls_observed']=2
            return result
        self.worker.run=run;self.worker.reviews=[assessment(False)]
        result=self.run_workflow()
        self.assertEqual(result['quality_status'],'needs_review');self.assertEqual(self.worker.outputs,1)
        self.assertEqual(result['workflow']['image_calls'],2)
    def test_rejected_composite_can_receive_one_automatic_local_repair_and_review(self):
        self.worker.plan={'protection':{'has_person':True,'mode':'subject_lock','subject_polygons':[[[300,100],[700,100],[700,1000],[300,1000]]],'identity_core_polygons':[[[440,200],[560,200],[560,350],[440,350]]],'face_box':[350,100,650,450]}}
        bad=assessment(True,decision='reject');bad['checks'][2]['status']='fail'
        bad['local_refinement']={'action':'soften_composite','effect_strength':.35,'feather_scale':1.8,'reason':'人物保护区外接缝过硬、背景光效强度明显过高。'}
        self.worker.reviews=[bad,assessment()]
        result=self.run_workflow(request='保留人物，适度增加柔光')
        self.assertEqual(result['quality_status'],'passed');self.assertEqual(result['workflow']['selected_version'],2)
        self.assertEqual(self.worker.outputs,1);self.assertEqual(result['workflow']['analysis_calls'],3)
        self.assertTrue(any(s['phase']=='local_refine' for s in result['workflow']['stages']))
        first,second=result['workflow']['versions'];self.assertNotEqual(first['path'],second['path'])
        self.assertTrue(Path(first['path']).is_file());self.assertEqual(second['protection']['effect_strength'],.35)
    def test_bad_plan_stops_before_spending_an_image_call(self):
        self.worker.plan={}
        result=self.run_workflow();self.assertEqual(result['quality_status'],'needs_review')
        self.assertEqual(self.worker.outputs,0);self.assertEqual(result['workflow']['image_calls'],0)
    def test_invalid_plan_can_be_corrected_once_within_analysis_budget(self):
        original=self.worker.run;count=[0]
        def run(*a,**kw):
            if kw.get('inspection') and '为修图整理' in a[1]:
                count[0]+=1;self.worker.plan={} if count[0]==1 else {'protection':{'has_person':False}}
            return original(*a,**kw)
        self.worker.run=run;result=self.run_workflow()
        self.assertEqual(result['quality_status'],'passed');self.assertEqual(result['workflow']['analysis_calls'],3)
        self.assertEqual(self.worker.outputs,1);self.assertEqual(len(result['workflow']['planning_errors']),1)
        self.assertIn('output_schema',self.worker.calls[0][1]);self.assertNotIn('output_schema',next(k for _,k in self.worker.calls if not k['inspection']))
    def test_uncertain_identity_cannot_be_repaired_by_dimming_background(self):
        data=assessment(False,decision='reject');data['local_refinement']={'action':'soften_composite','reason':'背景接缝需要进一步柔化处理。','effect_strength':.3,'feather_scale':2}
        self.assertIsNone(clean_review(data)['local_refinement'])
    def test_mask_repair_keeps_original_light_settings_and_has_one_final_review(self):
        self.worker.plan={'protection':{'has_person':True,'mode':'regions_only','subject_polygons':[[[300,100],[700,100],[700,1000],[300,1000]]],'identity_core_polygons':[[[440,200],[560,200],[560,350],[440,350]]],'face_box':[350,100,650,450],'edit_regions':[[[0,300],[310,300],[310,900],[0,900]]]}}
        bad=assessment(True,decision='refine');bad['checks'][2]['status']='fail'
        bad['local_refinement']={'action':'repair_protection_mask','effect_strength':.2,'feather_scale':2.5,'exclude_polygons':[[[300,500],[315,500],[315,700],[300,700]]],'reason':'主体外侧粗蒙版误保留了小块背景，清除误保护范围。'}
        self.worker.reviews=[bad,assessment()]
        result=self.run_workflow(request='保留人物去路人，添加特效，合适的都可以')
        self.assertEqual(result['quality_status'],'passed');self.assertEqual(self.worker.outputs,1)
        first,second=result['workflow']['versions'];self.assertEqual(second['protection']['background_effect_strength'],first['protection']['background_effect_strength'])
        self.assertEqual(second['protection']['feather_scale'],1);self.assertEqual(second['protection']['identity_core_pixels_changed'],0)
    def test_rejected_candidate_with_specific_content_fixes_can_retry_one_image(self):
        self.worker.reviews=[assessment(False,decision='reject'),assessment()]
        result=self.run_workflow();self.assertEqual(result['quality_status'],'passed')
        self.assertEqual(self.worker.outputs,2);self.assertEqual(result['workflow']['selected_version'],2)

class WorkflowRouteTests(unittest.TestCase):
    def setUp(self):
        from tests import test_image_edits as fixture
        self.case=fixture.ImageEditTests();self.case.setUp();self.root=self.case.root
        from fastapi import FastAPI,Header,HTTPException
        from fastapi.testclient import TestClient
        from image_workflow_routes import register_image_workflow
        app=FastAPI()
        def auth(authorization:str=Header(default='')):
            if authorization!='Bearer owner':raise HTTPException(401)
        register_image_workflow(app,root=self.root,authorize=auth,service=self.case.service)
        self.client=TestClient(app);self.headers={'Authorization':'Bearer owner'}
    def tearDown(self):self.case.tearDown()
    def test_controller_authentication_and_no_model_or_quota_override(self):
        self.assertEqual(self.client.get('/qq/creative/workflow/state').status_code,401)
        bad=self.client.post('/qq/creative/workflow/config',headers=self.headers,json={'enabled':True,'max_image_calls':99})
        self.assertEqual(bad.status_code,422)
        result=self.client.post('/qq/creative/workflow/config',headers=self.headers,json={'enabled':True});self.assertEqual(result.status_code,200)
        data=self.client.get('/qq/creative/workflow/state',headers=self.headers).json()
        self.assertTrue(data['enabled']);self.assertEqual(data['roles']['plus']['primary']['model'],'gpt-5.6-terra');self.assertEqual(data['limits']['max_image_calls'],2)
    def test_source_picker_only_contains_current_owners_images(self):
        with self.case.chat.memory.db() as db:db.execute("INSERT INTO image_messages VALUES('76543210',999,?,9999999999)",('a'*64,))
        data=self.client.get('/qq/creative/workflow/state',headers=self.headers).json()
        self.assertEqual([s['sha256'] for s in data['sources']],[self.case.image['sha256']])
        self.assertEqual(self.client.get('/qq/creative/workflow/sources/'+('a'*64),headers=self.headers).status_code,404)
    def test_unknown_source_and_recipient_override_are_rejected(self):
        body={'text':'帮我弄好看些','source_sha256':'a'*64}
        self.assertEqual(self.client.post('/qq/creative/workflow/preview',headers=self.headers,json=body).status_code,409)
        self.assertEqual(self.client.post('/qq/creative/workflow/preview',headers=self.headers,json={**body,'peer':'76543210'}).status_code,422)
    def candidate_job(self,delivery='preview',outside=False):
        folder=self.case.folder/'workflow';folder.mkdir(exist_ok=True)
        path=(self.root if outside else folder)/'candidate.png';Image.new('RGB',(600,900),'red').save(path)
        workflow={'versions':[{'version':1,'path':str(path),'structural':{'passed':True},'review':{'passed':False}}],'selected_version':None,'status':'needs_review'}
        with self.case.service.db() as db:
            db.execute('INSERT INTO jobs(id,peer,bot,mid,epoch,kind,args,state,at,updated,deliver,delivery,result,error,directory,workflow) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       ('b'*32,self.case.peer,self.case.bot,'','unit','draw_picture','{}','needs_review',1,1,0,delivery,json.dumps({'metadata':{'workflow':workflow},'attachments':[]}),'',str(folder),json.dumps(workflow)))
    def test_owner_can_preview_and_select_without_automatic_send(self):
        self.candidate_job()
        image=self.client.get('/qq/creative/workflow/jobs/'+('b'*32)+'/versions/1',headers=self.headers);self.assertEqual(image.status_code,200)
        selected=self.client.post('/qq/creative/workflow/jobs/'+('b'*32)+'/select',headers=self.headers,json={'version':1})
        self.assertEqual(selected.status_code,200,selected.text);self.assertFalse(selected.json()['sent']);self.assertFalse(self.case.bridge.events)
        with self.case.service.db() as db:row=db.execute('SELECT state,delivery FROM jobs WHERE id=?',('b'*32,)).fetchone()
        self.assertEqual(tuple(row),('completed','preview'))
    def test_unknown_receipt_and_outside_candidate_cannot_be_republished(self):
        self.candidate_job(delivery='uncertain')
        selected=self.client.post('/qq/creative/workflow/jobs/'+('b'*32)+'/select',headers=self.headers,json={'version':1})
        self.assertEqual(selected.status_code,409)
        with self.case.service.db() as db:db.execute('DELETE FROM jobs')
        self.candidate_job(outside=True)
        image=self.client.get('/qq/creative/workflow/jobs/'+('b'*32)+'/versions/1',headers=self.headers)
        self.assertEqual(image.status_code,409)
    def test_complete_workflow_progress_is_saved_in_actual_job_queue(self):
        worker=FakeImageWorkbench(self.root);self.case.service.workbench=worker
        source_path=self.root/'large.png';Image.new('RGB',(600,900),'blue').save(source_path)
        image=self.case.chat.images.prepare(source_path.read_bytes());mid=self.case.chat.memory.add(self.case.peer,'user','这张是要修的原图')
        self.case.chat.memory.remember_images(self.case.peer,mid,[image])
        result=self.case.service.request(self.case.peer,'draw_picture',{'prompt':'保留主体，去掉路人'},'帮我修图，保留主体，去掉路人',context={'deliver':False,'_image_edit':True,'_workflow_preview':True,'source_image_shas':[image['sha256']]})
        self.case.service.pool.submit(lambda:None).result(8)
        row=self.case.service.recent()[0];self.assertEqual(row['state'],'completed',row.get('error'));self.assertEqual(row['phase'],'selected')
        self.assertEqual(row['workflow']['selected_version'],1);self.assertEqual(row['delivery'],'preview')
        self.assertEqual(row['project_id'],'bound-project');self.assertTrue(row['result']['attachments'])
    def test_authorized_group_jobs_are_visible_but_revoked_group_media_is_blocked(self):
        self.candidate_job();scope='group:11223344:'+self.case.peer
        self.case.bridge.groups.allowed=lambda *_:True
        with self.case.service.db() as db:db.execute('UPDATE jobs SET args=?',(json.dumps({'_memory_peer':scope,'_image_workflow':True}),))
        data=self.client.get('/qq/creative/workflow/state',headers=self.headers).json()
        self.assertEqual(len(data['jobs']),1);self.assertEqual(data['jobs'][0]['conversation_scope'],'group')
        self.case.bridge.groups.allowed=lambda *_:False
        data=self.client.get('/qq/creative/workflow/state',headers=self.headers).json();self.assertFalse(data['jobs'])
        self.assertEqual(self.client.get('/qq/creative/workflow/jobs/'+('b'*32)+'/versions/1',headers=self.headers).status_code,409)
    def test_modified_candidate_cannot_reuse_previous_quality_approval(self):
        import hashlib
        self.candidate_job()
        with self.case.service.db() as db:
            workflow=json.loads(db.execute('SELECT workflow FROM jobs').fetchone()[0]);candidate=workflow['versions'][0]
            candidate['sha256']=hashlib.sha256(Path(candidate['path']).read_bytes()).hexdigest()
            db.execute('UPDATE jobs SET workflow=?',(json.dumps(workflow),))
        Path(candidate['path']).write_bytes(b'changed after review')
        response=self.client.post('/qq/creative/workflow/jobs/'+('b'*32)+'/select',headers=self.headers,json={'version':1})
        self.assertEqual(response.status_code,409);self.assertFalse(self.case.bridge.events)
    def test_per_job_cancel_does_not_stop_another_job(self):
        import threading
        self.candidate_job()
        with self.case.service.db() as db:db.execute("UPDATE jobs SET state='running',args=?",(json.dumps({'_image_workflow':True}),))
        target=threading.Event();other=threading.Event()
        self.case.service.cancels.update({'b'*32:target,'c'*32:other})
        response=self.client.post('/qq/creative/workflow/jobs/'+('b'*32)+'/cancel',headers=self.headers,json={})
        self.assertEqual(response.status_code,200);self.assertTrue(target.is_set());self.assertFalse(other.is_set())
    def test_task_original_is_authenticated_and_bound_to_recorded_source(self):
        self.candidate_job()
        with self.case.service.db() as db:db.execute('UPDATE jobs SET args=?',(json.dumps({'_source_image_shas':[self.case.image['sha256']]}),))
        url='/qq/creative/workflow/jobs/'+('b'*32)+'/original'
        self.assertEqual(self.client.get(url).status_code,401)
        response=self.client.get(url,headers=self.headers)
        self.assertEqual(response.status_code,200);self.assertEqual(response.content,Path(self.case.image['path']).read_bytes())
        source=Path(self.case.image['path']);folder=Path(self.case.service.workflow_job('b'*32)['directory'])
        copy=folder/('reference-user-'+self.case.image['sha256']+'-1'+source.suffix);copy.write_bytes(source.read_bytes());source.unlink()
        self.assertEqual(self.client.get(url,headers=self.headers).content,copy.read_bytes())
        with self.case.service.db() as db:db.execute('UPDATE jobs SET peer=?',('76543210',))
        self.assertEqual(self.client.get(url,headers=self.headers).status_code,409)

class WorkflowRoleBoundaryTests(unittest.TestCase):
    def test_actual_task_acceptance_rejects_plus_sol_and_user_terra(self):
        from tests import test_qq_roles_naming as fixture
        case=fixture.RoleTests();case.setUp()
        try:
            (case.root/'configs').mkdir(exist_ok=True);(case.root/'configs/image-workflow.local.json').write_text('{"enabled":true}')
            case.service.workbench.inspect_images=lambda:None
            with self.assertRaises(ValueError):case.service.request(case.other,'draw_picture',{'prompt':'修图','model':'gpt-5.6-terra'},'帮我修图')
            case.service.contacts.set_plus(case.other,True)
            with self.assertRaises(ValueError):case.service.request(case.other,'draw_picture',{'prompt':'修图','model':'gpt-6.1-sol'},'帮我修图')
            self.assertFalse(case.calls)
        finally:case.tearDown()

if __name__=='__main__':unittest.main()
