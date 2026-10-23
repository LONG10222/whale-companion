"""CPU-only original-region compositing, explicitly authorized by the owner.

The image tool supplies edited pixels. This module can copy them only outside
the protected person, or inside planned background regions. No resident model.
"""
from pathlib import Path
import hashlib,re,threading,time,uuid
import numpy as np
from PIL import Image,ImageOps,ImageDraw,ImageFilter

LOCK=threading.BoundedSemaphore(1)

def polygons(raw):
    if not isinstance(raw,list) or len(raw)>12:raise ValueError('区域列表无效。')
    result=[]
    for polygon in raw:
        if not isinstance(polygon,list) or not 3<=len(polygon)<=64:raise ValueError('区域轮廓需要3到64个点。')
        points=[]
        for point in polygon:
            if not isinstance(point,(list,tuple)) or len(point)!=2 or any(type(v) not in {int,float} or not 0<=v<=1000 for v in point):raise ValueError('区域坐标必须是0到1000的数字。')
            points.append(tuple(point))
        result.append(points)
    return result

def mask(size,shapes):
    image=Image.new('L',size);draw=ImageDraw.Draw(image)
    for polygon in shapes:draw.polygon([(round(x*(size[0]-1)/1000),round(y*(size[1]-1)/1000)) for x,y in polygon],fill=255)
    return image

def spec(plan):
    value=plan.get('protection')
    if not isinstance(value,dict) or type(value.get('has_person')) is not bool:raise ValueError('本轮没有可靠的人物区域方案，先保留候选供检查。')
    if not value['has_person']:return {'mode':'none','has_person':False}
    mode=value.get('mode')
    if mode not in {'subject_lock','regions_only'}:raise ValueError('有人物的修图必须保护原始人物区域。')
    subject=polygons(value.get('subject_polygons'));core=polygons(value.get('identity_core_polygons',[]));regions=polygons(value.get('edit_regions',[]))
    if not subject or not core:raise ValueError('人物轮廓或五官核心保护区缺失，不能自动采用。')
    area=np.count_nonzero(np.asarray(mask((1000,1000),subject+core)))/1_000_000
    if not .02<=area<=.98:raise ValueError('人物范围不合理：坐标必须按0到1000，不是0到100百分制；请重新核对实际原图。')
    if mode=='regions_only' and not regions:raise ValueError('没有给出允许替换的背景区域。')
    box=value.get('face_box')
    if box is not None and (not isinstance(box,list) or len(box)!=4 or any(type(v) not in {int,float} or not 0<=v<=1000 for v in box) or box[0]>=box[2] or box[1]>=box[3]):raise ValueError('人脸区域无效。')
    return {'has_person':True,'mode':mode,'subject':subject,'core':core,'regions':regions,'face_box':box}

def beauty_base(source,protection,request,core):
    # Beautification is applied to the actual original, not an invented face.
    skin=bool(re.search('磨皮|美皮|美肤|美颜|肤色|皮肤',request)) and not re.search('(?:不要|不用|不做|别).{0,5}(?:磨皮|美皮|美颜)',request)
    slim=bool(re.search('瘦脸|收.{0,3}下颌|脸.{0,3}瘦',request)) and not re.search('(?:不要|不用|不做|别).{0,5}瘦脸',request)
    base=source.copy();info={'skin_requested':skin,'slim_requested':slim,'skin_applied':False,'slim_applied':False}
    if not (skin or slim):return base,info
    box=protection.get('face_box')
    if not box:raise ValueError('用户要求美肤／瘦脸，但方案缺少人脸范围，不能猜位置。')
    w,h=source.size;x1,y1,x2,y2=[round(v*(w if i%2==0 else h)/1000) for i,v in enumerate(box)]
    if x2-x1<24 or y2-y1<24:raise ValueError('人脸区域过小，无法安全完成局部美化。')
    crop=base.crop((x1,y1,x2,y2));cw,ch=crop.size
    if slim:
        from scipy.ndimage import map_coordinates
        arr=np.asarray(crop);yy,xx=np.mgrid[:ch,:cw].astype(np.float32)
        # A small, smooth inverse warp near the lower cheek contour. Central
        # eyes/nose/mouth are restored byte-for-byte after the warp.
        cx=(cw-1)/2;nx=(xx-cx)/(cw/2);ny=yy/max(1,ch-1)
        influence=np.exp(-((np.abs(nx)-.7)/.25)**2)*np.exp(-((ny-.7)/.28)**2)
        sample_x=np.clip(xx+np.sign(nx)*min(12,cw*.018)*influence,0,cw-1)
        arr=np.stack([map_coordinates(arr[:,:,c],[yy,sample_x],order=1,mode='nearest') for c in range(3)],axis=-1)
        crop=Image.fromarray(arr.astype(np.uint8));info['slim_applied']=True;info['max_shift_pixels']=round(min(12,cw*.018),2)
    if skin:
        smooth=crop.filter(ImageFilter.GaussianBlur(max(.7,min(2.5,cw/300))))
        crop=Image.blend(crop,smooth,.16);info['skin_applied']=True;info['skin_blend']=.16
    fade=Image.new('L',(cw,ch));ImageDraw.Draw(fade).ellipse((2,2,cw-3,ch-3),fill=255);fade=fade.filter(ImageFilter.GaussianBlur(max(1,cw*.06)))
    base.paste(Image.composite(crop,source.crop((x1,y1,x2,y2)),fade),(x1,y1))
    base.paste(source,(0,0),core)
    return base,info

def match_region_tones(source,edited,shapes,person,background_strength):
    """Match inpaint patches to the surrounding composite, excluding people."""
    if not shapes:return edited,[]
    from scipy.ndimage import distance_transform_edt
    small_size=(max(1,source.width//4),max(1,source.height//4))
    original_small=np.asarray(source.resize(small_size,Image.Resampling.BOX),dtype=np.float32)
    edited_small=np.asarray(edited.resize(small_size,Image.Resampling.BOX),dtype=np.float32)
    protected=np.asarray(person.resize(small_size,Image.Resampling.NEAREST))>0
    all_regions=np.asarray(mask(small_size,shapes))>0
    desired=original_small*(1-background_strength)+edited_small*background_strength
    matched=edited.copy();records=[]
    for shape in shapes:
        region=mask(small_size,[shape]);distance=distance_transform_edt(np.asarray(region)==0)
        ring=(distance>3)&(distance<24)&~protected&~all_regions
        if int(ring.sum())<64:records.append({'applied':False,'reason':'周围可用背景不足'});continue
        delta=np.clip(np.median(desired[ring]-edited_small[ring],axis=0),-64,64)
        before=float(np.median(np.abs(desired[ring]-edited_small[ring])))
        after=float(np.median(np.abs(desired[ring]-np.clip(edited_small[ring]+delta,0,255))))
        if after>before+1:records.append({'applied':False,'reason':'色调匹配未改善边界'});continue
        lut=[max(0,min(255,round(v+float(channel)))) for channel in delta for v in range(256)]
        corrected=edited.point(lut)
        soft=region.filter(ImageFilter.MaxFilter(31)).filter(ImageFilter.GaussianBlur(4)).resize(source.size,Image.Resampling.BILINEAR)
        soft=Image.fromarray(np.maximum(np.asarray(soft),np.asarray(mask(source.size,[shape]))))
        matched=Image.composite(corrected,matched,soft)
        records.append({'applied':True,'offset_rgb':[round(float(v),2) for v in delta],'ring_pixels':int(ring.sum()),'median_error_before':round(before,2),'median_error_after':round(after,2)})
    return matched,records

def protect_candidate(original,candidate,plan,request,folder,number,*,adjustments=None):
    started=time.monotonic();config=spec(plan)
    if not config['has_person']:return {'path':str(candidate),'applied':False,'reason':'方案确认没有人物；继续正常图像审核。'}
    folder=Path(folder).resolve();candidate=Path(candidate).resolve()
    if not candidate.is_relative_to(folder) or candidate.is_symlink():raise ValueError('生成候选不在当前任务目录。')
    with LOCK:
        with Image.open(original) as im:source=ImageOps.exif_transpose(im).convert('RGB')
        if max(source.size)>4096 or source.width*source.height>20_000_000:raise ValueError('合成输入超过4096工作副本边界。')
        import psutil
        if psutil.virtual_memory().available<source.width*source.height*24+256*1024**2:raise ValueError('当前可用内存不足以安全合成，保留候选等待处理。')
        with Image.open(candidate) as im:
            if abs((im.width/im.height)/(source.width/source.height)-1)>.15:raise ValueError('候选画幅与原图不一致，不能强行合成。')
            edited=ImageOps.exif_transpose(im).convert('RGB').resize(source.size,Image.Resampling.LANCZOS)
        person=mask(source.size,config['subject']);core=mask(source.size,config['core'])
        person=Image.fromarray(np.maximum(np.asarray(person),np.asarray(core)))
        if config.get('face_box'):
            face=Image.new('L',source.size);box=[round(v*(source.width if i%2==0 else source.height)/1000) for i,v in enumerate(config['face_box'])];ImageDraw.Draw(face).rectangle(box,fill=255)
            person=Image.fromarray(np.maximum(np.asarray(person),np.asarray(face)))
        area=float(np.count_nonzero(np.asarray(person)))/(source.width*source.height)
        if not .02<=area<=.98:raise ValueError('人物保护区域过小或覆盖几乎全图，需要重新核对。')
        # Small safety dilation protects stray hair, veil and seams. A hard zero
        # after feathering guarantees no generated pixels enter the person mask.
        person=person.resize((max(1,source.width//4),max(1,source.height//4)),Image.Resampling.NEAREST).filter(ImageFilter.MaxFilter(5)).resize(source.size,Image.Resampling.NEAREST)
        allowed=mask(source.size,config['regions']) if config['mode']=='regions_only' else Image.new('L',source.size,255)
        released_boundary_pixels=0
        if config['mode']=='regions_only':
            inner=person.resize((max(1,source.width//4),max(1,source.height//4)),Image.Resampling.NEAREST).filter(ImageFilter.MinFilter(9)).resize(source.size,Image.Resampling.NEAREST)
            hard=np.asarray(inner)>0
            if config.get('face_box'):
                face=Image.new('L',source.size);box=[round(v*(source.width if i%2==0 else source.height)/1000) for i,v in enumerate(config['face_box'])];ImageDraw.Draw(face).rectangle(box,fill=255);hard|=np.asarray(face)>0
            hard|=np.asarray(core)>0
            release=(np.asarray(allowed)>0)&(np.asarray(person)>0)&~hard
            released_boundary_pixels=int(release.sum())
            if released_boundary_pixels>np.count_nonzero(np.asarray(person))*.08:raise ValueError('去人区域与主体轮廓冲突过大，需要重新规划。')
            keep=np.asarray(person).copy();keep[release]=0;person=Image.fromarray(keep)
        if config['mode']=='regions_only':
            # Feather outward, never turn the interior of a removal region
            # translucent. Expand the seed conservatively before softening.
            small_allowed=allowed.resize((max(1,source.width//4),max(1,source.height//4)),Image.Resampling.NEAREST)
            outside=small_allowed.filter(ImageFilter.MaxFilter(15)).resize(source.size,Image.Resampling.BILINEAR).filter(ImageFilter.GaussianBlur(max(1,min(source.size)*.004)))
            alpha=Image.fromarray(np.maximum(np.asarray(allowed),np.asarray(outside)))
        else:alpha=allowed.filter(ImageFilter.GaussianBlur(max(1,min(source.size)*.004)))
        effects_requested=bool(re.search('特效|光效|光感|柔光|光晕',request)) and not re.search('(?:不要|不用|不做|不加|别|无需).{0,5}(?:特效|光效|光感|柔光|光晕)',request)
        background_effect_strength=.35 if config['mode']=='regions_only' and effects_requested else 0
        broad_background=bool(effects_requested and re.search('合适.{0,5}都可以|特效.{0,5}都可以|你.{0,4}决定|看着.{0,3}弄',request) and not re.search('其他不动|背景不动|不要改背景|只去.{0,3}人',request))
        if broad_background and config['mode']=='regions_only':background_effect_strength=1
        from scipy.ndimage import distance_transform_edt
        adjustments=adjustments or {}
        if set(adjustments)-{'effect_strength','feather_scale','exclude_polygons'}:raise ValueError('不支持的本地返修参数。')
        mask_repair_pixels=0;replacement=Image.new('L',source.size)
        if adjustments.get('exclude_polygons'):
            exclusion=np.asarray(mask(source.size,polygons(adjustments['exclude_polygons'])))>0
            hard=np.asarray(core)>0
            if config.get('face_box'):
                face=Image.new('L',source.size);box=[round(v*(source.width if i%2==0 else source.height)/1000) for i,v in enumerate(config['face_box'])];ImageDraw.Draw(face).rectangle(box,fill=255);hard|=np.asarray(face)>0
            remove=exclusion&(np.asarray(person)>0)&~hard;mask_repair_pixels=int(remove.sum())
            if mask_repair_pixels+released_boundary_pixels>(np.count_nonzero(np.asarray(person))+released_boundary_pixels)*.08:raise ValueError('蒙版修正超过8%安全边界，保留候选供人工检查。')
            keep=np.asarray(person).copy();keep[remove]=0;person=Image.fromarray(keep)
            replace=exclusion&~hard
            if np.count_nonzero(replace)>(np.count_nonzero(np.asarray(person))+released_boundary_pixels)*.08:raise ValueError('背景局部修正范围过大，不能自动采用。')
            replacement=Image.fromarray((replace*255).astype(np.uint8));allowed=Image.fromarray(np.maximum(np.asarray(allowed),np.asarray(replacement)))
        small=person.resize((max(1,source.width//4),max(1,source.height//4)),Image.Resampling.NEAREST)
        distance=distance_transform_edt(np.asarray(small)==0)
        if background_effect_strength and 'effect_strength' in adjustments:background_effect_strength=min(background_effect_strength,float(adjustments['effect_strength']))
        feather=float(adjustments.get('feather_scale',1))
        if not 1<=feather<=3:raise ValueError('羽化调整超出允许范围。')
        ramp=np.clip(distance/max(4,min(source.size)*.035*feather/4),0,1)
        ramp=ramp*ramp*(3-2*ramp)
        fade=Image.fromarray((ramp*255).astype(np.uint8)).resize(source.size,Image.Resampling.BILINEAR)
        if config['mode']=='regions_only':
            effect=Image.fromarray((np.asarray(fade,dtype=np.uint16)*round(255*background_effect_strength)//255).astype(np.uint8))
            alpha=Image.fromarray(np.maximum(np.asarray(alpha),np.asarray(effect)))
        else:alpha=Image.fromarray((np.asarray(alpha,dtype=np.uint16)*np.asarray(fade,dtype=np.uint16)//255).astype(np.uint8))
        opaque=bool(config['mode']=='regions_only' or re.search('换背景|背景换|去.{0,5}路人|移除|去掉.{0,3}人',request))
        strength=.55 if not opaque else 1.0
        if 'effect_strength' in adjustments:
            requested=float(adjustments['effect_strength'])
            if not .2<=requested<=1:raise ValueError('光效调整超出允许范围。')
            if not opaque:strength=min(strength,requested)
        if strength<1:alpha=alpha.point(lambda x:round(x*strength))
        alpha=Image.fromarray(np.maximum(np.asarray(alpha),np.asarray(replacement)))
        alpha.paste(0,(0,0),person)
        edited,tone_matching=match_region_tones(source,edited,config['regions'],person,background_effect_strength) if config['mode']=='regions_only' else (edited,[])
        base,beauty=beauty_base(source,config,request,core)
        output=Image.composite(edited,base,alpha);output.paste(base,(0,0),person)
        # Explicit pixel invariants are checked on the final lossless output.
        protected=np.asarray(person)>0;core_pixels=np.asarray(core)>0
        digest=hashlib.sha256(candidate.read_bytes()).hexdigest();stem='protected-r7-'+str(number)+'-'+digest[:12]+'-'+uuid.uuid4().hex[:8]
        target=folder/(stem+'.webp');output.save(target,format='WEBP',lossless=True,method=3)
        if target.stat().st_size>10*1024**2:
            target=folder/(stem+'.png');output.save(target,format='PNG',compress_level=6)
        if target.stat().st_size>10*1024**2:raise ValueError('无损保护成品超过QQ发送上限，留本机检查，不擅自压缩人物细节。')
        with Image.open(target) as checked:actual=np.asarray(checked.convert('RGB'))
        changed=int(np.count_nonzero(np.any(actual[protected]!=np.asarray(base)[protected],axis=1)))
        core_changed=int(np.count_nonzero(np.any(actual[core_pixels]!=np.asarray(source)[core_pixels],axis=1)))
        removal_pixels=(np.asarray(allowed)==255)&~protected if config['mode']=='regions_only' else np.zeros_like(protected)
        translucent_removal=int(np.count_nonzero(np.asarray(alpha)[removal_pixels]<255))
        if translucent_removal:raise ValueError('去路人区域未保持不透明，已停止采用。')
        face_changed=None
        if config.get('face_box'):
            box=[round(v*(source.width if i%2==0 else source.height)/1000) for i,v in enumerate(config['face_box'])]
            original_face=np.asarray(source.crop(box));current_face=actual[box[1]:box[3],box[0]:box[2]]
            face_changed=int(np.count_nonzero(np.any(original_face!=current_face,axis=-1)))
        if changed or core_changed:raise ValueError('无损输出未通过人物区域像素检查。')
        mask_path=folder/(stem+'-person-mask.png');person.save(mask_path)
        alpha_path=folder/(stem+'-edit-mask.png');alpha.save(alpha_path)
        return {'path':str(target),'applied':True,'algorithm_version':7,'effect_strength':strength,'background_effect_strength':background_effect_strength,'broad_background_effects':broad_background,'released_boundary_pixels':released_boundary_pixels,'mask_repair_pixels':mask_repair_pixels,'feather_scale':feather,'tone_matching':tone_matching,'mode':config['mode'],'generated_candidate_path':str(candidate),'generated_candidate_sha256':digest,
                'person_mask':str(mask_path),'edit_mask':str(alpha_path),'person_fraction':round(area,4),'protected_pixels':int(protected.sum()),
                'protected_pixels_changed':changed,'identity_core_pixels_changed':core_changed,'original_face_box_pixels_changed':face_changed,'beauty':beauty,'output_dimensions':list(source.size),
                'opaque_removal_pixels':int(removal_pixels.sum()),'translucent_removal_pixels':translucent_removal,
                'lossless':True,'cpu_only':True,'elapsed_seconds':round(time.monotonic()-started,2)}
