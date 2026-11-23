"""Independent, editable calendar periods; comparison periods are never unioned."""
import copy
from datetime import date
import re

from .agent_planner import PlanningBlocked


def validate_periods(periods, comparison=False):
    if not isinstance(periods, list) or not 1 <= len(periods) <= 8 or (comparison and len(periods) < 2):
        raise PlanningBlocked('TIME_PERIODS_INVALID', '请提供1至8个时间段；对比问题至少保留基期和一个对比期')
    result = []
    for index, item in enumerate(periods):
        try:
            start, end = item['start'], item['end_exclusive']
            if not all(isinstance(v, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', v) for v in (start, end)) or date.fromisoformat(start) >= date.fromisoformat(end):
                raise ValueError()
            label = item.get('label') or ('基期' if comparison and index == 0 else '对比期' if comparison else '统计时间')
            if not isinstance(label, str) or len(label) > 80:
                raise ValueError()
            result.append({'label': label, 'start': start, 'end_exclusive': end})
        except (TypeError, KeyError, ValueError):
            raise PlanningBlocked('TIME_PERIODS_INVALID', '时间段日期无效，结束日期必须晚于开始日期') from None
    return result


def is_comparison(intent):
    return intent.get('analysis', {}).get('kind') == 'PERIOD_COMPARISON' or bool(re.search(
        r'环比|同比|相较|相比|增长|比较|\bmom\b|\byoy\b', intent.get('original_question') or intent.get('question', ''), re.I))


def calendar_periods(text, comparison):
    year = None
    periods = []
    for match in re.finditer(r'(?:(20\d{2}|\d{2})年\s*)?(1[0-2]|0?[1-9])月', text):
        if match[1]:
            year = int(match[1]) + (2000 if len(match[1]) == 2 else 0)
        if year is None:
            continue
        month = int(match[2])
        periods.append({'label': f'{year}年{month}月', 'start': f'{year:04d}-{month:02d}-01',
                        'end_exclusive': f'{year + (month == 12):04d}-{month % 12 + 1:02d}-01'})
    if not periods and comparison:
        for match in re.finditer(r'(20\d{2}|\d{2})年', text):
            year = int(match[1]) + (2000 if len(match[1]) == 2 else 0)
            periods.append({'label': f'{year}年', 'start': f'{year}-01-01', 'end_exclusive': f'{year+1}-01-01'})
    unique = {(p['start'], p['end_exclusive']): p for p in periods}
    values = list(unique.values())
    if len(values) > 8 or (not comparison and len(values) > 1):
        return []
    return values


def infer_periods(intent):
    comparison = is_comparison(intent)
    if intent.get('time_periods'):
        return validate_periods(intent['time_periods'], comparison)
    analysis = intent.get('analysis') or {}
    if comparison:
        try:
            return validate_periods([{'label': label, **(analysis.get(key) or {})} for label, key in
                [('基期', 'base_period'), ('对比期', 'comparison_period')]], True)
        except PlanningBlocked:
            return calendar_periods(intent.get('original_question') or intent.get('question', ''), True)
    try:
        return validate_periods([{'start': intent.get('start'), 'end_exclusive': intent.get('end_exclusive')}])
    except PlanningBlocked:
        return calendar_periods(intent.get('original_question') or intent.get('question', ''), False)


def set_periods(intent, periods, *, confirmed=False):
    comparison = is_comparison(intent) or len(periods) > 1
    periods = validate_periods(periods, comparison)
    intent['time_periods'] = copy.deepcopy(periods)
    intent['time_periods_confirmed'] = confirmed
    if comparison:
        analysis = copy.deepcopy(intent.get('analysis') or {})
        analysis.update(kind='PERIOD_COMPARISON', base_period={k: periods[0][k] for k in ('start', 'end_exclusive')},
                        comparison_period={k: periods[1][k] for k in ('start', 'end_exclusive')})
        analysis.setdefault('measures', [])
        intent.update(analysis=analysis, start=None, end_exclusive=None)
    else:
        intent.update(start=periods[0]['start'], end_exclusive=periods[0]['end_exclusive'])


def prepare_periods(intent, clarifications):
    periods = infer_periods(intent)
    if not periods or (is_comparison(intent) and len(periods) < 2):
        return clarifications
    set_periods(intent, periods, confirmed=bool(intent.get('time_periods_confirmed')))
    remaining = [c for c in clarifications if c['slot'] not in {'time', 'time_periods', 'comparison_direction', 'analysis.direction', 'analysis.comparison_direction'}]
    if is_comparison(intent) and not intent.get('time_periods_confirmed'):
        remaining.insert(0, {'id': 'confirm-time-periods', 'slot': 'time_periods',
            'question': '已识别以下时间段，请确认或修改；第一个时间段为基期，可调整对比方向。', 'options': []})
    return remaining


def confirm_periods(req, periods):
    from .agent_clarification import apply_answer
    intent = req['intent_frame']
    set_periods(intent, periods, confirmed=True)
    confirmed = req.setdefault('confirmed_intent', {})
    confirmed.update({key: copy.deepcopy(intent[key]) for key in
        ('time_periods', 'time_periods_confirmed', 'analysis', 'start', 'end_exclusive')})
    req['clarifications'] = [c for c in req.get('clarifications', []) if c['slot'] not in {'time', 'time_periods', 'comparison_direction', 'analysis.direction', 'analysis.comparison_direction'}]
    # Reuse answer auditing and invalidation, without scheduling another model call.
    placeholder = {'id': 'confirmed-time-periods', 'slot': 'time_periods', 'question': '确认时间段和对比方向'}
    req['clarifications'].append(placeholder)
    return apply_answer(req, placeholder, {'periods': intent['time_periods']})
