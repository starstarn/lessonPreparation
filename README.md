# 智能备课教研团队

面向 K12 教师的多 Agent 备课流水线（To B Demo）。

## Agent 分工

| Agent | 职责 |
| --- | --- |
| 课标解读员 | RAG 检索《义务教育数学课程标准（2022年版）》，提取本课时素养目标与内容要点 |
| 教案设计师 | 生成教学目标、重难点、环节设计（可消化老师填写的学情卡片） |
| 课件生成师 | 生成 PPT 大纲与配图/视频检索关键词 |
| 板书设计师 | 设计板书结构与书写顺序 |

流程：`课标解读 → 教案设计 →（课件 ∥ 板书）`

## 快速开始

### 1. 安装

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 配置

```bash
copy .env.example .env
```

编辑 `.env`：

- `OPENAI_API_KEY`：必填（真实模式）
- `OPENAI_API_BASE`：可改为国内兼容接口
- `OPENAI_MODEL` / `OPENAI_EMBEDDING_MODEL`
- `MOCK_LLM=true`：不调用模型，验证流水线结构

课标 PDF 放在 `doc/`（已包含数学课标）。

> 注意：若 PDF 是**扫描件**（无法复制文字），系统会自动使用 `doc/knowledge/` 下的课标摘要。
> 也可运行 OCR 把扫描 PDF 转成文本后再建索引：
>
> ```bash
> python scripts/ocr_pdf.py --start 0 --end 30
> ```

### 3. 构建课标索引

先预览可用文本块：

```bash
python scripts/build_index.py --preview-only
```

真实模式构建 FAISS：

```bash
python scripts/build_index.py --force
```

### 4. 跑一次备课

Mock：

```bash
# .env 中 MOCK_LLM=true
python scripts/run.py --title "有理数的加法"
```

真实调用：

```bash
# .env 中 MOCK_LLM=false 并填好 Key
python scripts/build_index.py --force
python scripts/run.py --title "有理数的加法" --grade "七年级"
```

结果写入 `outputs/prep_*.json`。

自定义输入示例见 `examples/lesson_input.json`。

## 目录

```text
doc/                 # 课标 PDF
src/lesson_prep/     # 核心代码
scripts/             # 建索引 / 运行
data/vectorstore/    # FAISS 索引（本地生成）
outputs/             # 运行结果
```

## 说明

- 无题库、无成绩库；学情仅来自老师可选填写。
- 课件只给素材检索词，不抓取版权资源。
- 练习只写「练习意图」，不自动组卷。
