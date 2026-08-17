# 智能备课教研团队

面向 K12 教师的多 Agent 备课工作台（FastAPI + React）。

## Agent 分工

| Agent | 职责 |
| --- | --- |
| 课标解读员 | 按需调用 `search_curriculum` 检索课标，再提取素养目标与内容要点 |
| 教案设计师 | 撰写教案；被审核员打回时按意见修改 |
| **教案审核员** | 审核教案；通过进入下游，不通过打回设计师（最多 1 次） |
| 习题组卷师 | 按需检索题库组卷；对照教案质检，不通过则回修一次 |
| 课件生成师 | 搜图/生图生成大纲；对照教案环节质检，不通过则回修一次 |
| 板书设计师 | 设计板书结构与书写顺序 |

流程：

```text
课标解读员 ──→ 教案设计师
                    ↓
              【教案审核员】
              ├─ 通过 ──→ 习题组卷师 ──→ 课件生成师 ──→ 板书设计师
              └─ 不通过 ──→ 教案设计师（修改）──→ 再审
```

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

填写课题（级联选择：年级 → 学习领域 → 主题 → 课时）→「开始智能备课」→ 中间看 Agent 进度 → 右侧看教案/习题/课件/板书。

结果区支持：开启「编辑」修改内容、「保存版本」、导出 Word / PDF / PPT 大纲。历史版本可从下拉加载。

目录数据：`doc/catalog/math_junior.json`（当前覆盖初中数学）。可通过「手动自定义课题 / 单元」兜底。

## 四、命令行（可选）

```powershell
python scripts\run.py --input-json examples\lesson_input.json
```

## 目录

```text
doc/                 # 课标 PDF + catalog + question_bank 演示题库
src/lesson_prep/     # Agent / RAG / FastAPI
web/                 # React + Vite + Ant Design
scripts/             # CLI / API 启动
outputs/             # 命令行结果
```

## 说明

- 扫描版课标 PDF 会自动回退到 `doc/knowledge/` 文本。
- 课标解读员使用 Tool Calling：`search_curriculum`（底层仍是 FAISS/关键词 RAG）；若模型未调工具则用默认查询兜底。
- 习题组卷师使用 Tool Calling：`search_question_bank`（`doc/question_bank/math_junior_demo.json`）；优先选题并标注 `source_id`，题库不足再 LLM 补生成。
- 教案由独立「教案审核员」审核：通过进入习题/课件；不通过则打回教案设计师修改（最多 1 次）。习题/课件仍含节点内质检回修；任务可从指定节点重跑（`POST /api/runs/{id}/rerun`）。
- 课件生成师使用 Tool Calling：`search_images` / `generate_diagram`；默认优先外网搜真图，失败再本地示意图（`MEDIA_SEARCH_ENABLED=false` 可强制只用本地图）。
- 学情来自老师填写，无成绩库。
- 若智谱返回 429，请稍后再试或临时开启 `MOCK_LLM=true`。
