#!/usr/bin/env python3
"""Check the twelve labelled fields in newly written G3 shot drafts."""
import argparse
import json
import re
from pathlib import Path
from check_handoff import fields

REQUIRED = ('场景', '机位', '站位', '角度', '光影', '色调', '画面内容', '状态', '台词', '重音', '人声', '音效')
SHOT = re.compile(r'^#{1,6}\s+镜头\s*(?:[A-Za-z]*\d+[A-Za-z]*|[一二三四五六七八九十百]+)(?=$|[^A-Za-z0-9一二三四五六七八九十百])')
SHOT_LIKE = re.compile(r'^#{1,6}\s+镜头(?=\s|[A-Za-z0-9一二三四五六七八九十百])')
PLACEHOLDERS = {'', '待填写', '待补充', 'TODO', 'TBD'}


def check_text(text):
    sections = re.split(r'(?m)^(?=\s*#{1,6}\s+)', text)
    shots = []
    errors = []
    for section in sections:
        lines = section.splitlines()
        if not lines or not SHOT_LIKE.match(lines[0]):
            continue
        heading = lines[0].strip()
        if not SHOT.match(heading):
            errors.append(heading + '：无法识别镜号')
            continue
        values = dict(fields(section))
        missing = [name for name in REQUIRED if values.get(name, '').strip().upper() in PLACEHOLDERS]
        shots.append(heading)
        if missing:
            errors.append(heading + '：缺少或未填写 ' + '、'.join(missing))
    if not shots:
        errors.append('未找到已编号的逐镜标题；既有等效表格使用G3交接检查。')
    return {'draft_structure_passed': bool(shots) and not errors, 'shots_checked': len(shots),
            'errors': errors, 'scope': '仅检查每镜十二栏是否有值；待确认、不适用、内容真实性、照明质量与用户确认仍需实际复核。'}


def main():
    parser = argparse.ArgumentParser(description='检查新写的G3逐镜十二栏结构；不审核内容。')
    parser.add_argument('storyboard')
    args = parser.parse_args()
    try:
        text = Path(args.storyboard).expanduser().read_text(encoding='utf-8-sig')
    except (OSError, UnicodeError) as exc:
        print(json.dumps({'draft_structure_passed': False, 'errors': [str(exc)]}, ensure_ascii=False))
        return 1
    result = check_text(text)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['draft_structure_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
