#!/usr/bin/env python3
"""Check an image task and its recorded prerequisites; never generates images."""
import argparse
import hashlib
import json
from pathlib import Path


KINDS = {'character_face', 'character_outfit', 'character_views', 'scene_overhead', 'scene_view', 'prop', 'storyboard', 'other'}
RATIOS = {'character_face': '3:4', 'character_outfit': '9:16', 'character_views': '16:9'}
DEPENDENCIES = {'character_outfit': ('identity',), 'character_views': ('identity', 'outfit'), 'scene_view': ('scene_base',)}
PLACEHOLDERS = {'', '待填', '待填写', '待补充', 'TODO', 'TBD', '无'}


def present(value):
    return isinstance(value, str) and value.strip() not in PLACEHOLDERS


def read_task(path):
    path = Path(path).expanduser().resolve()
    task = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(task, dict):
        raise ValueError('图片任务必须为JSON对象')
    return path, task


def actual_input_paths(path, task):
    values = [task.get('prompt_file')]
    model = task.get('model', {})
    source = model.get('spec_source', {}) if isinstance(model, dict) else {}
    if isinstance(source, dict):
        # Declared schema files are inputs even if their task record is invalid.
        # Bind their actual bytes and protect them from report output collisions.
        values.append(source.get('path'))
    references = task.get('references', [])
    if isinstance(references, list):
        values.extend(r.get('path') for r in references if isinstance(r, dict))
    paths = {}
    for value in values:
        if not present(value):
            continue
        p = Path(value).expanduser()
        paths[value] = p if p.is_absolute() else path.parent / p
    return paths


class ReportCollisionError(ValueError):
    pass


def validate_report_path(report, path, task):
    report = report.expanduser().resolve()
    protected = [path, Path(__file__), *actual_input_paths(path, task).values()]
    for source in protected:
        if report == source.expanduser().resolve():
            raise ReportCollisionError('报告不能覆盖任务、检查脚本或实际输入文件')
        try:
            collision = report.samefile(source)
        except FileNotFoundError:
            collision = False
        if collision:
            raise ReportCollisionError('报告不能覆盖任务、检查脚本或实际输入文件（硬链接）')


def actual_inputs(path, task):
    files = {}
    for value, p in actual_input_paths(path, task).items():
        try:
            files[value] = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            files[value] = None
    payload = {k: v for k, v in task.items() if k not in ('review', 'authorization')}
    payload['actual_files'] = files
    payload['checker_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return files, digest


def fingerprint(path):
    path, task = read_task(path)
    return actual_inputs(path, task)[1]


def check_model_spec(path, model, files, errors):
    if not isinstance(model, dict) or any(not present(model.get(k)) for k in ('name', 'mode', 'entry')):
        errors.append('缺少实际模型或工具名称、模式与入口')
        return
    source = model.get('spec_source', {})
    if not isinstance(source, dict):
        errors.append('模型或工具规范依据格式无效')
        return
    source_kind = source.get('kind', 'official_document')
    if source_kind == 'official_document':
        if any(not present(model.get(k)) for k in ('version', 'official_url', 'official_evidence')):
            errors.append('缺少实际模型版本或官方模板依据')
        elif not model['official_url'].startswith('https://'):
            errors.append('官方模板须记录可定位的HTTPS来源')
        return
    if source_kind != 'managed_tool_schema':
        errors.append('未支持的模型或工具规范依据类型')
        return
    if (not present(source.get('tool_name')) or source.get('tool_name') != model['name']
            or not present(source.get('evidence'))):
        errors.append('受管工具须记录匹配的实际工具名及接口核对依据')
    schema_path = source.get('path')
    if not present(schema_path) or files.get(schema_path) is None or files.get(schema_path) != source.get('sha256'):
        errors.append('受管工具schema不可读取或文件摘要不匹配')
    else:
        target = Path(schema_path).expanduser()
        target = target if target.is_absolute() else path.parent / target
        try:
            schema = json.loads(target.read_text(encoding='utf-8'))
        except (OSError, ValueError, UnicodeError):
            schema = None
        if (not isinstance(schema, dict) or schema.get('tool') != source.get('tool_name')
                or not present(schema.get('provenance')) or not isinstance(schema.get('input'), dict)
                or not schema['input']):
            errors.append('受管工具schema记录须包含匹配工具名、实际来源与非空输入声明')
    if not present(model.get('version')):
        if model.get('version_status') != 'not_exposed' or not present(model.get('version_unavailable_reason')):
            errors.append('未公开底层版本时须明确not_exposed及具体原因，不得猜填版本')
    elif model.get('version_status') == 'not_exposed':
        errors.append('版本未公开状态与已填写版本冲突')


def check(path):
    path, task = read_task(path)
    files, digest = actual_inputs(path, task)
    errors, pending = [], []
    kind, asset_id = task.get('kind'), task.get('asset_id')
    if kind not in KINDS or not present(asset_id):
        errors.append('缺少有效的图片任务类型或资产标识')
    requested_ratio = task.get('requested_aspect_ratio', task.get('aspect_ratio'))
    if not present(requested_ratio):
        errors.append('缺少请求目标画幅')
    if (present(task.get('aspect_ratio')) and present(task.get('requested_aspect_ratio'))
            and task['aspect_ratio'] != task['requested_aspect_ratio']):
        errors.append('兼容画幅字段与requested_aspect_ratio请求目标不一致')
    model = task.get('model', {})
    check_model_spec(path, model, files, errors)
    prompt = task.get('prompt_file')
    if not present(prompt) or files.get(prompt) is None:
        errors.append('缺少可读取的本次完整提示词')
    else:
        pp = Path(prompt).expanduser()
        pp = pp if pp.is_absolute() else path.parent / pp
        if not pp.read_text(encoding='utf-8').strip():
            errors.append('本次完整提示词为空')
    refs = task.get('references', [])
    if not isinstance(refs, list):
        errors.append('参考素材记录格式无效')
        refs = []
    valid_roles = set()
    for ref in refs:
        if not isinstance(ref, dict) or not isinstance(ref.get('roles'), list) or not ref['roles'] or any(not present(x) for x in ref['roles']):
            errors.append('参考素材缺少职责')
            continue
        selected = ref.get('selected') is True and present(ref.get('selection_evidence'))
        if not selected:
            pending.append('参考素材缺少本次指定或选用依据')
        if present(ref.get('path')):
            available = files.get(ref['path']) is not None and files.get(ref['path']) == ref.get('sha256')
        else:
            available = present(ref.get('media_id')) and present(ref.get('access_evidence'))
        if not available:
            errors.append('参考素材不可定位、未核实可用或文件摘要不匹配')
        if selected and available and ref.get('asset_id') == asset_id:
            valid_roles.update(ref['roles'])
    override = task.get('override', {})
    if not isinstance(override, dict):
        errors.append('用户指定的例外记录格式无效')
        override = {}
    rules = override.get('rules', [])
    if not isinstance(rules, list) or any(x not in ('ratio', 'character_sequence', 'scene_base') for x in rules):
        errors.append('用户指定的例外范围无效')
        rules = []
    if rules and not present(override.get('user_instruction')):
        errors.append('例外缺少用户本次明确指令')
        rules = []
    if kind in RATIOS and requested_ratio != RATIOS[kind] and 'ratio' not in rules:
        errors.append('人物图片请求画幅与当前步骤不符')
    for role in DEPENDENCIES.get(kind, ()):
        exception = 'scene_base' if role == 'scene_base' else 'character_sequence'
        if role not in valid_roles and exception not in rules:
            pending.append(f'本次步骤缺少当前资产已确认的前置参考：{role}')
    review = task.get('review', {})
    if not isinstance(review, dict):
        review = {}
    if review.get('fingerprint') != digest:
        pending.append('输入变更或缺少绑定当前版本的检查')
    for name in ('format', 'content'):
        item = review.get(name, {})
        if not isinstance(item, dict) or item.get('passed') is not True or not present(item.get('evidence')):
            pending.append(f'缺少实际{name}检查结果和依据')
    ready = not errors and not pending
    authorization = task.get('authorization', {})
    allowed = isinstance(authorization, dict) and authorization.get('generate') is True and present(authorization.get('scope'))
    return {'fingerprint': digest, 'actual_files': files, 'errors': errors, 'pending': pending,
            'formal_prompt_ready': ready, 'generation_ready': ready and allowed,
            'image_acceptance': 'not_evaluated',
            'limits': '检查记录与输入版本绑定；工具schema文件和名称匹配不证明其确为当前真实接口。用户确认真伪、官方依据或工具声明实际内容、请求画幅的可控性、输出尺寸与图像审美须另行核对。'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task', type=Path)
    parser.add_argument('--fingerprint', action='store_true')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    path, task = args.task.expanduser().resolve(), {}
    try:
        if args.report:
            validate_report_path(args.report, path, task)
        path, task = read_task(path)
        if args.report:
            validate_report_path(args.report, path, task)
        if args.fingerprint:
            print(actual_inputs(path, task)[1])
            return 0
        result = check(path)
    except ReportCollisionError as exc:
        parser.error(str(exc))
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        result = {'errors': [str(exc)], 'formal_prompt_ready': False, 'generation_ready': False, 'image_acceptance': 'not_evaluated'}
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        try:
            validate_report_path(args.report, path, task)
        except ReportCollisionError as exc:
            parser.error(str(exc))
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output + '\n', encoding='utf-8')
    print(output)
    return 0 if result['formal_prompt_ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
