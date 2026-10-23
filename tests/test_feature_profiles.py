import json
from pathlib import Path
import tempfile
import time
import unittest

from feature_profiles import FeatureProfiles, recommendation


def hardware(ram=32768, vram=16384, free=14000):
    return {'cpu_name': 'Synthetic 8-core CPU', 'physical_cores': 8, 'logical_cores': 16,
            'ram_total_mib': ram, 'ram_available_mib': ram // 2,
            'gpus': [{'index': 0, 'name': 'Synthetic GPU', 'total_mib': vram, 'free_mib': free,
                       'utilization_percent': 0}] if vram else [], 'at': time.time()}


class HardwareProfileTests(unittest.TestCase):
    def test_32gb_16gb_machine_uses_bounded_local_workers(self):
        result = recommendation(hardware())
        self.assertTrue(result['local_gpu_suitable'])
        self.assertEqual(result['settings']['gpu_memory_fraction'], 0.4)
        self.assertEqual(result['learning']['threads'], 2)
        self.assertGreaterEqual(result['settings']['min_free_vram_mib'], 12000)
        self.assertLessEqual(result['settings']['idle_seconds'], 30)

    def test_low_memory_or_cpu_only_keeps_background_models_paused(self):
        result = recommendation(hardware(ram=8192, vram=0))
        self.assertFalse(result['local_gpu_suitable'])
        self.assertEqual(result['learning']['mode'], 'paused')
        self.assertIn('voice_input', result['selected'])
        self.assertNotIn('singing', result['selected'])

    def test_busy_gpu_advice_does_not_claim_to_own_other_app_memory(self):
        result = recommendation(hardware(free=4000))
        self.assertTrue(any('当前可用显存不足' in item for item in result['notes']))
        self.assertIn('不是', result['scope'])

    def test_save_preserves_keys_paths_and_updates_real_audio_budget_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'configs').mkdir()
            (root / 'settings.local.json').write_text(json.dumps({'api_token': 'local-fixture', 'python': 'own-python'}))
            (root / 'dania.local.json').write_text(json.dumps({'dania_model_dir': 'my-models', 'cpu_threads': 6}))
            (root / 'score.local.json').write_text(json.dumps({'soulx': {'python': 'keep-runtime'},
                'dania_conversion': {'reference_audio': 'keep-private-reference'}}))
            profiles = FeatureProfiles(root, probe=hardware)
            profiles.save('quiet', ['chat', 'voice_input'], {'cpu_threads': 2})
            saved = json.loads((root / 'settings.local.json').read_text())
            self.assertEqual(saved['api_token'], 'local-fixture')
            self.assertEqual(saved['python'], 'own-python')
            self.assertEqual(saved['cpu_threads'], 2)
            dania = json.loads((root / 'dania.local.json').read_text())
            self.assertEqual(dania['dania_model_dir'], 'my-models')
            self.assertEqual(dania['cpu_threads'], 2)
            score=json.loads((root/'score.local.json').read_text())
            self.assertEqual(score['soulx']['cpu_threads'],2)
            self.assertEqual(score['soulx']['gpu_memory_fraction'],.55)
            self.assertEqual(score['soulx']['python'],'keep-runtime')
            self.assertEqual(score['dania_conversion']['reference_audio'],'keep-private-reference')
            self.assertEqual(score['dania_conversion']['gpu_memory_fraction'],.45)
            self.assertEqual(json.loads((root / 'configs/local-learning.local.json').read_text())['mode'], 'paused')

    def test_resource_form_cannot_change_credentials_models_or_permissions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'settings.local.json').write_text('{"api_token":"keep"}')
            profiles = FeatureProfiles(root, probe=hardware)
            for overrides in [{'api_token': 'replacement'}, {'model': 'other-model'}, {'cpu_threads': 99}, {'idle_seconds': 1}]:
                with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                    profiles.save('balanced', ['chat'], overrides)
            self.assertEqual(json.loads((root / 'settings.local.json').read_text())['api_token'], 'keep')

    def test_manual_conversion_and_cache_expiry_are_saved_with_synthesis_ceiling(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'settings.local.json').write_text('{"api_token":"keep"}')
            profiles = FeatureProfiles(root, probe=hardware)
            profiles.save('balanced', ['chat'], {'singing_gpu_memory_fraction': .5,
                'conversion_gpu_memory_fraction': .6, 'cache_ttl_hours': 8})
            settings = json.loads((root / 'settings.local.json').read_text())
            self.assertEqual(settings['conversion_gpu_memory_fraction'], .5)
            self.assertEqual(settings['cache_ttl_hours'], 8)
            self.assertEqual(settings['api_token'], 'keep')

    def test_uninstalled_capabilities_stay_pending_and_all_function_families_are_listed(self):
        with tempfile.TemporaryDirectory() as folder:
            state = FeatureProfiles(Path(folder), probe=hardware).state()
            self.assertGreaterEqual(len(state['catalog']), 19)
            for name in ['voice_input', 'voice_output', 'singing', 'drawing', 'photo_edit', 'learning']:
                self.assertFalse(state['readiness'][name]['ready'])
            self.assertEqual(state['service_tier'], 'default')
            self.assertIn('5.6 Terra', state['role_policy']['plus'])


if __name__ == '__main__':
    unittest.main()
