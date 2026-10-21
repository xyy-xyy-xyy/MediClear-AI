/**
 * 医算清 MediClear-AI · 发票 OCR（纯前端本地识别）
 * Tesseract.js v5 按需加载：jsdelivr 主源 + unpkg 备源（与 ECharts 双源回退同模式）
 * 图片仅在本机识别，不上传任何服务器；中文模型首次下载后由浏览器自动缓存
 * 模型下载策略：自带流式真实进度（Tesseract 内部下载不报进度，弱网下进度条会假死），
 * 下载完成后写入 Tesseract 同款 IndexedDB 缓存（keyval-store）预热，
 * 之后 Tesseract 读缓存直接命中，不再走网络。
 * 识别策略：首选 chi_sim（1.64MB）+ 图片预处理（裁黑边/限尺寸）；
 * 若解析不出有效金额，自动增强为 chi_sim+eng（数字识别更稳，额外约 3MB 仅此一次）重试。
 * 解析策略：空白/全角规整 → 关键词/大写金额/严格金额三级兜底 → 按金额切分多列明细行。
 * 暴露：window.MediClearOCR = { load, recognize, parse }
 */
(function () {
    'use strict';

    var CDN_SOURCES = [
        {
            script: 'https://cdn.jsdelivr.net/npm/tesseract.js@5/dist/tesseract.min.js',
            worker: 'https://cdn.jsdelivr.net/npm/tesseract.js@5/dist/worker.min.js',
            core: 'https://cdn.jsdelivr.net/npm/tesseract.js-core@5/'
        },
        {
            script: 'https://unpkg.com/tesseract.js@5/dist/tesseract.min.js',
            worker: 'https://unpkg.com/tesseract.js@5/dist/worker.min.js',
            core: 'https://unpkg.com/tesseract.js-core@5/'
        }
    ];
    // 模型 gz 直链（依次尝试）。
    // 注意：*_best_int 为 LSTM 专用精简模型；tessdata 官方 4.0.0 是 legacy+LSTM 混合包（19MB+），
    // 弱网下要 5 分钟以上 —— 只作最后兜底，绝不能作为首选。
    var LANGS = {
        chi_sim: {
            estimate: 1.7 * 1024 * 1024,
            sources: [
                'https://cdn.jsdelivr.net/npm/@tesseract.js-data/chi_sim@1.0.0/4.0.0_best_int/chi_sim.traineddata.gz',
                'https://unpkg.com/@tesseract.js-data/chi_sim@1.0.0/4.0.0_best_int/chi_sim.traineddata.gz',
                'https://tessdata.projectnaptha.com/4.0.0/chi_sim.traineddata.gz'
            ]
        },
        eng: {
            estimate: 3.1 * 1024 * 1024,
            sources: [
                'https://cdn.jsdelivr.net/npm/@tesseract.js-data/eng@1.0.0/4.0.0_best_int/eng.traineddata.gz',
                'https://unpkg.com/@tesseract.js-data/eng@1.0.0/4.0.0_best_int/eng.traineddata.gz'
            ]
        }
    };
    var LANG_PATH = 'https://cdn.jsdelivr.net/npm/@tesseract.js-data/chi_sim@1.0.0/4.0.0_best_int';

    var enginePromise = null;   // 引擎脚本加载
    var workerPromise = null;   // chi_sim worker
    var modelPromises = {};     // 各语言模型下载+预热（会话内只做一次）
    var chosenSource = null;
    var progressCb = null;      // 最近一次识别的进度回调
    var modelFallback = false;  // 预下载失败，正走 Tesseract 内置通道

    /* ==================== 引擎加载 ==================== */

    function loadScript(src, timeout) {
        return new Promise(function (resolve, reject) {
            var s = document.createElement('script');
            var done = false;
            var timer = setTimeout(function () {
                if (!done) { done = true; s.remove(); reject(new Error('引擎加载超时')); }
            }, timeout || 20000);
            s.src = src;
            s.onload = function () { if (!done) { done = true; clearTimeout(timer); resolve(); } };
            s.onerror = function () { if (!done) { done = true; clearTimeout(timer); s.remove(); reject(new Error('引擎脚本加载失败')); } };
            document.head.appendChild(s);
        });
    }

    function loadEngine() {
        if (window.Tesseract) {
            if (!chosenSource) chosenSource = CDN_SOURCES[0];
            return Promise.resolve();
        }
        if (enginePromise) return enginePromise;
        var attempt = function (i) {
            if (i >= CDN_SOURCES.length) return Promise.reject(new Error('OCR 引擎加载失败，请检查网络后重试'));
            return loadScript(CDN_SOURCES[i].script).then(function () {
                if (!window.Tesseract) throw new Error('引擎加载失败');
                chosenSource = CDN_SOURCES[i];
            }).catch(function () { return attempt(i + 1); });
        };
        enginePromise = attempt(0).catch(function (e) { enginePromise = null; throw e; });
        return enginePromise;
    }

    /* ==================== 模型下载 + 缓存预热 ==================== */

    // Tesseract v5 的语言模型缓存就是 idb-keyval：库 keyval-store / 表 keyval / 键 "./chi_sim.traineddata"。
    // 用原生 IndexedDB 写入同名键即可被其命中（值允许是 gzip 原始数据，读取端自带 gunzip 检测）。
    function idbOp(key, val) {
        return new Promise(function (resolve, reject) {
            var req;
            try { req = indexedDB.open('keyval-store'); } catch (e) { return reject(e); }
            req.onupgradeneeded = function () { try { req.result.createObjectStore('keyval'); } catch (e) { /* 已存在 */ } };
            req.onerror = function () { reject(req.error || new Error('IndexedDB 打开失败')); };
            req.onsuccess = function () {
                var db = req.result;
                try {
                    var tx = db.transaction('keyval', val === undefined ? 'readonly' : 'readwrite');
                    var st = tx.objectStore('keyval');
                    var out = val === undefined ? st.get(key) : st.put(val, key);
                    tx.oncomplete = function () { db.close(); resolve(out.result); };
                    tx.onerror = function () { db.close(); reject(tx.error || new Error('IndexedDB 操作失败')); };
                    tx.onabort = function () { db.close(); reject(tx.error || new Error('IndexedDB 事务中止')); };
                } catch (e) { try { db.close(); } catch (e2) { } reject(e); }
            };
        });
    }

    /** 流式下载单个模型源：真实字节进度 + 停滞检测（首字节 20s / 之后 25s 无新字节即放弃换源），resolve(Uint8Array) */
    function fetchModelBytes(url, onChunk) {
        return new Promise(function (resolve, reject) {
            var ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
            var stage = 0, lastTick = Date.now();
            var stalled = setInterval(function () {
                var gap = Date.now() - lastTick;
                if ((stage === 0 && gap > 20000) || (stage === 1 && gap > 25000)) fail(new Error('下载停滞'));
            }, 2000);
            function fail(e) { cleanup(); if (ctrl) ctrl.abort(); reject(e); }
            function cleanup() { clearInterval(stalled); }
            fetch(url, { signal: ctrl ? ctrl.signal : undefined }).then(function (res) {
                if (!res.ok) throw new Error('HTTP ' + res.status);
                var total = parseInt(res.headers.get('content-length') || '0', 10) || LANGS.chi_sim.estimate;
                if (!res.body || !res.body.getReader) {
                    return res.arrayBuffer().then(function (buf) {
                        cleanup();
                        onChunk(buf.byteLength, buf.byteLength);
                        resolve(new Uint8Array(buf));
                    });
                }
                stage = 1;
                var reader = res.body.getReader();
                var parts = [], got = 0;
                var step = function () {
                    return reader.read().then(function (r) {
                        if (r.done) {
                            cleanup();
                            var all = new Uint8Array(got), off = 0;
                            parts.forEach(function (p) { all.set(p, off); off += p.length; });
                            resolve(all);
                            return;
                        }
                        if (r.value && r.value.length) { parts.push(r.value); got += r.value.length; }
                        lastTick = Date.now();
                        onChunk(got, total);
                        return step();
                    });
                };
                return step();
            }).catch(function (e) { cleanup(); reject(e); });
        });
    }

    /**
     * 确保某语言模型可用：缓存命中 → 'cached'；
     * 未命中 → 逐源流式下载（真实进度）→ 写入 Tesseract 缓存键 → 'downloaded'；
     * 全部失败 → 'fallback'，交给 Tesseract 走 langPath 自行下载（不阻塞识别）。
     */
    function ensureModel(lang, onProgress) {
        var key = './' + lang + '.traineddata';
        if (!modelPromises[lang]) {
            var run = idbOp(key).then(function (cached) {
                // 小于 1MB 视为脏数据/未缓存
                if (cached && (cached.byteLength || cached.length || 0) > 1024 * 1024) return 'cached';
                var sources = (LANGS[lang] || LANGS.chi_sim).sources;
                var attempt = function (i) {
                    if (i >= sources.length) return 'fallback';
                    return fetchModelBytes(sources[i], function (rec, tot) {
                        onProgress(rec, tot);
                    }).then(function (bytes) {
                        return idbOp(key, bytes).then(function () { return 'downloaded'; });
                    }).catch(function () { return attempt(i + 1); });
                };
                return attempt(0);
            }).catch(function () { return 'fallback'; });
            modelPromises[lang] = run;
        }
        return modelPromises[lang];
    }

    /* ==================== Worker ==================== */

    function createTessWorker(langs) {
        return loadEngine().then(function () {
            return window.Tesseract.createWorker(langs, 1, {
                workerPath: chosenSource.worker,
                corePath: chosenSource.core,
                langPath: LANG_PATH,
                logger: function (m) {
                    if (!progressCb) return;
                    var label = '';
                    if (m.status === 'loading tesseract core') label = '正在加载识别核心…';
                    else if (m.status === 'initializing tesseract') label = '正在初始化引擎…';
                    else if (m.status === 'loading language traineddata') label = modelFallback ? '网络较慢，正在后台下载识别模型（约 2MB，请保持网络畅通）…' : '正在加载中文模型…';
                    else if (m.status === 'loaded language traineddata') label = '中文模型就绪';
                    else if (m.status === 'initializing api') label = '引擎准备就绪…';
                    else if (m.status === 'recognizing text') label = '正在识别发票文字…';
                    if (label) progressCb(label, typeof m.progress === 'number' ? Math.min(0.98, Math.max(0.02, m.progress)) : 0.02);
                }
            });
        });
    }

    function getWorker() {
        if (workerPromise) return workerPromise;
        workerPromise = createTessWorker('chi_sim').catch(function (e) { workerPromise = null; throw e; });
        return workerPromise;
    }

    /* ==================== 图片预处理 ==================== */

    /**
     * 翻拍照片常见黑边/暗背景会干扰 Tesseract 版面分析：
     * 1) 亮像素（纸张）占比找内容框裁掉黑边；2) 过大等比缩小、过小适度放大。
     * 任一步失败都回退原图，绝不因预处理挂掉识别。
     */
    function preprocessImage(dataUrl) {
        return new Promise(function (resolve) {
            var img = new Image();
            var done = false;
            var timer = setTimeout(function () { if (!done) { done = true; resolve(dataUrl); } }, 8000);
            var finish = function (url) { if (!done) { done = true; clearTimeout(timer); resolve(url); } };
            img.onload = function () {
                try {
                    var w = img.naturalWidth, h = img.naturalHeight;
                    if (!w || !h) return finish(dataUrl);
                    // 缩放：>2000 缩小到 2000；<1000 放大（不超过 2 倍，目标 ~1600）
                    var maxDim = Math.max(w, h), scale = 1;
                    if (maxDim > 2000) scale = 2000 / maxDim;
                    else if (maxDim < 1000) scale = Math.min(2, 1600 / maxDim);
                    var cw = Math.max(1, Math.round(w * scale)), ch = Math.max(1, Math.round(h * scale));
                    var c = document.createElement('canvas');
                    c.width = cw; c.height = ch;
                    var ctx = c.getContext('2d', { willReadFrequently: true });
                    if (!ctx) return finish(dataUrl);
                    ctx.drawImage(img, 0, 0, cw, ch);
                    var px = ctx.getImageData(0, 0, cw, ch).data;
                    // 亮度图
                    var lum = new Uint8Array(cw * ch), i;
                    for (i = 0; i < cw * ch; i++) {
                        lum[i] = (px[i * 4] * 0.299 + px[i * 4 + 1] * 0.587 + px[i * 4 + 2] * 0.114) | 0;
                    }
                    // 行/列亮像素占比 → 内容框
                    var BRIGHT = 70, FRAC = 0.02;
                    var rowCnt = new Uint32Array(ch), colCnt = new Uint32Array(cw), y, x;
                    for (y = 0; y < ch; y++) {
                        for (x = 0; x < cw; x++) if (lum[y * cw + x] > BRIGHT) { rowCnt[y]++; colCnt[x]++; }
                    }
                    var top = 0, bottom = ch - 1, left = 0, right = cw - 1;
                    while (top < bottom && rowCnt[top] < cw * FRAC) top++;
                    while (bottom > top && rowCnt[bottom] < cw * FRAC) bottom--;
                    while (left < right && colCnt[left] < ch * FRAC) left++;
                    while (right > left && colCnt[right] < ch * FRAC) right--;
                    if (top > 0 || bottom < ch - 1 || left > 0 || right < cw - 1) {
                        // 留 1% 边距
                        var mx = Math.max(2, Math.round(cw * 0.01)), my = Math.max(2, Math.round(ch * 0.01));
                        top = Math.max(0, top - my); bottom = Math.min(ch - 1, bottom + my);
                        left = Math.max(0, left - mx); right = Math.min(cw - 1, right + mx);
                        var ow = right - left + 1, oh = bottom - top + 1;
                        if (ow >= cw * 0.3 && oh >= ch * 0.3 && ow * oh >= 30000) {
                            var out = document.createElement('canvas');
                            out.width = ow; out.height = oh;
                            out.getContext('2d').drawImage(c, left, top, ow, oh, 0, 0, ow, oh);
                            return finish(out.toDataURL('image/jpeg', 0.92));
                        }
                    }
                    if (scale !== 1) return finish(c.toDataURL('image/jpeg', 0.92));
                    finish(dataUrl);
                } catch (e) { finish(dataUrl); }
            };
            img.onerror = function () { finish(dataUrl); };
            img.src = dataUrl;
        });
    }

    /* ==================== 识别 ==================== */

    function recognizeOnce(worker, dataUrl) {
        return worker.recognize(dataUrl).then(function (res) {
            var d = res && res.data ? res.data : {};
            return { text: d.text || '', confidence: d.confidence || 0 };
        });
    }

    function recognize(dataUrl, onProgress) {
        progressCb = onProgress || null;
        if (progressCb) progressCb('正在准备识别环境…', 0.02);
        return preprocessImage(dataUrl).then(function (prepUrl) {
            // 引擎脚本 / 模型下载 / 图片预处理 三路并行，全部就绪后再建 worker
            return Promise.all([
                loadEngine(),
                ensureModel('chi_sim', function (rec, tot) {
                    if (!progressCb) return;
                    var pct = Math.min(100, Math.round(rec / tot * 100));
                    progressCb('正在下载中文模型 ' + pct + '%（' + (rec / 1048576).toFixed(1) + '/' + (tot / 1048576).toFixed(1) + 'MB），完成后会缓存…',
                        0.02 + 0.55 * Math.min(1, rec / tot));
                }),
                prepUrl
            ]);
        }).then(function (arr) {
            modelFallback = arr[1] === 'fallback';
            if (progressCb && arr[1] === 'downloaded') progressCb('中文模型就绪', 0.6);
            return getWorker().then(function (w) {
                return recognizeOnce(w, arr[2]); // 预处理后的图片
            });
        }).then(function (r1) {
            // 首选模型已读出票据上的显式总额（严格金额/大写金额）→ 直接采用
            var p1 = parseInvoice(r1.text);
            if (p1.total > 0 && !p1.totalIsSum) { progressCb = null; return r1; }
            // 增强（rescue）：chi_sim+eng 对数字更稳；换用原图重识别，与预处理图形成互补。
            // eng 模型额外约 3MB，仅此一次，之后有缓存。
            if (progressCb) progressCb('识别效果不佳，正在增强识别…', 0.62);
            return ensureModel('eng', function (rec, tot) {
                if (!progressCb) return;
                var pct = Math.min(100, Math.round(rec / tot * 100));
                progressCb('下载英文数字模型 ' + pct + '%（' + (rec / 1048576).toFixed(1) + 'MB），增强数字识别…',
                    0.62 + 0.3 * Math.min(1, rec / tot));
            }).then(function () {
                return createTessWorker('chi_sim+eng');
            }).then(function (w2) {
                return recognizeOnce(w2, dataUrl).then(function (r2) {
                    try { w2.terminate(); } catch (e) { /* 忽略 */ }
                    return r2;
                });
            }).catch(function () { return null; }).then(function (r2) {
                progressCb = null;
                if (!r2) return r1;
                // 取信息量高者：显式总额(2分) > 明细(1分)
                var score = function (p) {
                    return (p.total > 0 && !p.totalIsSum ? 2 : 0) + (p.items.length > 0 ? 1 : 0);
                };
                return score(parseInvoice(r2.text)) > score(parseInvoice(r1.text)) ? r2 : r1;
            });
        }).catch(function (e) {
            progressCb = null;
            throw e;
        });
    }

    /* ==================== 发票文本解析 ==================== */

    // 费用类别启发式（甲类全额纳入、乙类先自付一部分、自费不报；结果标注"估算可微调"）
    var CAT_RULES = [
        { kw: ['挂号', '诊查', '诊察', '医事服务', '床位', '护理', '检验', '化验', '治疗'], cat: 'class_a' },
        { kw: ['西药', '中成药', '检查', '手术', '麻醉', '材料', '放射', '输氧'], cat: 'class_b' },
        { kw: ['中药饮片', '中草药', '伙食', '陪护', '空调', '其他'], cat: 'self_pay' }
    ];
    // 总额优先级：靠前的关键词命中即停（小写兜底放低位）
    var TOTAL_PATTERNS = [/医疗费用总额/, /费用总额/, /金额合计/, /合计金额/, /费用合计/, /总金额/, /实际收取金额/, /价税合计/, /收款金额/, /应收金额/, /小写/, /总费用/];
    // 明细行排除：支付方式 / 票据信息 / 人名等（含个人自付、预缴等非费用行）
    var SKIP_LINE_RE = /支付|自付|自负|日期|时间|号码|代码|编号|电话|地址|流水|机器|单位名称|收款人|复核|医保类型|预缴|补缴|退款|押金|账户|余额|天数|验证|查验|病历/;
    var AMOUNT_RE = /(?:[¥￥]\s*)?(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+\.\d{1,2}|\d{1,8})(?!\d)/g;
    // 严格金额：带千分位/小数点/货币符号的才可信（防止把日期、单据号当金额）
    var STRICT_RE = /(?:[¥￥]\s*\d[\d,]*(?:\.\d{1,2})?|\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+\.\d{1,2})(?!\d)/g;

    function extractAmounts(line, strict) {
        var re = strict ? STRICT_RE : AMOUNT_RE;
        var out = [], m;
        re.lastIndex = 0;
        while ((m = re.exec(line)) !== null) {
            var token = m[0];
            if (strict) {
                // 向左延伸到数字片段边界（含相邻乱码字符），整段校验：
                // "10,5?4.19" 整段含 ? 拒绝；"（小写)10,514.79" 从 ) 后开始整段有效
                var start = m.index;
                while (start > 0 && /[\d.,?？;；_*&^%$#@!~+'"=+]/.test(line.charAt(start - 1))) start--;
                token = line.slice(start, m.index + m[0].length);
                if (!/^[¥￥]?\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?$|^[¥￥]?\d+\.\d{1,2}$/.test(token)) continue;
            }
            var v = parseFloat(String(token).replace(/[¥￥,\s]/g, ''));
            if (v > 0 && v < 99999999) out.push(v);
        }
        return out;
    }

    /* 文本规整：全角数字/标点 → 半角；压缩数字/CJK 字符间被 OCR 拆开的空格 */
    function normalizeText(raw) {
        var s = String(raw || '');
        s = s.replace(/[０-９]/g, function (ch) { return String.fromCharCode(ch.charCodeAt(0) - 65248); });
        s = s.replace(/[，]/g, ',').replace(/[．]/g, '.');
        s = s.replace(/([\d.,])[ ]+(?=[\d.,])/g, '$1');
        s = s.replace(/([\u4e00-\u9fff（）()])[ ]+(?=[\u4e00-\u9fff（）()])/g, '$1');
        return s;
    }

    /* 大写金额 → 数字（壹万贰仟捌佰陆拾圆整 → 12860） */
    var CN_DIGIT = { '零': 0, '壹': 1, '贰': 2, '叁': 3, '肆': 4, '伍': 5, '陆': 6, '柒': 7, '捌': 8, '玖': 9 };
    var CN_SMALL = { '拾': 10, '佰': 100, '仟': 1000 };
    var CN_BIG = { '万': 1e4, '亿': 1e8 };

    function cnToNumber(str) {
        var s = String(str).replace(/人民币|整|正/g, '');
        var yuan = s.split(/[圆元]/)[0];
        if (!yuan) return null;
        var jiao = 0, fen = 0;
        var mj = s.match(/([零壹贰叁肆伍陆柒捌玖])角/);
        if (mj) jiao = CN_DIGIT[mj[1]];
        var mf = s.match(/([零壹贰叁肆伍陆柒捌玖])分/);
        if (mf) fen = CN_DIGIT[mf[1]];
        var total = 0, section = 0, num = 0, any = false;
        for (var i = 0; i < yuan.length; i++) {
            var ch = yuan.charAt(i);
            if (CN_DIGIT[ch] !== undefined) { num = CN_DIGIT[ch]; any = true; }
            else if (CN_SMALL[ch]) { section += (num || 1) * CN_SMALL[ch]; num = 0; any = true; }
            else if (CN_BIG[ch]) { total = (total + section + num) * CN_BIG[ch]; section = 0; num = 0; any = true; }
            else if (ch !== '零') return null;
        }
        if (!any) return null;
        return Math.round((total + section + num + jiao * 0.1 + fen * 0.01) * 100) / 100;
    }

    /** 把一行按金额切成多个 (名称, 金额) 对 —— 兼容多列并排版式的明细行 */
    function splitPairs(line) {
        var pairs = [], re = AMOUNT_RE, m, last = 0;
        re.lastIndex = 0;
        while ((m = re.exec(line)) !== null) {
            var name = line.slice(last, m.index);
            var v = parseFloat(m[1].replace(/,/g, ''));
            last = m.index + m[0].length;
            if (!(v > 0)) continue;
            name = name
                .replace(/(?:[¥￥]\s*)?\d[\d,.]*\s*$/g, '') // 尾部粘连的残缺数字
                .replace(/[¥￥元：:，,。()\[\]{}|/·\-—_*#]/g, '')
                .replace(/\s+/g, '')
                .trim();
            if (!name) continue;
            pairs.push({ name: name, amount: v });
        }
        return pairs;
    }

    function classifyName(name) {
        for (var r = 0; r < CAT_RULES.length; r++) {
            for (var k = 0; k < CAT_RULES[r].kw.length; k++) {
                if (name.indexOf(CAT_RULES[r].kw[k]) >= 0) return CAT_RULES[r].cat;
            }
        }
        return null;
    }

    function parseInvoice(text) {
        var raw = normalizeText(text);
        var lines = raw.split(/[\r\n]+/).map(function (s) { return s.trim(); }).filter(Boolean);
        var total = 0, totalIsSum = false, i, p;

        // 1) 关键词定位总额（仅采信带千分位/小数点/货币符号的严格金额，取该行最后一个）
        for (i = 0; i < lines.length && !total; i++) {
            for (p = 0; p < TOTAL_PATTERNS.length; p++) {
                if (TOTAL_PATTERNS[p].test(lines[i])) {
                    var nums = extractAmounts(lines[i], true);
                    if (nums.length) { total = nums[nums.length - 1]; break; }
                }
            }
        }

        // 2) 大写金额兜底
        if (!total) {
            var cnM = raw.match(/[壹贰叁肆伍陆柒捌玖零拾佰仟万亿]{2,}[圆元][壹贰叁肆伍陆柒捌玖零角分整正]*/);
            if (cnM) {
                var cv = cnToNumber(cnM[0]);
                if (cv && cv > 0) total = cv;
            }
        }

        // 3) 兜底：带货币符号/金额字样且非排除行的最大严格金额（上限 100 万）
        if (!total) {
            lines.forEach(function (line) {
                if (!/[¥￥]|金额|费用/.test(line)) return;
                if (SKIP_LINE_RE.test(line) || /日期|时间|天数/.test(line)) return;
                extractAmounts(line, true).forEach(function (n) {
                    if (n > total && n < 1000000) total = n;
                });
            });
        }

        // 4) 费用类别明细行：按金额切分，兼容一行多项（多列票据）
        var items = [];
        lines.forEach(function (line) {
            if (line.length > 120) return;
            if (/合计|总额|总计|小计|大写/.test(line)) return;
            splitPairs(line).forEach(function (pair) {
                if (SKIP_LINE_RE.test(pair.name)) return;
                var cat = classifyName(pair.name);
                if (!cat) return;
                if (total && pair.amount > total * 1.5) return; // 异常值过滤
                var name = pair.name.slice(0, 16);
                items.push({ name: name, amount: pair.amount, category: cat });
            });
        });

        // 同名同类合并
        var merged = [];
        items.forEach(function (it) {
            var same = null;
            for (var j = 0; j < merged.length; j++) {
                if (merged[j].name === it.name && merged[j].category === it.category) { same = merged[j]; break; }
            }
            if (same) same.amount = Math.round((same.amount + it.amount) * 100) / 100;
            else merged.push(it);
        });
        merged = merged.slice(0, 12);

        var classA = 0, classB = 0, selfPay = 0;
        merged.forEach(function (it) {
            if (it.category === 'class_a') classA += it.amount;
            else if (it.category === 'class_b') classB += it.amount;
            else selfPay += it.amount;
        });

        if (!total && merged.length) {
            total = Math.round((classA + classB + selfPay) * 100) / 100;
            totalIsSum = true; // 总额来自明细求和（票据上没读出显式总额）
        }

        return {
            ok: !!(total > 0 || merged.length),
            total: total,
            totalIsSum: totalIsSum,
            classA: Math.round(classA * 100) / 100,
            classB: Math.round(classB * 100) / 100,
            selfPay: Math.round(selfPay * 100) / 100,
            items: merged,
            rawText: raw.slice(0, 2000)
        };
    }

    window.MediClearOCR = {
        load: loadEngine,
        recognize: recognize,
        parse: parseInvoice
    };
})();
