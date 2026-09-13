"""Bind declared project inputs and inherit policy from the referenced Stage 1.

Digests identify the declared files, not their completeness, latest version, or
semantic correctness. Policy is local to this Stage 1 and its downstream chain.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from shotlist_common import parse_frontmatter, read_text
from v2_delivery_checks import check_digest, source_file


@dataclass(frozen=True)
class ProjectPolicy:
    lighting_mode: str = 'descriptive'
    sensitive_terms: tuple[str, ...] = ()
    dialogue_sensitive_authorizations: tuple[str, ...] = ()


def optional_input(owner: Path, front: dict[str, str], field: str,
                   errors: list[str]) -> Path | None:
    """A declared optional file always requires its matching current digest."""
    digest_field = field + '_sha256'
    if field not in front and digest_field not in front:
        return None
    if not front.get(field) or not front.get(digest_field):
        errors.append(f'{field} 与 {digest_field} 必须成对声明')
    path = source_file(owner, front.get(field, ''), field, errors)
    if path is not None:
        check_digest(path, front.get(digest_field, ''), field, errors)
    return path


def _terms(payload: dict, name: str, errors: list[str]) -> tuple[str, ...]:
    values = payload.get(name, [])
    if not isinstance(values, list) or any(not isinstance(item, str) or not item.strip() for item in values):
        errors.append(f'input_manifest policy.{name} 必须是非空词语的数组')
        return ()
    return tuple(dict.fromkeys(item.strip() for item in values))


def project_inputs(stage1: Path) -> tuple[list[str], ProjectPolicy]:
    """Validate declared inputs and return the policy owned by this Stage 1."""
    errors: list[str] = []
    policy = ProjectPolicy()
    try:
        parsed = parse_frontmatter(read_text(stage1))
        errors.extend(parsed.errors)
        front = parsed.values
        if front.get('artifact') not in {'horizontal-tv-shotlist', 'horizontal-tv-director-plan'}:
            errors.append('项目输入只能绑定 Stage 1 或其导演方案')
        optional_input(stage1, front, 'scene_facts', errors)
        manifest = optional_input(stage1, front, 'input_manifest', errors)
        if manifest is None:
            return errors, policy
        payload = json.loads(read_text(manifest))
        if not isinstance(payload, dict):
            return errors + ['input_manifest 必须是含 inputs 数组的 JSON 对象'], policy
        inputs = payload.get('inputs')
        if not isinstance(inputs, list):
            errors.append('input_manifest 必须包含 inputs 数组；仅有策略时可为空数组')
            inputs = []
        seen: set[Path] = set()
        for index, item in enumerate(inputs, 1):
            label = f'input_manifest 第{index}项'
            if not isinstance(item, dict):
                errors.append(f'{label} 必须是含 path 与 sha256 的对象')
                continue
            raw_path = item.get('path', '')
            if not isinstance(raw_path, str) or not raw_path.strip() or Path(raw_path).is_absolute():
                errors.append(f'{label} path 必须是相对清单文件的路径')
                continue
            if 'role' in item and (not isinstance(item['role'], str) or not item['role'].strip()):
                errors.append(f'{label} role 若声明必须为非空说明')
            target = source_file(manifest, raw_path, label, errors)
            if target is not None:
                if target in seen:
                    errors.append(f'{label} 重复引用同一输入文件')
                seen.add(target)
                check_digest(target, item.get('sha256', ''), label, errors)
        raw_policy = payload.get('policy', {})
        if not isinstance(raw_policy, dict):
            return errors + ['input_manifest policy 必须为对象'], policy
        known = {'lighting_mode', 'sensitive_terms', 'dialogue_sensitive_authorizations'}
        if set(raw_policy) - known:
            errors.append('input_manifest policy 含未知字段：' + '、'.join(sorted(set(raw_policy) - known)))
        lighting_mode = raw_policy.get('lighting_mode', 'descriptive')
        if lighting_mode not in ('descriptive', 'numeric'):
            errors.append('input_manifest policy.lighting_mode 只能为 descriptive 或 numeric')
            lighting_mode = 'descriptive'
        policy = ProjectPolicy(lighting_mode, _terms(raw_policy, 'sensitive_terms', errors),
                               _terms(raw_policy, 'dialogue_sensitive_authorizations', errors))
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        errors.append(f'项目输入无法校验：{exc}')
    return errors, policy


def inherited_policy(owner: Path) -> tuple[list[str], ProjectPolicy]:
    """Read only the actual source_stage1 path, never a downstream override."""
    errors: list[str] = []
    try:
        front = parse_frontmatter(read_text(owner)).values
        source = source_file(owner, front.get('source_stage1', ''), '项目策略的 Stage 1', errors)
        if source is not None:
            source_errors, policy = project_inputs(source)
            return errors + source_errors, policy
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        errors.append(f'项目策略来源无法校验：{exc}')
    return errors, ProjectPolicy()


def term_present(text: str, term: str) -> bool:
    if re.fullmatch(r'[A-Za-z0-9]+', term):
        return bool(re.search(rf'(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])', text, re.I))
    return term in text


def project_term_errors(non_dialogue: str, quotes: str, policy: ProjectPolicy,
                        authorized: set[str]) -> list[str]:
    """Check project additions without rewriting narration or protected speech."""
    errors = []
    allowed = {item.casefold() for item in authorized | set(policy.dialogue_sensitive_authorizations)}
    for term in policy.sensitive_terms:
        if term_present(non_dialogue, term):
            errors.append(f'非对白敏感词未处理：{term}')
        if term_present(quotes, term) and term.casefold() not in allowed:
            errors.append(f'对白敏感词缺少授权：{term}')
    return errors
