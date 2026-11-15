#!/usr/bin/env python3
"""Check G3 handoff documents; never creates assets or approves content."""
import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote


EMPTY = {'', '-', '—', '…', '...', '待填写', '待补充', 'TODO', 'TBD'}


def has_body(text):
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or line.endswith(('：', ':')):
            continue
        if line not in EMPTY and not re.fullmatch(r'[| :\-]+', line):
            return True
    return False


def asset_rows(text):
    rows = []
    in_assets = False
    columns = 0
    incomplete = False
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith('|') or not line.endswith('|'):
            in_assets = False
            continue
        cells = [x.strip() for x in re.split(r'(?<!\\)\|', line[1:-1])]
        if all(re.fullmatch(r':?-+:?', x.replace(' ', '')) for x in cells):
            continue
        if any(re.fullmatch(r'(?:关联|服务|对应|出现)?镜号(?:[、，/].*)?', x) for x in cells) and any('类型' in x or '类别' in x for x in cells) and any('需求' in x or '资产' in x or '名称' in x for x in cells):
            columns = len(cells)
            in_assets = columns >= 6
            continue
        if in_assets:
            if len(cells) != columns or any(x in EMPTY for x in cells):
                incomplete = True
            else:
                rows.append(cells)
    return rows, incomplete


def check(storyboard, assets, handoff):
    paths = {k: Path(v).expanduser().resolve() for k, v in (
        ('storyboard', storyboard), ('assets', assets), ('handoff', handoff))}
    labels = {'storyboard': '分镜定稿', 'assets': '资产需求', 'handoff': '复核与交接'}
    texts, files, errors = {}, {}, []
    for role, path in paths.items():
        try:
            data = path.read_bytes()
            text = data.decode('utf-8-sig')
        except (OSError, UnicodeError) as exc:
            errors.append(f'{labels[role]}无法读取：{path}（{exc}）')
            continue
        files[role] = {'path': str(path), 'sha256': hashlib.sha256(data).hexdigest()}
        texts[role] = text
        if not has_body(text):
            errors.append(f'{labels[role]}缺少实际内容：{path}')

    if 'assets' in texts:
        rows, incomplete = asset_rows(texts['assets'])
        if not rows:
            errors.append('资产需求缺少已填写的资产表；使用资产需求模板或列项等效的表格。')
        if incomplete:
            errors.append('资产需求表存在空行或未填写列；无相应需求时填写无或不适用，并说明依据。')

    for role, source in [('assets', 'storyboard'), ('handoff', 'storyboard'), ('handoff', 'assets')]:
        if role in texts and source in texts and paths[role] != paths[source]:
            if paths[source].name not in unquote(texts[role]):
                errors.append(f'{labels[role]}未引用本次{labels[source]}文件：{paths[source].name}')

    return {
        'documents_passed': not errors,
        'checker_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'files': files,
        'errors': errors,
        'content_review': 'required_separately',
        'check_scope': '文件可读性、资产表填写、当前文件名引用及文件摘要；逐镜覆盖、确认依据与制作适配另行复核。',
    }


def main():
    parser = argparse.ArgumentParser(description='G3制作交接文件检查；不生成素材。')
    parser.add_argument('--storyboard', required=True, help='本次分镜定稿或等效文件')
    parser.add_argument('--assets', required=True, help='资产需求或包含等效资产表的文件')
    parser.add_argument('--handoff', required=True, help='复核交接或合并交接文件')
    parser.add_argument('--report', help='保存JSON检查报告')
    args = parser.parse_args()
    inputs = [Path(x).expanduser().resolve() for x in (args.storyboard, args.assets, args.handoff)]
    if args.report and Path(args.report).expanduser().resolve() in inputs:
        parser.error('报告不能覆盖输入文件')
    result = check(*inputs)
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        target = Path(args.report).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(output + '\n', encoding='utf-8')
    print(output)
    return 0 if result['documents_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
