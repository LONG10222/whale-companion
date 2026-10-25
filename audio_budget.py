"""Apply the saved central budget without changing model or voice paths."""
import json
from pathlib import Path


def apply(root, script, config):
    file = Path(root) / 'settings.local.json'
    if not file.exists():
        return dict(config)
    settings = json.loads(file.read_text(encoding='utf-8'))
    result = dict(config)
    if 'cpu_threads' in settings:
        result['cpu_threads'] = max(1, min(8, int(settings['cpu_threads'])))
    if script.startswith('voice_worker'):
        for key in ['gpu_memory_fraction','min_free_vram_mib']:
            if key in settings:result[key]=settings[key]
    if script in {'soulx_singing_worker.py', 'score_singing_worker.py', 'music_worker.py', 'singing_worker.py'}:
        fraction = 'conversion_gpu_memory_fraction' if script == 'singing_worker.py' else 'singing_gpu_memory_fraction'
        if fraction in settings:
            result['gpu_memory_fraction'] = max(.1, min(.75, float(settings[fraction])))
        if 'singing_min_free_vram_mib' in settings:
            result['min_free_vram_mib'] = max(6144, int(settings['singing_min_free_vram_mib']))
    if settings.get('performance_mode')=='full':
        import os
        result.update(cpu_threads=max(1,os.cpu_count() or 1),gpu_memory_fraction=.95,performance_mode='full')
    return result
