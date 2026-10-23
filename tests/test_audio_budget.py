import json
from pathlib import Path
import tempfile
import unittest

from audio_budget import apply


class CentralAudioBudgetTests(unittest.TestCase):
    def test_each_stage_uses_its_budget_without_replacing_private_model_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'settings.local.json').write_text(json.dumps({'cpu_threads': 4,
                'gpu_memory_fraction': .4, 'min_free_vram_mib': 12000,
                'singing_gpu_memory_fraction': .65, 'conversion_gpu_memory_fraction': .55,
                'singing_min_free_vram_mib': 10240}), encoding='utf-8')
            config = {'python': 'operator-runtime', 'reference_audio': 'operator-reference',
                      'dania_model_dir': 'operator-model', 'cpu_threads': 12}
            for script, fraction in [('voice_worker_dania.py', .4),
                ('soulx_singing_worker.py', .65), ('singing_worker.py', .55)]:
                with self.subTest(stage=script):
                    result = apply(root, script, config)
                    self.assertEqual(result['gpu_memory_fraction'], fraction)
                    self.assertEqual(result['cpu_threads'], 4)
                    for key in ['python', 'reference_audio', 'dania_model_dir']:
                        self.assertEqual(result[key], config[key])
            self.assertEqual(config['cpu_threads'], 12)

    def test_no_central_config_retains_existing_worker_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            config = {'gpu_memory_fraction': .55, 'python': 'runtime'}
            self.assertEqual(apply(folder, 'singing_worker.py', config), config)

