"""Integrated offline gates. All task documents and collision targets are temporary."""
import copy
import importlib.util
import json
import os
from unittest import mock
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

    def test_image_reference_requires_decodable_media(self):
        self.assertTrue(self.report()['generation_ready'])
        label = '<Picture 1>'
        prefix = ('For the target video, at 0.00 seconds into the target video, '
                  f'{label} (from [Shot 1]) is fully referenced.\n\n')
        self.write('prompt.txt', prefix + self.prompt)
        self.write('delivery.md', self.delivery.replace(self.prompt, prefix + self.prompt))
        self.task['review']['delivery']['sha256'] = self.hash('delivery.md')
        self.task['mode'] = 'I2VA'
        image = self.root / 'reference.png'
        decoder = g.find_ffmpeg()
        self.assertIsNotNone(decoder, '此集成测试需要可执行的 FFmpeg')
        subprocess.run([decoder, '-v', 'error', '-f', 'lavfi', '-i', 'color=s=16x16',
                        '-frames:v', '1', str(image)], check=True)
        reference = {'path': image.name, 'sha256': self.hash(image.name), 'label': label,
                     'role': 'first frame', 'selected': True, 'selection_evidence': 'synthetic only'}
        self.task['references'] = [reference]
        self.save()
        result = self.report()
        self.assertTrue(result['generation_ready'], result)
        image.write_text('Plain text labelled as a PNG in the test.')
        reference['sha256'] = self.hash(image.name)
        self.save()
        result = self.report()
        self.assertFalse(result['generation_ready'], result)
        self.assertTrue(any('参考无法按标签要求解码' in e for e in result['errors']), result)

    def test_ffmpeg_locator_prefers_override_then_path_then_local_bin(self):
        binary = self.root / 'explicit-ffmpeg'
        binary.touch(mode=0o700)
        local = self.root / '.local/bin/ffmpeg'
        local.parent.mkdir(parents=True)
        local.touch(mode=0o700)
        with mock.patch.dict(os.environ, FFMPEG_BIN=str(binary)), mock.patch.object(
                g.shutil, 'which', return_value='/path-bin/ffmpeg') as which:
            self.assertEqual(g.find_ffmpeg(), str(binary))
            which.assert_not_called()
            binary.chmod(0o600)
            self.assertEqual(g.find_ffmpeg(), '/path-bin/ffmpeg')
            which.return_value = None
            with mock.patch.object(Path, 'home', return_value=self.root):
                self.assertEqual(g.find_ffmpeg(), str(local))

    def test_real_video_audio_reference_streams(self):
        decoder = g.find_ffmpeg()
        self.assertIsNotNone(decoder, '此集成测试需要可执行的 FFmpeg')
        subprocess.run([decoder, '-v', 'error', '-f', 'lavfi', '-i', 'color=s=16x16',
                        '-f', 'lavfi', '-i', 'sine=frequency=440', '-t', '0.2',
                        '-c:v', 'ffv1', '-c:a', 'pcm_s16le', str(self.root / 'av.mkv')], check=True)
        subprocess.run([decoder, '-v', 'error', '-i', str(self.root / 'av.mkv'), '-vn',
                        str(self.root / 'sound.wav')], check=True)
        for label, file, valid in [('@视频1', 'av.mkv', True), ('<Audio 1>', 'sound.wav', True),
                                   ('<Video 1>', 'sound.wav', False)]:
            with self.subTest(label=label, file=file):
                errors, pending = [], []
                g.check_reference_decoding(self.root, [{'label': label, 'path': file}], errors, pending)
                self.assertEqual(pending, [])
                self.assertEqual(not errors, valid, errors)

    def test_unknown_model_family_is_rejected(self):
        original = g.inputs
        def unsupported(path, task):
            prompt, profile, files, fingerprint = original(path, task)
            return prompt, dict(profile, family='unknown'), files, fingerprint
        with mock.patch.object(g, 'inputs', side_effect=unsupported):
            result = self.report()
        self.assertFalse(result['formal_prompt_ready'])
        self.assertTrue(any('unknown' in error for error in result['errors']), result)

    def test_bom_inputs_pass_full_gate(self):
        for name in ('source.md', 'prompt.txt', 'draft.txt', 'fixed.json', 'delivery.md'):
            p = self.root / name
            p.write_text(p.read_text(), encoding='utf-8-sig')
        plan = json.loads((self.root / 'plan.json').read_text())
        plan['source_sha256'] = [self.hash('source.md')]
        (self.root / 'plan.json').write_text(json.dumps(plan), encoding='utf-8-sig')
        self.task['source_files'][0]['sha256'] = self.hash('source.md')
        self.task['fixed_context']['sha256'] = self.hash('fixed.json')
        self.task['draft_review']['sha256'] = self.hash('draft.txt')
        self.task['review']['delivery']['sha256'] = self.hash('delivery.md')
        task = self.save()
        task.write_text(task.read_text(), encoding='utf-8-sig')
        result = self.report()
        self.assertTrue(result['generation_ready'], result)

    def test_measured_duration_is_checked_even_without_compression(self):
        for seconds, formal, ready in [(5, True, True), (9, True, False), (-3, False, False)]:
            self.task['duration_assessment'] = {'source_seconds': 5, 'basis': 'estimate',
                'status': 'measured', 'measured_seconds': seconds,
                'method': 'timeline', 'evidence': 'timed reading'}
            self.save()
            result = self.report()
            self.assertEqual(result['formal_prompt_ready'], formal, result)
            self.assertEqual(result['generation_ready'], ready, result)
        self.task['duration_assessment'] = {'source_seconds': 9, 'basis': 'estimate', 'status': 'bogus'}
        self.save()
        self.assertFalse(self.report()['formal_prompt_ready'])

    def test_compact_review_requires_each_shot_and_all_elements(self):
        self.task['review'].pop('coverage')
        self.task['review'].pop('detail_checks')
        self.task['review']['shot_reviews'] = {'S01': {'status': 'pass', 'checked': list(g.ELEMENTS)}}
        self.save()
        self.assertTrue(self.report()['formal_prompt_ready'], self.report())
        self.task['review']['shot_reviews']['S01']['checked'].pop()
        self.save()
        self.assertFalse(self.report()['formal_prompt_ready'], self.report())
        for invalid in [{}, {'S01': 'pass'}, {'S01': {'status': 'pass', 'checked': [{}]}}]:
            self.task['review']['shot_reviews'] = invalid
            self.save()
            self.assertFalse(self.report()['formal_prompt_ready'], self.report())

    def test_chinese_dialogue_estimate_over_duration_flags_risk(self):
        self.task['shots'][0]['dialogue'] = [{'text': '一二三四五六七八九十一二三四五六七八九十一二三四',
                                               'speaker': 'S1', 'voice': 'OS'}]
        self.save()
        result = self.report()
        self.assertIn('4字符', result['duration_risk'])
        self.assertFalse(result['generation_ready'])

    def test_compact_rejects_conflicting_legacy_and_accepts_empty_compact_for_legacy(self):
        self.task['review']['shot_reviews'] = {}
        self.save()
        self.assertTrue(self.report()['formal_prompt_ready'], self.report())
        self.task['review']['shot_reviews'] = {'S01': {'status': 'pass', 'checked': list(g.ELEMENTS)}}
        self.save()
        self.assertFalse(self.report()['formal_prompt_ready'], self.report())

    def test_chinese_lower_bound_survives_mixed_dialogue(self):
        self.task['shots'][0]['dialogue'] = [
            {'text': '一二三四五六七八九十一二三四五六七八九十一二三!', 'speaker': 'S1', 'voice': 'OS'},
            {'text': '等我2分钟', 'speaker': 'S1', 'voice': 'OS'}]
        self.save()
        result = self.report()
        self.assertIn('4字符', result['duration_risk'])
        self.assertIn('数字', result['duration_note'])
        self.assertFalse(result['generation_ready'])

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

    def test_prefix_and_chinese_source_ids_are_distinct_and_bound(self):
        for sid, heading in [('A01', '镜头A01'), ('S01', '镜头01'), ('02a', '镜头02A'), ('三', '镜头三')]:
            self.write('ids.md', '## ' + heading + '\nThe man sits.')
            errors, pending = [], []
            blocks = g.shot_source_blocks(self.root, [{'path': 'ids.md'}],
                {sid: {'path': 'ids.md', 'heading': '## ' + heading}}, [{'id': sid}], errors, pending)
            self.assertFalse(errors, (sid, errors))
            self.assertIn(sid, blocks)
        for other in ['01A', 'B01', 'A02']:
            self.assertNotEqual(g.normalize_shot_id('A01'), g.normalize_shot_id(other))
        self.assertNotEqual(g.normalize_shot_id('一'), g.normalize_shot_id('1'))

    def test_source_title_allows_parenthesized_description_without_partial_id_match(self):
        for title in ('镜头 02（近景）', '镜头02 (close-up)', '镜头02【近景】'):
            self.write('ids.md', '## ' + title + '\nbody')
            errors, pending = [], []
            blocks = g.shot_source_blocks(self.root, [{'path': 'ids.md'}],
                {'02': {'path': 'ids.md', 'heading': '## ' + title}}, [{'id': '02'}], errors, pending)
            self.assertFalse(errors, (title, errors))
            self.assertIn('02', blocks)
        for title in ('镜头02A（近景）', '镜头020（近景）', '镜头02-03'):
            self.write('ids.md', '## ' + title + '\nbody')
            errors, pending = [], []
            g.shot_source_blocks(self.root, [{'path': 'ids.md'}],
                {'02': {'path': 'ids.md', 'heading': '## ' + title}}, [{'id': '02'}], errors, pending)
            self.assertTrue(errors, title)

    def test_source_binding_rejects_wrong_and_ambiguous_titles(self):
        for heading, text in [('镜头01A', '## 镜头01A\nbody'),
                              ('镜头B01', '## 镜头B01\nbody'),
                              ('镜头A02', '## 镜头A02\nbody'),
                              ('镜头A01', '## 镜头A01\nfirst\n## 镜头A01\nsecond')]:
            self.write('ids.md', text)
            errors, pending = [], []
            g.shot_source_blocks(self.root, [{'path': 'ids.md'}],
                {'A01': {'path': 'ids.md', 'heading': '## ' + heading}}, [{'id': 'A01'}], errors, pending)
            self.assertTrue(errors, heading)

    def test_unparseable_ids_do_not_match_delivery_or_handoff(self):
        self.task['shots'] = [{'id': 'invalid', 'dialogue': []}]
        self.write('delivery.md', self.delivery.replace('| 1 |', '| wrong |'))
        self.task['review']['delivery']['sha256'] = self.hash('delivery.md')
        errors = []
        g.check_delivery_and_dependencies(self.root, self.task, self.prompt, errors)
        self.assertTrue(any('镜号' in e for e in errors), errors)
        self.write('post.md', '| wrong | POST-01 | The man sits. |')
        self.task['post_handoff_file'] = 'post.md'
        errors = []
        g.check_post_handoff(self.root, self.task, 'invalid', 'The man sits.', 'POST-01', errors)
        self.assertTrue(errors)

    def test_plan_rejects_equivalent_duplicate_ids(self):
        plan = {'scope_shots': ['01', 'S1'], 'source_sha256': ['hash'],
                'groups': [{'id': 'A', 'shots': ['01', 'S1'], 'duration': 5, 'reason': 'Fixture'}],
                'boundaries': [{'left': '01', 'right': 'S1', 'decision': 'merge', 'reason': 'Fixture'}]}
        errors = []
        g.check_segmentation(plan, [{'id': '01'}, {'id': 'S1'}], [{'sha256': 'hash'}], 5, errors)
        self.assertTrue(errors)

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

    def test_every_shot_needs_lighting_in_both_table_and_model_body(self):
        self.write('source.md', (self.root / 'source.md').read_text() + '\n## 镜头02\nThe man sits. ' + self.light)
        self.prompt = self.prompt.replace('\noverall_soundscape:', '\n[Shot 2] At 00:02.500, the camera cuts to a close shot. The man sits. ' + self.light + '\noverall_soundscape:')
        self.write('prompt.txt', self.prompt)
        self.write('draft.txt', self.prompt)
        self.task['source_files'][0]['sha256'] = self.hash('source.md')
        self.task['draft_review']['sha256'] = self.hash('draft.txt')
        self.task['shots'].append({'id': 'S02', 'dialogue': []})
        self.task['draft_review']['reviewed_shots'].append('S02')
        review = self.task['review']
        review['source_bindings']['S02'] = {'path': 'source.md', 'heading': '## 镜头02'}
        for field in ['coverage', 'detail_checks', 'lighting']:
            review[field]['S02'] = copy.deepcopy(review[field]['S01'])
        self.write('plan.json', {'scope_shots': ['S01', 'S02'], 'source_sha256': [self.hash('source.md')],
            'groups': [{'id': 'A', 'shots': ['S01', 'S02'], 'duration': 5, 'reason': 'Continuous test action.'}],
            'boundaries': [{'left': 'S01', 'right': 'S02', 'decision': 'merge', 'reason': 'Continuous test space.'}]})
        row2 = '| 2 | 2.5—5秒 | 近景，固定 | 男子坐着，窗光5600K照亮左脸。无台词。 |'
        good = self.delivery[:self.delivery.index('```text')] + '```text\n' + self.prompt + '\n```\n'
        good = good.replace('\n\n**参考资产', '\n' + row2 + '\n\n**参考资产', 1)
        def bind_delivery(value):
            self.write('delivery.md', value)
            review['delivery']['sha256'] = self.hash('delivery.md')
            self.save()
        bind_delivery(good)
        self.assertTrue(self.report()['formal_prompt_ready'], self.report())
        # Global/table-first-row light cannot stand in for the second table row.
        bad = good.replace(row2, '| 2 | 2.5—5秒 | 近景，固定 | 男子坐着，无台词。 |')
        bad = '公共照明：窗光5600K照亮左脸。\n' + bad
        bind_delivery(bad)
        r = self.report()
        self.assertFalse(r['formal_prompt_ready'])
        self.assertTrue(any('S02' in e and '中文镜头表' in e for e in r['errors']))
        # A complete Chinese table cannot mask a missing English per-shot clause.
        missing = self.prompt.replace('The man sits. ' + self.light + '\noverall_soundscape:', 'The man sits.\noverall_soundscape:')
        self.write('prompt.txt', missing)
        bind_delivery(good.replace(self.prompt, missing))
        r = self.report()
        self.assertFalse(r['formal_prompt_ready'])
        self.assertTrue(any('S02' in e and '照明证据' in e for e in r['errors']))

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

    def test_all_registered_profiles_match_template_sha256_and_seedance_25_validates(self):
        registry = json.loads((g.BASE / '模型模板/分支配置.json').read_text(encoding='utf-8'))
        for name, cfg in registry.items():
            with self.subTest(profile=name):
                actual = g.sha((g.BASE / cfg['template']).read_bytes())
                self.assertEqual(actual, cfg['template_sha256'])
        # End-to-end Seedance 2.5 validation must pass when properly bound.
        fixed = json.loads((self.root / 'fixed.json').read_text(encoding='utf-8'))
        fixed['styles']['seedance-2.5'] = '自然写实光影质感。'
        fixed['scenes'][0]['texts']['seedance-2.5'] = '简洁房间内，右侧窗口透入柔光。'
        self.write('fixed.json', fixed)
        prompt = ('房间内的男子静坐。\n\n'
                  '镜头1：近景，固定，男子端坐，右侧窗外5600K柔光从右侧照亮男子左脸。\n\n'
                  '自然写实光影质感。简洁房间内，右侧窗口透入柔光。\n'
                  '画面无任何文字，无背景音乐。')
        delivery = ('| 镜头 | 时间（估算） | 景别与运镜 | 画面、表演和台词 |\n'
                    '|---|---|---|---|\n'
                    '| S01 | 0—5秒 | 近景，固定 | 男子端坐，右侧窗外5600K柔光照亮左脸，无台词。 |\n\n'
                    '**参考资产：**无参考（纯文生测试）。\n\n'
                    '```text\n' + prompt + '\n```\n')
        self.write('prompt.txt', prompt)
        self.write('delivery.md', delivery)
        self.task['profile'] = 'seedance-2.5'
        self.task['mode'] = 'T2V'
        self.task['entry'] = 'jimeng-web'
        self.task['official_reads'] = registry['seedance-2.5']['official_urls']
        self.task['fixed_context']['sha256'] = self.hash('fixed.json')
        self.task['review']['delivery']['sha256'] = self.hash('delivery.md')
        self.task['review']['lighting']['S01'] = {
            'temperature_quote': '5600K柔光',
            'source_quote': '右侧窗外5600K柔光',
            'characters_present': True,
            'characters': [{'subject': '男子', 'prompt_quote': '从右侧照亮男子左脸'}],
            'basis': 'room-day scene lighting',
        }
        self.task['review']['coverage'] = {}
        self.task['review']['detail_checks'] = {}
        self.task['review']['shot_reviews'] = {'S01': {'status': 'pass', 'checked': list(g.ELEMENTS)}}
        self.save()
        result = self.report()
        self.assertTrue(result['formal_prompt_ready'], result)
        self.assertTrue(result['generation_ready'], result)

    def test_cli_fingerprint_sync_hashes_and_bom_tolerance(self):
        # Verify --fingerprint matches module.fingerprint()
        r = subprocess.run([sys.executable, str(SCRIPT), str(self.root / 'task.json'), '--fingerprint'],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), g.fingerprint(self.root / 'task.json'))
        # Verify --sync-hashes fills only empty/None/placeholder hashes, NEVER overwrites already-bound hashes,
        # and does not emit fingerprint.
        old_draft_hash = self.task['draft_review']['sha256']
        old_fixed_hash = self.task['fixed_context']['sha256']
        self.task['source_files'][0]['sha256'] = '待填'
        self.task['review']['delivery']['sha256'] = None
        self.write('draft.txt', 'modified draft after review')
        fixed_data = json.loads((self.root / 'fixed.json').read_text(encoding='utf-8'))
        fixed_data['version'] = 'v2-changed'
        self.write('fixed.json', fixed_data)
        self.write('source.md', '\ufeff' + (self.root / 'source.md').read_text(encoding='utf-8'))
        self.save(rebind=False)
        r = subprocess.run([sys.executable, str(SCRIPT), str(self.root / 'task.json'), '--sync-hashes'],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        payload = json.loads(r.stdout)
        self.assertNotIn('fingerprint', payload)
        self.assertTrue(any('source.md' in x for x in payload['updated']), payload)
        self.assertTrue(any('delivery.md' in x for x in payload['updated']), payload)
        self.assertTrue(any('draft.txt' in x for x in payload['skipped']), payload)
        self.assertTrue(any('fixed.json' in x for x in payload['skipped']), payload)
        reloaded = json.loads((self.root / 'task.json').read_text(encoding='utf-8'))
        self.assertEqual(reloaded['source_files'][0]['sha256'], self.hash('source.md'))
        self.assertEqual(reloaded['review']['delivery']['sha256'], self.hash('delivery.md'))
        self.assertEqual(reloaded['draft_review']['sha256'], old_draft_hash)
        self.assertEqual(reloaded['fixed_context']['sha256'], old_fixed_hash)
        # Mutually exclusive flags must fail cleanly
        conflict = subprocess.run([sys.executable, str(SCRIPT), str(self.root / 'task.json'),
                                   '--sync-hashes', '--fingerprint'], capture_output=True, text=True)
        self.assertNotEqual(conflict.returncode, 0)
        before = (self.root / 'task.json').read_bytes()
        output = self.root / 'report.json'
        conflict = subprocess.run([sys.executable, str(SCRIPT), str(self.root / 'task.json'),
                                   '--sync-hashes', '--report', str(output)], capture_output=True)
        self.assertEqual(conflict.returncode, 2)
        self.assertFalse(output.exists())
        self.assertEqual(before, (self.root / 'task.json').read_bytes())


if __name__ == '__main__':
    unittest.main()
