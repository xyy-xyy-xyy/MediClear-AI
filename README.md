# 医算清 MediClear-AI

> 医保报销，一算就清 —— 分段累进规则引擎 · 纯本地发票 OCR · 异地就医清单，所有数据不上传。

[![tests](https://github.com/xyy-xyy-xyy/MediClear-AI/actions/workflows/tests.yml/badge.svg)](https://github.com/xyy-xyy-xyy/MediClear-AI/actions/workflows/tests.yml)
![coverage](https://img.shields.io/badge/coverage-89%25-brightgreen)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**在线使用**：https://xyy-xyy-xyy.github.io/MediClear-AI/

## 界面预览

**首页 —— 医保报销，一算就清：三大入口 + 本地测算预览**

![首页](assets/home.png)

**AI 医保助手 —— 自然语言问答，金额仍由本地引擎计算，API Key 只存本机**

![AI 医保助手](assets/ai-assistant.png)

> 更多截图（报销测算 / 账单分析 / 异地就医 / 移动端）见 [`assets/screenshots`](assets/screenshots)

## 它解决什么问题

医院窗口会告诉你"报了多少"，医算清回答的是另外三个问题：**怎么报更多、要不要现在做、异地怎么办**。

- 手术放今年还是明年（起付线 / 大病累计）→ 年度最优策略
- 慢特病不认定就少报 → 认定前后报销对比
- 异地备案状态差 5%~20% → 比例测算 + 办理材料清单
- 结算单自掏费用看不懂 → 逐项归因："为什么比预期少 / 怎么多报"

## 功能特性

- **报销测算**：职工 / 居民住院与门诊，起付线 → 分段比例 → 封顶线 → 大病保险全流程分段累进计算，输出"发票式"明细单
- **发票 OCR**：Tesseract.js WASM 纯本地识别，自动解析金额与甲乙类费用，图片不离开设备
- **异地就医**：备案状态对报销比例的影响测算，附办理材料清单
- **可解释输出**：按长辈 / 普通 / 专业三种受众改写的归因分析（`explain` 模块）
- **年度视角**：多次住院合并计算、年度最优策略、方案对比
- **长辈模式**：大字号界面；移动端适配
- **数据 0 上传**：纯前端 + 本地存储，无后端、无网络请求（DevTools Network 面板可验证）

## 架构

两层结构，共享同一套政策规则：

```
src/
├── web/                  # 纯前端站点（原生 HTML/CSS/JS）
│   ├── js/calculator.js  #   前端计算引擎
│   ├── js/ocr.js         #   Tesseract.js 本地发票识别
│   ├── js/relocation.js  #   异地就医模块
│   └── test-web.js       #   49 项 jsdom 交互断言
└── python/               # Python 确定性规则引擎
    ├── calculator.py     #   门面入口
    ├── mediclear/
    │   ├── policies/     #   7 个险种模块
    │   ├── data/         #   JSON 政策数据层 + schema 校验
    │   ├── advanced/     #   年度策略 / 多次住院 / 方案对比
    │   ├── explain/      #   可解释输出生成
    │   └── export/       #   明细导出
    └── test_*.py         #   191 项 pytest 用例
```

**设计原则：金额计算全部由确定性引擎完成**。可解释文本由计算结果的结构化字段模板化生成，而不是语言模型自由发挥——数字永远不靠概率。这也是后续接入 LLM Agent 编排层时"LLM 只理解意图、不碰计算"的边界。

## 数据覆盖

- 19 城市政策 · 7 个险种模块 · 2025 / 2026 双年度版本
- 政策数据层带 schema 校验（分段表首尾相接、比例范围、起付线 ≤ 封顶线），脏数据在加载时即被拦截

## 快速开始

**在线使用**：直接访问 https://xyy-xyy-xyy.github.io/MediClear-AI/

**本地运行前端**：

```bash
# 在仓库根目录
python -m http.server 8000
# 浏览器打开 http://localhost:8000/src/web/
```

**运行 Python 引擎**：见 [src/python/README_python.md](src/python/README_python.md)

**运行测试**：

```bash
pip install pytest pytest-cov
pytest --cov=mediclear    # 在 src/python 目录下执行，191 项用例，核心引擎覆盖率 89%

npm install jsdom --no-save
node src/web/test-web.js  # 49 项交互断言
node src/web/test-agent.js  # 34 项 Agent 编排层断言
```

## 正确性验证

本项目大量使用 AI 辅助开发，正确性不由 AI 自证，而由四层确定性验证链保证：

1. **人工基准算例**：测试用例中包含按政策文件手工分段计算出的已知答案，引擎必须复现
2. **数据加载校验**：schema 层在加载时拦截格式与逻辑错误的政策数据
3. **双实现交叉对齐**：JS 与 Python 是同一套规则的独立实现，输出一致才可信
4. **真实结算单抽检**：与实际医保结算单人工比对

测试规模与覆盖率（pytest-cov 实测，CI 中每次推送自动复核）：

- Python 引擎：**191 项 pytest 用例，`mediclear` 包行覆盖率 89%**（2169 语句）
- 前端交互：**49 项 jsdom 断言**（含 AI 助手 UI 接入）+ **34 项 Agent 编排层断言**

## 免责声明

政策数据为演示用途整理，可能滞后于最新文件；实际报销金额以当地医保局政策与医院结算为准。

## Roadmap

- [ ] LLM Agent 编排层：将计算引擎注册为 function calling 工具，意图理解交给模型，数字零幻觉
- [ ] Docker 化后端服务（API 模式，供多端接入）
- [ ] 覆盖更多城市与险种

## License

[MIT](LICENSE)
