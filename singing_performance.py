"""Score-time controls and gentle F0 conditioning; never time-stretch finished vocals."""
import copy,math

def prepare_performance(payload):
    if payload.get('_performance_applied'):return payload
    out=copy.deepcopy(payload);tempo=float(out.get('tempo',1));rhythm=out.get('rhythm','normal')
    if not .8<=tempo<=1.15:raise ValueError('歌唱速度需在0.8至1.15之间。')
    gap=out.get('phrase_gap')
    gap=float((.20 if rhythm=='connected' else .40 if rhythm=='gentle' else .30) if gap is None else gap)
    if not .10<=gap<=.8:raise ValueError('句间停顿需在0.10至0.80秒之间。')
    for phrase in out['phrases']:
        phrase['durations']=[round(float(v)/tempo,5) for v in phrase['durations']]
        phrase['pre_gap']=.12 if rhythm=='connected' else .18
        phrase['post_gap']=gap
    out['_performance_applied']=True;return out

def steady_f0(values,seconds,phrases=None):
    import numpy as np
    from scipy.ndimage import median_filter,gaussian_filter1d
    from score_adapter import midi
    original=np.asarray(values,dtype=np.float32);output=original.copy();valid=original>50
    if not len(original) or not valid.any():return output,{'applied':False,'reason':'No voiced frames'}
    dt=seconds/len(original);cents=np.zeros_like(original);cents[valid]=1200*np.log2(original[valid]/440)
    smoothed=cents.copy()
    edges=np.diff(np.r_[False,valid,False].astype(int));runs=list(zip(np.where(edges==1)[0],np.where(edges==-1)[0]))
    for a,b in runs:
        if b-a>=5:smoothed[a:b]=gaussian_filter1d(median_filter(cents[a:b],size=5,mode='nearest'),max(1,.035/dt),mode='nearest')
    guide=smoothed.copy();times=np.arange(len(original))*dt;offset=0;guided=0
    for phrase in phrases or []:
        offset+=float(phrase.get('pre_gap',.18))
        for note,duration in zip(phrase['notes'],phrase['durations']):
            duration=float(duration);mask=valid&(times>=offset)&(times<offset+duration)
            if mask.any():
                target=(midi(note)-69)*100
                # Keep transitions soft and preserve a small amount of real pitch motion.
                weight=np.minimum(np.clip((times[mask]-offset)/.10,0,1),np.clip((offset+duration-times[mask])/.08,0,1))
                interior=target+np.clip(smoothed[mask]-target,-35,35)*.30
                guide[mask]=smoothed[mask]*(1-weight)+interior*weight;guided+=int(mask.sum())
            offset+=duration
        offset+=float(phrase.get('post_gap',.30))
    output[valid]=440*np.power(2,guide[valid]/1200)
    changed=np.abs(guide[valid]-cents[valid])
    return output,{'applied':True,'method':'voiced-only median/35ms smoothing plus soft score anchor','guided_frames':guided,
        'voicing_mask_preserved':True,'median_change_cents':round(float(np.median(changed)),2),'p90_change_cents':round(float(np.percentile(changed,90)),2)}
