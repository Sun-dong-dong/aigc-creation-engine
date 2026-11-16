#!/usr/bin/env python3
"""Offline G5 gate. Validates structure and review evidence; never submits media."""
import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
ELEMENTS = ('size', 'angle_composition', 'camera', 'subject', 'action',
            'environment_light', 'style_constraints')
JUDGMENTS = ('dialogue_speakers', 'states', 'sound', 'continuity',
             'clarity', 'current_basis', 'music_subtitles')
H3_BASE = ['integrated_multimodal_description', 'overall_soundscape', 'non_diegetic_music']
H3_REF = ['subject_definitions', 'summary', 'retention_analysis', 'detailed_description',
          'overall_soundscape', 'non_diegetic_music']
LABEL = r'<(?:Subject|Picture|Video|Audio) [1-9]\d*>'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_task(path):
    path = Path(path).resolve()
    task = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(task, dict):
        raise ValueError('任务必须为JSON对象')
    return path, task


def resolve(root, value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('文件路径为空')
    return (root / value).resolve()


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def normalize_shot_id(value):
    if not isinstance(value, str):
        return None
    value = re.sub(r'^(?:镜头|镜|shot)\s*', '', value.strip(), flags=re.I)
    if re.fullmatch(r'[一二三四五六七八九十百]+', value):
        return ('中文', value)
    match = re.fullmatch(r'([A-Za-z]*)([0-9]+)([A-Za-z]*)', value)
    if not match:
        return None
    prefix, number, suffix = match.groups()
    prefix, suffix = prefix.upper(), suffix.upper()
    # Historically S is the shot marker, not a distinct source prefix.
    if prefix in ('', 'S'):
        return (int(number), suffix)
    return (prefix, int(number), suffix)


def literal_in_narrative(value, body, seedance=False):
    if not nonempty(value):
        return False
    speech_pattern = r'\{[^{}]*\}' if seedance else r'<d>.*?</d>'
    speech = [m.span() for m in re.finditer(speech_pattern, body, re.S)]
    quoted = [m.span() for m in re.finditer(r'"[^"\n]*"|“[^”]*”|「[^」]*」|『[^』]*』|〖[^〗]*〗', body)]
    for m in re.finditer(re.escape(value), body):
        start, end = m.span()
        if any(start < b and end > a for a, b in speech):
            continue
        if any(a <= start and end <= b for a, b in quoted):
            continue
        return True
    return False


def task_input_paths(path, task):
    paths = [path, Path(__file__), BASE / '模型提示词规范.md', BASE / '模型模板/分支配置.json']
    root = path.parent
    for key in ('prompt_file', 'delivery_file', 'post_handoff_file', 'plan_file'):
        if nonempty(task.get(key)):
            paths.append(resolve(root, task[key]))
    first = task.get('draft_review', {})
    if isinstance(first, dict) and nonempty(first.get('path')):
        paths.append(resolve(root, first['path']))
    for key in ('source_files', 'references', 'dependency_files'):
        values = task.get(key, [])
        if isinstance(values, list):
            paths.extend(resolve(root, item['path']) for item in values if isinstance(item, dict) and nonempty(item.get('path')))
    fixed = task.get('fixed_context', {})
    if isinstance(fixed, dict) and nonempty(fixed.get('path')):
        paths.append(resolve(root, fixed['path']))
    profile = json.loads((BASE / '模型模板/分支配置.json').read_text()).get(task.get('profile'), {})
    if profile.get('template'):
        paths.append(BASE / profile['template'])
    return paths


def protect_report_output(report, paths):
    report = Path(report).resolve()
    for value in paths:
        target = Path(value).resolve()
        if report == target or (report.exists() and target.exists() and report.samefile(target)):
            raise ValueError('校验报告不能覆盖任务、提示词、参考或其他实际输入文件')


def check_post_handoff(root, task, sid, source_quote, note, errors):
    try:
        handoff = resolve(root, task.get('post_handoff_file')).read_text(encoding='utf-8')
    except (OSError, ValueError, TypeError):
        handoff = ''
    # A transfer must exist in a single row or a single event section, not as
    # unrelated words scattered across the handoff document.
    if not all(nonempty(x) for x in (sid, source_quote, note)):
        errors.append(f'{sid}后期文字转交索引缺失或无法定位同项来源与记录')
        return
    rows = [[cell.strip().replace('\\|', '|') for cell in re.split(r'(?<!\\)\|', line.strip()[1:-1])]
            for line in handoff.splitlines() if line.strip().startswith('|') and line.strip().endswith('|')]
    normalized_sid = normalize_shot_id(sid)
    row_match = normalized_sid is not None and any(any(normalize_shot_id(cell) == normalized_sid for cell in cells)
                    and note in cells and any(source_quote in cell for cell in cells) for cells in rows)
    sections = [section for section in re.split(r'(?m)(?=^#{1,6}\s+)', handoff)
                if not re.search(r'(?m)^\s*\|', section)]
    shot_pattern = r'(?<![A-Za-z0-9])' + re.escape(sid) + r'(?![A-Za-z0-9])'
    note_pattern = r'(?<![A-Za-z0-9_-])' + re.escape(note) + r'(?![A-Za-z0-9_-])'
    section_match = any(re.search(shot_pattern, section) and re.search(note_pattern, section)
                        and source_quote in section for section in sections)
    if not row_match and not section_match:
        errors.append(f'{sid}后期文字转交索引缺失或无法定位同项来源与记录')


def check_delivery_and_dependencies(root, task, prompt, errors):
    try:
        raw = resolve(root, task.get('delivery_file')).read_bytes()
        delivery = raw.decode('utf-8')
    except (OSError, ValueError, TypeError):
        errors.append('缺少可读取的最终完整交付文件')
        delivery, raw = '', b''
    review = task.get('review', {}).get('delivery', {})
    if (not isinstance(review, dict) or review.get('status') != 'pass'
            or review.get('sha256') != sha(raw) or not nonempty(review.get('evidence'))):
        errors.append('最终交付文件尚未实际复核或版本绑定已失效')
    # The reviewed Markdown contains exactly one model input block.
    codes = re.findall(r'^```[^\n]*\n(.*?)^```[ \t]*$', delivery, re.M | re.S)
    if len(codes) != 1 or codes[0].strip() != prompt.strip():
        errors.append('最终交付须有唯一且与当前提示词一致的完整复制区')
    # Ignore code blocks when checking the human-facing table.
    prose = re.sub(r'^```[^\n]*\n.*?^```[ \t]*$', '', delivery, flags=re.M | re.S)
    tables, current = [], None
    delivery_rows = {}
    for line in prose.splitlines():
        if not line.strip().startswith('|') or not line.strip().endswith('|'):
            current = None
            continue
        cells = [x.strip() for x in re.split(r'(?<!\\)\|', line.strip()[1:-1])]
        if len(cells) >= 2 and cells[0] == '镜头' and '时间' in cells[1]:
            current = [cells, [], False]
            tables.append(current)
        elif current is not None:
            if all(re.fullmatch(r':?-+:?', x) for x in cells):
                current[2] = not current[1]
            else:
                current[1].append(cells)
    if len(tables) != 1:
        errors.append('最终交付缺少唯一的四列镜头说明表')
    else:
        header, rows, separator = tables[0]
        if not separator:
            errors.append('最终交付镜头表缺少有效Markdown分隔行')
        if (len(header) != 4 or header[0] != '镜头' or header[1] not in ('时间', '时间（估算）', '时间（实测）')
                or header[2:] != ['景别与运镜', '画面、表演和台词']):
            errors.append('最终交付镜头说明表列数、名称或顺序不符合四列模板')
        shots = task.get('shots', [])
        if len(rows) != len(shots) or any(len(r) != 4 or any(not c for c in r) for r in rows):
            errors.append('最终交付表格存在缺镜、空列或列数错误')
        else:
            actual = [normalize_shot_id(r[0]) for r in rows]
            expected = [normalize_shot_id(s['id']) for s in shots]
            ordinal = [(i + 1, '') for i in range(len(shots))]
            if None in actual or None in expected or len(set(expected)) != len(expected) or (actual != expected and actual != ordinal):
                errors.append('最终交付表格镜号覆盖或顺序不一致')
            else:
                delivery_rows = {shot['id']: row[3] for shot, row in zip(shots, rows)}
    reference_prose = re.sub(r'[*_`]', '', prose)
    if not re.search(r'^参考资产\s*[：:]\s*\S[^\n]*$', reference_prose, re.M):
        errors.append('最终交付缺少实际参考资产说明；无参考时也须说明')
    dependencies = task.get('dependency_files', [])
    if not isinstance(dependencies, list):
        errors.append('非投喂依赖文件清单格式错误')
        dependencies = []
    by_path = {}
    for item in dependencies:
        if not isinstance(item, dict) or not nonempty(item.get('path')) or not nonempty(item.get('role')):
            errors.append('非投喂依赖缺少路径或职责')
            continue
        if item['path'] in by_path:
            errors.append('非投喂依赖重复登记')
        by_path[item['path']] = item
        try:
            actual = sha(resolve(root, item['path']).read_bytes())
        except OSError:
            actual = None
        if actual is None or actual != item.get('sha256'):
            errors.append('非投喂依赖缺失或版本摘要不一致：' + item['path'])
    context = task.get('continuity_context', {})
    if not isinstance(context, dict) or type(context.get('applicable')) is not bool or not nonempty(context.get('basis')):
        errors.append('缺少同场相邻段衔接的适用判断与具体依据')
    elif context['applicable']:
        dependency = by_path.get(context.get('dependency_path'))
        if not dependency:
            errors.append('同场前段的选用文件或有效分镜未绑定为非投喂依赖')
        elif dependency['role'] == 'previous_segment_video':
            at = context.get('frame_time_seconds')
            if type(at) not in (int, float) or not math.isfinite(at) or at < 0:
                errors.append('前段尾帧缺少有效的实际取帧位置')
        elif dependency['role'] not in ('previous_segment_tail_frame', 'previous_segment_storyboard'):
            errors.append('前段衔接依赖职责不匹配')
    return delivery_rows


def inputs(path, task):
    root = path.parent
    prompt = resolve(root, task['prompt_file']).read_text(encoding='utf-8')
    profile = json.loads((BASE / '模型模板/分支配置.json').read_text())[task['profile']]
    template = BASE / profile['template']
    files = {}
    for item in task.get('source_files', []) + task.get('references', []):
        p = resolve(root, item['path'])
        files[item['path']] = sha(p.read_bytes())
    payload = {k: v for k, v in task.items() if k not in ('review', 'authorization')}
    try:
        plan_hash = sha(resolve(root, task['plan_file']).read_bytes())
    except (KeyError, OSError, ValueError):
        plan_hash = None
    try:
        draft_hash = sha(resolve(root, task['draft_review']['path']).read_bytes())
    except (KeyError, OSError, ValueError, TypeError):
        draft_hash = None
    try:
        fixed_hash = sha(resolve(root, task['fixed_context']['path']).read_bytes())
    except (KeyError, OSError, ValueError, TypeError):
        fixed_hash = None
    def optional_hash(value):
        try:
            return sha(resolve(root, value).read_bytes())
        except (OSError, ValueError, TypeError):
            return None
    dependencies = {item['path']: optional_hash(item['path'])
                    for item in task.get('dependency_files', []) if isinstance(item, dict) and isinstance(item.get('path'), str)}
    payload.update(actual_delivery_sha256=optional_hash(task.get('delivery_file')),
                   actual_dependency_sha256=dependencies,
                   actual_post_handoff_sha256=optional_hash(task.get('post_handoff_file')),
                   actual_fixed_context_sha256=fixed_hash,
                   actual_prompt_sha256=sha(prompt.encode()), actual_files=files,
                   actual_segmentation_sha256=plan_hash,
                   actual_first_draft_sha256=draft_hash,
                   actual_template_sha256=sha(template.read_bytes()), profile_contract=profile,
                   actual_norm_sha256=sha((BASE / '模型提示词规范.md').read_bytes()),
                   validator_sha256=sha(Path(__file__).read_bytes()))
    fingerprint = sha(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                separators=(',', ':')).encode())
    return prompt, profile, files, fingerprint


def fingerprint(path):
    p, t = read_task(path)
    return inputs(p, t)[3]


def h3_format(text, task, errors):
    mode = task['mode']
    expected = H3_REF if mode == 'Ref2VA' else H3_BASE
    fields = list(re.finditer(r'^([a-z_]+):[ \t]*', text, re.M))
    if [m[1] for m in fields] != expected:
        errors.append('H3字段缺失、重复、顺序错误或出现非模板字段')
        return {}, ''
    sections = {m[1]: text[m.end():fields[i+1].start() if i+1 < len(fields) else len(text)].strip()
                for i, m in enumerate(fields)}
    if any(not v for v in sections.values()):
        errors.append('H3字段内容为空')
    prefix = text[:fields[0].start()]
    n = len(task['shots'])
    duration = float(task['parameters']['duration'])
    instructions = {
        'T2VA': '', 'Ref2VA': '',
        'I2VA': 'For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.\n\n',
        'FL2VA': f'How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot {n}) aligns with the {duration:.2f}-second mark of the target video.\n\n',
        'L2VA': f'How the reference pictures align with the target video — <Picture 1> (from [Shot {n}]) aligns with the {duration:.2f}-second mark of the target video.\n\n'}
    if prefix != instructions[mode]:
        errors.append('H3对齐指令、时长、末镜号或指令后空行不符合本模式模板')
    body = sections.get('detailed_description', sections.get('integrated_multimodal_description', ''))
    matches = list(re.finditer(r'\[Shot (\d+)\]', body))
    if [int(m[1]) for m in matches] != list(range(1, n+1)):
        errors.append('H3逐镜编号与任务镜数不一致')
    blocks, cut_times = {}, []
    for i, m in enumerate(matches):
        block = body[m.end():matches[i+1].start() if i+1 < len(matches) else len(body)].strip()
        if i == 0:
            if re.match(r'At \d', block):
                errors.append('H3首镜不能带切点时间')
        else:
            time = re.match(r'At (\d{2}):([0-5]\d)\.(\d{3}),', block)
            if not time:
                errors.append(f'H3第{i+1}镜缺少规范切点')
            else:
                cut_times.append(int(time[1])*60 + int(time[2]) + int(time[3])/1000)
        if i < n:
            blocks[task['shots'][i]['id']] = block
    if any(not 0 < t < duration for t in cut_times) or any(a >= b for a, b in zip(cut_times, cut_times[1:])):
        errors.append('H3切点必须严格递增且位于请求时长内部')
    if mode != 'Ref2VA' and body[:matches[0].start() if matches else 0].strip():
        errors.append('H3基础描述应从[Shot 1]开始')
    if mode == 'Ref2VA':
        if matches and not body[:matches[0].start()].strip():
            errors.append('H3全参考模式缺少首镜前的风格描述')
        types = ('keyframe completion', 'reference generation', 'video editing',
                 'video continuation', 'audio reuse', 'audio reference')
        pref = re.match(r'\[([^\]]+)\]', sections['summary'])
        if not pref or any(x not in types for x in pref[1].split(' + ')):
            errors.append('H3全参考summary缺少官方任务类型前缀')
        definitions = set(re.findall(r'^(' + LABEL + r')\s', sections['subject_definitions'], re.M))
        used = set(re.findall(LABEL, text))
        bound = {r['label'] for r in task.get('references', [])}
        if not used <= definitions | bound:
            errors.append('H3存在未定义或未绑定的参考标签')
        if bound - used:
            errors.append('H3实际参考未在提示词中明确引用')
        definition_entries = list(re.finditer(r'^(' + LABEL + r')\s', sections['subject_definitions'], re.M))
        source_graph = {}
        for i, item in enumerate(definition_entries):
            paragraph = sections['subject_definitions'][item.end():definition_entries[i+1].start() if i+1 < len(definition_entries) else len(sections['subject_definitions'])]
            source_graph[item[1]] = set(re.findall(LABEL, paragraph)) - {item[1]}
        def has_source(label, seen):
            if label in bound:
                return True
            if label in seen:
                return False
            return any(has_source(other, seen | {label}) for other in source_graph.get(label, set()))
        if any(not has_source(label, set()) for label in definitions):
            errors.append('H3参考定义无法追溯到实际输入文件')
        retention = set(re.findall(r'^(' + LABEL + r')(?=\s|:)', sections['retention_analysis'], re.M))
        if retention != definitions:
            errors.append('H3独立定义与retention_analysis条目不对应')
        for line in sections['retention_analysis'].splitlines():
            if re.match(LABEL, line):
                allowed = ('fully_copy', 'partially_copy', 'reference', 'weak_reference') if line.startswith('<Audio') else ('fully_preserved', 'partially_preserved', 'attribute_transfer', 'weak_reference')
                marker = re.search(r':\s*([a-z_]+)\s*-', line)
                if not marker or marker[1] not in allowed:
                    errors.append('H3保留关系标记不符合官方可选值')
    else:
        allowed = set() if mode == 'T2VA' else {'<Picture 1>'}
        if mode == 'FL2VA':
            allowed.add('<Picture 2>')
        if set(re.findall(LABEL, text)) - allowed:
            errors.append('H3基础模式混入其他模式参考标签')
    if re.search(r'@(?:图片|视频|音频|image|video|audio)\s*\d', text, re.I):
        errors.append('H3混入其他模型引用语法')
    if text.count('<d>') != text.count('</d>'):
        errors.append('H3对白标签未闭合')
    utterances = re.findall(r'<d>\[[^\]]+\]\s*(.*?)</d>', body, re.S)
    if len(utterances) != body.count('<d>'):
        errors.append('H3对白缺少语种或标签结构不正确')
    expected_lines = [d['text'] for s in task['shots'] for d in s.get('dialogue', [])]
    actual_lines = [re.sub(r'</?(?:scenetrans|cutoff)>', '', x).strip() for x in utterances]
    joined_lines, continued = [], False
    for utterance in utterances:
        head = bool(re.match(r'\s*<scenetrans>', utterance))
        tail = bool(re.search(r'<scenetrans>\s*$', utterance))
        clean = re.sub(r'</?(?:scenetrans|cutoff)>', '', utterance)
        if head != continued:
            errors.append('H3跨镜对白的scenetrans连接标记不成对')
        if continued and joined_lines:
            joined_lines[-1] += clean
        else:
            joined_lines.append(clean)
        continued = tail
    if continued:
        errors.append('H3跨镜对白在末尾缺少续接部分')
    if actual_lines != expected_lines and [x.strip() for x in joined_lines] != expected_lines:
        errors.append('H3台词顺序、原文或标点与任务不一致')
    for s in task['shots']:
        b = blocks.get(s['id'], '')
        if s.get('dialogue') and not re.search(r'\(S\d+(?:,S\d+)*\)', b):
            errors.append(f"{s['id']}缺少H3发声源编号")
    protected = re.sub(r'<d>.*?</d>|"[^"\n]*"', '', text, flags=re.S)
    if re.search(r'[\u4e00-\u9fff]', protected):
        errors.append('H3非对白或画内文字部分应使用英文')
    if 'No on-screen text. No subtitles. No background music.' not in re.sub(r'<d>.*?</d>|"[^"\n]*"', '', body, flags=re.S):
        errors.append('H3画面描述缺少固定无字无字幕无配乐要求')
    if re.search(r'\b(?:on[- ]screen text|subtitle|caption)\s+(?:reads|says|displays|shows|appears)\b', body, re.I):
        errors.append('H3画面描述包含显示文字的指令，需交给后期')
    if sections.get('non_diegetic_music') != 'N/A':
        errors.append('H3无背景音乐时non_diegetic_music必须为N/A')
    if sections['overall_soundscape'] == 'N/A' and task.get('sound_policy') != 'silent':
        errors.append('H3声景N/A只用于已明确全程静音的任务')
    return blocks, body


def seedance_format(text, task, errors):
    policy_text = re.sub(r'\{[^{}]*\}|〖[^〗]*〗|“[^”]*”|"[^"\n]*"|「[^」]*」|『[^』]*』', '', text)
    if '画面无任何文字，无背景音乐。' not in policy_text:
        errors.append('即梦提示词缺少贯穿要求：画面无任何文字，无背景音乐。')
    if re.search(r'〖[^〗]+〗', text):
        errors.append('即梦提示词包含画面文字标记，文字应交给后期')
    if re.search(r'^(?:' + '|'.join(H3_BASE + H3_REF) + r'):', text, re.M) or re.search(LABEL, text):
        errors.append('Seedance混入H3字段或标签')
    markers = list(re.finditer(r'^镜头\s*(\d+)\s*[：:]', text, re.M))
    # This profile fixes the official guide's shot-order option; it does not invent API fields.
    n = len(task['shots'])
    if [int(m[1]) for m in markers] != list(range(1, n+1)):
        errors.append('镜序骨架缺失或与任务不一致')
    if markers and not text[:markers[0].start()].strip():
        errors.append('缺少官方指南中的主体/事件概述')
    blocks = {task['shots'][i]['id']:text[m.end():markers[i+1].start() if i+1 < len(markers) else len(text)].strip()
              for i, m in enumerate(markers) if i < n}
    number = r'[+-]?(?:(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|inf(?:inity)?|nan)'
    timeline = re.findall(r'^(?:镜头\s*\d+\s*[：:]\s*)?(' + number + r')\s*[-—–~至]\s*(' + number + r')\s*(?:秒|s)\s*[：:]', text, re.M | re.I)
    if task['profile'] == 'seedance-2.0' and timeline:
        errors.append('2.0当前模板使用镜序，不能以旧秒数时间轴代替')
    if task['profile'] == 'seedance-2.5':
        timing_text = '\n'.join(blocks.values())
        timing_text = re.sub(r'\{[^{}]*\}|〖[^〗]*〗|“[^”]*”|"[^"\n]*"|「[^」]*」|『[^』]*』', '', timing_text)
        timeline = re.findall(r'(' + number + r')\s*[-—–~至]\s*(' + number + r')\s*(?:秒|s)(?![A-Za-z])', timing_text, re.I)
        if any(not re.fullmatch(r'\d+', value) for pair in timeline for value in pair):
            errors.append('2.5指南时间轴采用非负整数秒')
        duration = float(task['parameters']['duration'])
        for a, b in timeline:
            start, end = float(a), float(b)
            if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= duration):
                errors.append('2.5时间区间必须有限、正向且位于请求总时长内')
    refs = set(re.findall(r'@(?:图片|视频|音频)\s*[1-9]\d*', text))
    bound = {r['label'] for r in task.get('references', [])}
    if not refs <= bound:
        errors.append('存在未绑定的即梦引用')
    if bound - refs:
        errors.append('即梦实际参考未在提示词中明确引用')
    if re.search(r'@(?:image|video|audio)\d+', text, re.I):
        errors.append('即梦模板混入其他入口的引用写法')
    actual = re.findall(r'\{([^{}]*)\}', text)
    expected = [d['text'] for s in task['shots'] for d in s.get('dialogue', [])]
    if actual != expected:
        errors.append('Seedance台词顺序、原文或标点与任务不一致')
    if text.count('{') != len(actual) or text.count('}') != len(actual):
        errors.append('Seedance对白括号不完整')
    return blocks, text


def check_segmentation(plan, shots, sources, duration, errors):
    def reason_present(value):
        return isinstance(value, str) and value.strip() not in ('', '待填', '待填写', '待补充', '无', 'N/A', 'TODO') and not value.strip().startswith('填写')

    if not isinstance(plan, dict):
        errors.append('缺少有效的生成分段方案')
        return None
    scope = plan.get('scope_shots')
    groups = plan.get('groups')
    boundaries = plan.get('boundaries')
    if (not isinstance(scope, list) or not scope or any(normalize_shot_id(x) is None for x in scope)
            or len({normalize_shot_id(x) for x in scope}) != len(scope)):
        errors.append('分段范围镜号为空、无效或重复')
        return None
    if not isinstance(groups, list) or not groups or not isinstance(boundaries, list):
        errors.append('分段方案缺少生成段或相邻镜头判断')
        return None
    plan_sources = plan.get('source_sha256')
    task_sources = [s.get('sha256') for s in sources]
    if not isinstance(plan_sources, list) or not plan_sources or any(not isinstance(x, str) or not x.strip() or x not in task_sources for x in plan_sources):
        errors.append('分段方案的分镜版本摘要不匹配')
    group_ids, ordered, membership, matches = [], [], {}, []
    task_ids = [s.get('id') for s in shots]
    for group in groups:
        if not isinstance(group, dict):
            errors.append('生成段格式无效')
            return None
        gid, ids, why, seconds = group.get('id'), group.get('shots'), group.get('reason'), group.get('duration')
        if not isinstance(gid, str) or not gid.strip() or gid in group_ids:
            errors.append('生成段编号为空或重复')
            return None
        if not isinstance(ids, list) or not ids or any(not isinstance(x, str) or not x.strip() for x in ids):
            errors.append(f'{gid}缺少有效镜号')
            return None
        if not reason_present(why):
            errors.append(f'{gid}缺少分组或单镜理由')
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
            errors.append(f'{gid}缺少有效的请求时长')
        group_ids.append(gid)
        ordered.extend(ids)
        for sid in ids:
            membership[sid] = gid
        if ids == task_ids:
            matches.append(group)
    if ordered != scope:
        errors.append('生成段镜号遗漏、重复或顺序与本次分镜范围不一致')
    expected = list(zip(scope, scope[1:]))
    actual = []
    for item in boundaries:
        if not isinstance(item, dict):
            errors.append('相邻镜头判断格式无效')
            return None
        left, right = item.get('left'), item.get('right')
        actual.append((left, right))
        if not reason_present(item.get('reason')):
            errors.append('相邻镜头缺少合并或拆分理由')
        if left in membership and right in membership:
            expected_decision = 'merge' if membership[left] == membership[right] else 'split'
            if item.get('decision') != expected_decision:
                errors.append('相邻镜头的合并或拆分判断与实际分组不一致')
    if actual != expected:
        errors.append('缺少按原顺序逐一判断的相邻镜头')
    if len(matches) != 1:
        errors.append('本次任务镜号与分段方案中的生成段不一致')
        return None
    match = matches[0]
    if type(match.get('duration')) not in (int, float) or match['duration'] != duration:
        errors.append('本次任务时长与分段方案不一致')
    return match['id']


def shot_source_blocks(root, sources, bindings, shots, errors, pending):
    """Bind each shot to one uniquely identified Markdown heading and its body."""
    allowed = {item['path'] for item in sources}
    blocks = {}
    for shot in shots:
        sid = shot['id']
        binding = bindings.get(sid, {})
        name, heading = binding.get('path'), binding.get('heading', '')
        if not name or not heading:
            pending.append(f'{sid}缺少分镜来源文件和镜头标题绑定；等效分镜表行目前不能直接绑定，需提供可定位的逐镜Markdown标题来源')
            continue
        if name not in allowed or not re.fullmatch(r'#{1,6}\s+[^\n]+', heading):
            errors.append(f'{sid}的来源绑定不是已登记文件中的Markdown标题')
            continue
        source = resolve(root, name).read_text(encoding='utf-8')
        hits = list(re.finditer(r'^' + re.escape(heading) + r'\s*$', source, re.M))
        expected_id = binding.get('source_shot_id', sid)
        if expected_id != sid and not nonempty(binding.get('mapping_evidence')):
            errors.append(f'{sid}的源镜号映射缺少具体依据')
            continue
        header_match = re.match(r'^#{1,6}\s+(?:(?:镜头|镜|shot)\s*)?([A-Za-z]*[0-9]+[A-Za-z]*|[一二三四五六七八九十百]+)(?=$|[\s｜|：:，,。；;、！？!?（(【\[［〔《〈「『“‘])', heading, re.I)
        header_id = normalize_shot_id(header_match[1]) if header_match else None
        if len(hits) != 1 or normalize_shot_id(expected_id) is None or normalize_shot_id(expected_id) != header_id:
            errors.append(f'{sid}的镜头标题无法唯一定位或镜号不符')
            continue
        start = hits[0].start()
        following = re.search(r'^#{1,6}\s+', source[hits[0].end():], re.M)
        end = hits[0].end() + following.start() if following else len(source)
        blocks[sid] = source[start:end]
    return blocks


def check_fixed_context(root, task, text, errors):
    """Check exact approved project/scene text; semantic scope is reviewed separately."""
    binding = task.get('fixed_context')
    if not isinstance(binding, dict):
        errors.append('缺少项目固定描述绑定 fixed_context')
        return
    try:
        data = resolve(root, binding.get('path')).read_bytes()
        fixed = json.loads(data)
    except (OSError, ValueError, TypeError):
        errors.append('项目固定描述文件不可读取或不是有效JSON')
        return
    if not isinstance(fixed, dict):
        errors.append('项目固定描述必须为JSON对象')
        return
    if binding.get('sha256') != sha(data):
        errors.append('项目固定描述摘要不一致，须读取当前版本重新核对')
    for key in ('project_id', 'version'):
        if (not isinstance(fixed.get(key), str) or not fixed[key].strip()
                or binding.get(key) != fixed[key]):
            errors.append('项目固定描述的项目或版本不匹配：' + key)
    def nonempty(value):
        return isinstance(value, str) and bool(value.strip())
    if fixed.get('confirmed') is not True or not nonempty(fixed.get('confirmation_evidence')):
        errors.append('项目固定画风缺少真实确认记录')
    if binding.get('reviewed') is not True or not nonempty(binding.get('review_evidence')):
        errors.append('未记录固定描述适用范围及冲突的实际复核')
    profile = task.get('profile')
    if profile in ('h3-base', 'h3-ref'):
        field = 'detailed_description' if profile == 'h3-ref' else 'integrated_multimodal_description'
        match = re.search(r'^' + field + r':[ \t]*(.*?)(?=^[a-z_]+:|\Z)', text, re.M | re.S)
        narrative = match.group(1) if match else ''
        speech_spans = [m.span() for m in re.finditer(r'<d>.*?</d>', narrative, re.S)]
    else:
        narrative = text
        speech_spans = [m.span() for m in re.finditer(r'\{[^{}]*\}', narrative)]
    quoted_spans = [m.span() for m in re.finditer(r'〖[^〗]*〗|“[^”]*”|\"[^\"\n]*\"|「[^」]*」|『[^』]*』', narrative)]
    def in_narrative(value):
        for occurrence in re.finditer(re.escape(value), narrative):
            start, end = occurrence.span()
            if any(start < b and end > a for a, b in speech_spans):
                continue
            if any(a <= start and end <= b for a, b in quoted_spans):
                continue
            return True
        return False
    def require_text(mapping, name):
        value = mapping.get(profile) if isinstance(mapping, dict) else None
        if not isinstance(value, str) or not value.strip():
            errors.append(name + '缺少本模型分支的固定描述')
        elif value != value.strip():
            errors.append(name + '固定描述首尾须去掉空白')
        elif not in_narrative(value):
            errors.append(name + '固定描述未完整原样写入提示词')
    require_text(fixed.get('styles'), '项目画风')
    scene_ids = binding.get('scene_ids')
    if (not isinstance(scene_ids, list)
            or any(not isinstance(i, str) or not i.strip() for i in scene_ids)
            or len(set(scene_ids)) != len(scene_ids)):
        errors.append('场景状态清单非法或重复')
        return
    if not scene_ids and not nonempty(binding.get('scene_omission_reason')):
        errors.append('未声明本次适用场景状态或具体不适用依据')
    scenes = fixed.get('scenes', [])
    if not isinstance(scenes, list) or any(not isinstance(i, dict) for i in scenes):
        errors.append('固定描述场景表格式错误')
        return
    for scene_id in scene_ids:
        matches = [i for i in scenes if i.get('id') == scene_id]
        if len(matches) != 1:
            errors.append('场景状态不存在或重复：' + scene_id)
            continue
        scene = matches[0]
        if (scene.get('confirmed') is not True
                or not nonempty(scene.get('confirmation_evidence'))
                or not nonempty(scene.get('applies_to'))):
            errors.append('场景状态未确认或缺少适用范围：' + scene_id)
        require_text(scene.get('texts'), '场景 ' + scene_id)


def check_shot_lighting(task, blocks, errors, delivery_rows=None):
    """Verify located evidence, not physical lighting or completeness of character enumeration."""
    rows = task.get('review', {}).get('lighting', {})
    for shot in task.get('shots', []):
        sid = shot['id']
        row = rows.get(sid) if isinstance(rows, dict) else None
        if not isinstance(row, dict):
            errors.append(f'{sid}缺少逐镜光源、固定色温与人物受光核对')
            continue
        body = blocks.get(sid, '')
        def located(value):
            return literal_in_narrative(value, body, task.get('profile', '').startswith('seedance'))
        for key in ('temperature_quote', 'source_quote'):
            if not located(row.get(key)):
                errors.append(f'{sid}/{key}缺少本镜非对白正文中的照明证据')
        quote = row.get('temperature_quote', '')
        if not isinstance(quote, str) or not re.search(r'(?<![\d.])[1-9]\d{2,4}(?:\.\d+)?\s*[Kk](?![A-Za-z0-9])', quote):
            errors.append(f'{sid}色温须包含已确定的明确K值')
        if delivery_rows is not None:
            table_body = delivery_rows.get(sid, '')
            temperature_pattern = r'(?<![\d.])([1-9]\d{2,4}(?:\.\d+)?)\s*[Kk](?![A-Za-z0-9])'
            model_temperatures = {float(v) for v in re.findall(temperature_pattern, quote)} if isinstance(quote, str) else set()
            table_temperatures = {float(v) for v in re.findall(temperature_pattern, table_body)}
            if not table_temperatures:
                errors.append(f'{sid}中文镜头表本镜末列缺少独立照明与固定色温，不得用公共段或其他镜头代替')
            elif not model_temperatures <= table_temperatures:
                errors.append(f'{sid}中文镜头表未完整承接本镜照明记录的固定色温')
        if not isinstance(row.get('basis'), str) or not row['basis'].strip():
            errors.append(f'{sid}缺少照明设定及可见人物覆盖的实际核对依据')
        present, characters = row.get('characters_present'), row.get('characters')
        if type(present) is not bool or not isinstance(characters, list):
            errors.append(f'{sid}人物受光记录格式不完整')
            continue
        if (present and not characters) or (not present and characters):
            errors.append(f'{sid}可见人物状态与受光清单不一致')
        seen = set()
        for item in characters:
            if not isinstance(item, dict):
                errors.append(f'{sid}人物受光条目格式错误')
                continue
            subject = item.get('subject')
            if not isinstance(subject, str) or not subject.strip() or subject in seen:
                errors.append(f'{sid}人物受光缺主体或重复')
            else:
                seen.add(subject)
            if not located(item.get('prompt_quote')):
                errors.append(f'{sid}缺少本镜人物具体受光的正文证据')


def validate(path):
    path, task = read_task(path)
    text, profile, filehashes, digest = inputs(path, task)
    errors, pending = [], []
    check_fixed_context(path.parent, task, text, errors)
    if profile['official_verified'] is not True or task['mode'] not in profile['modes']:
        errors.append('没有对应的已核实官方模板分支')
    if sha((BASE / profile['template']).read_bytes()) != profile['template_sha256']:
        errors.append('模板与已核实登记不一致')
    if not set(profile['official_urls']) <= set(task.get('official_reads', [])):
        errors.append('未记录本分支全部官方规范的实际读取')
    if task.get('entry') not in profile['entries']:
        errors.append('入口与模板登记不匹配')
    if not isinstance(task.get('task_id'), str) or not task['task_id'].strip():
        errors.append('缺少任务ID')
    shots = task.get('shots', [])
    shot_ids = [normalize_shot_id(s['id']) for s in shots]
    if not shots or None in shot_ids or len(set(shot_ids)) != len(shots):
        raise ValueError('镜号为空、格式无效或存在同义重复')
    duration = task['parameters']['duration']
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
        raise ValueError('请求时长必须是有限正数')
    if re.search(r'\{\{|\[待填\]|\[TODO\]|<待填>|```', text):
        errors.append('正文含模板占位符或外层代码围栏')
    delivery_rows = check_delivery_and_dependencies(path.parent, task, text, errors)
    sources = task.get('source_files', [])
    if not sources:
        errors.append('缺少可逐镜对照的分镜来源文件')
    try:
        plan = json.loads(resolve(path.parent, task.get('plan_file')).read_text(encoding='utf-8'))
    except (OSError, ValueError, json.JSONDecodeError):
        plan = None
    group_id = check_segmentation(plan, shots, sources, duration, errors)
    if not isinstance(task.get('group_id'), str) or task.get('group_id') != group_id:
        errors.append('本次任务缺少匹配的生成段编号')
    source_text = '\n'.join(resolve(path.parent, x['path']).read_text(encoding='utf-8') for x in sources)
    for item in sources + task.get('references', []):
        if item.get('sha256') != filehashes[item['path']]:
            errors.append(f"文件摘要不一致：{item['path']}")
    refs = task.get('references', [])
    labels = [r['label'] for r in refs]
    if len(labels) != len(set(labels)):
        errors.append('参考标签重复绑定')
    if profile['family'] == 'h3':
        tokens = re.findall(r'<(?:Subject|Picture|Video|Audio)\b[^>\n]*(?:>|$)', text, re.I | re.M)
        if any(not re.fullmatch(LABEL, token) for token in tokens + labels):
            errors.append('H3参考标签格式或编号非法')
    else:
        tokens = re.findall(r'@(?:图片|视频|音频|image|video|audio)\s*[-+]?\d+', text, re.I)
        if any(not re.fullmatch(r'@(?:图片|视频|音频)[1-9]\d*', token) for token in tokens + labels):
            errors.append('即梦参考标签格式或编号非法')
    for ref in refs:
        if not ref.get('role'):
            errors.append('参考缺少职责')
        if ref.get('selected') is not True or not ref.get('selection_evidence'):
            pending.append(f"参考尚未确认选用：{ref['path']}")
    if profile['family'] == 'h3':
        if task['mode'] == 'Ref2VA' and not refs:
            errors.append('H3全参考模式缺少真实参考绑定')
        expected_labels = {'T2VA':set(), 'I2VA':{'<Picture 1>'}, 'FL2VA':{'<Picture 1>','<Picture 2>'}, 'L2VA':{'<Picture 1>'}}.get(task['mode'])
        if expected_labels is not None and set(labels) != expected_labels:
            errors.append('H3参考数量/标签与本模式不匹配')
        blocks, _ = h3_format(text, task, errors) if task['mode'] in profile['modes'] else ({}, '')
    else:
        if task['mode'] == 'T2V' and refs:
            errors.append('纯文生模式不能带参考')
        if task['mode'] == 'R2V' and not refs:
            errors.append('参考模式缺少真实参考')
        blocks, _ = seedance_format(text, task, errors)
    check_shot_lighting(task, blocks, errors, delivery_rows)
    format_errors = list(errors)
    before_review_errors = len(errors)
    review = task.get('review', {})
    source_blocks = shot_source_blocks(path.parent, sources, review.get('source_bindings', {}), shots, errors, pending)
    first = task.get('draft_review', {})
    draft = ''
    try:
        first_path = resolve(path.parent, first.get('path'))
        if first_path == resolve(path.parent, task['prompt_file']):
            errors.append('初稿与第二版必须分别保存')
        draft = first_path.read_text(encoding='utf-8')
        if first.get('sha256') != sha(draft.encode()):
            errors.append('初稿摘要不一致，须重新检查初稿')
    except (OSError, ValueError, TypeError):
        pending.append('缺少可读取的初稿及其复检记录')
    if (first.get('completed') is not True or not first.get('reviewer')
            or not first.get('reviewed_at') or not first.get('summary')):
        pending.append('初稿逐镜复检尚未完成或缺少复检人、时间与结论')
    if (first.get('review_method') != 'independent_subagent'
            or not str(first.get('review_evidence', '')).strip()):
        pending.append('缺少独立子代理初稿复检的实际记录')
    if first.get('reviewed_shots') != [s['id'] for s in shots]:
        pending.append('初稿复检尚未覆盖本段全部镜头')
    issues = first.get('issues')
    if not isinstance(issues, list):
        pending.append('缺少初稿问题记录；无问题须明确记录空数组')
    else:
        for issue in issues:
            sid = issue.get('shot')
            if sid not in source_blocks or not issue.get('finding', '').strip():
                pending.append('初稿问题缺少有效镜号或具体问题')
                continue
            a, b = issue.get('source_quote', ''), issue.get('revision_quote', '')
            deferred = issue.get('resolution') == 'deferred_post'
            if (not a.strip() or a not in source_blocks[sid]
                    or (deferred and not issue.get('post_note', '').strip())
                    or (not deferred and (not b.strip() or b not in blocks.get(sid, '')))):
                errors.append(f'{sid}初稿问题的分镜依据或第二版修订证据无法定位')
            if deferred:
                check_post_handoff(path.parent, task, sid, a, issue.get('post_note'), errors)
            old = issue.get('draft_quote', '')
            if old and old not in draft:
                errors.append(f'{sid}初稿问题引用无法定位')
            if issue.get('resolved') is not True:
                pending.append(f'{sid}初稿问题尚未在第二版解决')
    if review.get('fingerprint') != digest:
        pending.append('缺少对应当前输入指纹的内容复核，或原复核已失效')
    if not review.get('reviewer') or not review.get('reviewed_at'):
        pending.append('缺少实际内容复核者与时间')
    coverage = review.get('coverage', {})
    compact = review.get('shot_reviews') or None
    if compact is not None and coverage:
        errors.append('新旧逐镜检查格式不能同时使用，须选用一种并移除另一套')
    for shot in shots:
        sid = shot['id']
        if compact is not None:
            row = compact.get(sid, {}) if isinstance(compact, dict) else {}
            if not isinstance(row, dict):
                row = {}
            checked = row.get('checked', [])
            if (row.get('status') != 'pass' or not isinstance(checked, list)
                    or not all(isinstance(value, str) for value in checked)
                    or len(checked) != len(ELEMENTS) or set(checked) != set(ELEMENTS)):
                pending.append(f'{sid}逐镜复核未通过或七要素检查不完整')
        else:
            for element in ELEMENTS:
                row = coverage.get(sid, {}).get(element, {})
                if row.get('status') == 'not_applicable':
                    if not row.get('reason', '').strip():
                        pending.append(f'{sid}/{element}不适用时须说明依据')
                    continue
                if row.get('status') != 'pass':
                    pending.append(f'{sid}/{element}尚未复核通过')
                    continue
                a, b = row.get('source_quote', ''), row.get('prompt_quote', '')
                pool = text if row.get('scope') == 'shared' else blocks.get(sid, '')
                source_pool = source_text if row.get('scope') == 'shared' else source_blocks.get(sid, '')
                if row.get('scope') == 'shared' and not row.get('scope_reason', '').strip():
                    pending.append(f'{sid}/{element}的共享依据缺少适用说明')
                if not a.strip() or a not in source_pool or not b.strip() or b not in pool:
                    errors.append(f'{sid}/{element}的分镜或提示词证据无法定位')
        details = review.get('detail_checks', {}).get(sid, [])
        if not isinstance(details, list) or (not details and compact is None):
            pending.append(f'{sid}缺少逐条关键细节核对')
            continue
        for detail in details:
            a, b = detail.get('source_quote', ''), detail.get('prompt_quote', '')
            shared = detail.get('scope') == 'shared'
            source_pool = source_text if shared else source_blocks.get(sid, '')
            prompt_pool = text if shared else blocks.get(sid, '')
            if shared and not nonempty(detail.get('scope_reason')):
                errors.append(f'{sid}共享关键细节缺少适用范围依据')
            if detail.get('status') == 'deferred_post':
                if (not detail.get('item', '').strip() or not a.strip() or a not in source_pool
                        or not detail.get('post_note', '').strip()):
                    errors.append(f'{sid}后期文字交接缺少分镜依据或交接记录')
                check_post_handoff(path.parent, task, sid, a, detail.get('post_note'), errors)
            elif detail.get('status') != 'pass':
                pending.append(f'{sid}存在未完成的关键细节核对')
            elif (not detail.get('item', '').strip() or not a.strip() or a not in source_pool
                  or not b.strip() or b not in prompt_pool or (shared and not detail.get('scope_reason', '').strip())):
                errors.append(f'{sid}关键细节的来源或提示词证据无法定位')
    requirements = task.get('reference_requirements', {})
    if requirements.get('reviewed') is not True or not requirements.get('basis', '').strip():
        pending.append('尚未核对用户及资产清单中的全部素材与音色需求')
    required = requirements.get('items')
    if not isinstance(required, list):
        pending.append('缺少素材与音色需求清单；无需求也须显式登记空清单')
    else:
        by_label = {ref['label']: ref for ref in refs}
        for item in required:
            if not item.get('name') or not item.get('role') or not item.get('basis'):
                pending.append('素材需求缺少名称、职责或选用依据')
            involved = item.get('shots')
            if not isinstance(involved, list) or not involved or not set(involved) <= {s['id'] for s in shots}:
                pending.append(f"素材需求缺少本段适用镜号：{item.get('name', '未命名')}")
            label = item.get('label')
            if item.get('verified') is not True or label not in by_label:
                pending.append(f"素材需求尚未核实实际输入绑定：{item.get('name', '未命名')}")
            elif not item.get('binding_evidence', '').strip():
                pending.append(f"素材需求缺少绑定依据：{item.get('name', '未命名')}")
    timing = task.get('duration_assessment', {})
    duration_risk = None
    original = timing.get('source_seconds')
    if type(original) not in (int, float) or not math.isfinite(original) or original <= 0 or not timing.get('basis', '').strip():
        pending.append('缺少原分镜时长及本段容量判断依据')
    elif timing.get('status') not in ('pending', 'estimated', 'measured'):
        pending.append('本段容量判断状态无效')
    elif timing.get('status') == 'measured':
        measured = timing.get('measured_seconds')
        if (type(measured) not in (int, float) or not math.isfinite(measured)
                or measured <= 0 or not timing.get('method', '').strip()
                or not timing.get('evidence', '').strip()):
            pending.append('实测容量记录无效或缺少方法及依据')
        elif measured > duration:
            duration_risk = '对白、动作与反应的实测容量超过请求时长；生成前需核实容量并按授权执行。'
    elif original > duration:
        duration_risk = '压缩时长的对白、动作与反应容量尚未实测通过；交付提示词时须明确标记风险，生成前需核实容量并按授权执行。'
    elif timing.get('status') != 'estimated':
        pending.append('本段容量判断状态待记录')
    # 可直接计数的中文台词作为粗估下界；复杂读法由人工核对。
    spoken = [line.get('text', '') for shot in shots for line in shot.get('dialogue', []) if isinstance(line, dict)]
    countable = [line for line in spoken if isinstance(line, str) and re.fullmatch(r'[\u3400-\u9fff，。！？、；：‘’“”…—,.!?;:\s]+', line)]
    duration_note = None
    if timing.get('status') != 'measured' and spoken:
        if len(countable) != len(spoken):
            duration_note = '部分台词含数字、英文或其他复杂读法，需按实际发音人工估时。'
        estimated_dialogue = sum(len(re.sub(r'\s+', '', line)) for line in countable) / 4
        if estimated_dialogue > duration:
            duration_risk = '按每秒4字符估算，可直接计数的对白已超过请求时长；如存在并发对白需按实际时间轴试读验证，生成前需解决。'
    for name in JUDGMENTS:
        item = review.get('judgments', {}).get(name, {})
        if item.get('status') != 'pass' or not item.get('evidence', '').strip():
            pending.append(f'内容判断尚未通过：{name}')
    content_pending = list(pending)
    runtime = task.get('runtime', {})
    if runtime.get('verified') is not True or not runtime.get('evidence') or not runtime.get('checked_at'):
        pending.append('实际入口、模式与参数条件尚未核实')
    report = {'task_id':task.get('task_id'), 'fingerprint':digest,
              'format_result':'fail' if format_errors else 'pass',
              'content_review':('invalid_evidence' if len(errors) > before_review_errors else 'recorded_pass' if not content_pending else 'pending'),
              'errors':errors, 'pending':pending,
              'formal_prompt_ready':not errors and not pending,
              'generation_ready':not errors and not pending and not duration_risk and task.get('authorization', {}).get('generate') is True and nonempty(task.get('authorization', {}).get('scope')),
              'duration_risk':duration_risk, 'duration_note':duration_note,
              'media_acceptance':'not_evaluated',
              'limits':'格式检查与人工内容记录分开；程序只能核对已登记证据及绑定，不能发现未登记细节或声音需求，也不能证明引用语义、试读真实性、画面或声音效果。'}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    if args.report:
        try:
            task_path, task = read_task(args.task)
            protect_report_output(args.report, task_input_paths(task_path, task))
        except (KeyError, ValueError, TypeError, OSError) as exc:
            parser.error(str(exc))
    try:
        report = validate(args.task)
    except (KeyError, ValueError, TypeError, OSError, IndexError, AttributeError, OverflowError) as exc:
        report = {'format_result':'fail', 'errors':[str(exc)], 'formal_prompt_ready':False,
                  'generation_ready':False, 'media_acceptance':'not_evaluated'}
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output + '\n', encoding='utf-8')
    print(output)
    return 0 if report['formal_prompt_ready'] else 1 if report.get('errors') else 2


if __name__ == '__main__':
    sys.exit(main())
