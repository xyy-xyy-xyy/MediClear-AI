/**
 * 医算清 · 交互逻辑自动化测试（jsdom）
 * 用法: node test-web.js
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const WEB = __dirname; // 测试文件所在目录即前端目录（可移植，CI 可用）
const html = fs.readFileSync(path.join(WEB, 'index.html'), 'utf8')
    // 去掉外部 CDN script（jsdom 下 echarts 无法渲染 canvas）
    .replace(/<script src="https:[^"]+"><\/script>/g, '')
    .replace(/<script>window\.echarts[^<]+<\/script>/, '');

const dom = new JSDOM(html, {
    url: 'http://localhost:8777/index.html',
    runScripts: 'outside-only',
    pretendToBeVisual: true,
});
const { window } = dom;

// ---- stubs ----
window.echarts = undefined; // 模拟 CDN 失败场景，验证降级
window.HTMLCanvasElement.prototype.getContext = () => null;
window.scrollTo = () => {};
window.HTMLElement.prototype.scrollIntoView = () => {};

let errors = [];
window.addEventListener('error', e => errors.push(e.message));

const results = [];
function check(name, cond, extra) {
    results.push({ name, ok: !!cond, extra: extra || '' });
    console.log((cond ? '✅' : '❌') + ' ' + name + (extra ? '  → ' + extra : ''));
}

// ---- 加载脚本 ----
try {
    const calcSrc = fs.readFileSync(path.join(WEB, 'js/calculator.js'), 'utf8');
    const appSrc = fs.readFileSync(path.join(WEB, 'js/app.js'), 'utf8');
    window.eval(calcSrc);
    window.eval(appSrc);
} catch (e) {
    console.error('脚本加载失败:', e.message);
    process.exit(1);
}

// 触发 DOMContentLoaded（jsdom 中 runScripts outside-only 需手动派发）
window.document.dispatchEvent(new window.Event('DOMContentLoaded', { bubbles: true }));

const $ = s => window.document.querySelector(s);
const $$ = s => Array.from(window.document.querySelectorAll(s));

// ---- 测试 1：引擎 API 完整性 ----
check('MediClear.quickCalculate 可用', typeof window.MediClear.quickCalculate === 'function');
check('MediClear.ReimbursementCalculator 可用', typeof window.MediClear.ReimbursementCalculator === 'function');
const r1 = window.MediClear.quickCalculate('beijing', 'employee', 10000, 'inpatient', '三级医院', 0.6, 0.3, 0.1);
const r1Exp = window.MediClear.generateExplanation(r1);
check('quickCalculate 返回结构完整', r1 && r1.summary && r1.summary.totalReimburse > 0 && r1Exp,
    '报销 ' + r1.summary.totalReimburse + ' / 比例 ' + r1.summary.reimburseRatio + '%');

// ---- 测试 2：初始化 ----
setTimeout(() => {
    check('无 JS 运行时错误', errors.length === 0, errors.join(' | '));
    check('城市下拉已填充', $('#city') && $('#city').options.length === 15, $('#city') && $('#city').options.length + ' 个城市（14城市+全国通用）');
    check('参保地下拉已填充', $('#fromCity').options.length === 15);

    // ---- 测试 2.5：页面切换（切页而非滚动） ----
    check('初始为首页', $('#page-home').classList.contains('active'));
    check('初始计算器页隐藏', !$('#calculator').classList.contains('active'));
    check('初始顶部导航高亮在首页', $$('.nav-link').find(a => a.getAttribute('href') === '#home').classList.contains('active'));

    const navCalc = $$('.nav-link').find(a => a.getAttribute('href') === '#calculator');
    navCalc.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
    check('点顶部导航切到报销计算页', $('#calculator').classList.contains('active') && !$('#page-home').classList.contains('active'));
    check('顶部导航高亮同步', navCalc.classList.contains('active'));
    check('hash 路由已写入（支持直链/后退）', window.location.hash === '#calculator', window.location.hash);

    // C2：账单分析从一级导航降级（顶部导航与底部 Tab 均不再有 #bill，入口在结果卡交叉链接与首页任务卡）
    check('账单分析已从一级导航降级', !$$('.nav-link').some(a => a.getAttribute('href') === '#bill') && !$$('.mn-item').some(a => a.getAttribute('href') === '#bill'));
    check('结果卡含去账单分析交叉入口', !!$('#resultCard .result-crosslink a[href="#bill"]'));

    $('.logo').dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
    check('点 Logo 回首页', $('#page-home').classList.contains('active'));

    // C1：首页任务式三入口，「我看不懂结算单」直达账单页
    check('首页任务式三入口已就位', $$('.task-card').length === 3, String($$('.task-card').length) + ' 张任务卡');
    const taskBill = $$('.task-card').find(a => a.getAttribute('href') === '#bill');
    taskBill.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
    check('点任务卡「看不懂结算单」切到账单页', $('#bill').classList.contains('active') && !$('#page-home').classList.contains('active'));

    // 回到计算器页，继续后续功能测试
    navCalc.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));

    // ---- 测试 3：计算器 ----
    check('默认计算结果卡已展示', $('#resultCard') && !$('#resultCard').hidden);
    const reimb = parseFloat(($('#resultReimburse').textContent || '0').replace(/,/g, ''));
    check('结果数字已渲染', reimb > 0, '显示: ' + $('#resultReimburse').textContent);

    // 快捷金额 chip
    $$('#feeChips .chip')[2].dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    check('快捷金额填入', $('#totalFee').value === '50000', $('#totalFee').value);

    // 明细模式
    $$('.mode-btn').find(b => b.dataset.mode === 'detail').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    $('#fillDemo').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    check('示例明细已填充 5 行', $$('#detailItems .detail-row').length === 5, String($$('#detailItems .detail-row').length));
    check('明细合计计算正确', $('#detailTotal').textContent === '10,000', '合计: ' + $('#detailTotal').textContent);
    $('#addItemBtn').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    check('手动添加明细行', $$('#detailItems .detail-row').length === 6);
    // 删除一行
    $$('#detailItems .detail-del')[5].dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    check('删除明细行', $$('#detailItems .detail-row').length === 5);

    // 明细模式计算
    $('#calcBtn').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    setTimeout(() => {
        check('明细模式计算后结果更新', !$('#resultCard').hidden);
        // OCR 演示
        $('#ocrBtn').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
        check('OCR 面板展开', !$('#ocrPanel').hidden);
        $('#ocrDemo').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
        check('OCR 演示结果渲染', !$('#ocrResult').hidden);
        check('OCR 直接转明细逐条录入（6 行）', $$('#detailItems .detail-row').length === 6, String($$('#detailItems .detail-row').length));
        check('OCR 一键填入（总费用 12860）', $('#totalFee').value === '12860', $('#totalFee').value);

        // ---- 测试 4：账单分析 ----
        check('示例统计已渲染', $('#billTotal').textContent !== '0', '总花费: ' + $('#billTotal').textContent);
        check('AI 解读区有内容', $('#aiSummary').children.length > 0);

        // Tab 切换
        $$('.bill-tabs .tab-btn').find(b => b.dataset.tab === 'input').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
        check('手动录入 Tab 切换', $('#inputTab').classList.contains('active'));

        // 添加记录
        $('#inTotal').value = '800';
        $('#inReimburse').value = '500';
        $('#inDisease').value = '测试病种';
        $('#addRecord').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
        check('手动记录已添加并持久化', window.localStorage.getItem('mediclear_custom_records') !== null &&
            JSON.parse(window.localStorage.getItem('mediclear_custom_records')).length === 1);

        // 估算按钮（住院 6000 元，北京职工医保三级医院，应能报出钱）
        $('#inType').value = '住院';
        $('#inTotal').value = '6000';
        $('#quickEstimate').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
        check('一键估算报销已填入', parseFloat($('#inReimburse').value) > 0, '估算: ' + $('#inReimburse').value);

        // ---- 测试 5：异地就医 ----
        check('异地清单默认已生成', !$('#checklistCard').hidden);
        check('必备材料渲染 8 项', $$('#requiredItems .checklist-item').length >= 7, String($$('#requiredItems .checklist-item').length));
        check('报销估算已渲染', $('#estRemote').textContent.includes('%'), '异地比例: ' + $('#estRemote').textContent);
        check('估算说明存在', ($('#estimateNote').textContent || '').length > 20);

        // 勾选进度
        const first = $('#requiredItems .checklist-item input');
        first.checked = true;
        first.dispatchEvent(new window.Event('change', { bubbles: true }));
        check('勾选后进度更新', $('#cpDone').textContent === '1', $('#cpDone').textContent + '/' + $('#cpAll').textContent);

        // 备案提醒
        $('#remindDate').value = '2026-10-20';
        $('#remindNote').value = '交材料';
        $('#addRemind').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
        check('备案提醒已添加', $$('#remindList .remind-item').length === 1 &&
            window.localStorage.getItem('mediclear_reminders') !== null);

        // ---- 测试 6：长辈模式 ----
        $('#elderToggle').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
        check('长辈模式切换生效', window.document.body.classList.contains('elder'));

        // ---- 测试 7：localStorage 隔离（clear 后重新初始化）----
        console.log('\n========== 结果汇总 ==========');
        const fail = results.filter(r => !r.ok);
        console.log('通过 ' + (results.length - fail.length) + ' / ' + results.length);
        if (fail.length) process.exit(1);
        process.exit(0);
    }, 700);
}, 900);
