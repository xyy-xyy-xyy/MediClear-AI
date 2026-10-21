/**
 * 异地就医报销计算模块 - JavaScript版
 */

// 异地就医政策
const RELOCATION_POLICY = {
    recordTypes: {
        long_term: {
            name: '长期驻外备案',
            description: '异地长期居住、异地工作的人员',
            validity: '长期有效',
            adjustDesc: '报销比例与参保地一致',
            rateAdjust: 0.0,
        },
        temp: {
            name: '临时外出备案',
            description: '出差、旅游、探亲等临时外出就医',
            validity: '3-6个月',
            adjustDesc: '报销比例略有降低（约降5%）',
            rateAdjust: -0.05,
        },
        transfer: {
            name: '转诊转院备案',
            description: '因病情需要转往外地医院治疗',
            validity: '当次有效',
            adjustDesc: '报销比例降低（约降8%）',
            rateAdjust: -0.08,
        },
        none: {
            name: '未备案',
            description: '未办理异地就医备案手续',
            validity: '-',
            adjustDesc: '报销比例大幅降低（约降20%），建议先备案',
            rateAdjust: -0.20,
        },
    },
    recordChannels: [
        '国家医保服务平台APP',
        '参保地医保经办机构窗口',
        '参保地政务服务网',
        '12393医保服务热线',
    ],
};


/**
 * 异地就医报销计算器
 */
class RelocationCalculator {
    constructor(insuredCity, visitCity, insuranceType = 'employee',
        visitType = 'inpatient', recordType = 'long_term') {

        this.insuredCity = insuredCity;
        this.visitCity = visitCity;
        this.insuranceType = insuranceType;
        this.visitType = visitType;
        this.recordType = recordType;

        // 获取参保地政策
        const cityPolicy = window.MediClear.CITY_POLICIES[insuredCity];
        if (!cityPolicy || !cityPolicy[insuranceType]) {
            throw new Error(`不支持的参保地或医保类型`);
        }
        this.insuredPolicy = cityPolicy[insuranceType][visitType];
        if (!this.insuredPolicy) {
            throw new Error(`参保地无此就医类型政策`);
        }

        // 就诊地信息
        this.visitCityInfo = window.MediClear.CITY_POLICIES[visitCity];
        if (!this.visitCityInfo) {
            throw new Error(`不支持的就诊地`);
        }
    }

    calculate(totalFee, hospitalLevel = '三级医院',
        classARatio = 0.55, classBRatio = 0.30, selfPayRatio = 0.15) {

        // 1. 报销基数
        const reimburseBase =
            totalFee * classARatio * window.MediClear.FEE_CATEGORIES.class_a.ratio +
            totalFee * classBRatio * window.MediClear.FEE_CATEGORIES.class_b.ratio +
            totalFee * selfPayRatio * window.MediClear.FEE_CATEGORIES.self_pay.ratio;

        // 2. 基础报销比例
        const baseRate = this._getBaseRate(hospitalLevel);

        // 3. 备案调整
        const rateAdjust = this._getRateAdjust();
        const finalRate = Math.max(0, baseRate + rateAdjust);

        // 4. 扣除起付线
        const deductible = this.insuredPolicy.deductible;
        const aboveDeductible = Math.max(0, reimburseBase - deductible);

        // 5. 基本医保报销
        const basicReimburse = Math.min(
            aboveDeductible * finalRate,
            this.insuredPolicy.max_reimburse
        );

        // 6. 个人自付
        const personalPay = totalFee - basicReimburse;

        // 7. 大病保险
        const seriousResult = this._calcSeriousIllness(personalPay);

        // 8. 最终结果
        const totalReimburse = basicReimburse + seriousResult.reimburse;
        const finalPersonalPay = totalFee - totalReimburse;

        return {
            summary: {
                totalFee: this._round(totalFee),
                reimburseBase: this._round(reimburseBase),
                deductible,
                aboveDeductible: this._round(aboveDeductible),
                baseRate: this._round(baseRate * 100, 1),
                rateAdjust: this._round(rateAdjust * 100, 1),
                finalRate: this._round(finalRate * 100, 1),
                basicReimburse: this._round(basicReimburse),
                seriousReimburse: this._round(seriousResult.reimburse),
                totalReimburse: this._round(totalReimburse),
                personalPay: this._round(finalPersonalPay),
                reimburseRatio: totalFee > 0 ? this._round(totalReimburse / totalFee * 100, 1) : 0,
            },
            recordInfo: this._getRecordInfo(),
            seriousIllness: seriousResult,
            insuredCityName: window.MediClear.CITY_POLICIES[this.insuredCity].name,
            visitCityName: this.visitCityInfo.name,
            tips: this._generateTips(),
        };
    }

    _getBaseRate(hospitalLevel) {
        const rates = this.insuredPolicy.reimburse_rates.filter(
            r => r.location === hospitalLevel
        );
        if (rates.length > 0) {
            return rates[0].rate;
        }
        return this.insuredPolicy.reimburse_rates[0].rate;
    }

    _getRateAdjust() {
        const rt = RELOCATION_POLICY.recordTypes[this.recordType];
        return rt ? rt.rateAdjust : -0.10;
    }

    _calcSeriousIllness(personalPay) {
        const si = window.MediClear.SERIOUS_ILLNESS;
        if (!si.enabled) {
            return { reimburse: 0, eligible: false, description: '未纳入大病保险' };
        }

        const deductible = si.deductible;
        const rate = si.reimburse_rate;
        const maxReimburse = si.max_reimburse;

        const aboveDeductible = Math.max(0, personalPay - deductible);
        const reimburse = Math.min(aboveDeductible * rate, maxReimburse);

        return {
            reimburse: this._round(reimburse),
            eligible: aboveDeductible > 0,
            deductible,
            aboveDeductible: this._round(aboveDeductible),
            rate,
            maxReimburse,
            description: si.description,
        };
    }

    _getRecordInfo() {
        return RELOCATION_POLICY.recordTypes[this.recordType] || {};
    }

    _generateTips() {
        const tips = [
            '异地就医前建议先办理备案，可提高报销比例',
            '备案渠道：国家医保服务平台APP、参保地医保经办机构、12393热线',
            '请妥善保管所有就医凭证：发票、费用清单、病历、出院小结等',
            '以上计算为预估，实际报销以医保部门核定为准',
        ];

        if (this.recordType === 'none') {
            tips.unshift('⚠️ 您未办理异地就医备案，报销比例会降低，建议尽快备案！');
        }

        return tips;
    }

    _round(num, decimals = 2) {
        return Math.round(num * Math.pow(10, decimals)) / Math.pow(10, decimals);
    }
}


/**
 * 生成异地就医材料清单
 */
function generateChecklist(insuredCity, visitCity, visitType = 'inpatient', reason = 'work') {
    const insuredName = (window.MediClear.CITY_POLICIES[insuredCity] || {}).name || insuredCity;
    const visitName = (window.MediClear.CITY_POLICIES[visitCity] || {}).name || visitCity;

    const visitTypeNames = { inpatient: '住院', outpatient: '门诊', emergency: '急诊' };
    const reasonNames = {
        work: '异地工作', retirement: '异地养老',
        travel: '旅游/探亲', transfer: '转诊转院'
    };

    // 必备材料
    const required = [
        { text: '身份证原件及复印件', desc: '患者本人身份证，代办还需代办人身份证' },
        { text: '社会保障卡（医保卡）', desc: '确保已激活并处于正常参保状态' },
        { text: '医疗费用发票原件', desc: '需加盖医院收费专用章，遗失不补' },
        { text: '费用明细清单', desc: '需加盖医院章，包含各项费用明细' },
    ];

    if (visitType === 'inpatient') {
        required.push(
            { text: '出院小结/出院记录', desc: '需加盖医院公章' },
            { text: '住院病历复印件', desc: '含入院记录、医嘱单、检查报告等' },
            { text: '诊断证明', desc: '需加盖医院诊断证明章' }
        );
    } else if (visitType === 'outpatient') {
        required.push(
            { text: '门诊病历', desc: '历次门诊病历记录' },
            { text: '门诊处方', desc: '药品处方底方' },
            { text: '检查检验报告单', desc: '各项检查、检验结果报告' }
        );
    } else {
        required.push(
            { text: '急诊诊断证明', desc: '需加盖急诊专用章' },
            { text: '急诊病历', desc: '急诊就诊记录' },
            { text: '急诊留观证明', desc: '如留观治疗需提供' }
        );
    }

    // 补充材料
    const optional = [
        { text: '异地就医备案凭证', desc: '已备案的需提供，可提高报销比例' },
        { text: '转诊转院证明', desc: '因病情需要转诊的需提供' },
        { text: '出差/探亲证明', desc: '因工外出或探亲期间就医的相关证明' },
        { text: '代办人身份证', desc: '委托他人代办的需提供' },
        { text: '本人银行卡', desc: '用于接收报销款项' },
        { text: '委托书', desc: '委托代办的建议准备书面委托书' },
    ];

    // 办理步骤
    const steps = [
        { title: '确认参保状态', desc: '确认医保处于正常参保缴费状态，无欠费' },
        { title: '办理异地就医备案（推荐）', desc: '通过国家医保APP或参保地医保部门办理备案，备案后报销比例更高、可直接结算' },
        { title: '保存好所有就医凭证', desc: '妥善保管发票、费用清单、病历、出院小结等所有原始凭证' },
        { title: '准备报销材料', desc: '按清单准备所有材料，建议复印一份留存' },
        { title: '提交报销申请', desc: '到参保地医保经办机构提交申请，或通过线上渠道提交' },
        { title: '等待审核到账', desc: '一般15-30个工作日审核完成，报销款项打入指定银行卡' },
    ];

    return {
        route: insuredName + ' → ' + visitName,
        visitType: visitTypeNames[visitType] || visitType,
        reason: reasonNames[reason] || reason,
        requiredItems: required,
        optionalItems: optional,
        steps: steps,
        recordChannels: RELOCATION_POLICY.recordChannels,
    };
}


// 导出
window.MediClearRelocation = {
    RelocationCalculator,
    generateChecklist,
    RELOCATION_POLICY,
};
