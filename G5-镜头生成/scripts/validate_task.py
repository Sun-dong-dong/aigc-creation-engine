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
             'clarity', 'current_basis')
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


def inputs(path, task):
    root = path.parent
    prompt = resolve(root, task['prompt_file']).read_text(encoding='utf-8')
    profile = json.loads((BASE / '模型模板/分支配置.json').read_text())[task['profile']]
    template = BASE / profile['template']
    files = {}
    for item in task.get('source_files', []) + task.get('references', []):
        p = resolve(root, item['path'])
        files[item['path']] = sha(p.read_bytes())
    payload = {k: v for k, v in task.items() if k != 'review'}
    payload.update(actual_prompt_sha256=sha(prompt.encode()), actual_files=files,
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
    if sections['overall_soundscape'] == 'N/A' and task.get('sound_policy') != 'silent':
        errors.append('H3声景N/A只用于已明确全程静音的任务')
    return blocks, body


def seedance_format(text, task, errors):
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


def validate(path):
    path, task = read_task(path)
    text, profile, filehashes, digest = inputs(path, task)
    errors, pending = [], []
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
    if not shots or len({s['id'] for s in shots}) != len(shots):
        raise ValueError('镜号为空或重复')
    duration = task['parameters']['duration']
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
        raise ValueError('请求时长必须是有限正数')
    if re.search(r'\{\{|\[待填\]|\[TODO\]|<待填>|```', text):
        errors.append('正文含模板占位符或外层代码围栏')
    sources = task.get('source_files', [])
    if not sources:
        errors.append('缺少可逐镜对照的分镜来源文件')
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
    before_review_errors = len(errors)
    review = task.get('review', {})
    if review.get('fingerprint') != digest:
        pending.append('缺少对应当前输入指纹的内容复核，或原复核已失效')
    if not review.get('reviewer') or not review.get('reviewed_at'):
        pending.append('缺少实际内容复核者与时间')
    coverage = review.get('coverage', {})
    for shot in shots:
        sid = shot['id']
        for element in ELEMENTS:
            row = coverage.get(sid, {}).get(element, {})
            if row.get('status') != 'pass':
                pending.append(f'{sid}/{element}尚未复核通过')
                continue
            a, b = row.get('source_quote', ''), row.get('prompt_quote', '')
            pool = text if row.get('scope') == 'shared' else blocks.get(sid, '')
            if not a.strip() or a not in source_text or not b.strip() or b not in pool:
                errors.append(f'{sid}/{element}的分镜或提示词证据无法定位')
    for name in JUDGMENTS:
        item = review.get('judgments', {}).get(name, {})
        if item.get('status') != 'pass' or not item.get('evidence', '').strip():
            pending.append(f'内容判断尚未通过：{name}')
    content_pending = list(pending)
    runtime = task.get('runtime', {})
    if runtime.get('verified') is not True or not runtime.get('evidence') or not runtime.get('checked_at'):
        pending.append('实际入口、模式与参数条件尚未核实')
    report = {'task_id':task.get('task_id'), 'fingerprint':digest,
              'format_result':'fail' if errors else 'pass',
              'content_review':('invalid_evidence' if len(errors) > before_review_errors else 'recorded_pass' if not content_pending else 'pending'),
              'errors':errors, 'pending':pending,
              'formal_prompt_ready':not errors and not pending,
              'generation_ready':not errors and not pending and task.get('authorization', {}).get('generate') is True and bool(task.get('authorization', {}).get('scope')),
              'media_acceptance':'not_evaluated',
              'limits':'内容判断取自实际复核记录；程序检查证据与版本绑定，不自动证明语义或画面正确。'}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    try:
        report = validate(args.task)
    except (KeyError, ValueError, TypeError, OSError, IndexError, AttributeError, OverflowError) as exc:
        report = {'format_result':'fail', 'errors':[str(exc)], 'formal_prompt_ready':False,
                  'generation_ready':False, 'media_acceptance':'not_evaluated'}
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(output + '\n', encoding='utf-8')
    print(output)
    return 0 if report['formal_prompt_ready'] else 1 if report.get('errors') else 2


if __name__ == '__main__':
    sys.exit(main())
