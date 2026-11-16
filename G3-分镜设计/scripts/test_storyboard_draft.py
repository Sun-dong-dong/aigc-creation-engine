"""Check actual G3 draft structure without treating it as content approval."""
import unittest
import check_storyboard_draft as draft


def shot(name):
    return '### 镜头' + name + '｜近景\n' + '\n'.join(
        label + '：' + ('—' if label in ('台词', '重音', '人声') else '实际内容')
        for label in draft.REQUIRED) + '\n'


class DraftTests(unittest.TestCase):
    def test_populated_multiple_shots_and_non_applicable_values(self):
        result = draft.check_text(shot('A01') + shot('A02'))
        self.assertTrue(result['draft_structure_passed'], result)
        self.assertEqual(result['shots_checked'], 2)

    def test_missing_field_in_second_shot_is_rejected(self):
        result = draft.check_text(shot('01') + shot('02').replace('光影：实际内容', '光影：'))
        self.assertFalse(result['draft_structure_passed'])
        self.assertIn('光影', result['errors'][0])

    def test_empty_template_and_missing_heading_are_rejected(self):
        self.assertFalse(draft.check_text('# 分镜\n场景：待填写')['draft_structure_passed'])
        self.assertFalse(draft.check_text(shot('01').replace('站位：实际内容', '站位：待填写'))['draft_structure_passed'])

    def test_parenthesized_and_unrecognized_headings_are_not_skipped(self):
        incomplete = '## 镜头 02（近景）\n场景：卧室\n'
        result = draft.check_text(shot('01') + incomplete)
        self.assertFalse(result['draft_structure_passed'])
        self.assertEqual(result['shots_checked'], 2)
        self.assertIn('光影', result['errors'][0])
        self.assertFalse(draft.check_text(shot('01') + '## 镜头 ???\n场景：卧室\n')['draft_structure_passed'])


if __name__ == '__main__':
    unittest.main()
