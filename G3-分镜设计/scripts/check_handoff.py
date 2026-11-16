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


def filled(value):
    return value.strip().upper() not in {x.upper() for x in EMPTY}


def tables(text):
    """Yield real Markdown tables, excluding their header and separator rows."""
    lines = text.splitlines()
    index = 0
    while index + 1 < len(lines):
        def cells(line):
            line = line.strip()
            if not (line.startswith('|') and line.endswith('|')):
                return None
            return [x.strip() for x in re.split(r'(?<!\\)\|', line[1:-1])]
        header, separator = cells(lines[index]), cells(lines[index + 1])
        if not header or not separator or len(header) != len(separator) or not all(
                re.fullmatch(r':?-+:?', x.replace(' ', '')) for x in separator):
            index += 1
            continue
        index += 2
        rows = []
        while index < len(lines):
            row = cells(lines[index])
            if row is None:
                break
            rows.append(row)
            index += 1
        yield header, rows


def fields(text):
    """Read labelled values, including the template's separate value paragraphs."""
    entries = []
    label, values, multiline = None, [], False
    for line in text.splitlines() + ['# end']:
        stripped = line.strip()
        match = re.match(r'^(?:[-*]\s+)?([^：:|#]+)[：:]\s*(.*)$', stripped)
        if match or stripped.startswith(('#', '|')):
            if label is not None:
                entries.append((label, '\n'.join(values).strip()))
            label, values = (match.group(1).strip(), [match.group(2)]) if match else (None, [])
            # Unindented template instructions or file lists are not values of
            # empty bullet records. Explicitly indent a record's continuation.
            multiline = bool(match and not re.match(r'^[-*]\s+', stripped))
        elif stripped and label is not None and (multiline or line[:1].isspace()):
            values.append(stripped)
    # Two-column key/value tables are an equivalent way to carry records.
    for header, rows in tables(text):
        if len(header) == 2:
            entries.extend((row[0], row[1]) for row in rows if len(row) == 2)
    return entries


def shot_column(header):
    return any(re.search(r'镜号|镜头编号|(?:涉及|对应|关联|服务|出现)镜头', x) for x in header)


def asset_rows(text):
    rows, incomplete = [], False
    for header, data in tables(text):
        # Equivalent tables may split fields into more than six columns, but must
        # retain all six information groups rather than merely any six columns.
        groups = [r'编号|名称|需求项|资产名', r'类型|类别',
                  r'镜号|镜头编号|(?:涉及|对应|关联|服务|出现)镜头',
                  r'视角|状态|交互', r'已有|现有', r'缺口|缺失|需落实|需补充']
        candidates = [{i for i, cell in enumerate(header) if re.search(pattern, cell)}
                      for pattern in groups]

        def distinct_columns(index=0, used=frozenset()):
            if index == len(candidates):
                return True
            return any(distinct_columns(index + 1, used | {column})
                       for column in candidates[index] - used)

        # A shot-number column cannot double as asset identity, and the viewing
        # status of existing material cannot replace the requested asset state.
        if len(header) < 6 or not distinct_columns():
            continue
        for row in data:
            if len(row) != len(header) or not all(filled(x) for x in row):
                incomplete = True
            else:
                rows.append(row)
    return rows, incomplete


def has_storyboard_structure(text):
    # This is minimum populated structure, not a judgement of shot quality or
    # coverage. Keep source wording intact and accept equivalent shot tables.
    sections = re.split(r'(?m)^(?=\s*#{1,6}\s+)', text)
    for section in sections:
        heading = section.splitlines()[0] if section.splitlines() else ''
        if not re.search(r'镜头\s*(?:[A-Za-z]*\d+[A-Za-z]*|[一二三四五六七八九十百]+)', heading):
            continue
        values = dict(fields(section))
        if filled(values.get('场景', '')) and filled(values.get('画面内容', '')):
            return True
    for header, rows in tables(text):
        picture = next((i for i, x in enumerate(header) if '画面' in x or '镜内行动' in x), None)
        if shot_column(header) and picture is not None:
            shot = next(i for i, x in enumerate(header) if shot_column([x]))
            if any(len(row) == len(header) and filled(row[shot]) and filled(row[picture])
                   for row in rows):
                return True
    return False


def has_handoff_structure(text):
    entries = [(label, value) for label, value in fields(text) if filled(value)]
    review = any(re.search(r'检查方式|检查范围|检查结果|复核结论|回查范围|审读.*结果|查看.*结果', label)
                 for label, _ in entries)
    for header, rows in tables(text):
        if any(re.search(r'依据|观察|复核|检查', x) for x in header) and any(
                re.search(r'处理|结果|结论|状态', x) for x in header):
            review = review or any(len(row) == len(header) and all(filled(x) for x in row) for row in rows)
    handoff = any(re.search(r'用户确认|确认依据|确认版本|交接范围|接收范围|交接结论|门禁结论|必须保留|可调整项|未决事项', label)
                  for label, _ in entries)
    return review and handoff


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

    if 'storyboard' in texts and not has_storyboard_structure(texts['storyboard']):
        errors.append('分镜定稿缺少已填写的镜头结构；须有实际镜号、场景和画面内容，或含镜号与画面内容的等效分镜表。')
    if 'handoff' in texts and not has_handoff_structure(texts['handoff']):
        errors.append('复核与交接缺少已填写的检查记录及确认或交接记录；模板说明、空表头及文件名不算记录。')

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
        'check_scope': '文件可读性、分镜及交接最低有效结构、资产表信息组与填写、当前文件名引用及文件摘要；内容真实性、逐镜覆盖、确认依据与制作适配另行复核。',
    }


def main():
    parser = argparse.ArgumentParser(description='G3制作交接文件检查；不生成素材。')
    parser.add_argument('--storyboard', required=True, help='本次分镜定稿或等效文件')
    parser.add_argument('--assets', required=True, help='资产需求或包含等效资产表的文件')
    parser.add_argument('--handoff', required=True, help='复核交接或合并交接文件')
    parser.add_argument('--report', help='保存JSON检查报告')
    args = parser.parse_args()
    inputs = [Path(x).expanduser().resolve() for x in (args.storyboard, args.assets, args.handoff)]
    inputs.append(Path(__file__).resolve())
    report = Path(args.report).expanduser().resolve() if args.report else None
    if report and any(report == source or (report.exists() and source.exists() and report.samefile(source)) for source in inputs):
        parser.error('报告不能覆盖输入文件或其路径别名')
    result = check(*inputs[:3])
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        target = Path(args.report).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(output + '\n', encoding='utf-8')
    print(output)
    return 0 if result['documents_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
