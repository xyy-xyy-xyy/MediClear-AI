// 构建脚本：src/web 多文件 → qmuse-app/public/mediclear.html 内联单文件
// 用法：node scripts/build-inline.mjs（在 MediClear-AI 根目录运行）
// 之后在 qmuse-app 目录运行 npm run gen-html 生成 src/mediclear-html.ts
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const WEB = path.join(root, 'src', 'web');
const OUT = path.join(root, 'QMuse_React_Template', 'qmuse-app', 'public', 'mediclear.html');

let html = fs.readFileSync(path.join(WEB, 'index.html'), 'utf8');
const css = fs.readFileSync(path.join(WEB, 'css', 'style.css'), 'utf8');

// 1) 内联 CSS
const linkTag = '<link rel="stylesheet" href="css/style.css">';
if (!html.includes(linkTag)) throw new Error('找不到 CSS link 标签');
html = html.replace(linkTag, '<style>\n' + css + '\n</style>');

// 2) 内联本地 JS（外链 CDN 的 <script src="https://..."> 保留）
const jsFiles = ['calculator.js', 'relocation.js', 'ocr.js', 'app.js'];
for (const f of jsFiles) {
    const tag = '<script src="js/' + f + '"></script>';
    if (!html.includes(tag)) throw new Error('找不到 script 标签: ' + f);
    const js = fs.readFileSync(path.join(WEB, 'js', f), 'utf8');
    if (js.includes('</script')) throw new Error(f + ' 含 </script> 字面量，内联不安全');
    html = html.replace(tag, '<script>\n' + js + '\n</script>');
}

// 3) 保持与上一版一致的 BOM（若已存在）
const hadBom = fs.readFileSync(OUT).subarray(0, 3).toString('hex') === 'efbbbf';
fs.writeFileSync(OUT, (hadBom ? '\ufeff' : '') + html, 'utf8');
console.log('生成完成:', OUT, (hadBom ? '(BOM 保留)' : '(无 BOM)'), html.length, 'chars');
