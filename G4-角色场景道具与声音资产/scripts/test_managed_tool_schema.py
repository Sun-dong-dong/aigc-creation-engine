#!/usr/bin/env python3
"""Managed image tool records must bind real schema files without inventing versions."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

CHECKER = Path(__file__).with_name('check_image_task.py')
spec = importlib.util.spec_from_file_location('image_task_check', CHECKER)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ManagedToolSchemaTests(unittest.TestCase):
    def fixture(self, root, kind='scene_overhead'):
        schema = root / 'tool-schema.json'
        schema.write_text(json.dumps({
            'tool': 'test.imagegen',
            'provenance': 'Runtime tool declaration read for this task.',
            'input': {'prompt': 'required string', 'transparent_background': 'boolean'},
        }), encoding='utf-8')
        (root / 'prompt.txt').write_text('One complete test image prompt.', encoding='utf-8')
        task = {
            'asset_id': 'test-asset', 'kind': kind,
            'aspect_ratio': '16:9' if kind == 'character_views' else '3:2',
            'model': {
                'name': 'test.imagegen', 'version': '', 'mode': 'text-to-image',
                'entry': 'runtime test.imagegen', 'version_status': 'not_exposed',
                'version_unavailable_reason': 'The actual tool declaration does not expose it.',
                'spec_source': {
                    'kind': 'managed_tool_schema', 'tool_name': 'test.imagegen',
                    'path': 'tool-schema.json', 'sha256': digest(schema),
                    'evidence': 'Compared directly with the available runtime declaration.',
                },
            },
            'prompt_file': 'prompt.txt', 'references': [],
            'review': {
                'format': {'passed': True, 'evidence': 'Checked the actual tool fields.'},
                'content': {'passed': True, 'evidence': 'Checked the task requirements.'},
            },
            'authorization': {'generate': True, 'scope': 'One test image.'},
        }
        task_path = root / 'task.json'
        self.bind(task_path, task)
        return task_path, task, schema

    def bind(self, task_path, task):
        task_path.write_text(json.dumps(task), encoding='utf-8')
        task['review']['fingerprint'] = checker.fingerprint(task_path)
        task_path.write_text(json.dumps(task), encoding='utf-8')

    def test_two_first_image_types_accept_bound_managed_schema(self):
        for kind in ('scene_overhead', 'character_views'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as d:
                task_path, _, schema = self.fixture(Path(d), kind)
                result = checker.check(task_path)
                self.assertTrue(result['formal_prompt_ready'], result)
                self.assertTrue(result['generation_ready'], result)
                self.assertEqual(result['actual_files']['tool-schema.json'], digest(schema))
                self.assertEqual(result['image_acceptance'], 'not_evaluated')

    def test_codex_imagegen_character_views_contract(self):
        schema = CHECKER.parents[1] / '模板/image_gen.imagegen.schema.json'
        with tempfile.TemporaryDirectory() as tmp:
            task_path, task, _ = self.fixture(Path(tmp), 'character_views')
            model = task['model']
            model.update(name='image_gen.imagegen', entry='image_gen.imagegen')
            model['spec_source'].update(tool_name=model['name'], path=str(schema), sha256=digest(schema))
            self.bind(task_path, task)
            result = checker.check(task_path)
            self.assertTrue(result['generation_ready'], result)
            self.assertEqual(result['image_acceptance'], 'not_evaluated')

    def test_missing_changed_or_wrong_hash_schema_rejected(self):
        for mutation in ('missing', 'changed', 'wrong_hash'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as d:
                task_path, task, schema = self.fixture(Path(d))
                old_fingerprint = task['review']['fingerprint']
                if mutation == 'missing':
                    schema.unlink()
                elif mutation == 'changed':
                    schema.write_text(schema.read_text() + ' ', encoding='utf-8')
                else:
                    task['model']['spec_source']['sha256'] = '0' * 64
                    self.bind(task_path, task)
                result = checker.check(task_path)
                self.assertFalse(result['formal_prompt_ready'], result)
                self.assertTrue(result['errors'], result)
                if mutation != 'wrong_hash':
                    self.assertNotEqual(old_fingerprint, checker.fingerprint(task_path))

    def test_managed_schema_requires_tool_identity_evidence_and_unknown_reason(self):
        for missing in ('spec_source', 'tool_name', 'evidence', 'version_status', 'version_unavailable_reason'):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as d:
                task_path, task, _ = self.fixture(Path(d))
                if missing in ('tool_name', 'evidence'):
                    task['model']['spec_source'].pop(missing)
                else:
                    task['model'].pop(missing)
                self.bind(task_path, task)
                self.assertFalse(checker.check(task_path)['formal_prompt_ready'])
        for bad_schema in ({'tool': 'a.different.tool', 'provenance': 'runtime', 'input': {'prompt': 'string'}},
                           {'tool': 'test.imagegen', 'provenance': 'runtime', 'input': {}}):
            with self.subTest(schema=bad_schema), tempfile.TemporaryDirectory() as d:
                task_path, task, schema = self.fixture(Path(d))
                schema.write_text(json.dumps(bad_schema), encoding='utf-8')
                task['model']['spec_source']['sha256'] = digest(schema)
                self.bind(task_path, task)
                self.assertFalse(checker.check(task_path)['formal_prompt_ready'])

    def test_legacy_https_records_still_work_and_do_not_allow_unknown_version(self):
        with tempfile.TemporaryDirectory() as d:
            task_path, task, _ = self.fixture(Path(d))
            task['model'] = {
                'name': 'Example image model', 'version': '1.0', 'mode': 'text-to-image',
                'entry': 'model native API', 'official_url': 'https://example.com/official',
                'official_evidence': 'Official template was actually read.',
            }
            self.bind(task_path, task)
            self.assertTrue(checker.check(task_path)['generation_ready'])
            task['model']['version'] = ''
            task['model']['version_status'] = 'not_exposed'
            task['model']['version_unavailable_reason'] = 'Not published.'
            self.bind(task_path, task)
            self.assertFalse(checker.check(task_path)['formal_prompt_ready'])
            task['model']['version'] = '1.0'
            task['model']['official_url'] = 'http://example.com/official'
            self.bind(task_path, task)
            self.assertFalse(checker.check(task_path)['formal_prompt_ready'])

    def test_requested_ratio_is_target_and_does_not_assert_output_acceptance(self):
        with tempfile.TemporaryDirectory() as d:
            task_path, task, _ = self.fixture(Path(d), 'character_views')
            task['requested_aspect_ratio'] = '16:9'
            self.bind(task_path, task)
            input_fingerprint = checker.fingerprint(task_path)
            # Actual dimensions belong to output acceptance, not generation input.
            (Path(d) / 'output_acceptance.json').write_text(json.dumps({
                'task': task_path.name, 'input_fingerprint': input_fingerprint,
                'actual_aspect_ratio': '2:3', 'acceptance': 'pending',
            }))
            self.assertEqual(checker.fingerprint(task_path), input_fingerprint)
            result = checker.check(task_path)
            self.assertTrue(result['formal_prompt_ready'], result)
            self.assertEqual(result['image_acceptance'], 'not_evaluated')
            task['requested_aspect_ratio'] = '1:1'
            self.bind(task_path, task)
            self.assertFalse(checker.check(task_path)['formal_prompt_ready'])

    def test_schema_report_collision_direct_relative_symlink_hardlink(self):
        for alias in ('direct', 'relative', 'symlink', 'hardlink'):
            for fingerprint in (False, True):
                with self.subTest(alias=alias, fingerprint=fingerprint), tempfile.TemporaryDirectory() as d:
                    root = Path(d)
                    task_path, _, schema = self.fixture(root)
                    report = schema
                    if alias == 'relative':
                        (root / 'subdir').mkdir()
                        report = Path('subdir') / '..' / schema.name
                    elif alias in ('symlink', 'hardlink'):
                        report = root / 'report-alias'
                        if alias == 'symlink':
                            report.symlink_to(schema)
                        else:
                            os.link(schema, report)
                    before = schema.read_bytes()
                    args = [sys.executable, str(CHECKER), str(task_path), '--report', str(report)]
                    if fingerprint:
                        args.append('--fingerprint')
                    run = subprocess.run(args, cwd=root, capture_output=True, text=True)
                    self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
                    self.assertEqual(schema.read_bytes(), before)
                    self.assertIn('报告不能覆盖', run.stderr)


if __name__ == '__main__':
    unittest.main()
