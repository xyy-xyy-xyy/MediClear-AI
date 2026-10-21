# -*- coding: utf-8 -*-
"""报销结果的解释生成。

同一个计算结果，面向不同的人要说不同的话：

============  ==========================================================
受众          说话方式
============  ==========================================================
普通人        通俗、口语化，先给结论再讲原因
长辈          句子短、字大、少术语，重点反复强调
专业人士      保留公式、分段明细、政策口径与计算过程
============  ==========================================================

除了"钱是怎么算的"，本模块还回答两个用户真正关心的问题：

* **为什么报销比我预期少？**（:func:`explain_why_less`）
* **怎么才能多报一点？**（:func:`suggest_improvements`）
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from ..constants import AUDIENCE_ELDERLY, AUDIENCE_GENERAL, AUDIENCE_PROFESSIONAL

__all__ = [
    'generate_explanation',
    'generate_explanation_sections',
    'explain_why_less',
    'suggest_improvements',
    'ExplanationBuilder',
]

#: 提示语统一口径——合规要求：不做确定性结论
DISCLAIMER_LINES = [
    '以上计算为预估结果，实际报销金额以医保部门核定为准',
    '不同医院等级、不同药品分类会影响报销比例',
    '年度累计报销有封顶线，超过部分需要自付',
    '如有疑问，建议咨询当地医保部门或医院医保窗口',
]


def _money(value: Any) -> str:
    """金额展示：去掉无意义的小数尾巴。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(number - round(number)) < 0.005:
        return str(int(round(number)))
    return ('%.2f' % number).rstrip('0').rstrip('.')


def _percent(value: Any) -> str:
    """比例展示（输入为百分数，如 63.4 → '63.4%'）。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return '%.1f%%' % number


# --------------------------------------------------------------------------
# 分析：为什么报销比预期少
# --------------------------------------------------------------------------
def explain_why_less(result: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """分析"为什么报销得比想象中少"，按影响从大到小排序。

    Args:
        result: :meth:`~mediclear.engine.ReimbursementCalculator.calculate` 的结果

    Returns:
        ``[{'key', 'reason', 'detail', 'amount', 'severity'}, ...]``
    """
    summary = result.get('summary', {})
    details = result.get('basic_details', {})
    stats = result.get('category_stats', {})
    serious = result.get('serious_illness', {}) or {}

    total_fee = float(summary.get('total_fee', 0) or 0)
    if total_fee <= 0:
        return []

    reasons: List[Dict[str, Any]] = []

    # 1) 自费项目完全不报
    self_pay_total = float((stats.get('self_pay') or {}).get('total', 0) or 0)
    if self_pay_total > 0:
        reasons.append({
            'key': 'self_pay_items',
            'reason': '有自费项目，这部分一分都不报',
            'detail': '本次自费项目共 %s 元，占全部费用的 %s%%，'
                      '完全由个人承担。' % (_money(self_pay_total),
                                            round(self_pay_total / total_fee * 100, 1)),
            'amount': round(self_pay_total, 2),
            'severity': 'high' if self_pay_total / total_fee > 0.2 else 'medium',
        })

    # 2) 乙类要先自付一部分
    class_b_total = float((stats.get('class_b') or {}).get('total', 0) or 0)
    class_b_ratio_base = float((stats.get('class_b') or {}).get('reimburse_ratio_base', 0.8) or 0)
    class_b_self = round(class_b_total * (1 - class_b_ratio_base), 2)
    if class_b_self > 0:
        reasons.append({
            'key': 'class_b_deduct',
            'reason': '乙类项目要先自付一部分才纳入报销',
            'detail': '乙类费用 %s 元，其中 %s%% 需要先自付（%s 元），'
                      '剩余部分才计入报销基数。'
                      % (_money(class_b_total), round((1 - class_b_ratio_base) * 100, 1),
                         _money(class_b_self)),
            'amount': class_b_self,
            'severity': 'medium',
        })

    # 3) 起付线扣掉了一部分
    deductible = float(summary.get('deductible', 0) or 0)
    reimburse_base = float(summary.get('reimburse_base', 0) or 0)
    if deductible > 0 and reimburse_base > 0:
        used = min(deductible, reimburse_base)
        reasons.append({
            'key': 'deductible',
            'reason': '起付线以内的钱要自己先掏',
            'detail': '政策规定起付线 %s 元，这部分不参与报销。'
                      '本次从报销基数中扣除了 %s 元。'
                      % (_money(deductible), _money(used)),
            'amount': round(used, 2),
            'severity': 'high' if used >= reimburse_base * 0.5 else 'medium',
        })

    # 4) 医院等级偏高
    level = details.get('hospital_level') or ''
    tiers = details.get('reimburse_rates') or []
    if level == '三级医院' and len(tiers) == 1 and tiers[0].get('rate', 0) < 0.7:
        reasons.append({
            'key': 'hospital_level',
            'reason': '在三级医院就医，报销比例相对较低',
            'detail': '三级医院对应的报销比例为 %s%%，'
                      '同样的费用在一级或二级医院通常能报更多。'
                      % round(float(tiers[0]['rate']) * 100, 1),
            'amount': 0.0,
            'severity': 'medium',
        })

    # 5) 费用落在低档分段
    breakdown = details.get('tier_breakdown') or {}
    segments = breakdown.get('segments') or []
    if len(segments) == 1 and len(tiers) > 1:
        reasons.append({
            'key': 'tier',
            'reason': '费用还停留在最低档报销比例',
            'detail': '本次可报销金额 %s 元，落在 %s%% 这一档内；'
                      '费用超过 %s 元后，超出部分可按 %s%% 报销。'
                      % (_money(summary.get('above_deductible', 0)),
                         round(float(segments[0]['rate']) * 100, 1),
                         _money(segments[0]['max']),
                         round(float(tiers[1]['rate']) * 100, 1) if len(tiers) > 1 else ''),
            'amount': 0.0,
            'severity': 'low',
        })

    # 6) 年度封顶线
    if details.get('capped_by_quota'):
        reasons.append({
            'key': 'quota',
            'reason': '今年报销已经接近或达到封顶线',
            'detail': '本年度剩余报销额度仅 %s 元，超出部分需要自付。'
                      % _money(details.get('remaining_quota', 0)),
            'amount': 0.0,
            'severity': 'high',
        })

    # 7) 大病保险还没到起付线
    if serious and not serious.get('eligible') and serious.get('deductible'):
        gap = round(float(serious['deductible']) - float(summary.get('personal_pay', 0) or 0), 2)
        if gap > 0:
            reasons.append({
                'key': 'serious_illness',
                'reason': '还没到大病保险的起付线',
                'detail': '个人自付需超过 %s 元才能启动大病保险二次报销，'
                          '目前还差 %s 元。' % (_money(serious['deductible']), _money(gap)),
                'amount': 0.0,
                'severity': 'low',
            })

    reasons.sort(key=lambda r: (-{'high': 2, 'medium': 1, 'low': 0}[r['severity']],
                                -r['amount']))
    for reason in reasons:
        reason['plain'] = _plain_reason(result, reason)
    return reasons


# --------------------------------------------------------------------------
# 大白话改写（给老年受众用）
# --------------------------------------------------------------------------
#: 每条"为什么报得少"的大白话说法。
#: 值是 ``(一句话理由, 详细说明)`` 的模板函数，入参为 ``(result, reason)``。
PLAIN_REASONS: Dict[str, Any] = {
    'self_pay_items': lambda r, i: (
        '有些花费不在医保范围内，这些钱一分都报不了',
        '这次有 %s 元不属于医保范围，得自己全掏。' % _money(i['amount'])),
    'class_b_deduct': lambda r, i: (
        '有些药要先自己出一小部分',
        '这次这类药一共 %s 元，得先自付 %s 元，剩下的才能报。'
        % (_money((r.get('category_stats', {}).get('class_b') or {}).get('total', 0)),
           _money(i['amount']))),
    'deductible': lambda r, i: (
        '有一笔钱要先自己掏，过了这个数才开始报',
        '医保规定先自己付 %s 元，这 %s 元不报，超过的部分才按比例报。'
        % (_money((r.get('summary') or {}).get('deductible', 0)), _money(i['amount']))),
    'hospital_level': lambda r, i: (
        '在大医院看病，报的比例会低一些',
        '大医院的报销比例低；去小一点的一级、二级医院，同样的病能报得更多。'),
    'tier': lambda r, i: (
        '花的钱还没到报销更多的档次',
        '这次花的钱还在报得比较少的那一档里，花得再多一些，超出的部分报得更多。'),
    'quota': lambda r, i: (
        '今年的报销额度快用完了',
        '今年只剩下 %s 元还能报，再多的就得自己出。'
        % _money((r.get('basic_details') or {}).get('remaining_quota', 0))),
    'serious_illness': lambda r, i: (
        '还没到大病保险的报销门槛',
        '自己掏的钱要超过 %s 元，大病保险才会再报一次。'
        % _money((r.get('serious_illness') or {}).get('deductible', 0))),
}

#: 每条"怎么才能多报"的大白话说法。
PLAIN_SUGGESTIONS: Dict[str, Any] = {
    'replace_self_pay': lambda r, i, total: (
        '问问医生有没有能报销的替代药',
        '这次自己全掏的有 %s 元。如果有进医保的同类药，这些钱就能报了。'
        % _money((r.get('category_stats', {}).get('self_pay') or {}).get('total', 0))),
    'lower_level_hospital': lambda r, i, total: (
        '小病、慢病复查去社区医院或二级医院',
        '一级、二级医院报的比例一般比三级医院高，能少花不少钱。'),
    'chronic_certification': lambda r, i, total: (
        '有高血压、糖尿病的，去办门诊慢特病认定',
        '办下来以后，门诊拿药报得更多，门槛也更低。'),
    'accumulate_to_deductible': lambda r, i, total: (
        '同一年的看病费用是可以累计的',
        '这次没花过门槛，所以没报。一年内接着看，超过门槛的部分就能报了。'),
    'serious_illness_threshold': lambda r, i, total: (
        '大病保险是一年一累计，注意自己掏了多少钱',
        '同一个年度里，自己掏的钱累计超过 %s 元，大病保险会再报一次。'
        % _money((r.get('serious_illness') or {}).get('deductible', 0))),
    'relocation_record': lambda r, i, total: (
        '去外地看病前，先办好异地就医备案',
        '办过备案的，报的比例高很多；没办过的会少报不少。'),
    'household_mutual_aid': lambda r, i, total: (
        '让家里人用医保卡里的余额帮你付',
        '配偶、父母、子女自己掏的钱，可以用你医保卡里的余额来付。'),
}


def _plain_reason(result: Mapping[str, Any], reason: Mapping[str, Any]) -> Dict[str, str]:
    """把一条专业口径的原因改写成大白话（没有对应模板时退回原文）。"""
    template = PLAIN_REASONS.get(reason.get('key', ''))
    if template is None:
        return {'reason': reason['reason'], 'detail': reason['detail']}
    plain_reason, plain_detail = template(result, reason)
    return {'reason': plain_reason, 'detail': plain_detail}


# --------------------------------------------------------------------------
# 建议：怎么才能多报一点
# --------------------------------------------------------------------------
def suggest_improvements(result: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """针对本次结果给出可操作的"提高报销"建议。

    Returns:
        ``[{'key', 'action', 'detail', 'estimated_gain', 'confidence'}, ...]``
    """
    summary = result.get('summary', {})
    details = result.get('basic_details', {})
    stats = result.get('category_stats', {})
    meta = result.get('meta', {}) or {}

    total_fee = float(summary.get('total_fee', 0) or 0)
    if total_fee <= 0:
        return []

    effective_rate = float(summary.get('basic_reimburse', 0) or 0) / total_fee
    suggestions: List[Dict[str, Any]] = []

    # 1) 自费项目 → 目录内替代
    self_pay_total = float((stats.get('self_pay') or {}).get('total', 0) or 0)
    if self_pay_total > 0:
        suggestions.append({
            'key': 'replace_self_pay',
            'action': '问医生有没有医保目录内的替代药品',
            'detail': '本次自费项目 %s 元。同类药品/耗材若在医保目录内'
                      '（甲类或乙类），这部分就能纳入报销。' % _money(self_pay_total),
            'estimated_gain': round(self_pay_total * effective_rate, 2),
            'confidence': 'estimate',
        })

    # 2) 就医机构等级
    level = details.get('hospital_level') or ''
    if level == '三级医院':
        suggestions.append({
            'key': 'lower_level_hospital',
            'action': '常见病、慢病复诊优先去基层医院',
            'detail': '一级、二级医院的报销比例通常比三级医院高 5~15 个百分点。'
                      '病情允许时在社区医院或二级医院就诊更划算。',
            'estimated_gain': round(total_fee * 0.08, 2),
            'confidence': 'estimate',
        })

    # 3) 门诊慢特病认定
    suggestions.append({
        'key': 'chronic_certification',
        'action': '高血压、糖尿病等慢病办理门诊慢特病认定',
        'detail': '认定备案后，门诊拿药可按慢特病专项比例报销，'
                  '起付线更低甚至为 0，且不占用住院封顶线。',
        'estimated_gain': None,
        'confidence': 'qualitative',
    })

    # 4) 起付线策略：把多次门诊合并
    deductible = float(summary.get('deductible', 0) or 0)
    if deductible > 0 and float(summary.get('above_deductible', 0) or 0) <= 0:
        suggestions.append({
            'key': 'accumulate_to_deductible',
            'action': '年内多次就医会累计计算起付线',
            'detail': '本次费用没超过起付线 %s 元，所以基本医保没报。'
                      '同一年度内继续就医时，超过起付线的部分就能开始报销。'
                      % _money(deductible),
            'estimated_gain': None,
            'confidence': 'qualitative',
        })

    # 5) 大病保险
    serious = result.get('serious_illness', {}) or {}
    if serious and not serious.get('eligible') and serious.get('deductible'):
        gap = round(float(serious['deductible']) - float(summary.get('personal_pay', 0) or 0), 2)
        if gap > 0:
            suggestions.append({
                'key': 'serious_illness_threshold',
                'action': '大病保险按年度累计计算，留意自付累计进度',
                'detail': '同一年度内个人自付累计超过 %s 元后，'
                          '超出部分可由大病保险再报销一次，目前还差约 %s 元。'
                          % (_money(serious['deductible']), _money(gap)),
                'estimated_gain': None,
                'confidence': 'qualitative',
            })

    # 6) 异地就医备案
    if meta.get('relocation') or summary.get('record_type') in ('none', 'temp', 'transfer'):
        suggestions.append({
            'key': 'relocation_record',
            'action': '异地就医前先办备案',
            'detail': '已备案的异地就医报销比例可提高 5~20 个百分点，'
                      '未备案通常会大幅降低比例。',
            'estimated_gain': round(total_fee * 0.1, 2),
            'confidence': 'estimate',
        })

    # 7) 家庭共济
    suggestions.append({
        'key': 'household_mutual_aid',
        'action': '用职工医保个人账户余额给家人共济',
        'detail': '配偶、父母、子女在定点机构的自付费用，'
                  '可以用你的个人账户余额支付，减少现金支出。',
        'estimated_gain': None,
        'confidence': 'qualitative',
    })

    total_fee = float(summary.get('total_fee', 0) or 0)
    for suggestion in suggestions:
        suggestion['plain'] = _plain_suggestion(result, suggestion, total_fee)
    return suggestions


def _plain_suggestion(result: Mapping[str, Any], suggestion: Mapping[str, Any],
                      total_fee: float = 0.0) -> Dict[str, str]:
    """把一条建议改写成大白话（没有对应模板时退回原文）。"""
    template = PLAIN_SUGGESTIONS.get(suggestion.get('key', ''))
    if template is None:
        return {'action': suggestion['action'], 'detail': suggestion['detail']}
    action, detail = template(result, suggestion, total_fee)
    return {'action': action, 'detail': detail}


# --------------------------------------------------------------------------
# 解释构建器
# --------------------------------------------------------------------------
class ExplanationBuilder:
    """把计算结果组织成面向特定受众的解释文本。

    Args:
        result: 计算结果
        audience: ``'general'`` / ``'elderly'`` / ``'professional'``
    """

    def __init__(self, result: Mapping[str, Any], audience: str = AUDIENCE_GENERAL):
        if audience not in (AUDIENCE_GENERAL, AUDIENCE_ELDERLY, AUDIENCE_PROFESSIONAL):
            audience = AUDIENCE_GENERAL
        self.result = result
        self.audience = audience
        self.summary = result.get('summary', {}) or {}
        self.details = result.get('basic_details', {}) or {}
        self.stats = result.get('category_stats', {}) or {}
        self.serious = result.get('serious_illness', {}) or {}
        self.meta = result.get('meta', {}) or {}
        self.why_less = explain_why_less(result)
        self.improvements = suggest_improvements(result)

    # -- 各段落 ----------------------------------------------------------
    def headline(self) -> str:
        """一句话结论。"""
        total = _money(self.summary.get('total_fee', 0))
        reimburse = _money(self.summary.get('total_reimburse', 0))
        personal = _money(self.summary.get('personal_pay', 0))
        ratio = self.summary.get('reimburse_ratio', 0)

        if self.audience == AUDIENCE_ELDERLY:
            return ('【总费用】%s 元\n【医保报销】%s 元\n【自己掏钱】%s 元\n'
                    '【报销比例】约 %.1f%%' % (total, reimburse, personal, ratio))
        if self.audience == AUDIENCE_PROFESSIONAL:
            return ('本次医疗总费用 %s 元，统筹报销 %s 元（占 %.1f%%），'
                    '个人负担 %s 元。政策版本 %s（%s）。'
                    % (total, reimburse, ratio, personal,
                       self.meta.get('policy_version', '-'),
                       self.meta.get('policy_year', '-')))
        return ('📊 本次医疗总费用 %s 元，预计能报销 %s 元，'
                '自己需要花 %s 元，报销比例约 %.1f%%。' % (total, reimburse, personal, ratio))

    def how_it_works(self) -> List[str]:
        """计算过程。"""
        s, d, stats = self.summary, self.details, self.stats
        lines: List[str] = []

        if self.audience == AUDIENCE_PROFESSIONAL:
            lines.append('计算口径：报销基数 = Σ(项目金额 × 该类纳入比例)')
            for step in (self.result.get('formula', {}) or {}).get('steps', []):
                lines.append('  %s = %s' % (step['title'], step['expression']))
            lines.append('甲类 / 乙类 / 自费纳入比例：%s%% / %s%% / %s%%' % (
                round(float((stats.get('class_a') or {}).get('reimburse_ratio_base', 1)) * 100, 1),
                round(float((stats.get('class_b') or {}).get('reimburse_ratio_base', 0.8)) * 100, 1),
                round(float((stats.get('self_pay') or {}).get('reimburse_ratio_base', 0)) * 100, 1),
            ))
            lines.append('起付线 %s 元，封顶线 %s 元，年度已用 %s 元' % (
                _money(s.get('deductible', 0)), _money(d.get('max_reimburse', 0)),
                _money(d.get('year_accumulated', 0))))
            return lines

        if self.audience == AUDIENCE_ELDERLY:
            lines.append('① 不是所有花费都能报。')
            lines.append('   甲类药 %s 元，能报。' % _money((stats.get('class_a') or {}).get('total', 0)))
            lines.append('   乙类药 %s 元，先自己出一部分，剩下的能报。'
                         % _money((stats.get('class_b') or {}).get('total', 0)))
            lines.append('   自费药 %s 元，一分都不报。'
                         % _money((stats.get('self_pay') or {}).get('total', 0)))
            lines.append('② 能报的钱是 %s 元。' % _money(s.get('reimburse_base', 0)))
            lines.append('③ 要先扣掉 %s 元（这叫起付线），剩下的 %s 元才按比例报。'
                         % (_money(s.get('deductible', 0)), _money(s.get('above_deductible', 0))))
            lines.append('④ 报销比例按医院等级定，您在%s，比例大约 %s%%。'
                         % (d.get('hospital_level', '医院'), self._top_rate_percent()))
            lines.append('⑤ 医保一共给您报了 %s 元。' % _money(s.get('basic_reimburse', 0)))
            if self.serious.get('eligible'):
                lines.append('⑥ 自付太多，大病保险又帮您报了 %s 元。'
                             % _money(s.get('serious_reimburse', 0)))
            return lines

        # 普通人
        lines.append('💡 报销是怎么算出来的：')
        lines.append('1️⃣ 不是所有费用都能报销。甲类药全额纳入报销，'
                     '乙类药要先自付一部分，自费药完全不报。')
        lines.append('   本次费用中，甲类 %s 元，乙类 %s 元，自费 %s 元。'
                     % (_money((stats.get('class_a') or {}).get('total', 0)),
                        _money((stats.get('class_b') or {}).get('total', 0)),
                        _money((stats.get('self_pay') or {}).get('total', 0))))
        lines.append('2️⃣ 纳入报销基数的金额是 %s 元。' % _money(s.get('reimburse_base', 0)))
        lines.append('3️⃣ 然后要扣除起付线 %s 元，超过起付线的部分 %s 元才能按比例报销。'
                     % (_money(s.get('deductible', 0)), _money(s.get('above_deductible', 0))))
        lines.append('4️⃣ 您是在%s看的病，对应的报销比例大约是 %s%% 左右。'
                     % (d.get('hospital_level', '医院'), self._top_rate_percent()))
        lines.append('5️⃣ 基本医保报销了 %s 元。' % _money(s.get('basic_reimburse', 0)))
        if self.serious.get('eligible'):
            lines.append('6️⃣ 个人自付超过了大病保险起付线 %s 元，'
                         '大病保险又额外报销了 %s 元。'
                         % (_money(self.serious.get('deductible', 0)),
                            _money(s.get('serious_reimburse', 0))))
        return lines

    def why_less_lines(self) -> List[str]:
        """"为什么比我预期少"的段落。"""
        if not self.why_less:
            return []
        if self.audience == AUDIENCE_ELDERLY:
            lines = ['❓ 为什么报得比想得少？']
            for item in self.why_less[:3]:
                plain = item.get('plain') or item
                lines.append('• %s' % plain['reason'])
                lines.append('  %s' % plain['detail'])
            return lines

        lines = ['🔍 为什么报销比预期少：']
        for index, item in enumerate(self.why_less, 1):
            marker = {'high': '❗', 'medium': '•', 'low': '·'}[item['severity']]
            lines.append('%s %d. %s' % (marker, index, item['reason']))
            lines.append('    %s' % item['detail'])
        return lines

    def improvement_lines(self) -> List[str]:
        """"怎么才能多报"的段落。"""
        if not self.improvements:
            return []
        if self.audience == AUDIENCE_ELDERLY:
            lines = ['✅ 想多报一点，可以这样做：']
            for item in self.improvements[:3]:
                plain = item.get('plain') or item
                lines.append('• %s' % plain['action'])
                lines.append('  %s' % plain['detail'])
            return lines

        lines = ['📈 如何提高报销比例：']
        for index, item in enumerate(self.improvements, 1):
            gain = item.get('estimated_gain')
            suffix = '（预计可多报约 %s 元，仅供参考）' % _money(gain) if gain else ''
            lines.append('%d. %s%s' % (index, item['action'], suffix))
            lines.append('    %s' % item['detail'])
        return lines

    def tips(self) -> List[str]:
        """提示与免责说明。"""
        if self.audience == AUDIENCE_ELDERLY:
            return ['请看清楚：具体能报多少，以医保局的正式答复为准。',
                    '拿不准就打 12393 医保服务热线问一问。']
        return list(DISCLAIMER_LINES)

    def _top_rate_percent(self) -> str:
        """取当前医院等级的起始报销比例（百分数）。"""
        tiers = self.details.get('reimburse_rates') or []
        if not tiers:
            return '—'
        rate = float(tiers[0].get('rate', 0)) * 100
        return ('%g' % round(rate, 1))

    # -- 输出 ------------------------------------------------------------
    def build(self) -> Dict[str, Any]:
        """返回结构化解释（前端可以分段渲染）。"""
        return {
            'audience': self.audience,
            'headline': self.headline(),
            'how_it_works': self.how_it_works(),
            'why_less': self.why_less,
            'improvements': self.improvements,
            'tips': self.tips(),
            'sections': [
                {'key': 'how_it_works', 'title': '报销是怎么算出来的', 'lines': self.how_it_works()},
                {'key': 'why_less', 'title': '为什么报销比预期少', 'lines': self.why_less_lines()},
                {'key': 'improvements', 'title': '如何提高报销比例', 'lines': self.improvement_lines()},
                {'key': 'tips', 'title': '温馨提示', 'lines': self.tips()},
            ],
        }

    def text(self) -> str:
        """生成完整解释文本。"""
        blocks: List[str] = [self.headline(), '']
        blocks.extend(self.how_it_works())
        blocks.append('')

        why = self.why_less_lines()
        if why:
            blocks.extend(why)
            blocks.append('')

        improve = self.improvement_lines()
        if improve:
            blocks.extend(improve)
            blocks.append('')

        title = '⚠️ 温馨提示：' if self.audience != AUDIENCE_ELDERLY else '⚠️ 请注意：'
        blocks.append(title)
        blocks.extend('• %s' % line for line in self.tips())
        return '\n'.join(blocks)


# --------------------------------------------------------------------------
# 对外函数
# --------------------------------------------------------------------------
def generate_explanation(result: Mapping[str, Any],
                         audience: str = AUDIENCE_GENERAL) -> str:
    """生成通俗的报销解释文本。

    Args:
        result: :meth:`~mediclear.engine.ReimbursementCalculator.calculate` 的结果
        audience: 受众风格

            * ``'general'``（默认）——普通人，通俗口语
            * ``'elderly'``——长辈，短句、大字提示、少术语
            * ``'professional'``——专业人士，保留公式与口径

    Returns:
        str: 解释文本，包含"总费用""报销""温馨提示"等关键信息，
        可直接展示给用户。

    Examples:
        >>> result = quick_calculate('beijing', 'employee', 10000)
        >>> text = generate_explanation(result, audience='elderly')
        >>> '总费用' in text
        True
    """
    return ExplanationBuilder(result, audience).text()


def generate_explanation_sections(result: Mapping[str, Any],
                                  audience: str = AUDIENCE_GENERAL) -> Dict[str, Any]:
    """生成结构化解释，供前端分块渲染。

    Returns:
        dict: 含 ``headline`` / ``sections`` / ``why_less`` / ``improvements`` 等键
    """
    return ExplanationBuilder(result, audience).build()
