/**
 * 医算清 MediClear-AI · 主应用逻辑 v2
 * 纯前端实现：所有计算在本地完成，不上传任何数据
 * 依赖：calculator.js（MediClear.quickCalculate / ReimbursementCalculator / generateExplanation）
 */

(function () {
    'use strict';

    /* ==========================================================
       0. 通用工具
       ========================================================== */
    const $ = (s, r) => (r || document).querySelector(s);
    const $$ = (s, r) => Array.prototype.slice.call((r || document).querySelectorAll(s));
    // 可重新求值：ECharts 走异步回退时，加载完成后需要把降级占位改回真图表
    let HAS_ECHARTS = typeof window.echarts !== 'undefined';

    const money = n => (Math.round(n * 100) / 100).toLocaleString('zh-CN');
    const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

    function toast(msg, type) {
        const wrap = $('#toastWrap');
        if (!wrap) return;
        const el = document.createElement('div');
        el.className = 'toast' + (type ? ' ' + type : '');
        el.textContent = msg;
        wrap.appendChild(el);
        setTimeout(() => {
            el.style.transition = 'opacity .3s, transform .3s';
            el.style.opacity = '0';
            el.style.transform = 'translateY(10px)';
            setTimeout(() => el.remove(), 320);
        }, 2200);
    }

    /** 数字滚动动画 */
    function animateNumber(el, to, opt) {
        if (!el) return;
        opt = opt || {};
        const dur = opt.duration || 900;
        const from = 0;
        const t0 = performance.now();
        const fmt = opt.format || (v => money(v));
        function tick(t) {
            const p = clamp((t - t0) / dur, 0, 1);
            const eased = 1 - Math.pow(1 - p, 3);
            el.textContent = fmt(from + (to - from) * eased);
            if (p < 1) requestAnimationFrame(tick);
        }
        requestAnimationFrame(tick);
    }

    /** 打字机 */
    function typeWriter(el, paragraphs, speed) {
        if (!el) return;
        speed = speed || 14;
        el.innerHTML = '';
        let pi = 0, ci = 0, cur = null;
        let timer = null;
        function step() {
            if (pi >= paragraphs.length) return;
            if (!cur) {
                cur = document.createElement('p');
                el.appendChild(cur);
                ci = 0;
            }
            const text = paragraphs[pi];
            if (ci < text.length) {
                cur.textContent += text[ci++];
                timer = setTimeout(step, speed);
            } else {
                cur = null;
                pi++;
                timer = setTimeout(step, 180);
            }
        }
        step();
        return () => clearTimeout(timer);
    }

    /** 图表容器降级提示 */
    function chartFallback(el, tip) {
        if (!el) return;
        el.innerHTML = '<div class="chart-fallback">📉 ' + (tip || '图表组件加载失败，请检查网络后刷新') + '</div>';
    }

    const charts = {};
    function initChart(id) {
        const el = document.getElementById(id);
        if (!el) return null;
        if (!HAS_ECHARTS) { chartFallback(el); return null; }
        if (charts[id]) return charts[id];
        el.innerHTML = '';   // 清掉降级占位，避免和 echarts 画布叠在一起
        charts[id] = echarts.init(el, null, { renderer: 'canvas' });
        return charts[id];
    }

    /** ECharts 迟到（异步回退加载成功）时的恢复入口：把占位换回真图表 */
    function reinitCharts() {
        HAS_ECHARTS = typeof window.echarts !== 'undefined';
        if (!HAS_ECHARTS) return;

        // DOM 尚未初始化完成时先记下，启动流程末尾会再调一次
        if (!window.__MEDICLEAR_INITED__) return;

        Object.keys(charts).forEach(k => {
            try { charts[k] && charts[k].dispose(); } catch (e) { /* 忽略 */ }
            delete charts[k];
        });
        ['resultDonut', 'monthlyChart', 'typeChart', 'visitChart', 'deptChart', 'customChart'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.innerHTML = '';
        });

        try {
            if (lastResult && lastResult.summary) {
                const s = lastResult.summary;
                renderDonut(s.totalReimburse, s.personalPay, s.reimburseRatio);
            }
        } catch (e) { /* 忽略 */ }
        try { renderMonthly(SAMPLE_RECORDS); } catch (e) { /* 忽略 */ }
        try { renderTypePie(SAMPLE_RECORDS); } catch (e) { /* 忽略 */ }
        try { renderVisitPie(SAMPLE_RECORDS); } catch (e) { /* 忽略 */ }
        try { renderDept(SAMPLE_RECORDS); } catch (e) { /* 忽略 */ }
        try { renderCustom(); } catch (e) { /* 忽略 */ }
    }
    window.__MEDICLEAR_ON_ECHARTS__ = reinitCharts;

    const FONT = '-apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif';
    const AXIS_STYLE = {
        fontFamily: FONT, fontSize: 12, color: '#7ba6c4'
    };

    /* ==========================================================
       1. 全局 UI：导航 / 长辈模式 / 滚动动画 / Hero
       ========================================================== */
    function initGlobal() {
        // ============ 页面切换路由（切页而非滚动） ============
        // 页面以 iframe srcdoc 方式内联部署时，文档 URL 是 about:srcdoc，
        // 写 location.hash 会按整页导航把 iframe 跳走 —— 因此 iframe 内只用内存态切页；
        // 独立打开（本地/正式托管）时才写 hash，支持直链与浏览器前进后退。
        const inIframe = (() => { try { return window.parent !== window; } catch (e) { return true; } })();
        // 锚点 id → 页面 id（top/features 是首页内的滚动锚点）
        const ANCHOR_PAGE = {
            top: 'home', features: 'home', home: 'home',
            calculator: 'calculator', bill: 'bill',
            relocation: 'relocation', agent: 'agent', about: 'about'
        };
        // 页面 id → 页面根元素（首页是 Hero+功能介绍 的包裹层，其余四页直接是 section）
        const PAGE_EL = {
            home: 'page-home', calculator: 'calculator',
            bill: 'bill', relocation: 'relocation', agent: 'agent', about: 'about'
        };
        let currentPage = '';

        const resizeAllCharts = () => {
            Object.keys(charts).forEach(k => { try { charts[k] && charts[k].resize(); } catch (e) { /* 忽略 */ } });
        };

        /** 切换到指定页；返回是否真的发生了切换（silent 用于初始渲染，不写 hash 不滚动） */
        function setPage(id, opts) {
            opts = opts || {};
            if (ANCHOR_PAGE[id] === undefined) id = 'home';
            const changed = id !== currentPage;
            currentPage = id;
            Object.keys(PAGE_EL).forEach(k => {
                const el = document.getElementById(PAGE_EL[k]);
                if (el) el.classList.toggle('active', k === id);
            });
            $$('.nav-link').forEach(a => a.classList.toggle('active', a.getAttribute('href') === '#' + id));
            $$('.mn-item').forEach(a => a.classList.toggle('active', a.getAttribute('href') === '#' + id));
            if (!opts.silent && !inIframe) {
                try { if (location.hash !== '#' + id) location.hash = id; } catch (e) { /* 忽略 */ }
            }
            if (!opts.silent) {
                window.scrollTo(0, 0);
                const header = $('#header');
                if (header) header.classList.remove('scrolled');
                // 账单页含 ECharts：display:none 下初始化的实例尺寸为 0，恢复显示后需要重算
                if (id === 'bill') setTimeout(resizeAllCharts, 80);
            }
            return changed;
        }

        // 独立打开时支持浏览器前进/后退
        window.addEventListener('hashchange', () => {
            if (inIframe) return;
            const id = (location.hash || '').slice(1);
            if (ANCHOR_PAGE[id] !== undefined) setPage(id);
        });

        // 页面内锚点统一接管：先切页；同页锚点（如首页内「功能介绍」）平滑滚动定位
        document.addEventListener('click', function (e) {
            const a = e.target && e.target.closest ? e.target.closest('a[href^="#"]') : null;
            if (!a) return;
            const id = (a.getAttribute('href') || '').slice(1) || 'home';
            e.preventDefault();
            const page = ANCHOR_PAGE[id] || 'home';
            const stayed = !setPage(page);
            if (stayed && id !== page) {
                const el = document.getElementById(id);
                if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
            } else if (!stayed && page === 'home' && id === 'features') {
                setTimeout(() => {
                    const el = document.getElementById('features');
                    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }, 80);
            }
        }, true);

        // 城市下拉
        const cities = (window.MediClear && MediClear.getSupportedCities) ? MediClear.getSupportedCities() : [];
        const opts = cities.map(c => '<option value="' + c.code + '">' + c.name + '</option>').join('');
        const c1 = $('#city'), c2 = $('#fromCity'), c3 = $('#toCity');
        if (c1) { c1.innerHTML = opts; c1.value = 'beijing'; }
        if (c2) { c2.innerHTML = opts; c2.value = 'beijing'; }
        if (c3) { c3.innerHTML = opts; c3.value = 'shanghai'; }

        // Header 阴影
        const header = $('#header');
        const onScroll = () => { if (header) header.classList.toggle('scrolled', window.scrollY > 12); };
        window.addEventListener('scroll', onScroll, { passive: true });
        onScroll();

        // 长辈模式
        const elderBtn = $('#elderToggle');
        const applyElder = on => {
            document.body.classList.toggle('elder', on);
            if (elderBtn) elderBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
            try { localStorage.setItem('mediclear_elder', on ? '1' : '0'); } catch (e) { }
        };
        let elderOn = false;
        try { elderOn = localStorage.getItem('mediclear_elder') === '1'; } catch (e) { }
        applyElder(elderOn);
        if (elderBtn) elderBtn.addEventListener('click', () => {
            elderOn = !elderOn;
            applyElder(elderOn);
            toast(elderOn ? '已切换到长辈模式，字号更大更清楚' : '已恢复标准模式', 'ok');
            setTimeout(() => Object.keys(charts).forEach(k => charts[k] && charts[k].resize()), 60);
        });

        // 滚动入场
        if ('IntersectionObserver' in window) {
            const io = new IntersectionObserver(entries => {
                entries.forEach(en => { if (en.isIntersecting) { en.target.classList.add('in'); io.unobserve(en.target); } });
            }, { threshold: 0.08, rootMargin: '0px 0px -40px 0px' });
            $$('.reveal').forEach(el => io.observe(el));
        } else {
            $$('.reveal').forEach(el => el.classList.add('in'));
        }

        // 按钮光晕跟随
        $$('.btn').forEach(btn => {
            btn.addEventListener('mousemove', e => {
                const r = btn.getBoundingClientRect();
                btn.style.setProperty('--mx', ((e.clientX - r.left) / r.width * 100) + '%');
                btn.style.setProperty('--my', ((e.clientY - r.top) / r.height * 100) + '%');
            });
        });

        initHero();

        // 初始页：独立打开时优先读 hash 直链，默认首页；silent 渲染不写 hash 不滚动
        let initial = 'home';
        if (!inIframe) {
            try {
                const h = (location.hash || '').slice(1);
                if (ANCHOR_PAGE[h] !== undefined) initial = h;
            } catch (e) { /* 忽略 */ }
        }
        setPage(initial, { silent: true });
    }

    /** Hero：打字机 + 环形进度 + 数字滚动 */
    function initHero() {
        const phrases = ['拍照发票，3 秒算出能报多少', '账单看得懂，钱花在哪一目了然', '异地就医不跑冤枉路', '数据只在你手机里，绝不上传'];
        const typing = $('#heroTyping');
        if (typing) {
            let pi = 0, ci = 0, deleting = false;
            (function loop() {
                const text = phrases[pi];
                if (!deleting) {
                    typing.textContent = text.slice(0, ++ci);
                    if (ci >= text.length) { deleting = true; return setTimeout(loop, 1800); }
                    return setTimeout(loop, 90);
                } else {
                    typing.textContent = text.slice(0, --ci);
                    if (ci <= 0) { deleting = false; pi = (pi + 1) % phrases.length; return setTimeout(loop, 400); }
                    return setTimeout(loop, 45);
                }
            })();
        }

        // 数字统计
        $$('.hero-stat .counter').forEach(el => {
            animateNumber(el, parseFloat(el.dataset.to) || 0, { duration: 1200, format: v => Math.round(v) });
        });

        // 环形图（示例 75.7%）
        setTimeout(() => {
            const pct = 75.7;
            const ring = $('#heroRing');
            if (ring) ring.style.strokeDashoffset = String(314 * (1 - pct / 100));
            animateNumber($('#heroRingPct'), pct, { duration: 1400, format: v => v.toFixed(1) + '%' });
        }, 300);
    }

    /* ==========================================================
       2. 功能一：报销计算器
       ========================================================== */
    const CITY_NAME = {
        beijing: '北京', shanghai: '上海', guangzhou: '广州', shenzhen: '深圳',
        hangzhou: '杭州', nanjing: '南京', wuhan: '武汉', chengdu: '成都',
        chongqing: '重庆', xian: '西安', shenyang: '沈阳', jinan: '济南',
        zhengzhou: '郑州', changsha: '长沙', national: '全国通用'
    };
    let lastResult = null;
    let feeMode = 'quick';

    function initCollapse() {
        const heads = [
            { head: '#whyLessHead', body: '#whyLessBody' },
            { head: '#suggestHead', body: '#suggestBody' },
        ];
        heads.forEach(pair => {
            const h = $(pair.head);
            const b = $(pair.body);
            if (!h || !b) return;
            h.addEventListener('click', () => {
                h.classList.toggle('collapsed');
                b.classList.toggle('collapsed');
            });
        });
    }

    function initCalculator() {
        initSliders();
        initOcr();

        // 模式切换
        $$('.mode-btn').forEach(btn => {
            btn.addEventListener('click', () => setFeeMode(btn.dataset.mode));
        });

        // 快捷金额
        $$('#feeChips .chip').forEach(chip => {
            chip.addEventListener('click', () => {
                $('#totalFee').value = chip.dataset.fee;
                toast('已填入 ' + money(+chip.dataset.fee) + ' 元');
            });
        });

        // 明细
        const addBtn = $('#addItemBtn');
        if (addBtn) addBtn.addEventListener('click', () => addDetailRow());
        const fillBtn = $('#fillDemo');
        if (fillBtn) fillBtn.addEventListener('click', () => {
            const demo = [
                { name: '床位费', amount: 1200, category: 'class_a' },
                { name: '手术费', amount: 4800, category: 'class_a' },
                { name: '西药费（乙类）', amount: 2600, category: 'class_b' },
                { name: '检查费', amount: 900, category: 'class_a' },
                { name: '进口耗材（自费）', amount: 500, category: 'self_pay' },
            ];
            $('#detailItems').innerHTML = '';
            demo.forEach(d => addDetailRow(d));
            toast('已填充示例费用明细', 'ok');
        });

        // 计算
        const calcBtn = $('#calcBtn');
        if (calcBtn) calcBtn.addEventListener('click', () => calculate());
        const feeInput = $('#totalFee');
        if (feeInput) feeInput.addEventListener('keypress', e => { if (e.key === 'Enter') calculate(); });
        ['#city', '#insuranceType', '#visitType', '#hospitalLevel'].forEach(s => {
            const el = $(s);
            if (el) el.addEventListener('change', () => { if (!$('#resultCard').hidden) calculate(true); });
        });

        // 分享 / 重新计算
        const shareBtn = $('#shareBtn');
        if (shareBtn) shareBtn.addEventListener('click', openShare);
        const recalcBtn = $('#recalcBtn');
        if (recalcBtn) recalcBtn.addEventListener('click', () => calculate());

        // 分享弹窗
        const modal = $('#shareModal');
        if (modal) {
            modal.addEventListener('click', e => { if (e.target.hasAttribute('data-close') || e.target.closest('[data-close]')) modal.hidden = true; });
            const copy = $('#copyShare');
            if (copy) copy.addEventListener('click', () => {
                const text = '我用「医算清」算了一笔医保报销：总费用 ¥' + $('#shareTotal').textContent +
                    '，预计报销 ¥' + $('#shareReimburse').textContent + '（' + $('#sharePct').textContent + '），自付 ¥' + $('#shareSelf').textContent + '。';
                // srcdoc 下 location.href 是 about:srcdoc，拼不出可分享的地址，退回线上地址
                let shareUrl = location.href;
                if (/^about:/.test(shareUrl)) {
                    try { shareUrl = parent.location.href; } catch (e) { shareUrl = ''; }
                }
                if (!shareUrl || /^about:/.test(shareUrl)) shareUrl = 'https://www.qmuse.cn/app/6004504047818135';
                const url = shareUrl.split('#')[0] + '?from=share';
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(text + '\n' + url)
                        .then(() => toast('结果已复制，去粘贴给家人吧', 'ok'))
                        .catch(() => toast('复制失败，请手动选择文本'));
                } else {
                    toast('当前浏览器不支持一键复制', 'warn');
                }
            });
            const save = $('#saveShare');
            if (save) save.addEventListener('click', () => toast('图片保存功能开发中，可先截图分享', 'warn'));
        }

        // 默认算一次
        setTimeout(() => calculate(true), 400);
    }

    function initSliders() {
        const ids = ['classARatio', 'classBRatio', 'selfPayRatio'];
        const els = ids.map(i => document.getElementById(i)).filter(Boolean);
        function sync() {
            const a = +els[0].value, b = +els[1].value, c = +els[2].value;
            $('#classARatioValue').textContent = a + '%';
            $('#classBRatioValue').textContent = b + '%';
            $('#selfPayRatioValue').textContent = c + '%';
            const sum = a + b + c;
            const warn = $('#sliderWarn');
            if (warn) {
                warn.hidden = (sum === 100);
                $('#sliderSum').textContent = sum + '%';
            }
        }
        els.forEach(el => el.addEventListener('input', sync));
        sync();
    }

    /** 录入模式切换（快速滑块 / 明细逐条） */
    function setFeeMode(mode) {
        feeMode = mode;
        $$('.mode-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));
        $('#modeQuick').classList.toggle('active', mode === 'quick');
        $('#modeDetail').classList.toggle('active', mode === 'detail');
    }

    /** OCR 演示数据（走与真实识别相同的「转明细」管线） */
    const OCR_DEMO_RESULT = {
        total: 12860,
        classA: 8460, classB: 2600, selfPay: 1800,
        isDemo: true,
        items: [
            { name: '床位费', amount: 1200, category: 'class_a' },
            { name: '治疗费', amount: 1560, category: 'class_a' },
            { name: '手术费', amount: 4800, category: 'class_a' },
            { name: '检查费', amount: 900, category: 'class_a' },
            { name: '西药费（乙类）', amount: 2600, category: 'class_b' },
            { name: '进口耗材（自费）', amount: 1800, category: 'self_pay' }
        ]
    };

    /** OCR：发票识别 → 解析 → 直接转「明细逐条录入」 */
    function initOcr() {
        const btn = $('#ocrBtn'), panel = $('#ocrPanel'), drop = $('#ocrDrop'),
            fileInput = $('#ocrFile'), preview = $('#ocrPreview'),
            previewImg = $('#ocrPreviewImg'), bar = $('#ocrProgressBar'),
            barText = $('#ocrProgressText'), resultBox = $('#ocrResult');
        if (!btn || !panel) return;

        btn.addEventListener('click', () => {
            panel.hidden = !panel.hidden;
            if (!panel.hidden) panel.scrollIntoView({ behavior: 'smooth', block: 'center' });
        });
        const cancel = $('#ocrCancel');
        if (cancel) cancel.addEventListener('click', () => { panel.hidden = true; });

        if (drop && fileInput) {
            drop.addEventListener('click', () => fileInput.click());
            ['dragover', 'dragenter'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
            ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
            drop.addEventListener('drop', e => {
                const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
                if (f) handleOcrFile(f);
            });
            fileInput.addEventListener('change', () => {
                if (fileInput.files && fileInput.files[0]) handleOcrFile(fileInput.files[0]);
                fileInput.value = '';
            });
        }

        // 面板展开时支持 Ctrl+V 粘贴发票截图
        document.addEventListener('paste', e => {
            if (panel.hidden || !e.clipboardData) return;
            const its = e.clipboardData.items || [];
            for (let i = 0; i < its.length; i++) {
                if (its[i].type && its[i].type.indexOf('image') === 0) {
                    const f = its[i].getAsFile();
                    if (f) { e.preventDefault(); handleOcrFile(f); }
                    break;
                }
            }
        });

        // 演示按钮
        const demo = $('#ocrDemo');
        if (demo) demo.addEventListener('click', () => {
            renderOcrResult(OCR_DEMO_RESULT, '演示数据（模拟识别）');
            applyOcrResult(OCR_DEMO_RESULT);
        });

        const escHtml = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

        function setProgress(label, pct) {
            if (bar) bar.style.width = Math.round(pct * 100) + '%';
            if (barText) barText.textContent = label;
        }

        function readAsDataURL(file) {
            return new Promise((resolve, reject) => {
                const r = new FileReader();
                r.onload = () => resolve(r.result);
                r.onerror = () => reject(new Error('读取图片失败'));
                r.readAsDataURL(file);
            });
        }

        async function handleOcrFile(file) {
            if (!file.type || file.type.indexOf('image') !== 0) { toast('请选择 JPG / PNG 格式的发票图片', 'warn'); return; }
            if (file.size > 10 * 1024 * 1024) { toast('图片超过 10MB，请压缩后再试', 'warn'); return; }
            let dataUrl;
            try { dataUrl = await readAsDataURL(file); } catch (err) { toast(err.message, 'warn'); return; }
            preview.hidden = false;
            resultBox.hidden = true;
            setProgress('正在加载识别引擎…', 0.02);
            try {
                if (!window.MediClearOCR) throw new Error('识别组件未加载');
                const res = await MediClearOCR.recognize(dataUrl, (label, pct) => setProgress(label, pct));
                setProgress('识别完成', 1);
                const parsed = MediClearOCR.parse(res.text);
                parsed.confidence = Math.round(res.confidence);
                if (!parsed.ok) {
                    preview.hidden = true;
                    resultBox.hidden = false;
                    resultBox.innerHTML =
                        '<div class="ocr-res-head">⚠️ 没能从图片中识别出有效金额</div>' +
                        '<div class="ocr-note">建议：正对发票拍摄、光线充足、避免反光；或点「看一眼识别效果」查看演示。</div>';
                    toast('未识别出有效内容，请换张更清晰的图片', 'warn');
                    return;
                }
                preview.hidden = true;
                renderOcrResult(parsed, '识别置信度 ' + Math.max(0, parsed.confidence) + '%');
                applyOcrResult(parsed);
            } catch (err) {
                preview.hidden = true;
                toast('识别失败：' + (err && err.message ? err.message : '请检查网络后重试'), 'warn');
            }
        }

        function renderOcrResult(parsed, headNote) {
            resultBox.hidden = false;
            let rows = '';
            if (parsed.total) rows += '<tr><td>费用总额</td><td>¥' + money(parsed.total) + '</td></tr>';
            if (parsed.items && parsed.items.length) {
                const cnt = t => parsed.items.filter(i => i.category === t).length;
                rows += '<tr><td>识别明细</td><td>' + parsed.items.length + ' 条（甲 ' + cnt('class_a') +
                    ' · 乙 ' + cnt('class_b') + ' · 自费 ' + cnt('self_pay') + '）</td></tr>';
                parsed.items.slice(0, 8).forEach(it => {
                    const tag = it.category === 'class_a' ? '甲' : it.category === 'class_b' ? '乙' : '自';
                    rows += '<tr><td>' + escHtml(it.name) + ' <i class="ocr-tag">' + tag + '</i></td><td>¥' + money(it.amount) + '</td></tr>';
                });
                if (parsed.items.length > 8) rows += '<tr><td colspan="2" style="color:var(--text-3)">…其余 ' + (parsed.items.length - 8) + ' 条见下方明细列表</td></tr>';
            }
            resultBox.innerHTML =
                '<div class="ocr-res-head">' + (parsed.isDemo ? '🧪 ' + (headNote || '演示数据') : '✅ 识别完成 · ' + (headNote || '')) + '</div>' +
                '<table>' + rows + '</table>' +
                '<div class="ocr-note">' + (parsed.items && parsed.items.length
                    ? '已自动转入下方「明细逐条录入」，类别为启发式估算，可逐条微调后再计算'
                    : '已填入总费用，可调整费用结构滑块后再计算') + '</div>' +
                (parsed.rawText ? '<details class="ocr-raw"><summary>查看识别原文</summary><pre>' + escHtml(parsed.rawText) + '</pre></details>' : '');
        }

        function applyOcrResult(parsed) {
            if (!parsed) return;
            if (parsed.items && parsed.items.length) {
                // 主路径：直接转「明细逐条录入」
                $('#detailItems').innerHTML = '';
                parsed.items.forEach(it => addDetailRow({ name: it.name, amount: it.amount, category: it.category }));
                setFeeMode('detail');
                $('#totalFee').value = parsed.total;   // 同步快速模式滑块，避免切回快速模式时是旧值
                calculate();
                toast('已识别 ' + parsed.items.length + ' 条明细，已转入逐条录入', 'ok');
            } else if (parsed.total > 0) {
                // 兜底：只认出总额 → 滑块快速模式
                const sum = (parsed.classA || 0) + (parsed.classB || 0) + (parsed.selfPay || 0);
                if (sum > 0) {
                    const a = Math.round(parsed.classA / sum * 100);
                    const b = Math.round(parsed.classB / sum * 100);
                    $('#classARatio').value = a;
                    $('#classBRatio').value = b;
                    $('#selfPayRatio').value = Math.max(0, 100 - a - b);
                    ['classARatio', 'classBRatio', 'selfPayRatio'].forEach(id => $('#' + id).dispatchEvent(new Event('input')));
                }
                $('#totalFee').value = parsed.total;
                setFeeMode('quick');
                calculate();
                toast('已识别总费用 ¥' + money(parsed.total) + '，已填入快速模式', 'ok');
            }
        }
    }

    /** 明细行 */
    function addDetailRow(data) {
        const wrap = $('#detailItems');
        if (!wrap) return;
        const d = data || { name: '', amount: '', category: 'class_a' };
        const row = document.createElement('div');
        row.className = 'detail-row';
        row.innerHTML =
            '<input type="text" class="form-input d-name" placeholder="费用项目，如：西药费" value="' + d.name + '">' +
            '<input type="number" class="form-input d-amount" placeholder="金额" min="0" value="' + (d.amount || '') + '">' +
            '<select class="form-select d-cat">' +
            '<option value="class_a"' + (d.category === 'class_a' ? ' selected' : '') + '>甲类</option>' +
            '<option value="class_b"' + (d.category === 'class_b' ? ' selected' : '') + '>乙类</option>' +
            '<option value="self_pay"' + (d.category === 'self_pay' ? ' selected' : '') + '>自费</option>' +
            '</select>' +
            '<button class="detail-del" title="删除">×</button>';
        row.querySelector('.detail-del').addEventListener('click', () => {
            row.remove();
            refreshDetailTotal();
            if (!$('#detailItems').children.length) $('#detailEmpty').hidden = false;
        });
        row.querySelector('.d-amount').addEventListener('input', refreshDetailTotal);
        wrap.appendChild(row);
        $('#detailEmpty').hidden = true;
        refreshDetailTotal();
    }

    function refreshDetailTotal() {
        const sum = getDetailItems().reduce((s, i) => s + i.amount, 0);
        const el = $('#detailTotal');
        if (el) el.textContent = money(sum);
        const empty = $('#detailEmpty');
        if (empty) empty.hidden = !!$('#detailItems').children.length;
    }

    function getDetailItems() {
        return $$('#detailItems .detail-row').map(r => ({
            name: r.querySelector('.d-name').value.trim() || '未命名项目',
            amount: Math.max(0, parseFloat(r.querySelector('.d-amount').value) || 0),
            category: r.querySelector('.d-cat').value
        })).filter(i => i.amount > 0);
    }

    /** 执行计算 */
    function calculate(silent) {
        const city = $('#city').value;
        const insuranceType = $('#insuranceType').value;
        const visitType = $('#visitType').value;
        const hospitalLevel = $('#hospitalLevel').value;

        let result;
        try {
            if (feeMode === 'detail') {
                const items = getDetailItems();
                if (!items.length) { toast('请先添加至少一条费用明细', 'warn'); return; }
                const calc = new MediClear.ReimbursementCalculator(city, insuranceType, visitType, hospitalLevel);
                result = calc.calculate(items);
                result.explanation = MediClear.generateExplanation(result);
                result.mode = 'detail';
            } else {
                const totalFee = parseFloat($('#totalFee').value) || 0;
                if (totalFee <= 0) { toast('请输入有效的医疗费用', 'warn'); return; }
                const a = +$('#classARatio').value, b = +$('#classBRatio').value, c = +$('#selfPayRatio').value;
                const sum = a + b + c || 1;
                result = MediClear.quickCalculate(city, insuranceType, totalFee, visitType, hospitalLevel, a / sum, b / sum, c / sum);
                result.explanation = MediClear.generateExplanation(result);
                result.mode = 'quick';
            }
        } catch (err) {
            toast('计算出错：' + err.message, 'warn');
            return;
        }

        // 加载态
        const btn = $('#calcBtn');
        const label = btn && btn.querySelector('.btn-label');
        const spinner = btn && btn.querySelector('.btn-spinner');
        if (btn) {
            btn.disabled = true;
            if (label) label.textContent = '计算中…';
            if (spinner) spinner.hidden = false;
        }
        setTimeout(() => {
            if (btn) { btn.disabled = false; if (label) label.textContent = '立即计算'; if (spinner) spinner.hidden = true; }
            lastResult = result;
            renderResult(result);
            if (!silent) $('#resultCard').scrollIntoView({ behavior: 'smooth', block: 'start' });
        }, 480);
    }

    function renderResult(r) {
        const s = r.summary, st = r.categoryStats;
        const card = $('#resultCard');
        card.hidden = false;

        // 数字滚动
        animateNumber($('#resultTotalFee'), s.totalFee, { duration: 900 });
        animateNumber($('#resultReimburse'), s.totalReimburse, { duration: 1100 });
        animateNumber($('#resultPersonalPay'), s.personalPay, { duration: 1000 });

        // 元信息
        $('#metaDeductible').textContent = '¥' + money(s.deductible);
        $('#metaBase').textContent = '¥' + money(s.reimburseBase);
        $('#metaSerious').textContent = r.seriousIllness && r.seriousIllness.eligible ? '¥' + money(s.seriousReimburse) : '未触发';
        $('#resultRatio').textContent = s.reimburseRatio + '%';
        $('#reimburseNote').textContent = s.seriousReimburse > 0
            ? '含基本医保 ¥' + money(s.basicReimburse) + ' + 大病保险 ¥' + money(s.seriousReimburse)
            : '基本医保支付';

        // 分类条
        const total = s.totalFee || 1;
        const setBar = (bar, val, v) => {
            $(bar).style.width = (val / total * 100) + '%';
            $(v).textContent = '¥' + money(val);
        };
        setBar('#barClassA', st.class_a.total, '#valClassA');
        setBar('#barClassB', st.class_b.total, '#valClassB');
        setBar('#barSelfPay', st.self_pay.total, '#valSelfPay');

        // 解释（全国通用等参考政策先挂一条免责声明）
        const disc = MediClear.getCityDisclaimer ? MediClear.getCityDisclaimer($('#city').value) : '';
        $('#explanationBox').textContent = (disc ? '⚠ ' + disc + '\n\n' : '') + (r.explanation || '');

        // 为什么报得少
        const whyLess = MediClear.explainWhyLess ? MediClear.explainWhyLess(r) : [];
        const whyLessSection = $('#whyLessSection');
        const reasonList = $('#reasonList');
        if (whyLess && whyLess.length > 0) {
            whyLessSection.hidden = false;
            reasonList.innerHTML = whyLess.map(item => `
                <div class="reason-item ${item.severity || 'medium'}">
                    <div class="reason-item-title">
                        <span class="sev-tag ${item.severity || 'medium'}">${item.severity === 'high' ? '影响大' : item.severity === 'low' ? '影响小' : '有影响'}</span>
                        ${item.reason}
                    </div>
                    <div class="reason-item-desc">${item.detail}</div>
                    ${item.amount > 0 ? `<div class="reason-item-amount">涉及金额约 ¥${money(item.amount)}</div>` : ''}
                </div>
            `).join('');
        } else {
            whyLessSection.hidden = true;
        }

        // 怎么多报
        const suggestions = MediClear.suggestImprovements ? MediClear.suggestImprovements(r) : [];
        const suggestSection = $('#suggestSection');
        const suggestList = $('#suggestList');
        if (suggestions && suggestions.length > 0) {
            suggestSection.hidden = false;
            suggestList.innerHTML = suggestions.map(item => `
                <div class="suggest-item">
                    <div class="suggest-item-title">${item.action}</div>
                    <div class="suggest-item-desc">${item.detail}</div>
                    <div class="suggest-item-meta">
                        <span class="${item.confidence === 'high' ? 'conf-high' : 'conf-med'}">
                            ${item.confidence === 'high' ? '✓ 可行性高' : '○ 仅供参考'}
                        </span>
                    </div>
                </div>
            `).join('');
        } else {
            suggestSection.hidden = true;
        }

        // 环形图
        renderDonut(s.totalReimburse, s.personalPay, s.reimburseRatio);
    }

    function renderDonut(reimburse, personal, ratio) {
        animateNumber($('#donutPct'), ratio, { duration: 1100, format: v => v.toFixed(1) + '%' });
        const chart = initChart('resultDonut');
        if (!chart) return;
        chart.setOption({
            animationDuration: 900,
            animationEasing: 'cubicOut',
            tooltip: { trigger: 'item', formatter: p => p.name + '<br/>¥' + money(p.value) + '（' + p.percent + '%）' },
            legend: {
                bottom: 0, itemWidth: 10, itemHeight: 10, itemGap: 18,
                textStyle: { fontFamily: FONT, fontSize: 12, color: '#3f7fae' }
            },
            series: [{
                type: 'pie',
                radius: ['58%', '80%'],
                center: ['50%', '46%'],
                avoidLabelOverlap: true,
                itemStyle: { borderColor: '#fff', borderWidth: 3, borderRadius: 6 },
                label: { show: false },
                labelLine: { show: false },
                data: [
                    { value: Math.round(reimburse), name: '医保报销', itemStyle: { color: '#2eb887' } },
                    { value: Math.round(personal), name: '个人自付', itemStyle: { color: '#e8b04c' } }
                ]
            }]
        }, true);
    }

    /** 分享弹窗 */
    function openShare() {
        if (!lastResult) { toast('请先完成一次计算', 'warn'); return; }
        const s = lastResult.summary;
        const modal = $('#shareModal');
        $('#sharePct').textContent = s.reimburseRatio + '%';
        $('#shareTotal').textContent = '¥' + money(s.totalFee);
        $('#shareReimburse').textContent = '¥' + money(s.totalReimburse);
        $('#shareSelf').textContent = '¥' + money(s.personalPay);
        $('#shareDate').textContent = new Date().toLocaleDateString('zh-CN');
        drawFakeQr($('#shareQr'), 'mediclear://result/' + Math.round(s.totalReimburse));
        modal.hidden = false;
    }

    /** 生成装饰性二维码图形（非真实编码，仅用于占位展示） */
    function drawFakeQr(el, seed) {
        if (!el) return;
        let h = 0;
        for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) >>> 0;
        const n = 15, cell = 6;
        let cells = '';
        const rnd = (i) => ((h >> (i % 20)) + i * 7) % 3 === 0;
        for (let y = 0; y < n; y++) {
            for (let x = 0; x < n; x++) {
                const corner = (x < 4 && y < 4) || (x > n - 5 && y < 4) || (x < 4 && y > n - 5);
                const isFinderEdge = corner && (x === 0 || y === 0 || x === n - 1 || y === n - 1 || x === 3 || y === 3 || x === n - 4 || y === n - 4);
                if (corner) { if (isFinderEdge) cells += '<rect x="' + x * cell + '" y="' + y * cell + '" width="' + cell + '" height="' + cell + '" fill="#164f7d"/>'; continue; }
                if (rnd(x * n + y)) cells += '<rect x="' + x * cell + '" y="' + y * cell + '" width="' + cell + '" height="' + cell + '" fill="#164f7d"/>';
            }
        }
        el.innerHTML = '<svg viewBox="0 0 ' + n * cell + ' ' + n * cell + '" width="100%" height="100%">' + cells + '</svg>';
    }

    /* ==========================================================
       3. 功能二：账单分析
       ========================================================== */
    const SAMPLE_RECORDS = [
        { date: '2026-01-08', dept: '内科', disease: '上呼吸道感染', type: '门诊', total: 320, reimburse: 180 },
        { date: '2026-02-14', dept: '皮肤科', disease: '过敏性皮炎', type: '门诊', total: 150, reimburse: 80 },
        { date: '2026-03-03', dept: '骨科', disease: '右踝骨折', type: '住院', total: 2800, reimburse: 1960 },
        { date: '2026-04-19', dept: '口腔科', disease: '牙周炎治疗', type: '门诊', total: 420, reimburse: 250 },
        { date: '2026-05-06', dept: '内科', disease: '慢性胃炎复查', type: '门诊', total: 180, reimburse: 95 },
        { date: '2026-06-11', dept: '眼科', disease: '视疲劳 / 干眼症', type: '门诊', total: 560, reimburse: 320 },
        { date: '2026-07-02', dept: '儿科', disease: '小儿感冒（家属）', type: '门诊', total: 220, reimburse: 120 },
        { date: '2026-08-21', dept: '中医科', disease: '颈椎病推拿', type: '门诊', total: 380, reimburse: 210 },
        { date: '2026-09-05', dept: '外科', disease: '阑尾炎手术', type: '住院', total: 1500, reimburse: 900 },
        { date: '2026-10-02', dept: '内科', disease: '高血压随访', type: '门诊', total: 280, reimburse: 150 },
        { date: '2026-11-17', dept: '妇科', disease: '常规体检', type: '门诊', total: 450, reimburse: 260 },
        { date: '2026-12-09', dept: '急诊科', disease: '急性肠胃炎', type: '门诊', total: 320, reimburse: 180 }
    ];

    let customRecords = [];
    const LS_CUSTOM = 'mediclear_custom_records';

    function initBillAnalysis() {
        try {
            const raw = localStorage.getItem(LS_CUSTOM);
            if (raw) customRecords = JSON.parse(raw) || [];
        } catch (e) { customRecords = []; }

        // Tab 切换
        $$('.bill-tabs .tab-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                $$('.bill-tabs .tab-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                const tab = btn.dataset.tab;
                $('#sampleTab').classList.toggle('active', tab === 'sample');
                $('#inputTab').classList.toggle('active', tab === 'input');
                setTimeout(() => Object.keys(charts).forEach(k => charts[k] && charts[k].resize()), 120);
                if (tab === 'input') renderCustom();
            });
        });

        renderSample(SAMPLE_RECORDS);
        renderCustom();

        const regen = $('#regenAi');
        if (regen) regen.addEventListener('click', () => generateAiSummary(SAMPLE_RECORDS));

        const addRec = $('#addRecord');
        if (addRec) addRec.addEventListener('click', addCustomRecord);
        const est = $('#quickEstimate');
        if (est) est.addEventListener('click', () => {
            const total = parseFloat($('#inTotal').value) || 0;
            if (total <= 0) { toast('请先填写总费用', 'warn'); return; }
            try {
                const r = MediClear.quickCalculate($('#city').value, $('#insuranceType').value, total,
                    $('#inType').value === '住院' ? 'inpatient' : 'outpatient', '三级医院', 0.6, 0.3, 0.1);
                $('#inReimburse').value = Math.round(r.summary.totalReimburse);
                if (r.summary.totalReimburse <= 0) {
                    toast('按政策测算报销为 0：费用未达起付线或均为自费项目', 'warn');
                } else {
                    toast('已按当前参保城市估算报销金额', 'ok');
                }
            } catch (e) { toast('估算失败：' + e.message, 'warn'); }
        });

        window.addEventListener('resize', debounce(() => {
            Object.keys(charts).forEach(k => charts[k] && charts[k].resize());
        }, 200));
    }

    function debounce(fn, wait) {
        let t; return function () { clearTimeout(t); t = setTimeout(fn, wait); };
    }

    /** 示例数据渲染 */
    function renderSample(records) {
        const total = records.reduce((s, r) => s + r.total, 0);
        const reimburse = records.reduce((s, r) => s + r.reimburse, 0);
        animateNumber($('#billTotal'), total, { duration: 1000 });
        animateNumber($('#billReimbursed'), reimburse, { duration: 1100 });
        animateNumber($('#billSelfPay'), total - reimburse, { duration: 1000 });
        animateNumber($('#billCount'), records.length, { duration: 800, format: v => Math.round(v) });

        renderMonthly(records);
        renderTypePie(records);
        renderVisitPie(records);
        renderDept(records);
        renderRecordTable($('#recordTable'), records);
        generateAiSummary(records);
    }

    /** 月度趋势 */
    function renderMonthly(records) {
        const chart = initChart('monthlyChart');
        if (!chart) return;
        const months = [];
        for (let m = 1; m <= 12; m++) months.push(m + '月');
        const totals = new Array(12).fill(0), reims = new Array(12).fill(0);
        records.forEach(r => {
            const m = parseInt(String(r.date).split('-')[1], 10) - 1;
            if (m >= 0 && m < 12) { totals[m] += r.total; reims[m] += r.reimburse; }
        });
        chart.setOption({
            grid: { left: 8, right: 12, top: 30, bottom: 6, containLabel: true },
            tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, backgroundColor: 'rgba(22,79,125,.92)', textStyle: { color: '#fff', fontSize: 12 } },
            legend: { top: 0, right: 0, itemWidth: 10, itemHeight: 10, textStyle: { fontFamily: FONT, fontSize: 12, color: '#3f7fae' } },
            xAxis: { type: 'category', data: months, axisLine: { lineStyle: { color: '#cfe6f7' } }, axisTick: { show: false }, axisLabel: AXIS_STYLE },
            yAxis: { type: 'value', splitLine: { lineStyle: { color: '#e6f0f8' } }, axisLabel: AXIS_STYLE },
            series: [
                {
                    name: '总花费', type: 'bar', data: totals, barMaxWidth: 22,
                    itemStyle: { borderRadius: [5, 5, 0, 0], color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: '#32abfd' }, { offset: 1, color: '#8fd8f7' }]) },
                    animationDelay: i => i * 40
                },
                {
                    name: '报销', type: 'bar', data: reims, barMaxWidth: 22,
                    itemStyle: { borderRadius: [5, 5, 0, 0], color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: '#2eb887' }, { offset: 1, color: '#bfead9' }]) },
                    animationDelay: i => i * 40 + 100
                },
                {
                    name: '自付', type: 'line', smooth: true, symbolSize: 6,
                    data: totals.map((t, i) => t - reims[i]),
                    lineStyle: { width: 2, color: '#e8a93f' }, itemStyle: { color: '#e8a93f' },
                    areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: 'rgba(232,169,63,.24)' }, { offset: 1, color: 'rgba(232,169,63,0)' }]) }
                }
            ]
        }, true);
    }

    /** 费用类型占比 */
    function renderTypePie(records) {
        const chart = initChart('typeChart');
        if (!chart) return;
        let a = 0, b = 0, c = 0;
        records.forEach(r => { a += r.total * 0.55; b += r.total * 0.30; c += r.total * 0.15; });
        chart.setOption({
            tooltip: { trigger: 'item', formatter: '{b}<br/>¥{c}（{d}%）' },
            legend: { bottom: 0, itemWidth: 9, itemHeight: 9, textStyle: { fontFamily: FONT, fontSize: 12, color: '#3f7fae' } },
            series: [{
                type: 'pie', radius: ['48%', '72%'], center: ['50%', '44%'],
                itemStyle: { borderColor: '#fff', borderWidth: 2, borderRadius: 5 },
                label: { show: false }, labelLine: { show: false },
                data: [
                    { value: Math.round(a), name: '甲类', itemStyle: { color: '#2eb887' } },
                    { value: Math.round(b), name: '乙类', itemStyle: { color: '#e8b04c' } },
                    { value: Math.round(c), name: '自费', itemStyle: { color: '#ef7f6a' } }
                ]
            }]
        }, true);
    }

    /** 门诊 vs 住院 */
    function renderVisitPie(records) {
        const chart = initChart('visitChart');
        if (!chart) return;
        let out = 0, in_ = 0;
        records.forEach(r => { if (r.type === '住院') in_ += r.total; else out += r.total; });
        chart.setOption({
            tooltip: { trigger: 'item', formatter: '{b}<br/>¥{c}（{d}%）' },
            legend: { bottom: 0, itemWidth: 9, itemHeight: 9, textStyle: { fontFamily: FONT, fontSize: 12, color: '#3f7fae' } },
            series: [{
                type: 'pie', radius: ['48%', '72%'], center: ['50%', '44%'],
                itemStyle: { borderColor: '#fff', borderWidth: 2, borderRadius: 5 },
                label: { show: false }, labelLine: { show: false },
                data: [
                    { value: Math.round(out), name: '门诊', itemStyle: { color: '#32abfd' } },
                    { value: Math.round(in_), name: '住院', itemStyle: { color: '#3fa5ee' } }
                ]
            }]
        }, true);
    }

    /** 科室分布 */
    function renderDept(records) {
        const chart = initChart('deptChart');
        if (!chart) return;
        const map = {};
        records.forEach(r => {
            if (!map[r.dept]) map[r.dept] = { total: 0, diseases: {}, count: 0 };
            map[r.dept].total += r.total;
            map[r.dept].count++;
            map[r.dept].diseases[r.disease] = (map[r.dept].diseases[r.disease] || 0) + r.total;
        });
        const arr = Object.keys(map).map(k => ({ dept: k, ...map[k] })).sort((x, y) => x.total - y.total);
        chart.setOption({
            grid: { left: 8, right: 40, top: 10, bottom: 6, containLabel: true },
            tooltip: {
                trigger: 'item',
                formatter: p => {
                    const d = arr[p.dataIndex];
                    const top = Object.entries(d.diseases).sort((a, b) => b[1] - a[1]).slice(0, 3)
                        .map(x => x[0] + ' ¥' + money(x[1])).join('<br/>');
                    return '<b>' + d.dept + '</b><br/>共 ' + d.count + ' 次，合计 ¥' + money(d.total) + '<br/><span style="color:#7ba6c4">' + top + '</span>';
                }
            },
            xAxis: { type: 'value', splitLine: { lineStyle: { color: '#e6f0f8' } }, axisLabel: AXIS_STYLE },
            yAxis: { type: 'category', data: arr.map(a => a.dept), axisLine: { show: false }, axisTick: { show: false }, axisLabel: { ...AXIS_STYLE, color: '#3f7fae' } },
            series: [{
                type: 'bar', data: arr.map(a => Math.round(a.total)), barMaxWidth: 16,
                itemStyle: {
                    borderRadius: [0, 5, 5, 0],
                    color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [{ offset: 0, color: '#8ed8fa' }, { offset: 1, color: '#32abfd' }])
                },
                label: { show: true, position: 'right', fontSize: 11, color: '#7ba6c4', formatter: '¥{c}' }
            }]
        }, true);
    }

    /** AI 分析总结 */
    function generateAiSummary(records) {
        const box = $('#aiSummary');
        if (!box) return;
        box.innerHTML = '<div class="ai-loading"><span class="typing-dots"><i></i><i></i><i></i></span>AI 正在分析你的账单结构…</div>';
        $('#aiTags').innerHTML = '';

        const total = records.reduce((s, r) => s + r.total, 0);
        const reimburse = records.reduce((s, r) => s + r.reimburse, 0);
        const self = total - reimburse;
        const rate = total ? (reimburse / total * 100) : 0;

        const byMonth = new Array(12).fill(0);
        records.forEach(r => { const m = parseInt(String(r.date).split('-')[1], 10) - 1; if (m >= 0) byMonth[m] += r.total; });
        const peak = byMonth.indexOf(Math.max.apply(null, byMonth)) + 1;

        const byDept = {};
        records.forEach(r => { byDept[r.dept] = (byDept[r.dept] || 0) + r.total; });
        const deptArr = Object.entries(byDept).sort((a, b) => b[1] - a[1]);
        const topDept = deptArr[0];

        const inpatient = records.filter(r => r.type === '住院');
        const inSum = inpatient.reduce((s, r) => s + r.total, 0);
        const inRate = total ? inSum / total * 100 : 0;

        const variants = [
            '整体来看，你的医疗支出结构比较健康，小额门诊占大头，说明日常防护做得不错。',
            '从数据看，你的花费集中在少数几次大额就诊，属于典型的"少次数、高单价"型。',
            '你的账单呈现明显的季节性波动，建议关注高发月份的预防性支出。'
        ];
        const opening = variants[Math.floor(Math.random() * variants.length)];

        const paras = [
            opening + '全年共就诊 ' + records.length + ' 次，累计花费 ¥' + money(total) + '，其中医保报销 ¥' + money(reimburse) + '，个人自付 ¥' + money(self) + '，整体报销比例约 ' + rate.toFixed(1) + '%。',
            '花费最高的月份是 ' + peak + ' 月，支出最高科室是「' + topDept[0] + '」，累计 ¥' + money(topDept[1]) + '，占全年总花费的 ' + (topDept[1] / total * 100).toFixed(1) + '%。住院支出占比 ' + inRate.toFixed(1) + '%，' + (inRate > 50 ? '大额住院是主要压力来源' : '以日常门诊为主') + '。',
            rate >= 70
                ? '你的报销比例处于较好水平。建议继续保持：优先选择医保定点机构、主动办理异地就医备案、门诊优先使用医保目录内药品。'
                : '你的报销比例还有提升空间。三个可落地的动作：① 就诊前确认医院是否为医保定点；② 询问医生是否有医保目录内替代药；③ 长期异地居住务必先备案，通常能提升 10~20 个百分点。',
            '提醒：以上解读基于你录入的记录生成，数据全程保存在本机，未上传任何服务器。结果仅供参考，实际以医保部门核定为准。'
        ];

        const stop = typeWriter(box, paras, 12);

        const tags = [
            { t: '年均就诊 ' + records.length + ' 次', c: '' },
            { t: '报销比例 ' + rate.toFixed(0) + '%', c: rate >= 70 ? 'good' : 'warn' },
            { t: '花费峰值 ' + peak + ' 月', c: '' },
            { t: '重点科室 · ' + topDept[0], c: '' },
            { t: inRate > 50 ? '住院压力大' : '门诊为主', c: inRate > 50 ? 'warn' : 'good' }
        ];
        setTimeout(() => {
            $('#aiTags').innerHTML = tags.map(x => '<span class="ai-tag ' + x.c + '">' + x.t + '</span>').join('');
        }, 900);
        return stop;
    }

    /** 记录表格 */
    function renderRecordTable(el, records, onDelete) {
        if (!el) return;
        if (!records.length) { el.innerHTML = '<div class="remind-empty">暂无记录</div>'; return; }
        const rows = records.slice().sort((a, b) => String(a.date) < String(b.date) ? 1 : -1).map((r, i) =>
            '<tr>' +
            '<td>' + r.date + '</td>' +
            '<td>' + r.dept + '</td>' +
            '<td>' + r.disease + '</td>' +
            '<td><span class="rt-tag' + (r.type === '住院' ? ' in' : '') + '">' + r.type + '</span></td>' +
            '<td class="rt-num">¥' + money(r.total) + '</td>' +
            '<td class="rt-num" style="color:#2eb887">¥' + money(r.reimburse) + '</td>' +
            (onDelete ? '<td style="text-align:right"><button class="remind-del" data-i="' + i + '">×</button></td>' : '') +
            '</tr>').join('');
        el.innerHTML = '<table><thead><tr><th>日期</th><th>科室</th><th>病种</th><th>类型</th><th>总费用</th><th>报销</th>' + (onDelete ? '<th></th>' : '') + '</tr></thead><tbody>' + rows + '</tbody></table>';
        if (onDelete) {
            $$('button[data-i]', el).forEach(b => b.addEventListener('click', () => {
                onDelete(+b.dataset.i);
            }));
        }
    }

    /** 手动录入 */
    function addCustomRecord() {
        const rec = {
            date: $('#inDate').value || new Date().toISOString().slice(0, 10),
            dept: $('#inDept').value,
            disease: $('#inDisease').value.trim() || '未填写',
            type: $('#inType').value,
            total: Math.max(0, parseFloat($('#inTotal').value) || 0),
            reimburse: Math.max(0, parseFloat($('#inReimburse').value) || 0)
        };
        if (rec.total <= 0) { toast('请填写有效的总费用', 'warn'); return; }
        if (rec.reimburse > rec.total) { toast('报销金额不能大于总费用', 'warn'); return; }
        customRecords.push(rec);
        saveCustom();
        $('#inTotal').value = ''; $('#inReimburse').value = ''; $('#inDisease').value = '';
        renderCustom();
        toast('已添加记录，图表已更新', 'ok');
    }

    function saveCustom() {
        try { localStorage.setItem(LS_CUSTOM, JSON.stringify(customRecords)); } catch (e) { }
    }

    function renderCustom() {
        const has = customRecords.length > 0;
        $('#inputEmpty').hidden = has;
        $('#customArea').hidden = !has;
        if (!has) return;

        const total = customRecords.reduce((s, r) => s + r.total, 0);
        const reimburse = customRecords.reduce((s, r) => s + r.reimburse, 0);
        animateNumber($('#cTotal'), total, { duration: 800 });
        animateNumber($('#cReimburse'), reimburse, { duration: 900 });
        animateNumber($('#cSelf'), total - reimburse, { duration: 800 });
        animateNumber($('#cCount'), customRecords.length, { duration: 600, format: v => Math.round(v) });

        const chart = initChart('customChart');
        if (chart) {
            const byMonth = {};
            customRecords.forEach(r => {
                const k = String(r.date).slice(0, 7);
                byMonth[k] = (byMonth[k] || 0) + r.total;
            });
            const keys = Object.keys(byMonth).sort();
            chart.setOption({
                grid: { left: 8, right: 12, top: 20, bottom: 6, containLabel: true },
                tooltip: { trigger: 'axis', backgroundColor: 'rgba(22,79,125,.92)', textStyle: { color: '#fff', fontSize: 12 } },
                xAxis: { type: 'category', data: keys, axisLine: { lineStyle: { color: '#cfe6f7' } }, axisTick: { show: false }, axisLabel: AXIS_STYLE },
                yAxis: { type: 'value', splitLine: { lineStyle: { color: '#e6f0f8' } }, axisLabel: AXIS_STYLE },
                series: [{
                    type: 'bar', data: keys.map(k => Math.round(byMonth[k])), barMaxWidth: 26,
                    itemStyle: { borderRadius: [6, 6, 0, 0], color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: '#5cbdf0' }, { offset: 1, color: '#b3e2fc' }]) }
                }]
            }, true);
        }
        renderRecordTable($('#customTable'), customRecords, i => {
            customRecords.splice(i, 1);
            saveCustom();
            renderCustom();
            toast('已删除该记录');
        });
    }

    /* ==========================================================
       4. 功能三：异地就医
       ========================================================== */
    const LS_REMIND = 'mediclear_reminders';
    let reminders = [];
    let lastChecklist = null;

    // ============ AI 助手（D1：LLM 编排层 UI，数字全部来自本地引擎） ============
    const AGENT_TOOL_NAMES = {
        list_supported_cities: '城市列表',
        get_city_policy: '政策查询',
        calculate_reimbursement: '报销测算',
        explain_result: '结果解读',
        relocation_checklist: '异地清单',
        parse_invoice_text: '票据解析'
    };

    function initAgent() {
        const A = window.MediClearAgent;
        const msgs = $('#agentMsgs');
        const input = $('#agentInput');
        const sendBtn = $('#agentSend');
        const modal = $('#agentModal');
        if (!A || !msgs || !input || !sendBtn || !modal) return;

        let busy = false;

        function addMsg(role, text) {
            const wrap = document.createElement('div');
            wrap.className = 'msg ' + (role === 'me' ? 'msg-me' : 'msg-ai');
            const bubble = document.createElement('div');
            bubble.className = 'bubble';
            bubble.textContent = text;
            wrap.appendChild(bubble);
            msgs.appendChild(wrap);
            msgs.scrollTop = msgs.scrollHeight;
            return wrap;
        }

        function addToolTag(events) {
            if (!events || !events.length) return;
            const tag = document.createElement('div');
            tag.className = 'msg msg-tool';
            tag.textContent = '🔧 已调用本地引擎：' + events.map(e =>
                (AGENT_TOOL_NAMES[e.name] || e.name) + (e.ok ? '' : '（失败）')).join('、');
            msgs.appendChild(tag);
            msgs.scrollTop = msgs.scrollHeight;
        }

        function renderWelcome() {
            msgs.innerHTML = '';
            const configured = A.isConfigured();
            addMsg('ai', configured
                ? '你好！我是医保 AI 助手。报销测算、政策查询、异地清单都可以问我——所有金额都由本地引擎计算，我只负责理解你的问题与解释结果。'
                : '你好！我是医保 AI 助手，首次使用需要配置模型服务（API Key 只保存在本机浏览器）。点击下方按钮开始设置，智谱 GLM 有免费模型可选。');
            if (!configured) {
                const wrap = document.createElement('div');
                wrap.className = 'msg msg-ai';
                const btn = document.createElement('button');
                btn.className = 'btn btn-primary btn-sm';
                btn.type = 'button';
                btn.textContent = '⚙ 配置 AI 服务';
                btn.style.minHeight = '44px';
                btn.addEventListener('click', openModal);
                wrap.appendChild(btn);
                msgs.appendChild(wrap);
            }
        }

        function autoGrow() {
            input.style.height = 'auto';
            input.style.height = Math.min(input.scrollHeight, 120) + 'px';
        }

        async function doSend(preset) {
            const text = String(preset || input.value || '').trim();
            if (!text || busy) return;
            if (!A.isConfigured()) { openModal(); return; }
            busy = true;
            sendBtn.disabled = true;
            input.value = '';
            autoGrow();
            addMsg('me', text);
            const wait = addMsg('ai', '正在理解问题，并按需查询本地确定性引擎…');
            try {
                const res = await A.chat(text);
                wait.remove();
                if (res.ok) {
                    addToolTag(res.toolEvents);
                    addMsg('ai', res.reply);
                } else {
                    addMsg('ai', '出错了：' + res.error + '\n可点击右上角「⚙ 设置」检查服务配置。');
                }
            } catch (e) {
                wait.remove();
                addMsg('ai', '发生异常：' + e.message);
            } finally {
                busy = false;
                sendBtn.disabled = false;
            }
        }

        // ---- 设置弹层 ----
        function syncProviderUI() {
            const p = $('#cfgProvider').value;
            const isCustom = p === 'custom';
            $('#cfgBaseField').hidden = !isCustom;
            $('#cfgModelField').hidden = !isCustom;
            const preset = p && A.PROVIDERS[p];
            $('#cfgTip').textContent = preset && preset.keyUrl
                ? 'Key 申请地址：' + preset.keyUrl
                : (isCustom ? '填写任意 OpenAI 兼容的 chat/completions 完整地址' : '');
        }

        function openModal() {
            const s = A.getSettings();
            $('#cfgProvider').value = s.provider || '';
            $('#cfgKey').value = s.apiKey || '';
            $('#cfgBase').value = s.baseUrl || '';
            $('#cfgModel').value = s.model || '';
            syncProviderUI();
            modal.hidden = false;
        }

        $('#agentCfg').addEventListener('click', openModal);
        $('#cfgProvider').addEventListener('change', syncProviderUI);
        $('#cfgSave').addEventListener('click', () => {
            A.saveSettings({
                provider: $('#cfgProvider').value,
                apiKey: $('#cfgKey').value.trim(),
                baseUrl: $('#cfgBase').value.trim(),
                model: $('#cfgModel').value.trim()
            });
            modal.hidden = true;
            if (!A.isConfigured()) {
                toast('请先选择服务商并填写 API Key', 'warn');
                return;
            }
            toast('AI 服务配置已保存（仅存本机）', 'ok');
            renderWelcome();
        });
        $('#cfgClear').addEventListener('click', () => {
            A.saveSettings({ provider: '', apiKey: '', baseUrl: '', model: '' });
            modal.hidden = true;
            toast('已清除本机 AI 配置', 'ok');
            renderWelcome();
        });
        modal.addEventListener('click', e => { if (e.target === modal) modal.hidden = true; });

        // ---- 会话交互 ----
        $('#agentNew').addEventListener('click', () => {
            A.resetChat();
            renderWelcome();
        });
        $('#agentChips').addEventListener('click', e => {
            const chip = e.target.closest ? e.target.closest('.chip') : null;
            if (chip) doSend(chip.getAttribute('data-q'));
        });
        sendBtn.addEventListener('click', () => doSend());
        input.addEventListener('input', autoGrow);
        input.addEventListener('keydown', e => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                doSend();
            }
        });

        renderWelcome();
    }

    function initRelocation() {
        try {
            const raw = localStorage.getItem(LS_REMIND);
            if (raw) reminders = JSON.parse(raw) || [];
        } catch (e) { reminders = []; }

        const genBtn = $('#generateBtn');
        if (genBtn) genBtn.addEventListener('click', () => generateChecklist());

        const addRemind = $('#addRemind');
        if (addRemind) addRemind.addEventListener('click', addReminder);

        const exp = $('#exportBtn');
        if (exp) exp.addEventListener('click', exportChecklist);
        const prt = $('#printBtn');
        if (prt) prt.addEventListener('click', () => {
            toast('已调起打印，可在弹窗中选择「另存为 PDF」');
            setTimeout(() => window.print(), 400);
        });

        renderReminders();
        setTimeout(() => generateChecklist(true), 600);
    }

    /** 异地就医备案类型映射（下拉值 → MediClearRelocation 类型） */
    const REASON_MAP = {
        filed:     { type: 'long_term', label: '已备案',   cls: '',      reason_key: 'work' },
        transfer:  { type: 'transfer',  label: '转诊转院', cls: '',      reason_key: 'transfer' },
        emergency: { type: 'temp',      label: '临时急诊', cls: 'mid',   reason_key: 'travel' },
        travel:    { type: 'none',      label: '未备案',   cls: 'low',   reason_key: 'travel' },
    };

    function generateChecklist(silent) {
        const from = $('#fromCity').value;
        const to = $('#toCity').value;
        const visitType = $('#relocationVisitType').value;
        const reason = $('#relocationReason').value;
        const fee = Math.max(0, parseFloat($('#relocationFee').value) || 0);

        if (from === to) toast('参保地与就诊地相同，无需异地就医流程', 'warn');

        // 加载态
        const btn = $('#generateBtn');
        const label = btn && btn.querySelector('.btn-label');
        const spinner = btn && btn.querySelector('.btn-spinner');
        if (btn) { btn.disabled = true; if (label) label.textContent = '生成中…'; if (spinner) spinner.hidden = false; }

        setTimeout(() => {
            if (btn) { btn.disabled = false; if (label) label.textContent = '生成材料清单'; if (spinner) spinner.hidden = true; }
            doRenderChecklist(from, to, visitType, reason, fee, silent);
        }, 460);
    }

    function doRenderChecklist(from, to, visitType, reason, fee, silent) {
        const visitName = { inpatient: '住院', outpatient: '门诊', emergency: '急诊' }[visitType];
        const rm = REASON_MAP[reason] || REASON_MAP.travel;

        // ====== 报销比例估算（使用 MediClearRelocation 正式计算） ======
        let localRate = 0;
        let remoteRate = 0;
        let estAmount = 0;
        let rateAdjust = 0;
        let relocationResult = null;
        let checklistData = null;

        try {
            const insType = ($('#insuranceType') && $('#insuranceType').value) || 'employee';
            const calcVisitType = visitType === 'emergency' ? 'inpatient' : visitType;

            // 本地就医比例（用于对比）
            const localResult = MediClear.quickCalculate(from, insType, fee || 30000, calcVisitType, '三级医院', 0.55, 0.3, 0.15);
            localRate = localResult.summary.reimburseRatio;

            // 异地就医正式计算
            if (from !== to && window.MediClearRelocation) {
                const calc = new MediClearRelocation.RelocationCalculator(
                    from, to, insType, calcVisitType, rm.type
                );
                relocationResult = calc.calculate(fee || 30000, '三级医院', 0.55, 0.3, 0.15);
                remoteRate = relocationResult.summary.finalRate;
                estAmount = relocationResult.summary.totalReimburse;
                rateAdjust = relocationResult.summary.rateAdjust;

                // 材料清单（使用正式API）
                checklistData = MediClearRelocation.generateChecklist(from, to, visitType, rm.reason_key);
            }
        } catch (e) {
            console.warn('异地就医计算失败，使用估算值', e);
            remoteRate = Math.max(0, localRate * 0.8);
            estAmount = fee ? fee * remoteRate / 100 : 0;
        }

        $('#checklistRoute').textContent = CITY_NAME[from] + ' → ' + CITY_NAME[to];
        $('#checklistType').textContent = visitName;

        const tag = $('#estimateTag');
        tag.textContent = rm.label;
        tag.className = 'estimate-tag ' + rm.cls;

        setTimeout(() => {
            $('#ebFill').style.width = Math.min(95, Math.max(0, remoteRate)) + '%';
            animateNumber($('#estLocal'), localRate, { duration: 900, format: v => v.toFixed(1) + '%' });
            animateNumber($('#estRemote'), remoteRate, { duration: 1100, format: v => v.toFixed(1) + '%' });
            animateNumber($('#estAmount'), estAmount, { duration: 1100, format: v => '¥' + money(v) });
        }, 80);

        let noteText = rm.label + '：参保地基准比例约 ' + localRate.toFixed(1) + '%';
        if (rateAdjust !== 0) {
            noteText += '，异地执行比例调整 ' + (rateAdjust > 0 ? '+' : '') + rateAdjust.toFixed(1) + ' 个百分点';
        }
        noteText += '，预计可报销约 ¥' + money(estAmount);
        noteText += '。以上为基于政策模型的估算，实际比例以两地医保经办机构核定为准。';
        if (relocationResult && relocationResult.tips && relocationResult.tips.length) {
            noteText += ' ' + relocationResult.tips[relocationResult.tips.length - 1];
        }
        $('#estimateNote').textContent = noteText;

        // ====== 材料清单 ======
        let requiredItems, optionalItems, steps;
        if (checklistData) {
            requiredItems = checklistData.requiredItems.map(i => ({ text: i.text, desc: i.desc }));
            optionalItems = checklistData.optionalItems.map(i => ({ text: i.text, desc: i.desc }));
            steps = checklistData.steps.map(s => ({ title: s.title, desc: s.desc }));
        } else {
            // 降级：硬编码清单
            const same = from === to;
            requiredItems = [
                { text: '身份证原件及复印件', desc: '患者本人身份证，代办还需代办人身份证' },
                { text: '社会保障卡（医保卡）', desc: '确保已激活并处于正常参保状态' },
                { text: '医疗费用发票原件', desc: '需加盖医院收费专用章，遗失不补' },
                { text: '费用明细清单', desc: '需加盖医院章，包含各项费用明细' },
                { text: visitType === 'inpatient' ? '出院小结' : '诊断证明', desc: visitType === 'inpatient' ? '住院需提供出院小结，需加盖医院章' : '门诊需提供医生开具的诊断证明' },
                { text: '病历资料', desc: '门诊病历或住院病历复印件' },
                { text: '本人银行卡', desc: '用于接收报销款项，建议使用参保人本人账户' }
            ];
            if (visitType === 'inpatient') requiredItems.push({ text: '住院费用总汇总清单', desc: '住院处打印并盖章，含医保分类明细' });

            optionalItems = [
                { text: '异地就医备案凭证', desc: '已备案的需提供，可显著提高报销比例' },
                { text: '转诊转院证明', desc: '因病情需要转诊的需提供' },
                { text: '急诊证明', desc: '急诊就医需提供急诊相关证明' },
                { text: '出差 / 探亲证明', desc: '因工外出或探亲期间就医的相关证明' },
                { text: '代办人身份证', desc: '委托他人代办的需提供' },
                { text: '委托书', desc: '委托代办的建议准备书面委托书' }
            ];

            steps = [
                { title: '确认参保状态', desc: '确认医保处于正常参保缴费状态，无欠费' },
                { title: '办理异地就医备案（强烈推荐）', desc: '通过国家医保服务平台 APP 或参保地医保部门办理，备案后报销比例更高、可直接结算' },
                { title: '保存好所有就医凭证', desc: '妥善保管发票、费用清单、病历、出院小结等所有原始凭证' },
                { title: '准备报销材料', desc: '按上方清单逐项准备，建议复印一份留存' },
                { title: '提交报销申请', desc: '到参保地医保经办机构提交，或通过线上渠道上传材料' },
                { title: '等待审核到账', desc: '一般 15~30 个工作日完成审核，款项打入指定银行卡' }
            ];
            if (same) steps.unshift({ title: '注意：同城就医', desc: '参保地与就诊地相同，按本地流程直接结算即可，无需异地备案' });
        }

        renderChecklistItems('#requiredItems', requiredItems);
        renderChecklistItems('#optionalItems', optionalItems);

        $('#stepsList').innerHTML = steps.map((s, i) =>
            '<div class="step-item"><div class="step-number">' + (i + 1) + '</div><div class="step-content">' +
            '<div class="step-title">' + s.title + '</div><div class="step-desc">' + s.desc + '</div></div></div>').join('');

        lastChecklist = { from, to, visitType, reason, fee, visitName, requiredItems, optionalItems, steps, remoteRate, localRate, estAmount };

        const card = $('#checklistCard');
        card.hidden = false;
        updateProgress();
        if (!silent) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    function renderChecklistItems(sel, items) {
        const el = $(sel);
        if (!el) return;
        el.innerHTML = items.map(it =>
            '<label class="checklist-item"><input type="checkbox"><div class="item-text">' + it.text +
            '<div class="item-desc">' + it.desc + '</div></div></label>').join('');
        $$('.checklist-item input', el).forEach(cb => {
            cb.addEventListener('change', () => {
                cb.closest('.checklist-item').classList.toggle('done', cb.checked);
                updateProgress();
            });
        });
    }

    function updateProgress() {
        const all = $$('.checklist-item');
        const done = $$('.checklist-item.done').length;
        $('#cpDone').textContent = done;
        $('#cpAll').textContent = all.length;
        $('#cpFill').style.width = (all.length ? done / all.length * 100 : 0) + '%';
    }

    /** 备案提醒 */
    function addReminder() {
        const date = $('#remindDate').value;
        const note = $('#remindNote').value.trim();
        if (!date) { toast('请选择提醒日期', 'warn'); return; }
        reminders.push({ id: Date.now(), date, note: note || '异地就医材料办理提醒' });
        reminders.sort((a, b) => a.date < b.date ? -1 : 1);
        try { localStorage.setItem(LS_REMIND, JSON.stringify(reminders)); } catch (e) { }
        $('#remindNote').value = '';
        renderReminders();
        toast('提醒已添加（保存在本机）', 'ok');
    }

    function renderReminders() {
        const el = $('#remindList');
        if (!el) return;
        if (!reminders.length) {
            el.innerHTML = '<div class="remind-empty">还没有提醒，添加一个重要时间节点吧</div>';
            return;
        }
        const today = new Date().toISOString().slice(0, 10);
        el.innerHTML = reminders.map(r => {
            let cls = '', tail = '';
            if (r.date === today) { cls = 'due'; tail = ' · 就是今天'; }
            else if (r.date < today) { cls = 'over'; tail = ' · 已过期'; }
            return '<div class="remind-item ' + cls + '"><span class="remind-date">' + r.date + '</span>' +
                '<span class="remind-text">' + r.note + '<b style="color:#a5701a">' + tail + '</b></span>' +
                '<button class="remind-del" data-id="' + r.id + '">×</button></div>';
        }).join('');
        $$('.remind-del', el).forEach(b => b.addEventListener('click', () => {
            reminders = reminders.filter(x => String(x.id) !== b.dataset.id);
            try { localStorage.setItem(LS_REMIND, JSON.stringify(reminders)); } catch (e) { }
            renderReminders();
            toast('已删除提醒');
        }));
    }

    /** 导出清单 */
    function exportChecklist() {
        if (!lastChecklist) { toast('请先生成清单', 'warn'); return; }
        const c = lastChecklist;
        const lines = [];
        lines.push('医算清 · 异地就医报销材料清单');
        lines.push('生成时间：' + new Date().toLocaleString('zh-CN'));
        lines.push('参保地：' + CITY_NAME[c.from] + '    就诊地：' + CITY_NAME[c.to] + '    就医类型：' + c.visitName);
        lines.push('');
        lines.push('【报销比例估算】');
        lines.push('本地参考比例：' + c.localRate.toFixed(1) + '%');
        lines.push('异地执行比例：' + c.remoteRate.toFixed(1) + '%');
        if (c.fee) lines.push('预计费用：¥' + money(c.fee) + '    预计可报销：¥' + money(c.estAmount));
        lines.push('说明：' + (REASON_FACTOR[c.reason] || {}).desc);
        lines.push('');
        lines.push('【必备材料】');
        c.requiredItems.forEach((i, n) => lines.push((n + 1) + '. ' + i.text + '（' + i.desc + '）'));
        lines.push('');
        lines.push('【补充材料】');
        c.optionalItems.forEach((i, n) => lines.push((n + 1) + '. ' + i.text + '（' + i.desc + '）'));
        lines.push('');
        lines.push('【办理步骤】');
        c.steps.forEach((s, n) => lines.push((n + 1) + '. ' + s.title + '：' + s.desc));
        lines.push('');
        lines.push('温馨提示：本清单由「医算清」生成，仅供参考，请以参保地医保部门最新要求为准。');

        const blob = new Blob(['\ufeff' + lines.join('\n')], { type: 'text/plain;charset=utf-8' });
        const filename = '异地就医清单_' + CITY_NAME[c.from] + 'to' + CITY_NAME[c.to] + '_' + new Date().toISOString().slice(0, 10) + '.txt';

        // 沙箱里 blob URL 可能被分区策略挡住，创建失败就退回 data URL 兜底
        let href;
        try {
            href = URL.createObjectURL(blob);
        } catch (e) {
            href = 'data:text/plain;charset=utf-8,' + encodeURIComponent(lines.join('\n'));
        }

        const a = document.createElement('a');
        a.href = href;
        a.download = filename;
        a.rel = 'noopener';
        a.style.display = 'none';
        document.body.appendChild(a);
        a.click();
        setTimeout(() => {
            try { if (href.indexOf('blob:') === 0) URL.revokeObjectURL(href); } catch (e) { /* 忽略 */ }
            a.remove();
        }, 800);
        toast('清单已导出为文本文件（若被浏览器拦截，请改用「复制链接」）', 'ok');
    }

    /* ==========================================================
       5. 启动
       ========================================================== */
    document.addEventListener('DOMContentLoaded', function () {
        // 幂等守卫：防止脚本被意外加载/执行两次导致重复绑定
        if (window.__MEDICLEAR_INITED__) return;
        window.__MEDICLEAR_INITED__ = true;

        if (!window.MediClear) {
            toast('计算引擎加载失败，请检查 calculator.js', 'warn');
            return;
        }
        initGlobal();
        initCalculator();
        initBillAnalysis();
        initRelocation();
        initAgent();
        initCollapse();

        if (!HAS_ECHARTS) {
            if (typeof window.echarts !== 'undefined') {
                // 异步回退脚本赶在初始化前加载完了，补画一次
                reinitCharts();
            } else {
                console.warn('[MediClear] ECharts 未加载，图表区域已降级为提示文案');
            }
        }
    });
})();
