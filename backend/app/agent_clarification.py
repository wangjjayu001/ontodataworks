"""Clarification answers are input constraints, never execution approval."""
import copy
import json
import re
from datetime import date

from .agent_planner import PlanningBlocked


def clarified_question(req):
    original = req['intent_frame'].get('original_question') or req['question']
    answers = [{key: a[key] for key in ('slot', 'question', 'answer') if key in a}
               for a in req.get('answers', []) if a.get('slot')]
    if not answers:
        return original
    return original + '\n用户逐项补充的澄清答案：\n' + json.dumps(answers, ensure_ascii=False)


def _confirmed_fields(req):
    confirmed = copy.deepcopy(req.get('confirmed_intent', {}))
    intent = req['intent_frame']
    # Existing runs have answers without slot metadata. Only recover explicit,
    # recognizable choices; never treat every model-extracted value as confirmed.
    for a in req.get('answers', []):
        answer = a.get('answer')
        if answer == 'SOURCE' and intent.get('timezone') == 'SOURCE':
            confirmed['timezone'] = 'SOURCE'
        if isinstance(answer, str) and intent.get('metric_id') == answer:
            confirmed.update(metric_id=answer, metric_terms=[answer])
        if isinstance(answer, str) and intent.get('time_event') == answer:
            confirmed.update(time_event=answer, requested_time_event=answer)
        if isinstance(answer, dict) and {'start', 'end_exclusive'}.issubset(answer) and all(
                intent.get(k) == answer[k] for k in ('start', 'end_exclusive')):
            confirmed.update({k: answer[k] for k in ('start', 'end_exclusive')})
    return confirmed


def confirmed_choices(req):
    return _confirmed_fields(req)


def analysis_from_answers(req):
    """Translate explicit period/measure choices without assuming a direction."""
    answers = [a for a in req.get('answers', []) if a.get('slot') in {'comparison_direction', 'growth_measure'}]
    if not answers:
        return None
    analysis = copy.deepcopy(req['intent_frame'].get('analysis') or {
        'kind': 'PERIOD_COMPARISON', 'base_period': {'start': None, 'end_exclusive': None},
        'comparison_period': {'start': None, 'end_exclusive': None}, 'measures': []})
    analysis['kind'] = 'PERIOD_COMPARISON'
    for item in answers:
        answer = str(item['answer'])
        if item['slot'] == 'comparison_direction':
            matched = re.search(r'(20\d{2})年?\s*为基期[，,、\s]*(?:比较)?\s*(20\d{2})年?', answer)
            if not matched:
                return None
            base, compared = map(int, matched.groups())
            analysis['base_period'] = {'start': f'{base}-01-01', 'end_exclusive': f'{base+1}-01-01'}
            analysis['comparison_period'] = {'start': f'{compared}-01-01', 'end_exclusive': f'{compared+1}-01-01'}
        elif answer == '两者都要':
            analysis['measures'] = ['GROWTH_RATE', 'ABSOLUTE_CHANGE']
        elif answer == '销售额增长率（百分比）':
            analysis['measures'] = ['GROWTH_RATE']
        elif answer == '销售额增长金额':
            analysis['measures'] = ['ABSOLUTE_CHANGE']
        else:
            return None
    return analysis


def apply_answer(req, clarification, answer):
    """Mutate a copied requirement; HTTP validation errors leave persisted state intact."""
    from .agent import fail
    slot = clarification['slot']
    if isinstance(answer, dict) and slot not in {'time', 'time_periods'}:
        answer = answer.get('value', answer.get('text'))
    if slot not in {'time', 'time_periods'}:
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 2000:
            fail('CLARIFICATION_VALUE_INVALID', '请提供1至2000字的明确说明', 422)
        answer = answer.strip()
        for option in clarification.get('options', []):
            if isinstance(option, dict) and answer == option.get('label'):
                answer = option.get('value', answer)
                break
    confirmed = _confirmed_fields(req)
    intent = req['intent_frame']
    if slot == 'timezone':
        if answer not in {'SOURCE', '源库时间', '使用源库时间（不转换）'}:
            fail('CLARIFICATION_VALUE_INVALID', '当前仅支持明确使用源库原始时间，不做时区转换', 422)
        answer = 'SOURCE'
        confirmed['timezone'] = 'SOURCE'
    elif slot == 'time':
        if isinstance(answer, str):
            try:
                answer = json.loads(answer)
            except ValueError:
                fail('TIME_ANSWER_INVALID', '请填写开始日期和结束日期（不含）', 422)
        if not isinstance(answer, dict) or set(answer) != {'start', 'end_exclusive'}:
            fail('TIME_ANSWER_INVALID', '请提供start和end_exclusive两个日期', 422)
        try:
            start, end = answer['start'], answer['end_exclusive']
            if not all(isinstance(v, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', v) for v in (start, end)) or date.fromisoformat(start) >= date.fromisoformat(end):
                raise ValueError()
        except (ValueError, TypeError):
            fail('TIME_ANSWER_INVALID', '日期无效，结束日期必须晚于开始日期', 422)
        confirmed.update(start=start, end_exclusive=end)
    elif slot == 'metric':
        # Exact catalog resolution still takes place after target confirmation.
        confirmed.update(metric_terms=[answer], metric_id=answer)
    elif slot == 'time_event':
        options = [o if isinstance(o, str) else o.get('value', o.get('label')) for o in clarification.get('options', [])]
        if options and answer not in options:
            fail('CLARIFICATION_VALUE_INVALID', '请选择当前澄清项提供的时间事件', 422)
        confirmed.update(time_event=answer, requested_time_event=answer)
    elif slot == 'time_periods':
        from .agent_time import validate_periods, is_comparison
        if not isinstance(answer, dict) or set(answer) != {'periods'}:
            fail('TIME_PERIODS_INVALID', '请确认完整时间段', 422)
        validate_periods(answer['periods'], is_comparison(intent))
    elif slot in {'comparison_direction', 'growth_measure'}:
        # These two slots have a closed, validated representation below. They
        # must remain in one requirement and do not need another model parse.
        pass
    else:
        # Model-proposed semantic slots are not arbitrary intent dictionary keys.
        # Reparse the original question plus the complete answer history instead.
        req['clarification_requires_reparse'] = True
    intent.update(confirmed)
    req['confirmed_intent'] = confirmed
    req.setdefault('answers', []).append({'clarification_id': clarification['id'],
        'slot': slot, 'question': clarification.get('question', ''), 'answer': copy.deepcopy(answer)})
    if len(req['answers']) > 32 or len(clarified_question(req)) > 6000:
        fail('CLARIFICATION_LIMIT', '本次澄清累计内容过长，请缩短补充说明', 422)
    intent['clarification_answers'] = [{key: a[key] for key in ('slot', 'question', 'answer')}
        for a in req['answers'] if a.get('slot')]
    analysis = analysis_from_answers(req)
    if analysis and not confirmed.get('time_periods_confirmed'):
        intent['analysis'] = analysis
        if all(analysis.get(key, {}).get('start') and analysis.get(key, {}).get('end_exclusive') for key in ('base_period', 'comparison_period')):
            from .agent_time import set_periods
            set_periods(intent, [{'label': label, **analysis[key]} for label, key in [('基期', 'base_period'), ('对比期', 'comparison_period')]], confirmed=True)
    req['clarifications'] = [c for c in req['clarifications'] if c['id'] != clarification['id']]
    req['error'] = None
    # A revised intent invalidates any earlier retrieved or approved artifact.
    for key in ('bundle', 'bundle_content_hash', 'metric_card', 'metric_card_hash',
                'target_confirmation', 'metric_confirmation', 'query_frame', 'query_frame_ref',
                'preview', 'sql_hash', 'gate', 'gate_result_ref', 'authorization',
                'metadata_candidate','effective_next_action'):
        req.pop(key, None)
    queued = not req['clarifications'] and req.get('clarification_requires_reparse', False)
    req['state'] = 'NEED_CLARIFICATION' if req['clarifications'] else 'DRAFT' if queued else 'TARGET_CONFIRMATION'
    if queued:
        req['clarification_parse_version'] = req['requirement_version'] + 1
    return queued


def merge_clarified_intent(previous, intent, clarifications):
    """Keep explicit choices and require the parser to retain previous constraints."""
    confirmed = _confirmed_fields(previous)
    intent.update(confirmed)
    intent['original_question'] = previous['intent_frame'].get('original_question') or previous['question']
    intent['clarification_answers'] = copy.deepcopy(previous['intent_frame'].get('clarification_answers', []))
    intent['depends_on_requirement_ids'] = copy.deepcopy(previous['intent_frame'].get('depends_on_requirement_ids', []))
    analysis = analysis_from_answers(previous)
    if analysis and not confirmed.get('time_periods_confirmed'):
        intent['analysis'] = analysis
        from .agent_time import set_periods
        if all(analysis.get(key, {}).get('start') and analysis.get(key, {}).get('end_exclusive') for key in ('base_period', 'comparison_period')):
            set_periods(intent, [{'label': label, **analysis[key]} for label, key in [('基期', 'base_period'), ('对比期', 'comparison_period')]], confirmed=True)
    old_filters = previous['intent_frame'].get('filters', [])
    if any(f not in intent.get('filters', []) for f in old_filters):
        raise PlanningBlocked('CLARIFICATION_CONSTRAINT_LOST', '补充说明后的解析没有完整保留原筛选条件，未继续生成查询')
    if set(previous['intent_frame'].get('dimension_ids', [])) - set(intent.get('dimension_ids', [])):
        raise PlanningBlocked('CLARIFICATION_CONSTRAINT_LOST', '补充说明后的解析没有完整保留原分组要求，未继续生成查询')
    intent['negative_terms'] = list(dict.fromkeys(previous['intent_frame'].get('negative_terms', []) + intent.get('negative_terms', [])))
    resolved = set(confirmed)
    if intent.get('time_periods_confirmed'):
        resolved.update({'time', 'time_periods', 'comparison_direction'})
    if 'start' in resolved and 'end_exclusive' in resolved:
        resolved.add('time')
    if 'metric_id' in resolved:
        resolved.add('metric')
    return intent, [c for c in clarifications if c['slot'] not in resolved]


def check_analysis_support(intent):
    """Never turn a requested comparison into a single aggregate for both years."""
    text = '\n'.join(str(intent.get(k) or '') for k in ('original_question', 'question'))
    comparison_slots = {'comparison_direction', 'growth_measure', 'comparison', 'growth_rate'}
    if intent.get('analysis', {}).get('kind') == 'PERIOD_COMPARISON' or re.search(r'同比|环比|增长|year.over.year|year.on.year|growth|\byoy\b|\bmom\b', text, re.I) or any(
            a.get('slot') in comparison_slots for a in intent.get('clarification_answers', [])):
        raise PlanningBlocked('COMPARISON_PLANNING_UNSUPPORTED',
            '已保留各时间段及对比方向；当前查询规划器尚未实现多期分别计算与增长分析，不能把多个时间段合并成一次总计。')
