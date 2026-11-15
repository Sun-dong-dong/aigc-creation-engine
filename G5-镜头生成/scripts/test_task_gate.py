"""Integrated offline gates. All task documents and collision targets are temporary."""
import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name('validate_task.py')
spec = importlib.util.spec_from_file_location('g5_integrated', SCRIPT)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='aigc-task-gate-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.light = 'A 5600K window light from the east wall illuminates the left side of the man face.'
        self.prompt = ('integrated_multimodal_description:\n[Shot 1] Natural photographic texture. '
                       'A daytime room. Medium shot. Camera stays still. The man sits. ' + self.light +
                       ' No on-screen text. No subtitles. No background music.\n'
                       'overall_soundscape:\nRoom tone.\nnon_diegetic_music:\nN/A')
        self.write('source.md', '## 镜头01\nMedium shot. Camera stays still. The man sits. ' + self.light)
        self.write('prompt.txt', self.prompt)
        self.write('draft.txt', self.prompt)
        fixed = {'project_id': 'test', 'version': 'v1', 'confirmed': True,
                 'confirmation_evidence': 'Synthetic approval fixture, not real approval.',
                 'styles': {'h3-base': 'Natural photographic texture.'},
                 'scenes': [{'id': 'room-day', 'applies_to': 'Daytime room', 'confirmed': True,
                             'confirmation_evidence': 'Synthetic scene fixture',
                             'texts': {'h3-base': 'A daytime room.'}}]}
        self.write('fixed.json', fixed)
        sourcehash = self.hash('source.md')
        self.write('plan.json', {'scope_shots': ['S01'], 'source_sha256': [sourcehash],
                   'groups': [{'id': 'A', 'shots': ['S01'], 'duration': 5, 'reason': 'User requests one shot.'}], 'boundaries': []})
        self.delivery = ('| 镜头 | 时间（估算） | 景别与运镜 | 画面、表演和台词 |\n'
                         '|---|---|---|---|\n| 1 | 0—5秒 | 中景，固定 | 男子坐着，窗光5600K照亮左脸。无台词。 |\n\n'
                         '**参考资产：**无外部参考，本段纯文生。\n\n```text\n' + self.prompt + '\n```\n')
        self.write('delivery.md', self.delivery)
        self.task = {'task_id': 'test', 'profile': 'h3-base', 'mode': 'T2VA', 'entry': 'h3-native-prompt',
                     'sound_policy': 'sound', 'prompt_file': 'prompt.txt', 'delivery_file': 'delivery.md',
                     'plan_file': 'plan.json', 'group_id': 'A',
                     'source_files': [{'path': 'source.md', 'sha256': sourcehash}], 'references': [],
                     'dependency_files': [], 'continuity_context': {'applicable': False, 'basis': 'First segment.'},
                     'reference_requirements': {'reviewed': True, 'basis': 'Synthetic pure-text task.', 'items': []},
                     'duration_assessment': {'source_seconds': 5, 'basis': 'Synthetic timing estimate', 'status': 'estimated'},
                     'shots': [{'id': 'S01', 'dialogue': []}],
                     'parameters': {'duration': 5, 'aspect_ratio': '16:9', 'audio': True},
                     'official_reads': json.loads((g.BASE / '模型模板/分支配置.json').read_text())['h3-base']['official_urls'],
                     'runtime': {'verified': True, 'checked_at': 'synthetic', 'evidence': 'Synthetic only'},
                     'authorization': {'generate': True, 'scope': 'Synthetic only; no model calls.'},
                     'fixed_context': {'path': 'fixed.json', 'sha256': self.hash('fixed.json'), 'project_id': 'test', 'version': 'v1',
                                       'scene_ids': ['room-day'], 'reviewed': True, 'review_evidence': 'Synthetic scope review'},
                     'draft_review': {'path': 'draft.txt', 'sha256': self.hash('draft.txt'), 'completed': True,
                                      'reviewer': 'fixture', 'reviewed_at': 'synthetic', 'review_method': 'independent_subagent',
                                      'review_evidence': 'Synthetic record, not actual independent review.',
                                      'reviewed_shots': ['S01'], 'summary': 'Fixture only', 'issues': []},
                     'review': {'fingerprint': '', 'reviewer': 'fixture', 'reviewed_at': 'synthetic',
                                'delivery': {'status': 'pass', 'sha256': self.hash('delivery.md'), 'evidence': 'Synthetic table and copy check'},
                                'source_bindings': {'S01': {'path': 'source.md', 'heading': '## 镜头01'}},
                                'coverage': {'S01': {e: {'status': 'pass', 'source_quote': 'The man sits.', 'prompt_quote': 'The man sits.'} for e in g.ELEMENTS}},
                                'detail_checks': {'S01': [{'item': 'Sitting', 'status': 'pass', 'source_quote': 'The man sits.', 'prompt_quote': 'The man sits.'}]},
                                'judgments': {e: {'status': 'pass', 'evidence': 'Synthetic fixture check'} for e in g.JUDGMENTS},
                                'lighting': {'S01': {'temperature_quote': '5600K', 'source_quote': 'window light from the east wall',
                                            'characters_present': True, 'characters': [{'subject': 'man', 'prompt_quote': self.light}], 'basis': 'Synthetic one man scene.'}}}}
        self.save()

    def write(self, name, data):
        p = self.root / name
        p.write_text(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False), encoding='utf-8')
        return p

    def hash(self, name):
        return g.sha((self.root / name).read_bytes())

    def save(self, rebind=True):
        p = self.write('task.json', self.task)
        if rebind:
            self.task['review']['fingerprint'] = g.fingerprint(p)
            self.write('task.json', self.task)
        return p

    def report(self):
        return g.validate(self.root / 'task.json')

    def test_valid_and_generation_boundary(self):
        self.assertTrue(self.report()['generation_ready'], self.report())
        for scope in [' ', True, [], None]:
            self.task['authorization']['scope'] = scope
            self.save()
            r = self.report()
            self.assertTrue(r['formal_prompt_ready'])
            self.assertFalse(r['generation_ready'])

    def test_all_bound_file_changes_invalidate(self):
        for name in ['prompt.txt', 'source.md', 'fixed.json', 'plan.json', 'draft.txt', 'delivery.md']:
            with self.subTest(name=name):
                p = self.root / name
                original = p.read_bytes()
                before = g.fingerprint(self.root / 'task.json')
                p.write_bytes(original + b'\n')
                self.assertNotEqual(before, g.fingerprint(self.root / 'task.json'))
                self.assertFalse(self.report()['formal_prompt_ready'])
                p.write_bytes(original)

    def test_delivery_structure_and_prompt_copy(self):
        cases = [self.delivery.replace('|---|---|---|---|\n', ''),
                 self.delivery.replace('景别与运镜 | ', ''),
                 self.delivery.replace('| 1 |', '| 99 |'),
                 self.delivery.replace('Camera stays still.', 'The camera moves.'),
                 self.delivery.replace('**参考资产：**无外部参考，本段纯文生。', '**参考资产：**'),
                 self.delivery + '\n```text\nExtra copy\n```\n']
        for value in cases:
            with self.subTest(value=value[:80]):
                self.write('delivery.md', value)
                self.task['review']['delivery']['sha256'] = self.hash('delivery.md')
                self.save()
                self.assertFalse(self.report()['formal_prompt_ready'])
        (self.root / 'delivery.md').unlink()
        self.assertFalse(self.report()['formal_prompt_ready'])

    def test_non_uploaded_predecessor_binding(self):
        self.write('previous.bin', b'previous'.decode())
        self.task['dependency_files'] = [{'path': 'previous.bin', 'sha256': self.hash('previous.bin'), 'role': 'previous_segment_video'}]
        self.task['continuity_context'] = {'applicable': True, 'basis': 'Same scene follow-on segment',
                                          'dependency_path': 'previous.bin', 'frame_time_seconds': 4.9}
        self.save()
        self.assertTrue(self.report()['formal_prompt_ready'], self.report())
        self.write('previous.bin', 'new version')
        self.assertFalse(self.report()['formal_prompt_ready'])
        self.task['dependency_files'][0]['sha256'] = self.hash('previous.bin')
        self.task['continuity_context']['frame_time_seconds'] = None
        self.save()
        self.assertFalse(self.report()['formal_prompt_ready'])

    def test_complete_source_id_and_explicit_mapping(self):
        self.write('suffix.md', '## 镜头39B\nThe man sits.')
        sources = [{'path': 'suffix.md'}]
        bindings = {'S39A': {'path': 'suffix.md', 'heading': '## 镜头39B'}}
        errors, pending = [], []
        g.shot_source_blocks(self.root, sources, bindings, [{'id': 'S39A'}], errors, pending)
        self.assertTrue(errors)
        bindings['S39A'].update(source_shot_id='S39B', mapping_evidence='Confirmed split mapping fixture')
        errors, pending = [], []
        blocks = g.shot_source_blocks(self.root, sources, bindings, [{'id': 'S39A'}], errors, pending)
        self.assertFalse(errors)
        self.assertIn('S39A', blocks)

    def test_deferred_text_requires_scope_and_real_handoff(self):
        detail = {'item': 'Post text fixture', 'status': 'deferred_post', 'scope': 'shared',
                  'source_quote': 'The man sits.', 'post_note': 'POST-01'}
        self.task['review']['detail_checks']['S01'] = [detail]
        self.save()
        self.assertFalse(self.report()['formal_prompt_ready'])
        detail['scope_reason'] = 'Synthetic shared scope justification'
        self.write('post.md', 'S01 | POST-01 | The man sits. | Not a real production text event.')
        self.task['post_handoff_file'] = 'post.md'
        self.save()
        self.assertTrue(self.report()['formal_prompt_ready'], self.report())
        self.write('post.md', 'Missing transfer item')
        self.assertFalse(self.report()['formal_prompt_ready'])

    def test_draft_post_issue_requires_matching_real_index(self):
        self.task['draft_review']['issues'] = [{'shot': 'S01', 'finding': 'Post transfer fixture',
            'source_quote': 'The man sits.', 'resolution': 'deferred_post', 'post_note': 'POST-01',
            'revision_quote': '', 'resolved': True}]
        self.save()
        self.assertFalse(self.report()['formal_prompt_ready'])
        self.write('post.md', '| POST-01 | S01 | The man sits. |')
        self.task['post_handoff_file'] = 'post.md'
        self.save()
        self.assertTrue(self.report()['formal_prompt_ready'], self.report())
        self.write('post.md', '| POST-01 | S02 | Other source |\n| POST-02 | S01 | The man sits. |')
        self.save()
        self.assertFalse(self.report()['formal_prompt_ready'])

    def test_post_handoff_requires_full_shot_and_record_id(self):
        self.task['review']['detail_checks']['S01'] = [{'item': 'Post fixture', 'status': 'deferred_post',
            'source_quote': 'The man sits.', 'post_note': 'POST-01'}]
        self.task['post_handoff_file'] = 'post.md'
        for value in ['| S010 | The man sits. | POST-01 |', '| S01 | The man sits. | POST-010 |']:
            self.write('post.md', value)
            self.save()
            self.assertFalse(self.report()['formal_prompt_ready'])
        self.write('post.md', '| S01 | The man sits. | POST-01 |')
        self.save()
        self.assertTrue(self.report()['formal_prompt_ready'], self.report())

    def test_lighting_embedded_quotes_and_spoken_text(self):
        light = 'A 5600K window light gives "wraparound" illumination on the man face.'
        t = copy.deepcopy(self.task)
        t['review']['lighting']['S01'].update(source_quote='window light', characters=[{'subject': 'man', 'prompt_quote': light}])
        errors = []
        g.check_shot_lighting(t, {'S01': light}, errors)
        self.assertFalse(errors)
        g.check_shot_lighting(t, {'S01': '<d>[English] ' + light + '</d>'}, errors)
        self.assertTrue(errors)

    def test_report_collision_cli_never_changes_inputs(self):
        self.write('previous.bin', 'previous')
        self.task['dependency_files'] = [{'path': 'previous.bin', 'sha256': self.hash('previous.bin'), 'role': 'audit'}]
        self.write('post.md', 'Post handoff')
        self.task['post_handoff_file'] = 'post.md'
        self.save()
        for name in ['task.json', 'prompt.txt', 'draft.txt', 'source.md', 'fixed.json', 'plan.json', 'delivery.md', 'previous.bin', 'post.md']:
            target = self.root / name
            original = target.read_bytes()
            outputs = [target, self.root / 'alias']
            for alias_kind in ['same', 'hard', 'soft']:
                alias = self.root / 'alias'
                alias.unlink(missing_ok=True)
                if alias_kind == 'hard': os.link(str(target), str(alias))
                if alias_kind == 'soft': alias.symlink_to(target)
                output = target if alias_kind == 'same' else alias
                with self.subTest(name=name, alias=alias_kind):
                    r = subprocess.run([sys.executable, str(SCRIPT), str(self.root / 'task.json'), '--report', str(output)], capture_output=True)
                    self.assertNotEqual(r.returncode, 0)
                    self.assertEqual(original, target.read_bytes())
        output = self.root / 'reports' / 'result.json'
        r = subprocess.run([sys.executable, str(SCRIPT), str(self.root / 'task.json'), '--report', str(output)], capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(json.loads(output.read_text())['formal_prompt_ready'])


if __name__ == '__main__':
    unittest.main()
