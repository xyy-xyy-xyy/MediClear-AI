# -*- coding: utf-8 -*-
"""异地就医报销计算模块。

异地就医的报销规则可以概括成一句话：

    报销比例按**参保地**政策走，再根据**备案类型**做加减。

所以本模块在 :mod:`mediclear` 的计算能力之上，多加了一层"比例调整"：

============================  ==========================
备案类型                      比例调整
============================  ==========================
长期驻外（``long_term``）      不调整
临时外出（``temp``）          −5%
转诊转院（``transfer``）      −8%
未备案（``none``）            −20%
============================  ==========================

返回结构保持与旧版一致（``summary`` 使用 camelCase 键名），
新增 ``formula`` / ``seriousIllness`` 增强字段。
"""

from mediclear.constants import (
    RECORD_LONG_TERM,
    RECORD_NONE,
    RECORD_TEMP,
    RECORD_TRANSFER,
)
from mediclear.data import get_repository
from mediclear.models import normalize_fee_items
from mediclear.policies.base import TierTable, resolve_tiers, round_money
from mediclear.policies.serious_illness import SeriousIllnessInsurance

from rules import (  # 兼容层：保持既有导入路径可用
    CITY_POLICIES,
    FEE_CATEGORIES,
    RELOCATION_POLICY,
    SERIOUS_ILLNESS_INSURANCE,
)

__all__ = [
    'RelocationCalculator',
    'generate_checklist',
    'RECORD_TYPES',
    'CITY_POLICIES',
    'FEE_CATEGORIES',
    'RELOCATION_POLICY',
    'SERIOUS_ILLNESS_INSURANCE',
]

#: 备案类型 → 报销比例调整（百分点，负数表示降低）
RECORD_TYPES = {
    RECORD_LONG_TERM: {
        'name': '长期驻外备案',
        'description': '异地长期居住、异地工作的人员',
        'validity': '长期有效',
        'adjust': 0.0,
        'adjust_desc': '报销比例与参保地一致',
    },
    RECORD_TEMP: {
        'name': '临时外出备案',
        'description': '出差、旅游、探亲等临时外出就医',
        'validity': '3-6个月',
        'adjust': -0.05,
        'adjust_desc': '报销比例略有降低（约降5%）',
    },
    RECORD_TRANSFER: {
        'name': '转诊转院备案',
        'description': '因病情需要转往外地医院治疗',
        'validity': '当次有效',
        'adjust': -0.08,
        'adjust_desc': '报销比例降低（约降8%）',
    },
    RECORD_NONE: {
        'name': '未备案',
        'description': '未办理异地就医备案手续',
        'validity': '-',
        'adjust': -0.20,
        'adjust_desc': '报销比例大幅降低（约降20%），建议先备案',
    },
}

#: 未识别的备案类型默认降幅
DEFAULT_ADJUST = -0.10


class RelocationCalculator:
    """异地就医报销计算器。

    Args:
        insured_city: 参保地城市代码
        visit_city: 就诊地城市代码
        insurance_type: 医保类型
        visit_type: 就医类型
        record_type: 备案类型，见 :data:`RECORD_TYPES`
        policy_year: 政策版本年份

    Raises:
        ValueError: 参保地 / 就诊地 / 医保类型 / 就医类型不支持
    """

    def __init__(self, insured_city, visit_city, insurance_type='employee',
                 visit_type='inpatient', record_type=RECORD_LONG_TERM,
                 policy_year=None):
        self.insured_city = insured_city
        self.visit_city = visit_city
        self.insurance_type = insurance_type
        self.visit_type = visit_type
        self.record_type = record_type

        self.bundle = get_repository().load(policy_year)
        self.policy_year = self.bundle.year

        if not self.bundle.has_city(insured_city):
            raise ValueError('不支持的参保地: %s' % insured_city)
        if insurance_type not in self.bundle.supported_insurance_types(insured_city):
            raise ValueError('参保地无此医保类型: %s' % insurance_type)

        self.insured_policy = self.bundle.get_visit_policy(
            insured_city, insurance_type, visit_type)
        if not self.insured_policy:
            raise ValueError('参保地无此就医类型政策: %s' % visit_type)

        self.visit_city_info = self.bundle.city(visit_city)
        self.serious_illness = SeriousIllnessInsurance(self.bundle)

    # ------------------------------------------------------------------
    def calculate(self, total_fee, hospital_level='三级医院',
                  class_a_ratio=0.55, class_b_ratio=0.30, self_pay_ratio=0.15,
                  fee_items=None):
        """计算异地就医报销。

        Args:
            total_fee: 总费用。当 ``fee_items`` 给出时本参数被忽略
            hospital_level: 就诊医院等级
            class_a_ratio / class_b_ratio / self_pay_ratio: 费用构成占比
            fee_items: 可选。直接给费用明细（``dict`` 或
                :class:`~mediclear.models.FeeItem` 列表），
                比按比例拆分更精确

        Returns:
            dict: ``summary`` 使用 camelCase 键名（与旧版一致），
            并附 ``comparison`` / ``recordInfo`` / ``seriousIllness`` / ``tips`` / ``formula``
        """
        if fee_items is not None:
            items = normalize_fee_items(fee_items)
            total_fee = round_money(sum(item.amount for item in items))
            reimburse_base = round_money(sum(
                item.amount * self._class_ratio(item.category) for item in items))
        else:
            total_fee = round_money(total_fee)
            reimburse_base = round_money(self._blend_base(
                total_fee, class_a_ratio, class_b_ratio, self_pay_ratio))

        # 参保地基准比例（按医院等级取分段表）+ 备案调整
        adjust = self._get_rate_adjustment()
        basic = self._basic_stage(hospital_level, reimburse_base, adjust)

        # 个人自付与大病保险
        personal_pay = round_money(total_fee - basic['reimburse'])
        serious = self.serious_illness.calculate(personal_pay)

        total_reimburse = round_money(basic['reimburse'] + serious['reimburse'])
        final_personal_pay = round_money(total_fee - total_reimburse)

        summary = {
            'totalFee': total_fee,
            'reimburseBase': reimburse_base,
            'deductible': round_money(basic['deductible']),
            'aboveDeductible': basic['above_deductible'],
            'baseRate': round(basic['base_rate'] * 100, 1),
            'rateAdjust': round(adjust * 100, 1),
            'finalRate': round(basic['final_rate'] * 100, 1),
            'basicReimburse': basic['reimburse'],
            'seriousReimburse': serious['reimburse'],
            'totalReimburse': total_reimburse,
            'personalPay': final_personal_pay,
            'reimburseRatio': round(total_reimburse / total_fee * 100, 1)
                              if total_fee > 0 else 0.0,
        }

        return {
            'summary': summary,
            'comparison': self._compare_with_local(
                total_fee, hospital_level, class_a_ratio, class_b_ratio, self_pay_ratio,
                fee_items=fee_items, actual_reimburse=basic['reimburse']),
            'recordInfo': self._get_record_info(),
            'seriousIllness': serious,
            'insuredCity': (self.bundle.cities.get(self.insured_city) or {}).get('name',
                                                                                 self.insured_city),
            'visitCity': self.visit_city_info.get('name', self.visit_city),
            'tips': self._generate_tips(),
            'hospitalLevel': basic['level'],
            'formula': self._build_formula(reimburse_base, basic),
            'policyYear': self.policy_year,
        }

    # ------------------------------------------------------------------
    def _basic_stage(self, hospital_level, reimburse_base, adjust):
        """跑一遍基本医保这一层，本地与异地共用同一套公式。

        异地与本地唯一的差别就是 ``adjust``（备案调整）。把它做成参数而不是
        复制一份逻辑，是为了保证"本地对比"与"本次报销"算出来的数是同一口径——
        否则两处浮点路径不同，差额会对不上。

        Returns:
            dict: ``level`` / ``base_detail`` / ``deductible`` / ``above_deductible``
            / ``base_rate`` / ``final_rate`` / ``reimburse``
        """
        level, tiers = resolve_tiers(self.insured_policy, hospital_level)
        table = TierTable(tiers)

        deductible = float(self.insured_policy.get('deductible', 0) or 0)
        above_deductible = round_money(max(0.0, reimburse_base - deductible))
        base_detail = table.breakdown(above_deductible)

        # 基准比例取"分段报销额 / 报销基数"，而不是 breakdown 里的 effective_rate——
        # 后者被四舍五入到 4 位小数，回乘之后会有分位偏差，导致本地/异地两条
        # 路径算出的差额对不上。
        base_rate = (base_detail['reimburse'] / above_deductible
                     if above_deductible > 0 else table.min_rate)

        # 备案调整统一作用在最终比例上：最终比例 = 参保地基准比例 + 备案调整
        final_rate = max(0.0, base_rate + adjust)
        amount = round_money(above_deductible * final_rate)

        max_reimburse = float(self.insured_policy.get('max_reimburse', 0) or 0)
        return {
            'level': level,
            'base_detail': base_detail,
            'deductible': deductible,
            'above_deductible': above_deductible,
            'base_rate': base_rate,
            'final_rate': final_rate,
            'reimburse': round_money(min(amount, max_reimburse)),
        }

    def _class_ratio(self, category):
        """某报销类别纳入报销基数的比例。"""
        return float((self.bundle.fee_categories.get(category) or {})
                     .get('reimburse_ratio_base', 0.0))

    @staticmethod
    def _blend_base(total_fee, class_a_ratio, class_b_ratio, self_pay_ratio):
        """按甲乙自费占比折算报销基数。"""
        return (total_fee * class_a_ratio * 1.0
                + total_fee * class_b_ratio * 0.8
                + total_fee * self_pay_ratio * 0.0)

    def _get_rate_adjustment(self):
        """按备案类型返回比例调整（小数，如 -0.05）。"""
        info = RECORD_TYPES.get(self.record_type)
        return info['adjust'] if info else DEFAULT_ADJUST

    def _compare_with_local(self, total_fee, hospital_level, class_a_ratio,
                            class_b_ratio, self_pay_ratio, fee_items=None,
                            actual_reimburse=0.0):
        """对比"同样花费在参保地本地就医"能报多少。

        Returns:
            ``{'localReimburse', 'localRate', 'difference'}``。
            ``difference`` 是**基本医保**一层的差额（本地 − 本次异地），
            不含大病保险——大病保险按各自的实际个人自付分别计算，
            拿总额相减会重复计入。
        """
        if fee_items is not None:
            items = normalize_fee_items(fee_items)
            reimburse_base = round_money(sum(
                item.amount * self._class_ratio(item.category) for item in items))
        else:
            reimburse_base = round_money(self._blend_base(
                total_fee, class_a_ratio, class_b_ratio, self_pay_ratio))

        # 本地就医 = 异地计算的 adjust=0 版本，复用同一条公式路径
        local = self._basic_stage(hospital_level, reimburse_base, 0.0)

        return {
            'localReimburse': local['reimburse'],
            'localRate': round(local['base_rate'] * 100, 1),
            'difference': round_money(local['reimburse'] - actual_reimburse),
        }

    def _get_record_info(self):
        """当前备案类型的说明。"""
        return dict(RECORD_TYPES.get(self.record_type, {}))

    def _generate_tips(self):
        """生成温馨提示。"""
        tips = [
            '异地就医前建议先办理备案，可提高报销比例',
            '备案渠道：国家医保服务平台APP、参保地医保经办机构、12393热线',
            '请妥善保管所有就医凭证：发票、费用清单、病历、出院小结等',
            '以上计算为预估，实际报销以医保部门核定为准',
        ]
        if self.record_type == RECORD_NONE:
            adjust = RECORD_TYPES[RECORD_NONE]['adjust']
            tips.insert(0, '⚠️ 您未办理异地就医备案，报销比例降低约 %d%%，建议尽快备案！'
                           % abs(round(adjust * 100)))
        return tips

    @staticmethod
    def _segments_expression(base_detail):
        """把分段表的各段拼成一条算式，如 ``30000 × 82% + 5000 × 88%``。"""
        parts = []
        for seg in base_detail.get('segments') or []:
            amount, rate = seg['amount'], round(seg['rate'] * 100, 1)
            parts.append('%s × %s%%' % (amount, rate) if rate < 100 else '%s' % amount)
        return ' + '.join(parts) if parts else '0'

    def _build_formula(self, reimburse_base, basic):
        """生成异地就医的公式说明。

        Args:
            reimburse_base: 纳入报销基数
            basic: :meth:`_basic_stage` 的返回值
        """
        base_detail = basic['base_detail']
        expression = self._segments_expression(base_detail)
        adjust = self._get_rate_adjustment()
        steps = [
            {'key': 'reimburseBase', 'title': '纳入报销基数',
             'expression': '%s 元' % reimburse_base, 'result': reimburse_base},
            {'key': 'aboveDeductible', 'title': '超过起付线部分',
             'expression': 'max(0, %s - %s) = %s 元'
                           % (reimburse_base, basic['deductible'],
                              basic['above_deductible']),
             'result': basic['above_deductible']},
            {'key': 'baseRate', 'title': '参保地基准比例',
             'expression': '%s = %s（加权平均 %s%%）'
                           % (expression, base_detail['reimburse'],
                              round(basic['base_rate'] * 100, 1)),
             'result': base_detail['reimburse']},
            {'key': 'adjust', 'title': '备案类型调整',
             'expression': '%s%% %s' % (round(adjust * 100, 1),
                                        self._get_record_info().get('adjust_desc', '')),
             'result': round(adjust * 100, 1)},
            {'key': 'basicReimburse', 'title': '实际报销',
             'expression': '调整后比例 %s%% → %s 元'
                           % (round(basic['final_rate'] * 100, 1), basic['reimburse']),
             'result': basic['reimburse']},
        ]
        text = '\n'.join([
            '【异地就医计算过程】',
            '1. 报销基数 %s 元（按参保地政策折算）' % reimburse_base,
            '2. 扣除参保地起付线 %s 元，可报销部分 %s 元'
            % (basic['deductible'], basic['above_deductible']),
            '3. 参保地基准报销比例 %s%%（%s）'
            % (round(basic['base_rate'] * 100, 1), expression),
            '4. 备案类型「%s」调整 %s%%' % (self._get_record_info().get('name', '-'),
                                            round(adjust * 100, 1)),
            '5. 最终报销比例 %s%%，报销金额 %s 元'
            % (round(basic['final_rate'] * 100, 1), basic['reimburse']),
        ])
        return {'steps': steps, 'text': text}


# --------------------------------------------------------------------------
# 材料清单
# --------------------------------------------------------------------------
def generate_checklist(insured_city, visit_city, visit_type='inpatient', reason='work'):
    """生成异地就医材料清单与办理步骤。

    Args:
        insured_city: 参保地城市代码
        visit_city: 就诊地城市代码
        visit_type: ``'inpatient'`` / ``'outpatient'`` / ``'emergency'``
        reason: 就医原因 ``'work'`` / ``'retirement'`` / ``'travel'`` / ``'transfer'``

    Returns:
        dict: 含 ``route`` / ``requiredItems`` / ``optionalItems`` / ``steps`` /
        ``recordChannels``
    """
    bundle = get_repository().current
    insured_name = (bundle.cities.get(insured_city) or {}).get('name', insured_city)
    visit_name = (bundle.cities.get(visit_city) or {}).get('name', visit_city)

    visit_type_names = {'inpatient': '住院', 'outpatient': '门诊', 'emergency': '急诊'}
    reason_names = {
        'work': '异地工作', 'retirement': '异地养老',
        'travel': '旅游/探亲', 'transfer': '转诊转院',
    }

    # 必备材料
    required = [
        {'text': '身份证原件及复印件', 'desc': '患者本人身份证，代办还需代办人身份证'},
        {'text': '社会保障卡（医保卡）', 'desc': '确保已激活并处于正常参保状态'},
        {'text': '医疗费用发票原件', 'desc': '需加盖医院收费专用章，遗失不补'},
        {'text': '费用明细清单', 'desc': '需加盖医院章，包含各项费用明细'},
    ]

    if visit_type == 'inpatient':
        required.extend([
            {'text': '出院小结/出院记录', 'desc': '需加盖医院公章'},
            {'text': '住院病历复印件', 'desc': '含入院记录、医嘱单、检查报告等'},
            {'text': '诊断证明', 'desc': '需加盖医院诊断证明章'},
        ])
    elif visit_type == 'outpatient':
        required.extend([
            {'text': '门诊病历', 'desc': '历次门诊病历记录'},
            {'text': '门诊处方', 'desc': '药品处方底方'},
            {'text': '检查检验报告单', 'desc': '各项检查、检验结果报告'},
        ])
    else:  # emergency
        required.extend([
            {'text': '急诊诊断证明', 'desc': '需加盖急诊专用章'},
            {'text': '急诊病历', 'desc': '急诊就诊记录'},
            {'text': '急诊留观证明', 'desc': '如留观治疗需提供'},
        ])

    # 补充材料
    optional = [
        {'text': '异地就医备案凭证', 'desc': '已备案的需提供，可提高报销比例'},
        {'text': '转诊转院证明', 'desc': '因病情需要转诊的需提供'},
        {'text': '出差/探亲证明', 'desc': '因工外出或探亲期间就医的相关证明'},
        {'text': '代办人身份证', 'desc': '委托他人代办的需提供'},
        {'text': '本人银行卡', 'desc': '用于接收报销款项'},
        {'text': '委托书', 'desc': '委托代办的建议准备书面委托书'},
    ]

    # 办理步骤
    steps = [
        {'title': '确认参保状态', 'desc': '确认医保处于正常参保缴费状态，无欠费'},
        {'title': '办理异地就医备案（推荐）',
         'desc': '通过国家医保APP或参保地医保部门办理备案，备案后报销比例更高、可直接结算'},
        {'title': '保存好所有就医凭证', 'desc': '妥善保管发票、费用清单、病历、出院小结等所有原始凭证'},
        {'title': '准备报销材料', 'desc': '按清单准备所有材料，建议复印一份留存'},
        {'title': '提交报销申请', 'desc': '到参保地医保经办机构提交申请，或通过线上渠道提交'},
        {'title': '等待审核到账', 'desc': '一般15-30个工作日审核完成，报销款项打入指定银行卡'},
    ]

    return {
        'route': '%s → %s' % (insured_name, visit_name),
        'visitType': visit_type_names.get(visit_type, visit_type),
        'reason': reason_names.get(reason, reason),
        'requiredItems': required,
        'optionalItems': optional,
        'steps': steps,
        'recordChannels': bundle.relocation.get('备案渠道', []),
        'recordTypes': [
            {'code': code, 'name': info['name'], 'desc': info['adjust_desc'],
             'validity': info['validity']}
            for code, info in RECORD_TYPES.items()
        ],
    }


if __name__ == '__main__':
    # 示例：武汉参保，上海就医，职工医保，住院，长期驻外备案
    calc = RelocationCalculator(
        insured_city='wuhan',
        visit_city='shanghai',
        insurance_type='employee',
        visit_type='inpatient',
        record_type=RECORD_LONG_TERM,
    )
    result = calc.calculate(total_fee=20000)

    print('=' * 60)
    print('异地就医报销计算结果')
    print('=' * 60)
    print('参保地：%s' % result['insuredCity'])
    print('就诊地：%s' % result['visitCity'])
    print('备案类型：%s' % result['recordInfo']['name'])
    print('总费用：%s 元' % result['summary']['totalFee'])
    print('报销比例：%s%% (参保地基准 %s%% %+.1f%%)'
          % (result['summary']['finalRate'], result['summary']['baseRate'],
             result['summary']['rateAdjust']))
    print('报销金额：%s 元' % result['summary']['totalReimburse'])
    print('个人自付：%s 元' % result['summary']['personalPay'])
    print()
    print(result['formula']['text'])
    print()
    print('温馨提示：')
    for tip in result['tips']:
        print('  • %s' % tip)
