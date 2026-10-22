/**
 * 医算清 · Agent 编排层自动化测试（jsdom + mock fetch）
 * 用法: node test-agent.js
 * 覆盖: 工具白名单、参数校验/归一化、与直调引擎结果一致性、function calling 两轮循环
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const WEB = __dirname;
const dom = new JSDOM('<!doctype html><html><body></body></html>', {
    url: 'http://localhost:8777/test-agent.html',
    runScripts: 'outside-only',
    pretendToBeVisual: true,
});
const { window } = dom;

const results = [];
function check(name, cond, extra) {
    results.push({ name, ok: !!cond, extra: extra || '' });
    console.log((cond ? '✅' : '❌') + ' ' + name + (extra ? '  → ' + extra : ''));
}

// ---- 加载被测脚本（顺序依赖：calculator → relocation → ocr → agent） ----
try {
    for (const f of ['js/calculator.js', 'js/relocation.js', 'js/ocr.js', 'js/agent.js']) {
        window.eval(fs.readFileSync(path.join(WEB, f), 'utf8'));
    }
} catch (e) {
    console.error('脚本加载失败:', e.message);
    process.exit(1);
}

const Agent = window.MediClearAgent;
const d = (name, args) => Agent.dispatchTool(name, typeof args === 'string' ? args : JSON.stringify(args));

// ---- 测试 1：模块导出完整性 ----
check('MediClearAgent 已导出', !!Agent);
check('工具白名单共 6 个', Agent.TOOLS && Agent.TOOLS.length === 6,
    Agent.TOOLS && Agent.TOOLS.map(t => t.function.name).join(', '));
check('dispatchTool 可调用', typeof Agent.dispatchTool === 'function');

// ---- 测试 2：未配置 BYO-key 时 chat 直接拒绝 ----
window.localStorage.removeItem('mediclear_agent_settings');
Agent.chat('测试').then(res1 => {
    check('未配置时 chat 返回错误', res1.ok === false && /未配置/.test(res1.error), res1.error);

    // ---- 测试 3：dispatcher 基础防御 ----
    check('未知工具被拒绝', d('no_such_tool', {}).ok === false);
    check('非法 JSON 参数被拒绝', Agent.dispatchTool('list_supported_cities', '{bad json').ok === false);

    // ---- 测试 4：工具 1 —— list_supported_cities ----
    const cities = d('list_supported_cities', {});
    check('城市列表可查询', cities.ok && Array.isArray(cities.cities) && cities.cities.length >= 14,
        cities.ok ? cities.cities.length + ' 个城市' : cities.error);

    // ---- 测试 5：工具 2 —— get_city_policy（含城市名归一化） ----
    const pol = d('get_city_policy', { city: '北京', insurance_type: 'employee' });
    check('中文城市名归一化为 code', pol.ok && pol.city === 'beijing', pol.ok ? pol.city : pol.error);
    check('政策明细含起付线', pol.ok && pol.policy && pol.policy.inpatient && pol.policy.inpatient.deductible > 0,
        pol.ok ? '起付线 ' + pol.policy.inpatient.deductible : (pol.error || '结构缺失'));
    check('非法医保类型被拒绝', d('get_city_policy', { city: '北京', insurance_type: 'commercial' }).ok === false);

    // ---- 测试 6：explain_result 在未测算前被拒绝 ----
    Agent.resetChat();
    check('未测算时 explain_result 被拒绝', d('explain_result', { kind: 'why_less' }).ok === false);

    // ---- 测试 7：工具 3 —— calculate_reimbursement ----
    check('非正数金额被拒绝', d('calculate_reimbursement',
        { city: '北京', insurance_type: 'employee', visit_type: 'inpatient', total_fee: -1 }).ok === false);
    const calc = d('calculate_reimbursement',
        { city: '北京', insurance_type: 'employee', visit_type: 'inpatient', total_fee: '20000' });
    check('字符串金额自动归一化', calc.ok && calc.summary && calc.summary.totalFee === 20000,
        calc.ok ? '报销 ' + calc.summary.totalReimburse : calc.error);
    const direct = window.MediClear.quickCalculate('beijing', 'employee', 20000, 'inpatient');
    check('工具结果与直调引擎完全一致', calc.ok && calc.summary.totalReimburse === direct.summary.totalReimburse,
        calc.ok ? calc.summary.totalReimburse + ' vs ' + direct.summary.totalReimburse : '跳过（calc 失败）');
    check('测算结果已缓存供解读', !!Agent._state.lastResult);

    // ---- 测试 8：工具 4 —— explain_result ----
    const why = d('explain_result', { kind: 'why_less' });
    check('why_less 返回原因数组', why.ok && Array.isArray(why.reasons) && why.reasons.length > 0,
        why.ok ? why.reasons.length + ' 条原因' : why.error);
    const acts = d('explain_result', { kind: 'next_actions' });
    check('next_actions 返回建议数组', acts.ok && Array.isArray(acts.suggestions) && acts.suggestions.length > 0);
    const expl = d('explain_result', { kind: 'explanation', audience: 'elderly' });
    check('explanation 生成长辈版文本', expl.ok && /元/.test(expl.text), expl.ok ? expl.text.slice(0, 24) + '…' : expl.error);
    check('非法 kind 被拒绝', d('explain_result', { kind: 'hack' }).ok === false);

    // ---- 测试 9：工具 5 —— relocation_checklist ----
    const rel = d('relocation_checklist', { insured_city: '北京', visit_city: '上海', visit_type: 'inpatient' });
    check('异地清单生成成功', rel.ok && /北京/.test(rel.checklist.route) && /上海/.test(rel.checklist.route),
        rel.ok ? rel.checklist.route : rel.error);
    check('清单含必备材料', rel.ok && rel.checklist.requiredItems.length >= 4);
    check('非法就医类型被拒绝', d('relocation_checklist',
        { insured_city: '北京', visit_city: '上海', visit_type: 'surgery' }).ok === false);

    // ---- 测试 10：工具 6 —— parse_invoice_text ----
    check('空文本被拒绝', d('parse_invoice_text', { text: '  ' }).ok === false);
    const inv = d('parse_invoice_text', { text: '住院收费票据\n医保统筹支付：1200.00\n合计（大写）：叁仟肆佰伍拾陆圆柒角捌分\n合计: ¥3,456.78' });
    check('结算单文本可解析', inv.ok && inv.parsed && inv.parsed.total > 0,
        inv.ok ? '解析总额 ' + inv.parsed.total : inv.error);

    // ---- 测试 11：function calling 对话循环（mock fetch 两轮） ----
    Agent.saveSettings({ provider: 'deepseek', apiKey: 'sk-test-only', model: '' });
    check('isConfigured 为真', Agent.isConfigured() === true);

    let fetchCalls = [];
    window.fetch = async (url, opts) => {
        fetchCalls.push({ url: String(url), body: JSON.parse(opts.body) });
        if (fetchCalls.length === 1) {
            return {
                ok: true,
                json: async () => ({
                    choices: [{
                        message: {
                            role: 'assistant', content: '',
                            tool_calls: [{
                                id: 'call_1', type: 'function',
                                function: {
                                    name: 'calculate_reimbursement',
                                    arguments: JSON.stringify({
                                        city: '北京', insurance_type: 'employee',
                                        visit_type: 'inpatient', total_fee: 20000
                                    })
                                }
                            }]
                        }
                    }]
                })
            };
        }
        return {
            ok: true,
            json: async () => ({ choices: [{ message: { role: 'assistant', content: '预计可报销 1 万元以上，测算仅供参考。' } }] })
        };
    };

    Agent.resetChat();
    Agent.chat('我是北京职工医保，住院花了 2 万，能报多少？').then(res => {
        check('chat 返回成功', res.ok === true, res.error || '');
        check('最终回答返回', /报销/.test(res.reply), res.reply.slice(0, 20) + '…');
        check('工具事件记录', res.toolEvents.length === 1 && res.toolEvents[0].name === 'calculate_reimbursement' && res.toolEvents[0].ok);
        check('共发起 2 次 API 请求', fetchCalls.length === 2);
        const second = fetchCalls[1].body;
        const toolMsg = second.messages.find(m => m.role === 'tool');
        check('工具结果已回传给模型', !!toolMsg && toolMsg.tool_call_id === 'call_1');
        const toolPayload = toolMsg ? JSON.parse(toolMsg.content) : {};
        check('回传内容含工具计算的真实金额', toolPayload.ok === true && toolPayload.summary && toolPayload.summary.totalReimburse > 0,
            toolPayload.summary ? '报销 ' + toolPayload.summary.totalReimburse : '无');
        check('system 提示词在最前', second.messages[0].role === 'system' && /只能来自工具/.test(second.messages[0].content));
        check('对话历史完整入列（4 条）', Agent._state.messages.length === 4,
            'user→assistant(tool_calls)→tool→assistant');

        // ---- 测试 12：resetChat 清空 ----
        Agent.resetChat();
        check('resetChat 清空历史与缓存', Agent._state.messages.length === 0 && Agent._state.lastResult === null);

        // ---- 汇总 ----
        const pass = results.filter(r => r.ok).length;
        console.log('\n===== Agent 测试汇总: ' + pass + ' / ' + results.length + ' 通过 =====');
        process.exit(pass === results.length ? 0 : 1);
    }).catch(e => {
        console.error('❌ chat 测试异常:', e.message);
        process.exit(1);
    });
}).catch(e => {
    console.error('❌ 测试异常:', e.message);
    process.exit(1);
});
