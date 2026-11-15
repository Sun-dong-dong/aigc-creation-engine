"""Isolated G3 document-gate regressions; no media generation or project writes."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('check_handoff.py')
spec = importlib.util.spec_from_file_location('g3_handoff', SCRIPT)
g3 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g3)
TEMPLATES = SCRIPT.parent.parent / '模板'
STORY = '''# 分镜定稿
### 镜头01｜中景｜35mm｜平视｜固定｜4秒
场景：屋内
画面内容：男子将钥匙放在桌上，望向门口。
状态：持钥匙 → 空手
光影：东窗日光5600K照亮男子左脸。
色调：暖灰
台词：无
重音：无
人声：无
音效：钥匙落桌声
'''
ASSET = '''# 资产需求
来源：分镜.md v1
| 需求编号或名称 | 类型与叙事用途 | 关联镜号 | 所需视角、状态或交互 | 已有材料、版本及实际查看状态 | 缺口与G4需落实内容 |
|---|---|---|---|---|---|
| 钥匙 | 道具，交代回家 | 01 | 手持与放桌 | 无，尚未制作 | 制作钥匙正侧面 |
'''
HANDOFF = '''# 复核与交接
分镜：分镜.md v1
资产：资产.md v1
- 本次实际检查方式与范围：连读01，核对持物变化；实际动态未验证。
- 用户确认的准确版本、范围与依据：v1，01；用户在本次会话明确确认。
- G4接收范围及下一步：钥匙正侧面资产制作。
'''


class HandoffTests(unittest.TestCase):
    def run_documents(self, story=STORY, assets=ASSET, handoff=HANDOFF, merged=False):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            if merged:
                path = base / '合并.md'
                path.write_text(story + '\n' + assets + '\n' + handoff, encoding='utf-8')
                return g3.check(path, path, path)
            paths = [base / name for name in ['分镜.md', '资产.md', '交接.md']]
            for path, content in zip(paths, [story, assets, handoff]):
                path.write_text(content, encoding='utf-8')
            return g3.check(*paths)

    def test_filled_separate_documents_pass(self):
        result = self.run_documents()
        self.assertTrue(result['documents_passed'], result['errors'])
        self.assertEqual(result['content_review'], 'required_separately')
        self.assertEqual(set(result['files']), {'storyboard', 'assets', 'handoff'})

    def test_filled_merged_document_passes_without_self_reference(self):
        result = self.run_documents(assets=ASSET.replace('分镜.md', '来源原文v1'),
                                    handoff=HANDOFF.replace('分镜.md', '来源原文v1').replace('资产.md', '表内资产v1'), merged=True)
        self.assertTrue(result['documents_passed'], result['errors'])

    def test_equivalent_shot_header_aliases_pass(self):
        for alias in ['涉及镜头', '对应镜头编号', '镜号', '对应镜号']:
            with self.subTest(alias=alias):
                result = self.run_documents(assets=ASSET.replace('关联镜号', alias))
                self.assertTrue(result['documents_passed'], result['errors'])

    def test_assets_without_required_information_column_fail(self):
        text = ASSET.replace('已有材料、版本及实际查看状态', '备注')
        self.assertFalse(self.run_documents(assets=text)['documents_passed'])

    def test_shot_number_header_does_not_replace_asset_identity_column(self):
        text = ASSET.replace('关联镜号', '对应镜头编号').replace('需求编号或名称', '备注')
        self.assertFalse(self.run_documents(assets=text)['documents_passed'])

    def test_material_viewing_status_does_not_replace_required_state_column(self):
        text = ASSET.replace('所需视角、状态或交互', '备注')
        self.assertFalse(self.run_documents(assets=text)['documents_passed'])

    def test_equivalent_asset_table_can_split_information_columns(self):
        text = ASSET.replace('所需视角、状态或交互', '所需视角 | 状态或交互')
        text = text.replace('|---|---|---|---|---|---|', '|---|---|---|---|---|---|---|')
        text = text.replace('手持与放桌', '正侧面 | 手持与放桌')
        result = self.run_documents(assets=text)
        self.assertTrue(result['documents_passed'], result['errors'])

    def test_incomplete_asset_row_fails(self):
        self.assertFalse(self.run_documents(assets=ASSET.replace('手持与放桌', ''))['documents_passed'])

    def test_unfilled_real_story_template_with_filename_fails(self):
        text = (TEMPLATES / '分镜脚本.md').read_text() + '\n分镜.md\n'
        self.assertFalse(self.run_documents(story=text)['documents_passed'])

    def test_unfilled_real_handoff_template_with_filenames_fails(self):
        text = (TEMPLATES / '复核与交接.md').read_text() + '\n分镜.md\n资产.md\n'
        self.assertFalse(self.run_documents(handoff=text)['documents_passed'])

    def test_templates_merged_with_asset_data_and_filenames_fail(self):
        story = (TEMPLATES / '分镜脚本.md').read_text()
        handoff = (TEMPLATES / '复核与交接.md').read_text()
        self.assertFalse(self.run_documents(story=story, handoff=handoff, merged=True)['documents_passed'])

    def test_shot_heading_without_filled_picture_fails(self):
        text = STORY.replace('男子将钥匙放在桌上，望向门口。', '待填写')
        self.assertFalse(self.run_documents(story=text)['documents_passed'])

    def test_equivalent_story_table_and_handoff_review_table_pass(self):
        story = '| 镜号 | 场景 | 画面内容 | 状态 |\n|---|---|---|---|\n| S01 | 屋内 | 男子放下钥匙。 | 持钥匙→空手 |\n'
        handoff = HANDOFF.replace('- 本次实际检查方式与范围：连读01，核对持物变化；实际动态未验证。',
            '| 位置或镜号 | 依据、观察与影响 | 处理及关联回查 | 状态 |\n|---|---|---|---|\n| S01 | 核对持物 | 回查相邻镜头 | 通过 |')
        result = self.run_documents(story=story, handoff=handoff)
        self.assertTrue(result['documents_passed'], result['errors'])

    def test_story_value_paragraph_and_indented_handoff_continuation_pass(self):
        story = STORY.replace('画面内容：男子', '画面内容：\n\n男子')
        handoff = HANDOFF.replace('检查方式与范围：连读', '检查方式与范围：\n  连读')
        result = self.run_documents(story=story, handoff=handoff)
        self.assertTrue(result['documents_passed'], result['errors'])

    def test_handoff_key_value_table_passes(self):
        handoff = '''分镜.md 与 资产.md
| 项目 | 记录 |
|---|---|
| 复核结论 | 连读01，持物承接通过；动态未验证。 |
| G4接收范围 | 钥匙正侧面资产制作。 |
'''
        result = self.run_documents(handoff=handoff)
        self.assertTrue(result['documents_passed'], result['errors'])

    def test_review_without_populated_handoff_record_fails(self):
        text = (TEMPLATES / '复核与交接.md').read_text()
        text = text.replace('本次实际检查方式与范围：', '本次实际检查方式与范围：连读01，核对持物变化。')
        self.assertFalse(self.run_documents(handoff=text + '\n分镜.md\n资产.md\n')['documents_passed'])

    def test_handoff_without_review_record_fails(self):
        text = HANDOFF.replace('- 本次实际检查方式与范围：连读01，核对持物变化；实际动态未验证。', '')
        self.assertFalse(self.run_documents(handoff=text)['documents_passed'])

    def test_missing_current_reference_fails(self):
        self.assertFalse(self.run_documents(handoff=HANDOFF.replace('资产.md', '旧表.md'))['documents_passed'])

    def test_cli_rejects_empty_templates_and_writes_report(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            story = base / '分镜.md'
            assets = base / '资产.md'
            handoff = base / '交接.md'
            story.write_text((TEMPLATES / '分镜脚本.md').read_text())
            assets.write_text(ASSET)
            handoff.write_text(HANDOFF)
            result = subprocess.run([sys.executable, str(SCRIPT), '--storyboard', str(story), '--assets', str(assets), '--handoff', str(handoff), '--report', str(base / '报告.json')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertTrue((base / '报告.json').is_file())


    def test_cli_protects_hardlinked_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            inputs = [base / name for name in ('分镜.md', '资产.md', '交接.md')]
            for path, text in zip(inputs, (STORY, ASSET, HANDOFF)):
                path.write_text(text)
            for path in inputs:
                output = base / 'hardlink-report.json'
                output.unlink(missing_ok=True)
                os.link(str(path), str(output))
                before = path.read_bytes()
                result = subprocess.run([sys.executable, str(SCRIPT), '--storyboard', str(inputs[0]), '--assets', str(inputs[1]), '--handoff', str(inputs[2]), '--report', str(output)], capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
