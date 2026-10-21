/**
 * 医算清 MediClear-AI · 医保报销计算引擎 v2.0
 * 基于Python计算引擎 v2.0.0 翻译（14城市 + 全国通用参考）
 * 政策版本：2026.1（示例数据，仅供参考）
 */

(function () {
    "use strict";

    // ===== 费用分类 =====
    const FEE_CATEGORIES = {
        class_a:  { name: "甲类",  ratio: 1.0, desc: "全额纳入报销基数" },
        class_b:  { name: "乙类",  ratio: 0.8, desc: "先自付20%，其余纳入报销" },
        self_pay: { name: "自费",  ratio: 0.0, desc: "完全不纳入报销" }
    };

    // ===== 大病保险 =====
    const SERIOUS_ILLNESS = {
        deductible: 15000,
        rate: 0.60,
        max_reimburse: 300000
    };

    // ===== 14城市医保政策 + 全国通用参考 =====
    const CITY_POLICIES = {
        beijing: {
            name: "北京市",
            province: "北京市",
            tier: "一线",
            employee: {
                outpatient: {
                    deductible: 1800,
                    max_reimburse: 20000,
                    reimburse_rates: [
                        { min: 0, max: 20000, rate: 0.7, location: "一级医院" },
                        { min: 0, max: 20000, rate: 0.6, location: "二级医院" },
                        { min: 0, max: 20000, rate: 0.5, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
                inpatient: {
                    deductible: 1300,
                    max_reimburse: 300000,
                    reimburse_rates: [
                        { min: 0, max: 30000, rate: 0.9, location: "一级医院" },
                        { min: 0, max: 30000, rate: 0.85, location: "三级医院" },
                        { min: 0, max: 30000, rate: 0.87, location: "二级医院" },
                        { min: 30000, max: 40000, rate: 0.94, location: "一级医院" },
                        { min: 30000, max: 40000, rate: 0.9, location: "三级医院" },
                        { min: 30000, max: 40000, rate: 0.92, location: "二级医院" },
                        { min: 40000, max: 300000, rate: 0.98, location: "一级医院" },
                        { min: 40000, max: 300000, rate: 0.95, location: "三级医院" },
                        { min: 40000, max: 300000, rate: 0.97, location: "二级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
            },
            resident: {
                outpatient: {
                    deductible: 100,
                    max_reimburse: 3000,
                    reimburse_rates: [
                        { min: 0, max: 3000, rate: 0.5, location: "一级医院" },
                        { min: 0, max: 3000, rate: 0.4, location: "二级医院" },
                        { min: 0, max: 3000, rate: 0.35, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
                inpatient: {
                    deductible: 1300,
                    max_reimburse: 200000,
                    reimburse_rates: [
                        { min: 0, max: 200000, rate: 0.75, location: "一级医院" },
                        { min: 0, max: 200000, rate: 0.7, location: "二级医院" },
                        { min: 0, max: 200000, rate: 0.65, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
            },
        },
        shanghai: {
            name: "上海市",
            province: "上海市",
            tier: "一线",
            employee: {
                outpatient: {
                    deductible: 500,
                    max_reimburse: 25000,
                    reimburse_rates: [
                        { min: 0, max: 25000, rate: 0.65, location: "一级医院" },
                        { min: 0, max: 25000, rate: 0.6, location: "二级医院" },
                        { min: 0, max: 25000, rate: 0.55, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
                inpatient: {
                    deductible: 1500,
                    max_reimburse: 350000,
                    reimburse_rates: [
                        { min: 0, max: 350000, rate: 0.85, location: "一级医院" },
                        { min: 0, max: 350000, rate: 0.8, location: "二级医院" },
                        { min: 0, max: 350000, rate: 0.75, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
            },
            resident: {
                outpatient: {
                    deductible: 500,
                    max_reimburse: 5000,
                    reimburse_rates: [
                        { min: 0, max: 5000, rate: 0.5, location: "一级医院" },
                        { min: 0, max: 5000, rate: 0.4, location: "二级医院" },
                        { min: 0, max: 5000, rate: 0.3, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
                inpatient: {
                    deductible: 1200,
                    max_reimburse: 250000,
                    reimburse_rates: [
                        { min: 0, max: 250000, rate: 0.7, location: "一级医院" },
                        { min: 0, max: 250000, rate: 0.65, location: "二级医院" },
                        { min: 0, max: 250000, rate: 0.6, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
            },
        },
        guangzhou: {
            name: "广州市",
            province: "广东省",
            tier: "一线",
            employee: {
                outpatient: {
                    deductible: 1000,
                    max_reimburse: 20000,
                    reimburse_rates: [
                        { min: 0, max: 20000, rate: 0.75, location: "一级医院" },
                        { min: 0, max: 20000, rate: 0.65, location: "二级医院" },
                        { min: 0, max: 20000, rate: 0.55, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
                inpatient: {
                    deductible: 1600,
                    max_reimburse: 300000,
                    reimburse_rates: [
                        { min: 0, max: 300000, rate: 0.9, location: "一级医院" },
                        { min: 0, max: 300000, rate: 0.85, location: "二级医院" },
                        { min: 0, max: 300000, rate: 0.8, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
            },
            resident: {
                outpatient: {
                    deductible: 1000,
                    max_reimburse: 3000,
                    reimburse_rates: [
                        { min: 0, max: 3000, rate: 0.55, location: "一级医院" },
                        { min: 0, max: 3000, rate: 0.45, location: "二级医院" },
                        { min: 0, max: 3000, rate: 0.35, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
                inpatient: {
                    deductible: 1500,
                    max_reimburse: 200000,
                    reimburse_rates: [
                        { min: 0, max: 200000, rate: 0.75, location: "一级医院" },
                        { min: 0, max: 200000, rate: 0.7, location: "二级医院" },
                        { min: 0, max: 200000, rate: 0.65, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
            },
        },
        shenzhen: {
            name: "深圳市",
            province: "广东省",
            tier: "一线",
            employee: {
                outpatient: {
                    deductible: 0,
                    max_reimburse: 15000,
                    reimburse_rates: [
                        { min: 0, max: 15000, rate: 0.7, location: "一级医院" },
                        { min: 0, max: 15000, rate: 0.6, location: "二级医院" },
                        { min: 0, max: 15000, rate: 0.5, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
                inpatient: {
                    deductible: 300,
                    max_reimburse: 360000,
                    reimburse_rates: [
                        { min: 0, max: 360000, rate: 0.85, location: "一级医院" },
                        { min: 0, max: 360000, rate: 0.8, location: "二级医院" },
                        { min: 0, max: 360000, rate: 0.75, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
            },
            resident: {
                outpatient: {
                    deductible: 100,
                    max_reimburse: 2000,
                    reimburse_rates: [
                        { min: 0, max: 2000, rate: 0.5, location: "一级医院" },
                        { min: 0, max: 2000, rate: 0.4, location: "二级医院" },
                        { min: 0, max: 2000, rate: 0.3, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
                inpatient: {
                    deductible: 1000,
                    max_reimburse: 200000,
                    reimburse_rates: [
                        { min: 0, max: 200000, rate: 0.7, location: "一级医院" },
                        { min: 0, max: 200000, rate: 0.65, location: "二级医院" },
                        { min: 0, max: 200000, rate: 0.6, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
            },
        },
        hangzhou: {
            name: "杭州市",
            province: "浙江省",
            tier: "新一线",
            employee: {
                outpatient: {
                    deductible: 1000,
                    max_reimburse: 20000,
                    reimburse_rates: [
                        { min: 0, max: 20000, rate: 0.72, location: "一级医院" },
                        { min: 0, max: 20000, rate: 0.62, location: "二级医院" },
                        { min: 0, max: 20000, rate: 0.52, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
                inpatient: {
                    deductible: 1200,
                    max_reimburse: 280000,
                    reimburse_rates: [
                        { min: 0, max: 280000, rate: 0.88, location: "一级医院" },
                        { min: 0, max: 280000, rate: 0.82, location: "二级医院" },
                        { min: 0, max: 280000, rate: 0.78, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
            },
            resident: {
                outpatient: {
                    deductible: 300,
                    max_reimburse: 4000,
                    reimburse_rates: [
                        { min: 0, max: 4000, rate: 0.55, location: "一级医院" },
                        { min: 0, max: 4000, rate: 0.45, location: "二级医院" },
                        { min: 0, max: 4000, rate: 0.35, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
                inpatient: {
                    deductible: 1000,
                    max_reimburse: 180000,
                    reimburse_rates: [
                        { min: 0, max: 180000, rate: 0.72, location: "一级医院" },
                        { min: 0, max: 180000, rate: 0.68, location: "二级医院" },
                        { min: 0, max: 180000, rate: 0.62, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
            },
        },
        nanjing: {
            name: "南京市",
            province: "江苏省",
            tier: "新一线",
            employee: {
                outpatient: {
                    deductible: 1200,
                    max_reimburse: 22000,
                    reimburse_rates: [
                        { min: 0, max: 22000, rate: 0.7, location: "一级医院" },
                        { min: 0, max: 22000, rate: 0.6, location: "二级医院" },
                        { min: 0, max: 22000, rate: 0.5, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
                inpatient: {
                    deductible: 1000,
                    max_reimburse: 260000,
                    reimburse_rates: [
                        { min: 0, max: 260000, rate: 0.9, location: "一级医院" },
                        { min: 0, max: 260000, rate: 0.85, location: "二级医院" },
                        { min: 0, max: 260000, rate: 0.8, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
            },
            resident: {
                outpatient: {
                    deductible: 500,
                    max_reimburse: 3500,
                    reimburse_rates: [
                        { min: 0, max: 3500, rate: 0.5, location: "一级医院" },
                        { min: 0, max: 3500, rate: 0.4, location: "二级医院" },
                        { min: 0, max: 3500, rate: 0.3, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
                inpatient: {
                    deductible: 900,
                    max_reimburse: 190000,
                    reimburse_rates: [
                        { min: 0, max: 190000, rate: 0.75, location: "一级医院" },
                        { min: 0, max: 190000, rate: 0.68, location: "二级医院" },
                        { min: 0, max: 190000, rate: 0.62, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
            },
        },
        wuhan: {
            name: "武汉市",
            province: "湖北省",
            tier: "新一线",
            employee: {
                outpatient: {
                    deductible: 800,
                    max_reimburse: 25000,
                    reimburse_rates: [
                        { min: 0, max: 25000, rate: 0.7, location: "一级医院" },
                        { min: 0, max: 25000, rate: 0.6, location: "二级医院" },
                        { min: 0, max: 25000, rate: 0.5, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
                inpatient: {
                    deductible: 1200,
                    max_reimburse: 240000,
                    reimburse_rates: [
                        { min: 0, max: 30000, rate: 0.88, location: "一级医院" },
                        { min: 0, max: 30000, rate: 0.82, location: "三级医院" },
                        { min: 0, max: 30000, rate: 0.85, location: "二级医院" },
                        { min: 30000, max: 80000, rate: 0.92, location: "一级医院" },
                        { min: 30000, max: 80000, rate: 0.88, location: "三级医院" },
                        { min: 30000, max: 80000, rate: 0.9, location: "二级医院" },
                        { min: 80000, max: 240000, rate: 0.96, location: "一级医院" },
                        { min: 80000, max: 240000, rate: 0.92, location: "三级医院" },
                        { min: 80000, max: 240000, rate: 0.94, location: "二级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
            },
            resident: {
                outpatient: {
                    deductible: 400,
                    max_reimburse: 4000,
                    reimburse_rates: [
                        { min: 0, max: 4000, rate: 0.5, location: "一级医院" },
                        { min: 0, max: 4000, rate: 0.4, location: "二级医院" },
                        { min: 0, max: 4000, rate: 0.3, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
                inpatient: {
                    deductible: 1000,
                    max_reimburse: 180000,
                    reimburse_rates: [
                        { min: 0, max: 180000, rate: 0.7, location: "一级医院" },
                        { min: 0, max: 180000, rate: 0.65, location: "二级医院" },
                        { min: 0, max: 180000, rate: 0.6, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
            },
        },
        chengdu: {
            name: "成都市",
            province: "四川省",
            tier: "新一线",
            employee: {
                outpatient: {
                    deductible: 900,
                    max_reimburse: 20000,
                    reimburse_rates: [
                        { min: 0, max: 20000, rate: 0.65, location: "一级医院" },
                        { min: 0, max: 20000, rate: 0.55, location: "二级医院" },
                        { min: 0, max: 20000, rate: 0.5, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
                inpatient: {
                    deductible: 1000,
                    max_reimburse: 280000,
                    reimburse_rates: [
                        { min: 0, max: 280000, rate: 0.88, location: "一级医院" },
                        { min: 0, max: 280000, rate: 0.82, location: "二级医院" },
                        { min: 0, max: 280000, rate: 0.78, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
            },
            resident: {
                outpatient: {
                    deductible: 200,
                    max_reimburse: 5000,
                    reimburse_rates: [
                        { min: 0, max: 5000, rate: 0.6, location: "一级医院" },
                        { min: 0, max: 5000, rate: 0.5, location: "二级医院" },
                        { min: 0, max: 5000, rate: 0.4, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
                inpatient: {
                    deductible: 800,
                    max_reimburse: 200000,
                    reimburse_rates: [
                        { min: 0, max: 200000, rate: 0.75, location: "一级医院" },
                        { min: 0, max: 200000, rate: 0.7, location: "二级医院" },
                        { min: 0, max: 200000, rate: 0.65, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
            },
        },
        chongqing: {
            name: "重庆市",
            province: "重庆市",
            tier: "新一线",
            employee: {
                outpatient: {
                    deductible: 1500,
                    max_reimburse: 18000,
                    reimburse_rates: [
                        { min: 0, max: 18000, rate: 0.65, location: "一级医院" },
                        { min: 0, max: 18000, rate: 0.55, location: "二级医院" },
                        { min: 0, max: 18000, rate: 0.45, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
                inpatient: {
                    deductible: 1100,
                    max_reimburse: 270000,
                    reimburse_rates: [
                        { min: 0, max: 270000, rate: 0.85, location: "一级医院" },
                        { min: 0, max: 270000, rate: 0.8, location: "二级医院" },
                        { min: 0, max: 270000, rate: 0.75, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
            },
            resident: {
                outpatient: {
                    deductible: 300,
                    max_reimburse: 3000,
                    reimburse_rates: [
                        { min: 0, max: 3000, rate: 0.5, location: "一级医院" },
                        { min: 0, max: 3000, rate: 0.4, location: "二级医院" },
                        { min: 0, max: 3000, rate: 0.3, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
                inpatient: {
                    deductible: 800,
                    max_reimburse: 170000,
                    reimburse_rates: [
                        { min: 0, max: 170000, rate: 0.7, location: "一级医院" },
                        { min: 0, max: 170000, rate: 0.65, location: "二级医院" },
                        { min: 0, max: 170000, rate: 0.58, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
            },
        },
        xian: {
            name: "西安市",
            province: "陕西省",
            tier: "新一线",
            employee: {
                outpatient: {
                    deductible: 1200,
                    max_reimburse: 18000,
                    reimburse_rates: [
                        { min: 0, max: 18000, rate: 0.68, location: "一级医院" },
                        { min: 0, max: 18000, rate: 0.58, location: "二级医院" },
                        { min: 0, max: 18000, rate: 0.48, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
                inpatient: {
                    deductible: 1200,
                    max_reimburse: 250000,
                    reimburse_rates: [
                        { min: 0, max: 250000, rate: 0.88, location: "一级医院" },
                        { min: 0, max: 250000, rate: 0.82, location: "二级医院" },
                        { min: 0, max: 250000, rate: 0.76, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
            },
            resident: {
                outpatient: {
                    deductible: 300,
                    max_reimburse: 3000,
                    reimburse_rates: [
                        { min: 0, max: 3000, rate: 0.55, location: "一级医院" },
                        { min: 0, max: 3000, rate: 0.45, location: "二级医院" },
                        { min: 0, max: 3000, rate: 0.35, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
                inpatient: {
                    deductible: 900,
                    max_reimburse: 180000,
                    reimburse_rates: [
                        { min: 0, max: 180000, rate: 0.72, location: "一级医院" },
                        { min: 0, max: 180000, rate: 0.65, location: "二级医院" },
                        { min: 0, max: 180000, rate: 0.58, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
            },
        },
        shenyang: {
            name: "沈阳市",
            province: "辽宁省",
            tier: "二线",
            employee: {
                outpatient: {
                    deductible: 1000,
                    max_reimburse: 15000,
                    reimburse_rates: [
                        { min: 0, max: 15000, rate: 0.65, location: "一级医院" },
                        { min: 0, max: 15000, rate: 0.55, location: "二级医院" },
                        { min: 0, max: 15000, rate: 0.45, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
                inpatient: {
                    deductible: 1000,
                    max_reimburse: 220000,
                    reimburse_rates: [
                        { min: 0, max: 220000, rate: 0.88, location: "一级医院" },
                        { min: 0, max: 220000, rate: 0.82, location: "二级医院" },
                        { min: 0, max: 220000, rate: 0.76, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
            },
            resident: {
                outpatient: {
                    deductible: 200,
                    max_reimburse: 2500,
                    reimburse_rates: [
                        { min: 0, max: 2500, rate: 0.5, location: "一级医院" },
                        { min: 0, max: 2500, rate: 0.4, location: "二级医院" },
                        { min: 0, max: 2500, rate: 0.3, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
                inpatient: {
                    deductible: 800,
                    max_reimburse: 150000,
                    reimburse_rates: [
                        { min: 0, max: 150000, rate: 0.7, location: "一级医院" },
                        { min: 0, max: 150000, rate: 0.62, location: "二级医院" },
                        { min: 0, max: 150000, rate: 0.55, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
            },
        },
        jinan: {
            name: "济南市",
            province: "山东省",
            tier: "二线",
            employee: {
                outpatient: {
                    deductible: 1200,
                    max_reimburse: 18000,
                    reimburse_rates: [
                        { min: 0, max: 18000, rate: 0.7, location: "一级医院" },
                        { min: 0, max: 18000, rate: 0.6, location: "二级医院" },
                        { min: 0, max: 18000, rate: 0.5, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
                inpatient: {
                    deductible: 1000,
                    max_reimburse: 240000,
                    reimburse_rates: [
                        { min: 0, max: 240000, rate: 0.85, location: "一级医院" },
                        { min: 0, max: 240000, rate: 0.8, location: "二级医院" },
                        { min: 0, max: 240000, rate: 0.75, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
            },
            resident: {
                outpatient: {
                    deductible: 400,
                    max_reimburse: 3500,
                    reimburse_rates: [
                        { min: 0, max: 3500, rate: 0.55, location: "一级医院" },
                        { min: 0, max: 3500, rate: 0.45, location: "二级医院" },
                        { min: 0, max: 3500, rate: 0.35, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
                inpatient: {
                    deductible: 1000,
                    max_reimburse: 180000,
                    reimburse_rates: [
                        { min: 0, max: 180000, rate: 0.72, location: "一级医院" },
                        { min: 0, max: 180000, rate: 0.65, location: "二级医院" },
                        { min: 0, max: 180000, rate: 0.58, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
            },
        },
        zhengzhou: {
            name: "郑州市",
            province: "河南省",
            tier: "二线",
            employee: {
                outpatient: {
                    deductible: 1500,
                    max_reimburse: 18000,
                    reimburse_rates: [
                        { min: 0, max: 18000, rate: 0.65, location: "一级医院" },
                        { min: 0, max: 18000, rate: 0.55, location: "二级医院" },
                        { min: 0, max: 18000, rate: 0.45, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
                inpatient: {
                    deductible: 1200,
                    max_reimburse: 230000,
                    reimburse_rates: [
                        { min: 0, max: 230000, rate: 0.88, location: "一级医院" },
                        { min: 0, max: 230000, rate: 0.82, location: "二级医院" },
                        { min: 0, max: 230000, rate: 0.75, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
            },
            resident: {
                outpatient: {
                    deductible: 300,
                    max_reimburse: 3000,
                    reimburse_rates: [
                        { min: 0, max: 3000, rate: 0.5, location: "一级医院" },
                        { min: 0, max: 3000, rate: 0.4, location: "二级医院" },
                        { min: 0, max: 3000, rate: 0.3, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
                inpatient: {
                    deductible: 900,
                    max_reimburse: 160000,
                    reimburse_rates: [
                        { min: 0, max: 160000, rate: 0.7, location: "一级医院" },
                        { min: 0, max: 160000, rate: 0.62, location: "二级医院" },
                        { min: 0, max: 160000, rate: 0.55, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
            },
        },
        changsha: {
            name: "长沙市",
            province: "湖南省",
            tier: "二线",
            employee: {
                outpatient: {
                    deductible: 1000,
                    max_reimburse: 20000,
                    reimburse_rates: [
                        { min: 0, max: 20000, rate: 0.7, location: "一级医院" },
                        { min: 0, max: 20000, rate: 0.6, location: "二级医院" },
                        { min: 0, max: 20000, rate: 0.5, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
                inpatient: {
                    deductible: 1100,
                    max_reimburse: 250000,
                    reimburse_rates: [
                        { min: 0, max: 250000, rate: 0.88, location: "一级医院" },
                        { min: 0, max: 250000, rate: 0.82, location: "二级医院" },
                        { min: 0, max: 250000, rate: 0.76, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
            },
            resident: {
                outpatient: {
                    deductible: 300,
                    max_reimburse: 3500,
                    reimburse_rates: [
                        { min: 0, max: 3500, rate: 0.55, location: "一级医院" },
                        { min: 0, max: 3500, rate: 0.45, location: "二级医院" },
                        { min: 0, max: 3500, rate: 0.35, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
                inpatient: {
                    deductible: 900,
                    max_reimburse: 180000,
                    reimburse_rates: [
                        { min: 0, max: 180000, rate: 0.72, location: "一级医院" },
                        { min: 0, max: 180000, rate: 0.65, location: "二级医院" },
                        { min: 0, max: 180000, rate: 0.58, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
            },
        },
        // 全国通用参考政策：当用户所在城市不在上面 14 个城市里时使用。
        // 数值为国家层面的常见参考口径，各地差异很大，结果仅供参考。
        national: {
            name: "全国通用（参考）",
            province: "全国",
            tier: "全国",
            disclaimer: "以上为全国通用参考值，各地政策差异较大，实际报销以当地医保局口径为准。",
            employee: {
                outpatient: {
                    deductible: 200,
                    max_reimburse: 20000,
                    reimburse_rates: [
                        { min: 0, max: 20000, rate: 0.7, location: "一级医院" },
                        { min: 0, max: 20000, rate: 0.7, location: "二级医院" },
                        { min: 0, max: 20000, rate: 0.7, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
                inpatient: {
                    deductible: 1500,
                    max_reimburse: 200000,
                    reimburse_rates: [
                        { min: 0, max: 200000, rate: 0.85, location: "一级医院" },
                        { min: 0, max: 200000, rate: 0.85, location: "二级医院" },
                        { min: 0, max: 200000, rate: 0.85, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
            },
            resident: {
                outpatient: {
                    deductible: 200,
                    max_reimburse: 20000,
                    reimburse_rates: [
                        { min: 0, max: 20000, rate: 0.5, location: "一级医院" },
                        { min: 0, max: 20000, rate: 0.5, location: "二级医院" },
                        { min: 0, max: 20000, rate: 0.5, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
                inpatient: {
                    deductible: 1500,
                    max_reimburse: 200000,
                    reimburse_rates: [
                        { min: 0, max: 200000, rate: 0.6, location: "一级医院" },
                        { min: 0, max: 200000, rate: 0.6, location: "二级医院" },
                        { min: 0, max: 200000, rate: 0.6, location: "三级医院" },
                    ],
                    default_hospital_level: "三级医院"
                },
            },
        },
    };

    /** 某城市的政策参考声明（全国通用政策会返回「参考值，以当地医保局为准」） */
    function getCityDisclaimer(cityCode) {
        const c = CITY_POLICIES[cityCode];
        return (c && c.disclaimer) || '';
    }

    // ===== 超额累积分段计算 =====
    function calcTiered(amount, rates, hospitalLevel) {
        const filtered = rates.filter(r => r.location === hospitalLevel);
        const tiers = filtered.length > 0 ? filtered : rates;
        tiers.sort((a, b) => a.min - b.min);
        let reimburse = 0;
        const breakdown = [];
        for (const t of tiers) {
            if (amount <= t.min) break;
            const inSeg = Math.min(amount, t.max) - t.min;
            if (inSeg <= 0) continue;
            const segReimburse = inSeg * t.rate;
            reimburse += segReimburse;
            breakdown.push({ min: t.min, max: t.max, rate: t.rate, inSeg: Math.round(inSeg*100)/100, reimburse: Math.round(segReimburse*100)/100 });
        }
        return { total: reimburse, breakdown };
    }

    // ===== 报销计算主函数 =====
    function quickCalculate(cityCode, insuranceType, totalFee, visitType, hospitalLevel, classARatio, classBRatio, selfPayRatio) {
        if (classARatio === undefined) classARatio = 0.55;
        if (classBRatio === undefined) classBRatio = 0.30;
        if (selfPayRatio === undefined) selfPayRatio = 0.15;
        const city = CITY_POLICIES[cityCode];
        if (!city || !city[insuranceType] || !city[insuranceType][visitType]) {
            throw new Error("不支持的城市或医保类型");
        }
        const policy = city[insuranceType][visitType];
        const hl = hospitalLevel || policy.default_hospital_level;

        // 1. 报销基数
        const reimburseBase =
            totalFee * classARatio * FEE_CATEGORIES.class_a.ratio +
            totalFee * classBRatio * FEE_CATEGORIES.class_b.ratio +
            totalFee * selfPayRatio * FEE_CATEGORIES.self_pay.ratio;

        // 2. 扣起付线
        const deductible = policy.deductible;
        const aboveDeductible = Math.max(0, reimburseBase - deductible);

        // 3. 分段计算基本医保
        const tierResult = calcTiered(aboveDeductible, policy.reimburse_rates, hl);
        const basicReimburse = Math.min(tierResult.total, policy.max_reimburse);

        // 4. 个人自付
        const personalPay = totalFee - basicReimburse;

        // 5. 大病保险
        let seriousReimburse = 0;
        let seriousEligible = false;
        if (personalPay > SERIOUS_ILLNESS.deductible) {
            seriousEligible = true;
            const siAbove = personalPay - SERIOUS_ILLNESS.deductible;
            seriousReimburse = Math.min(siAbove * SERIOUS_ILLNESS.rate, SERIOUS_ILLNESS.max_reimburse);
        }

        const totalReimburse = basicReimburse + seriousReimburse;
        const finalPersonalPay = totalFee - totalReimburse;

        return {
            summary: {
                totalFee: Math.round(totalFee * 100) / 100,
                reimburseBase: Math.round(reimburseBase * 100) / 100,
                deductible,
                aboveDeductible: Math.round(aboveDeductible * 100) / 100,
                basicReimburse: Math.round(basicReimburse * 100) / 100,
                seriousReimburse: Math.round(seriousReimburse * 100) / 100,
                totalReimburse: Math.round(totalReimburse * 100) / 100,
                personalPay: Math.round(finalPersonalPay * 100) / 100,
                reimburseRatio: totalFee > 0 ? Math.round(totalReimburse / totalFee * 1000) / 10 : 0,
                cappedByMax: basicReimburse >= policy.max_reimburse,
            },
            categoryStats: {
                classA:   { amount: Math.round(totalFee * classARatio * 100) / 100, ratio: classARatio, total: Math.round(totalFee * classARatio * 100) / 100, count: 0 },
                classB:   { amount: Math.round(totalFee * classBRatio * 100) / 100, ratio: classBRatio, total: Math.round(totalFee * classBRatio * 100) / 100, count: 0 },
                selfPay:  { amount: Math.round(totalFee * selfPayRatio * 100) / 100, ratio: selfPayRatio, total: Math.round(totalFee * selfPayRatio * 100) / 100, count: 0 },
                class_a:  { total: Math.round(totalFee * classARatio * 100) / 100, ratio: classARatio, amount: Math.round(totalFee * classARatio * 100) / 100, count: 0 },
                class_b:  { total: Math.round(totalFee * classBRatio * 100) / 100, ratio: classBRatio, amount: Math.round(totalFee * classBRatio * 100) / 100, count: 0 },
                self_pay: { total: Math.round(totalFee * selfPayRatio * 100) / 100, ratio: selfPayRatio, amount: Math.round(totalFee * selfPayRatio * 100) / 100, count: 0 },
            },
            basicDetails: {
                hospitalLevel: hl,
                maxReimburse: policy.max_reimburse,
                tierBreakdown: tierResult.breakdown,
            },
            seriousIllness: {
                eligible: seriousEligible,
                deductible: SERIOUS_ILLNESS.deductible,
                rate: SERIOUS_ILLNESS.rate,
                reimburse: Math.round(seriousReimburse * 100) / 100,
            },
            policy: {
                city: city.name,
                cityCode,
                insuranceType,
                visitType,
                year: "2026",
            },
        };
    }

    // ===== 解释生成（三受众模式） =====
    function generateExplanation(result, audience) {
        audience = audience || "general";
        const s = result.summary;
        const p = result.policy;

        if (audience === "elderly") {
            const parts = [];
            parts.push("您好！这笔医药费总共 " + s.totalFee + " 元。");
            parts.push("医保能给您报 " + s.totalReimburse + " 元，您自己要掏 " + s.personalPay + " 元（大约报 " + s.reimburseRatio + "%）。");
            parts.push("怎么算出来的呢？先扣掉 " + s.deductible + " 元（这叫起付线，就是说没超过这个数的都得自己付），剩下的部分按比例报销。");
            if (s.seriousReimburse > 0) {
                parts.push("另外，您自付的钱比较多，大病保险还能多报 " + s.seriousReimburse + " 元。");
            }
            return parts.join("\n\n");
        }

        if (audience === "professional") {
            const insName = p.insuranceType === "employee" ? "职工医保" : "居民医保";
            const visitName = p.visitType === "inpatient" ? "住院" : "门诊";
            const parts = [];
            parts.push("【计算口径】" + p.city + " · " + insName + " · " + visitName + " · 政策版本 2026.1");
            parts.push("【费用结构】总费用 " + s.totalFee + " 元，其中报销基数 " + s.reimburseBase + " 元（甲类×100%、乙类×80%、自费×0%）");
            parts.push("【基本医保】起付线 " + s.deductible + " 元，起付线以上 " + s.aboveDeductible + " 元按超额累积分段计算，基本医保报销 " + s.basicReimburse + " 元");
            if (s.seriousReimburse > 0) {
                parts.push("【大病保险】个人自付超过大病起付线 " + SERIOUS_ILLNESS.deductible + " 元部分，按 " + (SERIOUS_ILLNESS.rate * 100) + "% 报销，大病保险报销 " + s.seriousReimburse + " 元");
            } else {
                parts.push("【大病保险】个人自付未超过大病起付线 " + SERIOUS_ILLNESS.deductible + " 元，本次不享受");
            }
            parts.push("【最终结果】合计报销 " + s.totalReimburse + " 元，个人自付 " + s.personalPay + " 元，实际报销比例 " + s.reimburseRatio + "%");
            return parts.join("\n\n");
        }

        // 默认：普通人
        const parts = [];
        parts.push("本次医疗总费用 " + s.totalFee + " 元，预计医保报销 " + s.totalReimburse + " 元（报销比例约 " + s.reimburseRatio + "%），个人自付 " + s.personalPay + " 元。");
        parts.push("计算过程：先从总费用中算出报销基数（甲类全额计入、乙类打8折、自费不计），再扣掉起付线 " + s.deductible + " 元，剩下的部分按医院等级对应的比例分段报销。");
        if (s.seriousReimburse > 0) {
            parts.push("另外，因为您个人自付的金额超过了大病保险的起付线（" + SERIOUS_ILLNESS.deductible + "元），大病保险还能额外报销 " + s.seriousReimburse + " 元。");
        }
        parts.push("💡 提示：不同医院等级报销比例不同，一级医院最高。如果病情允许，可以考虑在基层医院就诊，报销更多。");
        return parts.join("\n\n");
    }

    // ===== 为什么报得比预期少 =====
    function explainWhyLess(result) {
        const reasons = [];
        const s = result.summary;
        const cs = result.categoryStats;

        if (cs.selfPay.amount > 0) {
            reasons.push({
                key: "self_pay", reason: "有自费项目完全不能报销",
                detail: "自费费用共 " + cs.selfPay.amount + " 元，这部分医保完全不承担。",
                amount: cs.selfPay.amount,
                severity: cs.selfPay.ratio > 0.2 ? "high" : "medium",
                plain: { reason: "有些药和检查完全不能报", detail: "自费的部分花了 " + cs.selfPay.amount + " 元，这部分全得自己掏。" }
            });
        }

        if (cs.classB.amount > 0) {
            const selfPart = Math.round(cs.classB.amount * 0.2 * 100) / 100;
            reasons.push({
                key: "class_b", reason: "乙类药品/项目要先自付20%",
                detail: "乙类费用 " + cs.classB.amount + " 元，其中 20%（约 " + selfPart + " 元）需要先自己付，剩下的 80% 才进入报销基数。",
                amount: selfPart, severity: "medium",
                plain: { reason: "有些药不是全报的", detail: "乙类药品自己先掏 20%，剩下的 80% 才参加报销。" }
            });
        }

        if (s.deductible > 0 && s.reimburseBase > 0) {
            reasons.push({
                key: "deductible", reason: "起付线以内的钱要自己先掏",
                detail: "政策规定起付线 " + s.deductible + " 元，这部分不参与报销。",
                amount: Math.min(s.deductible, s.reimburseBase),
                severity: s.reimburseBase <= s.deductible * 1.5 ? "high" : "medium",
                plain: { reason: "有一笔钱要先自己掏，过了这个数才开始报", detail: "医保规定先自己付 " + s.deductible + " 元，过了这个数才按比例报。" }
            });
        }

        if (result.basicDetails && result.basicDetails.hospitalLevel === "三级医院") {
            reasons.push({
                key: "hospital_level", reason: "三级医院报销比例最低",
                detail: "三级医院的报销比例比一级、二级医院低 10~20 个百分点。如果是常见病，可以考虑去二级或社区医院，报销比例更高。",
                amount: 0, severity: "low",
                plain: { reason: "大医院报得少一点", detail: "大医院（三级）设备好但报销比例低，小毛病可以去社区医院，报得多还不用排队。" }
            });
        }

        reasons.sort((a, b) => b.amount - a.amount);
        return reasons;
    }

    // ===== 怎么才能多报一点 =====
    function suggestImprovements(result) {
        const suggestions = [];

        if (result.basicDetails && result.basicDetails.hospitalLevel === "三级医院") {
            suggestions.push({
                key: "lower_level", action: "选择等级低一点的医院",
                detail: "如果病情允许，一级/二级医院的报销比例比三级医院高 10~20 个百分点。常见病、慢病复查可以考虑去社区医院或二级医院。",
                estimatedGain: "estimate", confidence: "high",
                plain: { action: "小毛病去小医院", detail: "感冒发烧、拿药复查这种小事，去社区医院就行，报得多还不用排队。" }
            });
        }

        if (result.categoryStats.classB.ratio > 0.2) {
            suggestions.push({
                key: "ask_generic", action: "问问医生有没有甲类替代药",
                detail: "乙类药品需要先自付20%。如果有甲类（全额报销）的同类药品，可以请医生考虑替换。",
                estimatedGain: "qualitative", confidence: "medium",
                plain: { action: "问问医生能不能换便宜点的药", detail: "有些药有便宜又能全报的替代款，可以问问医生。" }
            });
        }

        if (result.policy && result.policy.visitType === "outpatient") {
            suggestions.push({
                key: "chronic_disease", action: "如果是高血压/糖尿病等慢病，办个慢特病认定",
                detail: "办理了门诊慢特病认定后，相关药品和检查的报销比例会更高，年度限额也更大。",
                estimatedGain: "qualitative", confidence: "high",
                plain: { action: "长期吃药的可以办个慢病本", detail: "高血压、糖尿病这些需要长期吃药的病，办个慢特病认定，买药能报更多。" }
            });
        }

        suggestions.push({
            key: "year_end", action: "年底前花过起付线的，能今年看就今年看",
            detail: "如果今年已经达到起付线了，年底前需要做的检查、治疗尽量在年内完成，不用再重新承担一次起付线。",
            estimatedGain: "qualitative", confidence: "medium",
            plain: { action: "今年看过病的，年底前把该查的都查了", detail: "今年已经花过起付线了，再看病就不用重新掏那笔钱了，能今年看就今年看。" }
        });

        suggestions.push({
            key: "relocation_record", action: "异地就医前先备案",
            detail: "在外地看病前，通过国家医保服务平台APP办好异地就医备案，报销比例比不备案高很多，还能直接刷卡结算不用自己先垫付。",
            estimatedGain: "qualitative", confidence: "high",
            plain: { action: "去外地看病前先在手机上备案", detail: "下载国家医保服务平台APP，提前办好异地就医备案，报得多还能直接刷卡。" }
        });

        return suggestions;
    }

    // ===== 工具函数 =====
    function getSupportedCities() {
        return Object.keys(CITY_POLICIES).map(code => ({
            code: code,
            name: CITY_POLICIES[code].name,
            province: CITY_POLICIES[code].province,
            tier: CITY_POLICIES[code].tier,
        }));
    }

    function getCityPolicy(cityCode, insuranceType) {
        const c = CITY_POLICIES[cityCode];
        return c ? c[insuranceType] : null;
    }

    // ===== 兼容：明细计算类（供逐条录入模式使用） =====
    function ReimbursementCalculator(cityCode, insuranceType, visitType, hospitalLevel) {
        this.cityCode = cityCode;
        this.insuranceType = insuranceType;
        this.visitType = visitType;
        this.hospitalLevel = hospitalLevel;
    }
    ReimbursementCalculator.prototype.calculate = function(items) {
        let totalFee = 0, classAAmount = 0, classBAmount = 0, selfPayAmount = 0;
        const itemsDetail = [];
        for (const item of items) {
            const amount = item.amount || 0;
            totalFee += amount;
            const cat = item.category || (item.item_type ? _guessCategory(item.item_type) : 'class_b');
            if (cat === 'class_a' || cat === '甲类') {
                classAAmount += amount;
            } else if (cat === 'class_b' || cat === '乙类') {
                classBAmount += amount;
            } else {
                selfPayAmount += amount;
            }
            itemsDetail.push({
                name: item.name || '未命名项目',
                amount: amount,
                category: cat,
                categoryName: _catName(cat),
            });
        }
        const aRatio = totalFee > 0 ? classAAmount / totalFee : 0.55;
        const bRatio = totalFee > 0 ? classBAmount / totalFee : 0.3;
        const sRatio = totalFee > 0 ? selfPayAmount / totalFee : 0.15;

        const result = quickCalculate(this.cityCode, this.insuranceType, totalFee,
            this.visitType, this.hospitalLevel, aRatio, bRatio, sRatio);

        result.itemsDetail = itemsDetail;
        result.categoryStats.classA.count = itemsDetail.filter(i => i.category === 'class_a' || i.category === '甲类').length;
        result.categoryStats.classB.count = itemsDetail.filter(i => i.category === 'class_b' || i.category === '乙类').length;
        result.categoryStats.selfPay.count = itemsDetail.filter(i => i.category === 'self_pay' || i.category === '自费').length;
        return result;
    };
    function _guessCategory(itemType) {
        const map = {
            '药品': 'class_b', '西药': 'class_b', '中成药': 'class_b', '中药': 'class_b',
            '检查': 'class_b', '检验': 'class_b', '检查检验': 'class_b',
            '治疗': 'class_b', '手术': 'class_b', '耗材': 'class_b', '医用耗材': 'class_b',
            '床位': 'class_a', '诊查': 'class_a', '护理': 'class_a', '服务': 'class_a',
        };
        return map[itemType] || 'class_b';
    }
    function _catName(cat) {
        if (cat === 'class_a' || cat === '甲类') return '甲类';
        if (cat === 'class_b' || cat === '乙类') return '乙类';
        return '自费';
    }

    // ===== 对外暴露 =====
    window.MediClear = {
        CITY_POLICIES,
        FEE_CATEGORIES,
        SERIOUS_ILLNESS,
        quickCalculate,
        generateExplanation,
        explainWhyLess,
        suggestImprovements,
        getSupportedCities,
        getCityPolicy,
        getCityDisclaimer,
        calcTiered,
        ReimbursementCalculator,
        get_supported_cities: getSupportedCities,
    };

})();