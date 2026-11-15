#!/usr/bin/env python3
"""CLI regressions: reports must never replace task or actual input files."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


CHECKER = Path(__file__).with_name('check_image_task.py')


class ReportSafetyTests(unittest.TestCase):
    def fixture(self, root):
        paths = {name: root / filename for name, filename in (
            ('task', 'task.json'), ('prompt', 'prompt.txt'),
            ('reference', 'reference.png'), ('second_reference', 'second-reference.png'),
            ('checker', 'checker.py'))}
        shutil.copyfile(CHECKER, paths['checker'])
        paths['prompt'].write_text('A complete image prompt.', encoding='utf-8')
        paths['reference'].write_bytes(b'reference image bytes')
        paths['second_reference'].write_bytes(b'second reference image bytes')
        task = {
            'asset_id': 'asset-1', 'kind': 'prop', 'aspect_ratio': '1:1',
            'model': {key: 'recorded evidence' for key in
                      ('name', 'version', 'mode', 'entry', 'official_evidence')},
            'prompt_file': 'prompt.txt',
            'references': [{
                'asset_id': 'asset-1', 'path': 'reference.png',
                'sha256': hashlib.sha256(paths['reference'].read_bytes()).hexdigest(),
                'roles': ['appearance'], 'selected': True,
                'selection_evidence': 'Selected for this task.'}, {
                'asset_id': 'asset-1', 'path': str(paths['second_reference']),
                'sha256': hashlib.sha256(paths['second_reference'].read_bytes()).hexdigest(),
                'roles': ['texture'], 'selected': True,
                'selection_evidence': 'Also selected for this task.'}],
            'review': {'format': {'passed': True, 'evidence': 'Format checked.'},
                       'content': {'passed': True, 'evidence': 'Content checked.'}},
            'authorization': {'generate': True, 'scope': 'This image.'},
        }
        task['model']['official_url'] = 'https://example.com/template'
        paths['task'].write_text(json.dumps(task), encoding='utf-8')
        result = self.run_checker(paths, '--fingerprint')
        self.assertEqual(result.returncode, 0, result.stderr)
        task['review']['fingerprint'] = result.stdout.strip()
        paths['task'].write_text(json.dumps(task), encoding='utf-8')
        return paths

    def run_checker(self, paths, *args):
        return subprocess.run(
            [sys.executable, str(paths['checker']), str(paths['task']), *map(str, args)],
            cwd=paths['task'].parent, capture_output=True, text=True)

    def test_report_writes_successfully_without_changing_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = self.fixture(Path(directory))
            before = {key: path.read_bytes() for key, path in paths.items()}
            report = Path(directory) / 'reports' / 'result.json'
            result = self.run_checker(paths, '--report', report)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(report.read_text())['generation_ready'])
            self.assertEqual(json.loads(report.read_text()), json.loads(result.stdout))
            self.assertEqual(before, {key: path.read_bytes() for key, path in paths.items()})

    def test_report_rejects_all_input_collisions_and_aliases(self):
        # Removing path normalization or samefile protection must fail these CLI cases.
        for source in ('task', 'prompt', 'reference', 'second_reference', 'checker'):
            for alias in ('direct', 'relative', 'symlink', 'hardlink'):
                for fingerprint in (False, True):
                    with self.subTest(source=source, alias=alias, fingerprint=fingerprint):
                        with tempfile.TemporaryDirectory() as directory:
                            root = Path(directory)
                            paths = self.fixture(root)
                            target = paths[source]
                            if alias == 'relative':
                                (root / 'subdir').mkdir()
                                target = Path('subdir') / '..' / target.name
                            elif alias in ('symlink', 'hardlink'):
                                target = root / 'report-alias'
                                if alias == 'symlink':
                                    target.symlink_to(paths[source])
                                else:
                                    os.link(paths[source], target)
                            before = {key: path.read_bytes() for key, path in paths.items()}
                            args = ['--report', target]
                            if fingerprint:
                                args.append('--fingerprint')
                            result = self.run_checker(paths, *args)
                            self.assertEqual(before, {key: path.read_bytes() for key, path in paths.items()})
                            self.assertEqual(result.returncode, 2, result.stderr)
                            self.assertIn('报告不能覆盖', result.stderr)

    def test_missing_input_path_is_still_protected(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = self.fixture(Path(directory))
            paths['prompt'].unlink()
            result = self.run_checker(paths, '--report', paths['prompt'])
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(paths['prompt'].exists())

    def test_safe_report_option_preserves_fingerprint_only_behavior(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = self.fixture(Path(directory))
            before = {key: path.read_bytes() for key, path in paths.items()}
            report = Path(directory) / 'unused-report.json'
            result = self.run_checker(paths, '--fingerprint', '--report', report)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertRegex(result.stdout, r'^[0-9a-f]{64}\n$')
            self.assertFalse(report.exists())
            self.assertEqual(before, {key: path.read_bytes() for key, path in paths.items()})

    def test_malformed_task_is_protected_from_hardlink_report(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = self.fixture(Path(directory))
            paths['task'].write_text('{ malformed task', encoding='utf-8')
            report = Path(directory) / 'report.json'
            os.link(paths['task'], report)
            before = paths['task'].read_bytes()
            result = self.run_checker(paths, '--report', report)
            self.assertEqual(paths['task'].read_bytes(), before)
            self.assertEqual(result.returncode, 2, result.stderr)


if __name__ == '__main__':
    unittest.main()
