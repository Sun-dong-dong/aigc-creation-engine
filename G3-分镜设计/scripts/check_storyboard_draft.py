#!/usr/bin/env python3
"""Check G3 draft structure and flag tight dialogue budgets."""
import argparse
import json
import math
import re
from pathlib import Path
from check_handoff import fields

REQUIRED = ('场景', '机位', '站位', '角度', '光影', '色调', '画面内容', '状态', '台词', '重音', '人声', '音效')
SHOT = re.compile(r'^#{1,6}\s+镜头\s*(?:[A-Za-z]*\d+[A-Za-z]*|[一二三四五六七八九十百]+)(?=$|[^A-Za-z0-9一二三四五六七八九十百])')
SHOT_LIKE = re.compile(r'^#{1,6}\s+镜头(?=\s|[A-Za-z0-9一二三四五六七八九十百])')
PLACEHOLDERS = {'', '待填写', '待补充', 'TODO', 'TBD'}

SECONDS = re.compile(r'[｜|]\s*(?:预计|约)?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:秒|s)\s*$', re.I)
DIALOGUE_FIELD = re.compile(r'(?m)^台词[：:][ \t]*([^\n]*(?:\n(?!\s*(?:' + '|'.join(REQUIRED) + r')[：:])[^\n]*)*)')
# Inline turns need explicit speaker metadata to distinguish them from spoken text.
DIALOGUE_BREAK = re.compile(
    r'<br\s*/?>|\n|(?<=[。！？!?…])[ \t]*(?='
    r'[\u3400-\u9fff·]{1,12}(?:（[^（）\n]+）|\([^()\n]+\)|[，,]对[^，,。！？：:\n]+说)[：:])',
    flags=re.IGNORECASE,
)
NO_DIALOGUE = {'无', '无台词', '无对白', '—', '–', '-', '不适用'}
DEFAULT_RATE = 4.0
TIGHT_REMAINDER = 1.5


def timing_result(heading, dialogue, rate):
    shot = SHOT.match(heading).group().lstrip('# ')
    duration = SECONDS.search(heading)
    lines = DIALOGUE_BREAK.split(dialogue)
    lines = [line.strip() for line in lines if line.strip()]
    if not lines or all(line in NO_DIALOGUE for line in lines):
        return None

    manual = {'shot': shot, 'status': 'manual_check',
              'message': '请按实际读法、时长标注或跨镜与重叠情况人工估算。'}
    if not duration:
        return manual
    parts = [re.split('[：:]', line, maxsplit=1) for line in lines]
    if any(len(part) != 2 or not part[0].strip() or not part[1].strip() for part in parts):
        return manual
    if any(re.search(r'跨镜|续说|承接上一镜|同时说|并行|抢话|重叠对白', part[0]) for part in parts):
        return manual
    speech = re.sub(r'\s', '', ''.join(part[1] for part in parts))
    if (re.search(r'[A-Za-z0-9０-９]|[()（）<>：:]', speech)
            or not re.search(r'[\u3400-\u9fff]', speech)):
        return manual

    shot_seconds = float(duration.group(1))
    dialogue_seconds = len(speech) / rate
    remaining = shot_seconds - dialogue_seconds
    if remaining <= 0:
        status = 'dialogue_fills_shot'
        message = '对白已占满或超出整镜，检查是否给串行动作、读图与反应留出时间。'
    elif remaining < TIGHT_REMAINDER:
        status = 'little_time_left'
        message = '对白外余量较少，请核对哪些动作、读图与反应必须单独占用时间。'
    else:
        status = 'review_timing'
        message = '请结合镜内动作与反应复核实际容量。'
    return {'shot': shot, 'shot_seconds': shot_seconds, 'dialogue_chars': len(speech),
            'dialogue_seconds': round(dialogue_seconds, 3),
            'remaining_seconds': round(remaining, 3), 'status': status, 'message': message}


def check_text(text, dialogue_rate=DEFAULT_RATE):
    sections = re.split(r'(?m)^(?=\s*#{1,6}\s+)', text)
    shots = []
    errors = []
    timing = []
    for section in sections:
        lines = section.splitlines()
        if not lines or not SHOT_LIKE.match(lines[0]):
            continue
        heading = lines[0].strip()
        if not SHOT.match(heading):
            errors.append(heading + '：无法识别镜号')
            continue
        values = dict(fields(section))
        dialogue_field = DIALOGUE_FIELD.search(section)
        if dialogue_field:
            values['台词'] = dialogue_field.group(1).strip()
        result = timing_result(heading, values.get('台词', ''), dialogue_rate)
        if result:
            timing.append(result)
        missing = [name for name in REQUIRED if values.get(name, '').strip().upper() in PLACEHOLDERS]
        shots.append(heading)
        if missing:
            errors.append(heading + '：缺少或未填写 ' + '、'.join(missing))
    if not shots:
        errors.append('未找到已编号的逐镜标题；既有等效表格使用G3交接检查。')
    return {'draft_structure_passed': bool(shots) and not errors, 'shots_checked': len(shots),
            'errors': errors, 'scope': '结构检查只核对每镜十二栏；时长提示需按表演与语速回查，内容、照明和用户确认仍需复核。',
            'dialogue_rate': dialogue_rate, 'timing': timing}


def main():
    parser = argparse.ArgumentParser(description='检查G3分镜初稿十二栏并提示对白时间。')
    parser.add_argument('storyboard')
    parser.add_argument('--dialogue-rate', type=float, default=DEFAULT_RATE,
                        help='有项目依据时覆盖默认语速（每秒字符数）')
    args = parser.parse_args()
    if not math.isfinite(args.dialogue_rate) or args.dialogue_rate <= 0:
        parser.error('--dialogue-rate 必须为有限正数')
    try:
        text = Path(args.storyboard).expanduser().read_text(encoding='utf-8-sig')
    except (OSError, UnicodeError) as exc:
        print(json.dumps({'draft_structure_passed': False, 'errors': [str(exc)]}, ensure_ascii=False))
        return 1
    result = check_text(text, args.dialogue_rate)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['draft_structure_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
