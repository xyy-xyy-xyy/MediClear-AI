/*
 * MediClear Agent —— LLM function calling 编排层（D1）
 *
 * 架构铁律（与项目约束一致）：
 *   1. 大模型只负责：意图理解 → 选择工具/填充参数 → 将工具返回的 JSON 转述为人话
 *   2. 一切金额、比例、政策数字 100% 来自本地确定性引擎（window.MediClear 等），
 *      大模型未经工具返回而输出的任何数字即为违规
 *   3. 工具 = 现有确定性函数的薄包装，本文件不新增任何计算逻辑
 *   4. BYO-key：API Key 仅存浏览器 localStorage，不进 git、不经任何中间服务器
 *   5. 已知边界：对话文字会发送给用户自行配置的 LLM 服务商；金额计算始终本地完成
 */
(function () {
    'use strict';

    // ==================== 常量 ====================

    var STORAGE_KEY = 'mediclear_agent_settings';
    var MAX_TOOL_ROUNDS = 6; // 单次提问最多工具调用轮数，防死循环

    var SYSTEM_PROMPT = [
        '你是"医算清"的医保 AI 助手，帮助普通用户理解医保报销、城市政策与异地就医流程。',
        '',
        '你必须遵守以下铁律：',
        '1. 一切金额、报销比例、起付线、封顶线等数字，只能来自工具返回结果；禁止自己计算、估算或凭记忆给出任何数字。',
        '2. 政策细节一律通过工具查询，不得编造。',
        '3. 工具返回 ok:false 时，向用户解释原因并追问缺失信息；同一参数不得反复猜测重试。',
        '4. 用户问题与医保无关时，礼貌说明你的职责范围。',
        '5. 用通俗中文回答，少用术语。',
        '6. 涉及测算金额时，注明"测算结果仅供参考，以当地医保局结算为准"。'
    ].join('\n');

    // ==================== BYO-key 设置 ====================

    var PROVIDERS = {
        deepseek: {
            label: 'DeepSeek',
            baseUrl: 'https://api.deepseek.com/chat/completions',
            model: 'deepseek-chat',
            keyUrl: 'https://platform.deepseek.com/api_keys'
        },
        glm: {
            label: '智谱 GLM',
            baseUrl: 'https://open.bigmodel.cn/api/paas/v4/chat/completions',
            model: 'glm-4-flash',
            keyUrl: 'https://open.bigmodel.cn/usercenter/apikeys'
        },
        custom: {
            label: '自定义（OpenAI 兼容接口）',
            baseUrl: '',
            model: '',
            keyUrl: ''
        }
    };

    function getSettings() {
        try {
            var raw = localStorage.getItem(STORAGE_KEY);
            return raw ? JSON.parse(raw) : { provider: '', apiKey: '', model: '' };
        } catch (e) {
            return { provider: '', apiKey: '', model: '' };
        }
    }

    function saveSettings(s) {
        localStorage.setItem(STORAGE_KEY, JSON.stringify({
            provider: s.provider || '',
            apiKey: s.apiKey || '',
            model: s.model || '',
            baseUrl: s.baseUrl || ''
        }));
    }

    function isConfigured() {
        var s = getSettings();
        if (!s.provider || !s.apiKey) return false;
        if (!PROVIDERS[s.provider]) return false;
        if (s.provider === 'custom' && (!s.baseUrl || !s.model)) return false;
        return true;
    }

    // ==================== 工具层 ====================
    // 6 个工具，全部是现有确定性函数的薄包装，参数校验/归一化在此完成。

    var state = { messages: [], lastResult: null };

    // 城市归一化：支持 "北京" / "北京市" / "beijing" 等输入 → 标准 code；无法唯一确定时返回 null
    function cityToCode(v) {
        if (v === undefined || v === null || v === '') return null;
        var cities = window.MediClear.getSupportedCities();
        var raw = String(v).trim();
        for (var i = 0; i < cities.length; i++) {
            if (cities[i].code === raw) return cities[i].code;
        }
        var s = raw.replace(/市$/, '');
        if (!s) return null;
        var hits = [];
        for (i = 0; i < cities.length; i++) {
            var n = String(cities[i].name || '').replace(/市$/, '');
            if (n === s) { return cities[i].code; } // 去后缀后精确命中
            if (n.indexOf(s) !== -1 || s.indexOf(n) !== -1) hits.push(cities[i]);
        }
        return hits.length === 1 ? hits[0].code : null; // 多义匹配（如"南"）不猜，交回 LLM 追问
    }

    function unknownCity(name) {
        return {
            ok: false,
            error: '暂不支持该城市：' + name + '。可调用 list_supported_cities 查询支持列表，并建议用户使用支持城市或选择"异地就医"话题。'
        };
    }

    var TOOL_IMPLS = {

        list_supported_cities: function () {
            return { ok: true, cities: window.MediClear.getSupportedCities() };
        },

        get_city_policy: function (args) {
            var code = cityToCode(args.city);
            if (!code) return unknownCity(args.city);
            var type = args.insurance_type;
            if (type !== 'employee' && type !== 'resident') {
                return { ok: false, error: 'insurance_type 必须是 employee（职工医保）或 resident（居民医保）' };
            }
            var policy = window.MediClear.getCityPolicy(code, type);
            if (!policy) {
                return { ok: false, error: '该城市暂无此医保类型的政策数据' };
            }
            return { ok: true, city: code, insurance_type: type, policy: policy };
        },

        calculate_reimbursement: function (args) {
            var code = cityToCode(args.city);
            if (!code) return unknownCity(args.city);
            var type = args.insurance_type;
            if (type !== 'employee' && type !== 'resident') {
                return { ok: false, error: 'insurance_type 必须是 employee（职工医保）或 resident（居民医保）' };
            }
            var visit = args.visit_type;
            if (visit !== 'inpatient' && visit !== 'outpatient') {
                return { ok: false, error: 'visit_type 必须是 inpatient（住院）或 outpatient（门诊）' };
            }
            var fee = Number(args.total_fee);
            if (!isFinite(fee) || fee <= 0) {
                return { ok: false, error: 'total_fee 必须是大于 0 的数字，请向用户确认金额后重试' };
            }
            var r = window.MediClear.quickCalculate(
                code, type, fee, visit,
                args.hospital_level,
                args.class_a_ratio, args.class_b_ratio, args.self_pay_ratio
            );
            state.lastResult = r; // 供 explain_result 复用，避免重复计算
            return {
                ok: true,
                summary: r.summary,
                policy: r.policy,
                seriousIllness: r.seriousIllness,
                hospitalLevel: r.basicDetails.hospitalLevel
            };
        },

        explain_result: function (args) {
            var r = state.lastResult;
            if (!r) {
                return { ok: false, error: '当前会话还没有测算结果，请先调用 calculate_reimbursement' };
            }
            var kind = args.kind || 'why_less';
            if (kind === 'why_less') {
                return { ok: true, kind: kind, reasons: window.MediClear.explainWhyLess(r) };
            }
            if (kind === 'next_actions') {
                return { ok: true, kind: kind, suggestions: window.MediClear.suggestImprovements(r) };
            }
            if (kind === 'explanation') {
                return { ok: true, kind: kind, text: window.MediClear.generateExplanation(r, args.audience) };
            }
            return { ok: false, error: 'kind 必须是 why_less / next_actions / explanation' };
        },

        relocation_checklist: function (args) {
            if (!window.MediClearRelocation) {
                return { ok: false, error: '异地就医模块未加载' };
            }
            var visit = args.visit_type || 'inpatient';
            if (['inpatient', 'outpatient', 'emergency'].indexOf(visit) === -1) {
                return { ok: false, error: 'visit_type 必须是 inpatient / outpatient / emergency' };
            }
            var reason = args.reason || 'work';
            if (['work', 'retirement', 'travel', 'transfer'].indexOf(reason) === -1) {
                return { ok: false, error: 'reason 必须是 work / retirement / travel / transfer' };
            }
            var from = cityToCode(args.insured_city);
            if (!from) return unknownCity(args.insured_city);
            // 就诊城市不要求在支持列表内（清单不依赖当地政策数据），归一化失败时透传原名
            var to = cityToCode(args.visit_city) || String(args.visit_city || '').trim();
            var checklist = window.MediClearRelocation.generateChecklist(from, to, visit, reason);
            return { ok: true, checklist: checklist };
        },

        parse_invoice_text: function (args) {
            if (!window.MediClearOCR) {
                return { ok: false, error: '发票解析模块未加载' };
            }
            var text = String(args.text || '').trim();
            if (!text) {
                return { ok: false, error: 'text 不能为空，请让用户粘贴结算单/发票上的文字' };
            }
            var parsed = window.MediClearOCR.parse(text);
            return { ok: true, parsed: parsed };
        }
    };

    // OpenAI tools 格式描述（function calling 的工具清单，写给 LLM 看）
    var TOOL_LIST = [
        {
            type: 'function',
            function: {
                name: 'list_supported_cities',
                description: '获取本工具支持测算的城市列表（含城市代码、省份、线级）。当用户提到的城市不确定是否支持时调用。',
                parameters: { type: 'object', properties: {}, required: [] }
            }
        },
        {
            type: 'function',
            function: {
                name: 'get_city_policy',
                description: '查询某城市某医保类型的政策明细：起付线、分段报销比例、封顶线、医院等级差异。回答"XX城市报销政策是什么"类问题时调用。',
                parameters: {
                    type: 'object',
                    properties: {
                        city: { type: 'string', description: '城市中文名（如"长沙"）或城市代码' },
                        insurance_type: { type: 'string', enum: ['employee', 'resident'], description: 'employee=职工医保，resident=居民医保' }
                    },
                    required: ['city', 'insurance_type']
                }
            }
        },
        {
            type: 'function',
            function: {
                name: 'calculate_reimbursement',
                description: '医保报销测算（确定性引擎）。用户给出金额并想估算能报多少时调用。所有金额数字必须取自本工具返回的 summary 字段。',
                parameters: {
                    type: 'object',
                    properties: {
                        city: { type: 'string', description: '参保城市中文名或城市代码' },
                        insurance_type: { type: 'string', enum: ['employee', 'resident'], description: '职工医保/居民医保；不确定时先追问用户' },
                        visit_type: { type: 'string', enum: ['inpatient', 'outpatient'], description: 'inpatient=住院，outpatient=门诊' },
                        total_fee: { type: 'number', description: '医疗总费用（元），必须大于 0' },
                        hospital_level: { type: 'string', enum: ['一级医院', '二级医院', '三级医院'], description: '医院等级，用户未提及时可省略（用默认）' },
                        class_a_ratio: { type: 'number', description: '甲类费用占比 0~1，可省略（默认 0.55）' },
                        class_b_ratio: { type: 'number', description: '乙类费用占比 0~1，可省略（默认 0.30）' },
                        self_pay_ratio: { type: 'number', description: '自费费用占比 0~1，可省略（默认 0.15）' }
                    },
                    required: ['city', 'insurance_type', 'visit_type', 'total_fee']
                }
            }
        },
        {
            type: 'function',
            function: {
                name: 'explain_result',
                description: '解读最近一次测算结果：为什么报得比预期少（why_less）、还能怎么多报（next_actions）、生成通俗解释（explanation）。必须先调用过 calculate_reimbursement。',
                parameters: {
                    type: 'object',
                    properties: {
                        kind: { type: 'string', enum: ['why_less', 'next_actions', 'explanation'], description: '解读类型' },
                        audience: { type: 'string', enum: ['general', 'elderly', 'professional'], description: '仅 kind=explanation 时有效，默认 general' }
                    },
                    required: ['kind']
                }
            }
        },
        {
            type: 'function',
            function: {
                name: 'relocation_checklist',
                description: '生成异地就医材料清单与办理步骤（备案、发票、明细等）。用户说"要去外地/异地看病怎么报销"时调用。',
                parameters: {
                    type: 'object',
                    properties: {
                        insured_city: { type: 'string', description: '参保城市中文名或城市代码' },
                        visit_city: { type: 'string', description: '就诊城市中文名' },
                        visit_type: { type: 'string', enum: ['inpatient', 'outpatient', 'emergency'], description: '默认 inpatient' },
                        reason: { type: 'string', enum: ['work', 'retirement', 'travel', 'transfer'], description: '异地原因：工作/养老/旅游探亲/转诊，默认 work' }
                    },
                    required: ['insured_city', 'visit_city']
                }
            }
        },
        {
            type: 'function',
            function: {
                name: 'parse_invoice_text',
                description: '从用户粘贴的结算单/发票文字中解析总金额等关键信息（本地规则引擎，非 OCR 图片识别）。用户直接粘贴一段文字时调用。',
                parameters: {
                    type: 'object',
                    properties: {
                        text: { type: 'string', description: '用户粘贴的原始文字，保持原样不要改写' }
                    },
                    required: ['text']
                }
            }
        }
    ];

    // ==================== Dispatcher ====================

    function dispatchTool(name, argsJson) {
        var impl = TOOL_IMPLS[name];
        if (!impl) return { ok: false, error: '未知工具：' + name };
        var args = {};
        if (argsJson) {
            try {
                args = typeof argsJson === 'string' ? JSON.parse(argsJson) : argsJson;
            } catch (e) {
                return { ok: false, error: '工具参数不是合法 JSON：' + e.message };
            }
        }
        try {
            return impl(args);
        } catch (e) {
            return { ok: false, error: e.message };
        }
    }

    // ==================== 对话循环 ====================

    function buildApiMessages() {
        return [{ role: 'system', content: SYSTEM_PROMPT }].concat(state.messages);
    }

    async function chat(userText) {
        if (!isConfigured()) {
            return { ok: false, error: '尚未配置 AI 服务商或 API Key' };
        }
        var s = getSettings();
        var preset = PROVIDERS[s.provider];
        var url = s.provider === 'custom' ? s.baseUrl : preset.baseUrl;
        var model = s.model || preset.model;

        state.messages.push({ role: 'user', content: userText });
        var toolEvents = [];

        for (var round = 0; round < MAX_TOOL_ROUNDS; round++) {
            var resp;
            try {
                resp = await fetch(url, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': 'Bearer ' + s.apiKey
                    },
                    body: JSON.stringify({
                        model: model,
                        messages: buildApiMessages(),
                        tools: TOOL_LIST,
                        tool_choice: 'auto'
                    })
                });
            } catch (e) {
                state.messages.pop(); // 本轮用户消息不入历史，便于重试
                return { ok: false, error: '网络请求失败：' + e.message, toolEvents: toolEvents };
            }
            if (!resp.ok) {
                var errBody = '';
                try { errBody = await resp.text(); } catch (e) { /* 忽略 */ }
                return {
                    ok: false,
                    error: 'API 返回 ' + resp.status + '：' + String(errBody).slice(0, 300),
                    toolEvents: toolEvents
                };
            }

            var data = await resp.json();
            var msg = data.choices && data.choices[0] && data.choices[0].message;
            if (!msg) {
                return { ok: false, error: 'API 响应格式异常（缺少 choices[0].message）', toolEvents: toolEvents };
            }

            // 模型请求调用工具 → 逐个分发并回传结果，进入下一轮
            if (msg.tool_calls && msg.tool_calls.length) {
                state.messages.push({ role: 'assistant', content: msg.content || '', tool_calls: msg.tool_calls });
                for (var i = 0; i < msg.tool_calls.length; i++) {
                    var tc = msg.tool_calls[i];
                    var result = dispatchTool(tc.function && tc.function.name, tc.function && tc.function.arguments);
                    toolEvents.push({ name: (tc.function && tc.function.name) || '?', ok: result.ok !== false });
                    state.messages.push({
                        role: 'tool',
                        tool_call_id: tc.id,
                        content: JSON.stringify(result)
                    });
                }
                continue;
            }

            // 最终回答
            var reply = msg.content || '';
            state.messages.push({ role: 'assistant', content: reply });
            return { ok: true, reply: reply, toolEvents: toolEvents };
        }

        return {
            ok: true,
            reply: '本轮工具调用已达上限，请换个问法，或点击"新会话"重新开始。',
            toolEvents: toolEvents
        };
    }

    function resetChat() {
        state.messages = [];
        state.lastResult = null;
    }

    // ==================== 导出 ====================

    window.MediClearAgent = {
        SYSTEM_PROMPT: SYSTEM_PROMPT,
        TOOLS: TOOL_LIST,
        dispatchTool: dispatchTool,
        chat: chat,
        resetChat: resetChat,
        getSettings: getSettings,
        saveSettings: saveSettings,
        isConfigured: isConfigured,
        PROVIDERS: PROVIDERS,
        _state: state // 仅供测试断言
    };
})();
