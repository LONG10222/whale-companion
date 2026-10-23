"""Native per-turn structured output contracts for image planning and review."""
def obj(properties):return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}
TEXT={'type':'string'}
TEXTS={'type':'array','items':TEXT,'maxItems':12}
NUMBER={'type':'number','minimum':0,'maximum':1000}
POLYGONS={'type':'array','maxItems':12,'items':{'type':'array','minItems':3,'maxItems':64,'items':{'type':'array','minItems':2,'maxItems':2,'items':NUMBER}}}
PLAN=obj({'requested_changes':TEXTS,'preserve':TEXTS,'style':TEXT,'needs_clarification':{'type':'boolean'},'question':TEXT,
          'protection':obj({'has_person':{'type':'boolean'},'mode':{'type':'string','enum':['subject_lock','regions_only','none']},
                            'coordinate_max':{'type':'integer','enum':[1000]},'subject_polygons':POLYGONS,'identity_core_polygons':POLYGONS,
                            'face_box':{'anyOf':[{'type':'array','minItems':4,'maxItems':4,'items':NUMBER},{'type':'null'}]},'edit_regions':POLYGONS})})
REVIEW=obj({'decision':{'type':'string','enum':['pass','refine','reject','uncertain']},
            'checks':{'type':'array','maxItems':10,'items':obj({'criterion':{'type':'string','enum':['identity','requested_changes','artifacts','composition','lighting','style']},'status':{'type':'string','enum':['pass','fail','uncertain']},'evidence':TEXT})},
            'critical_failures':TEXTS,'fixes':TEXTS,'summary':TEXT,'comparison':TEXT,
            'local_refinement':{'anyOf':[{'type':'null'},obj({'action':{'type':'string','enum':['soften_composite','repair_protection_mask']},'effect_strength':{'type':'number','minimum':.2,'maximum':1},'feather_scale':{'type':'number','minimum':1,'maximum':3},'exclude_polygons':POLYGONS,'reason':TEXT})]}})
