"""Synthetic offline cases for no-change promotion; not proof of real content review."""
import copy
import unittest

import test_task_gate as fixtures


class UnchangedPromotionTests(unittest.TestCase):
    setUp = fixtures.GateTests.setUp
    write = fixtures.GateTests.write
    hash = fixtures.GateTests.hash
    save = fixtures.GateTests.save
    report = fixtures.GateTests.report

    def accept_original(self):
        self.task['draft_review'].update(disposition='accepted_unchanged', path='prompt.txt',
                                         sha256=self.hash('prompt.txt'))
        self.save()

    def test_legacy_distinct_files_remain_valid(self):
        self.assertTrue(self.report()['formal_prompt_ready'], self.report())

    def test_explicit_unchanged_can_keep_reviewed_original(self):
        self.accept_original()
        self.assertTrue(self.report()['formal_prompt_ready'], self.report())

    def test_same_file_requires_explicit_unchanged_disposition(self):
        for disposition in [None, 'revised', '', 'pass', True]:
            with self.subTest(disposition=disposition):
                self.task['draft_review'].update(path='prompt.txt', sha256=self.hash('prompt.txt'))
                if disposition is None:
                    self.task['draft_review'].pop('disposition', None)
                else:
                    self.task['draft_review']['disposition'] = disposition
                self.save()
                self.assertFalse(self.report()['formal_prompt_ready'])

    def test_unchanged_rejects_every_recorded_issue_even_resolved(self):
        self.task['draft_review']['disposition'] = 'accepted_unchanged'
        self.task['draft_review']['issues'] = [{'shot': 'S01', 'finding': 'Synthetic resolved issue',
            'source_quote': 'The man sits.', 'draft_quote': 'The man sits.',
            'revision_quote': 'The man sits.', 'resolved': True}]
        self.save()
        self.assertFalse(self.report()['formal_prompt_ready'])

    def test_unchanged_requires_explicit_empty_issue_list(self):
        for issues in [None, {}, '', False]:
            self.task['draft_review'].update(disposition='accepted_unchanged', issues=issues)
            self.save()
            self.assertFalse(self.report()['formal_prompt_ready'])

    def test_unchanged_rejects_changed_bytes_even_if_read_text_normalizes_them(self):
        self.task['draft_review']['disposition'] = 'accepted_unchanged'
        self.write('draft.txt', self.prompt + '\n')
        self.task['draft_review']['sha256'] = self.hash('draft.txt')
        self.save()
        self.assertFalse(self.report()['formal_prompt_ready'])
        # Same decoded text after universal newline conversion is still changed bytes.
        (self.root / 'draft.txt').write_bytes(self.prompt.replace('\n', '\r\n').encode())
        self.task['draft_review']['sha256'] = self.hash('draft.txt')
        self.save()
        self.assertFalse(self.report()['formal_prompt_ready'])

    def test_unchanged_requires_reviewed_hash_and_complete_independent_record(self):
        self.accept_original()
        original = copy.deepcopy(self.task['draft_review'])
        invalid = [('sha256', 'old'), ('completed', False), ('reviewer', ''), ('reviewed_at', ''),
                   ('summary', ''), ('review_method', 'self'), ('review_evidence', ''),
                   ('review_evidence', []), ('review_evidence', True), ('reviewed_shots', [])]
        for field, value in invalid:
            with self.subTest(field=field, value=value):
                self.task['draft_review'] = copy.deepcopy(original)
                self.task['draft_review'][field] = value
                self.save()
                self.assertFalse(self.report()['formal_prompt_ready'])

    def test_unchanged_still_requires_current_fingerprint(self):
        self.accept_original()
        self.task['review']['fingerprint'] = 'old'
        self.save(rebind=False)
        self.assertFalse(self.report()['formal_prompt_ready'])

    def test_unchanged_does_not_bypass_other_gates(self):
        self.accept_original()
        baseline = copy.deepcopy(self.task)
        for gate in ['runtime', 'fixed_context', 'content', 'delivery', 'authorization']:
            with self.subTest(gate=gate):
                self.task = copy.deepcopy(baseline)
                if gate == 'runtime': self.task['runtime']['verified'] = False
                elif gate == 'fixed_context': self.task['fixed_context']['reviewed'] = False
                elif gate == 'content': self.task['review']['judgments']['clarity']['status'] = 'pending'
                elif gate == 'delivery': self.task['review']['delivery']['status'] = 'pending'
                else: self.task['authorization']['generate'] = False
                self.save()
                result = self.report()
                self.assertFalse(result['generation_ready'])
                self.assertEqual(result['formal_prompt_ready'], gate == 'authorization', result)

    def test_unchanged_changed_file_invalidates_reviewed_hash(self):
        self.accept_original()
        self.write('prompt.txt', self.prompt + '\n')
        # Even rebinding all other current records cannot change the old reviewed hash.
        self.write('delivery.md', self.delivery.replace(self.prompt, self.prompt + '\n'))
        self.task['review']['delivery']['sha256'] = self.hash('delivery.md')
        self.save()
        self.assertFalse(self.report()['formal_prompt_ready'])

    def test_revised_cannot_hide_same_file_behind_hardlink(self):
        import os
        (self.root / 'draft.txt').unlink()
        os.link(self.root / 'prompt.txt', self.root / 'draft.txt')
        self.save()
        self.assertFalse(self.report()['formal_prompt_ready'])

    def test_unknown_disposition_is_rejected_with_separate_files(self):
        self.task['draft_review']['disposition'] = 'skip_review'
        self.save()
        self.assertFalse(self.report()['formal_prompt_ready'])


if __name__ == '__main__':
    unittest.main()
