"""校验模型提出的声明式验收用例；测试解释器始终位于受信任目录。"""
import json
import math
import re

UI_ATTRIBUTES = ('aria-label', 'aria-describedby', 'aria-pressed', 'aria-live', 'aria-atomic', 'tabindex', 'role', 'title')


def proposal(candidate, task):
    path = candidate / 'game/acceptance.json'
    if not path.exists():
        raise ValueError('Write game/acceptance.json with task and 1–4 observable checks')
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or data.get('task') != task:
        raise ValueError('acceptance.json task must match ' + task)
    checks = data.get('checks')
    if not isinstance(checks, list) or not 1 <= len(checks) <= 4:
        raise ValueError('acceptance.json requires 1–4 checks')
    for check in checks:
        validate_check(check)
    return checks


def validate_check(check):
    if not isinstance(check, dict) or check.get('kind') not in ('core', 'ui', 'visual'):
        raise ValueError('Check kind must be core, ui or visual')
    fields = {
        'core': {'kind', 'initial', 'steps', 'input', 'path', 'op', 'value'},
        'ui': {'kind', 'selector', 'phase', 'property', 'attribute', 'op', 'value'},
        'visual': {'kind', 'selector', 'phase'},
    }
    extra = set(check) - fields[check['kind']]
    if extra:
        raise ValueError('Unsupported '+check['kind']+' check fields: '+', '.join(sorted(extra))+'. UI/visual scenes do not simulate custom inputs or actions.')
    if check['kind'] in ('ui', 'visual'):
        if not re.fullmatch(r'#[a-zA-Z][a-zA-Z0-9_-]{0,48}', check.get('selector', '')):
            raise ValueError('UI check selector must be a single element ID')
        if check.get('phase', 'ready') not in ('ready', 'playing', 'paused'):
            raise ValueError('UI phase must be ready, playing or paused')
        if check['kind'] == 'visual':
            if check['selector'] != '#arena':
                raise ValueError('Visual evidence must inspect the actual arena')
            return
        if check.get('property') not in ('text', 'visible', 'attribute'):
            raise ValueError('UI property must be text, visible or attribute')
        if check.get('property') == 'attribute' and check.get('attribute') not in UI_ATTRIBUTES:
            raise ValueError('Unsupported UI attribute')
    else:
        initial = check.get('initial', {})
        steps = check.get('steps', 1)
        if not isinstance(initial, dict) or not set(initial) <= {'time', 'score', 'hp', 'spawn', 'shot', 'player', 'enemies', 'pickups', 'shield', 'level', 'bullets'}:
            raise ValueError('Unsupported core initial fields')
        if type(steps) is not int or not 1 <= steps <= 4000:
            raise ValueError('Core steps must be 1–4000')
        if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z0-9_]+){0,5}', check.get('path', '')):
            raise ValueError('Invalid core assertion path')
        if any(p in ('__proto__', 'prototype', 'constructor') for p in check['path'].split('.')):
            raise ValueError('Invalid core assertion path')
        inputs = check.get('input', {})
        if not isinstance(inputs, dict) or not set(inputs) <= {'x', 'y', 'shield', 'reducedMotion'}:
            raise ValueError('Unsupported core input')
    if check.get('op') not in ('eq', 'gt', 'gte', 'lt', 'lte', 'contains', 'exists'):
        raise ValueError('Unsupported assertion operator')
    if check['op'] != 'exists' and 'value' not in check:
        raise ValueError('Comparison requires an explicit value; never compare with undefined')
    if check['op'] in ('gt', 'gte', 'lt', 'lte') and type(check['value']) not in (int, float):
        raise ValueError('Numeric comparisons require a finite numeric value')
    if check['kind'] == 'ui' and check.get('property') == 'attribute' and check['op'] == 'eq' and check.get('value') is not None and not isinstance(check['value'], str):
        raise ValueError('DOM attribute equality requires a string value, not a boolean or number')
    if not isinstance(check.get('value'), (str, int, float, bool, type(None))):
        raise ValueError('Assertion value must be a scalar')
    if isinstance(check.get('value'), float) and not math.isfinite(check['value']):
        raise ValueError('Nonfinite assertion value')
    # 结构深度、数组和数值统一设界，防止声明式场景耗尽验证器。
    def bounded(value, depth=0):
        if depth > 6:
            raise ValueError('Check nesting budget exceeded')
        if isinstance(value, dict):
            if len(value) > 24 or any(k in ('__proto__', 'constructor', 'prototype') for k in value):
                raise ValueError('Invalid scenario object')
            for item in value.values(): bounded(item, depth+1)
        elif isinstance(value, list):
            if len(value) > 20: raise ValueError('Scenario array budget exceeded')
            for item in value: bounded(item, depth+1)
        elif isinstance(value, (int, float)):
            if not math.isfinite(value) or abs(value) > 1000000:
                raise ValueError('Scenario numeric budget exceeded')
        elif not isinstance(value, (str, bool, type(None))):
            raise ValueError('Unsupported scenario value')
    bounded(check)
