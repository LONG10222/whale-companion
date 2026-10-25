"""Remove excessive outer silence without touching words, inner pauses or speed."""
def trim_edges(audio,sample_rate):
    import numpy as np
    samples=np.asarray(audio,dtype=np.float32)
    width=max(1,round(sample_rate*.02));count=len(samples)//width
    if not count:return samples,{'trimmed_seconds':0}
    frames=samples[:count*width].reshape(count,width)
    energy=np.sqrt(np.mean(frames*frames,axis=1))
    active=np.flatnonzero(energy>max(.001,float(energy.max())*.015))
    if not len(active):return samples,{'trimmed_seconds':0}
    start=max(0,int(active[0])*width-round(.18*sample_rate))
    end=min(len(samples),(int(active[-1])+1)*width+round(.25*sample_rate))
    # Short lead-ins and natural tail breath remain exactly as generated.
    if start<sample_rate*.6:start=0
    if len(samples)-end<sample_rate*.6:end=len(samples)
    return samples[start:end],{'trimmed_seconds':round((start+len(samples)-end)/sample_rate,3),'playback_rate':1}
