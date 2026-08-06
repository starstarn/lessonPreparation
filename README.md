# 智能备课教研团队

面向 K12 教师的多 Agent 备课工作台（FastAPI + React）。

## Agent 分工

| Agent | 职责 |
| --- | --- |
| 课标解读员 | 检索课标知识，提取本课时素养目标与内容要点 |
| 教案设计师 | 生成教学目标、重难点、环节设计 |
| 课件生成师 | 生成 PPT 大纲与素材关键词 |
| 板书设计师 | 设计板书结构与书写顺序 |

流程：`课标解读 → 教案设计 → 课件 → 板书`

## 一、安装

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt

cd web
npm install
cd ..
```

## 二、配置

```powershell
copy .env.example .env
```

填写智谱等 OpenAI 兼容接口。调试可不调模型：`MOCK_LLM=true`。

## 三、启动（推荐：网页）

开两个终端：

**终端 1 · 后端 API**

```powershell
.\.venv\Scripts\activate
python scripts\serve_api.py
```

**终端 2 · 前端**

```powershell
cd web
npm run dev
```

浏览器打开：http://127.0.0.1:5173  

填写课题（级联选择：年级 → 学习领域 → 主题 → 课时）→「开始智能备课」→ 中间看 Agent 进度 → 右侧看教案/课件/板书。

结果区支持：开启「编辑」修改内容、「保存版本」、导出 Word / PDF / PPT 大纲。历史版本可从下拉加载。

目录数据：`doc/catalog/math_junior.json`（当前覆盖初中数学）。可通过「手动自定义课题 / 单元」兜底。

## 四、命令行（可选）

```powershell
python scripts\run.py --input-json examples\lesson_input.json
```

## 目录

```text
doc/                 # 课标 PDF + knowledge 知识摘要
src/lesson_prep/     # Agent / RAG / FastAPI
web/                 # React + Vite + Ant Design
scripts/             # CLI / API 启动
outputs/             # 命令行结果
```

## 说明

- 扫描版课标 PDF 会自动回退到 `doc/knowledge/` 文本。
- 无题库、无成绩库；学情来自老师填写。
- 若智谱返回 429，请稍后再试或临时开启 `MOCK_LLM=true`。
