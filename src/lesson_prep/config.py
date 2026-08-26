from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parents[2]
DOC_DIR = ROOT_DIR / "doc"
DATA_DIR = ROOT_DIR / "data"
VECTORSTORE_DIR = DATA_DIR / "vectorstore"
OUTPUT_DIR = ROOT_DIR / "outputs"
MEDIA_DIR = OUTPUT_DIR / "media"

UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY", "")
# 默认开启外网搜真图；失败再回退本地示意图。网络差可设为 false
MEDIA_SEARCH_ENABLED = os.getenv("MEDIA_SEARCH_ENABLED", "true").lower() in {"1", "true", "yes"}

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
# 兼容 OPENAI_API_BASE / OPENAI_BASE_URL 两种命名
OPENAI_API_BASE = (
    os.getenv("OPENAI_API_BASE")
    or os.getenv("OPENAI_BASE_URL")
    or "https://api.openai.com/v1"
)
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "glm-4.7-flash")
OPENAI_EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "BAAI/bge-m3")

# Embedding 可单独走硅基流动等；未配置时回退到 Chat 同一套 Key/Base
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY") or OPENAI_API_KEY
EMBEDDING_API_BASE = (
    os.getenv("EMBEDDING_API_BASE")
    or os.getenv("EMBEDDING_BASE_URL")
    or OPENAI_API_BASE
)

# ---- Chat 模型路由 / 降级（默认与 OPENAI_MODEL 相同，行为兼容旧配置）----
# fast：Tool Calling / 轻量检索决策；strong：教案撰写、质检、一致性等关键生成
LLM_ROUTING_ENABLED = os.getenv("LLM_ROUTING_ENABLED", "true").lower() in {
    "1",
    "true",
    "yes",
}
LLM_FALLBACK_ENABLED = os.getenv("LLM_FALLBACK_ENABLED", "true").lower() in {
    "1",
    "true",
    "yes",
}
LLM_MODEL_FAST = (os.getenv("LLM_MODEL_FAST") or OPENAI_MODEL).strip() or OPENAI_MODEL
LLM_MODEL_STRONG = (os.getenv("LLM_MODEL_STRONG") or OPENAI_MODEL).strip() or OPENAI_MODEL
# 主模型失败后的备用；留空则尝试另一档（fast↔strong），仍相同则不换模型
LLM_MODEL_FALLBACK = (os.getenv("LLM_MODEL_FALLBACK") or "").strip()
# 备用模型可走另一套 Key/Base（例如另一账号）；不填则与主 Chat 相同
LLM_FALLBACK_API_KEY = os.getenv("LLM_FALLBACK_API_KEY") or OPENAI_API_KEY
LLM_FALLBACK_API_BASE = (
    os.getenv("LLM_FALLBACK_API_BASE")
    or os.getenv("LLM_FALLBACK_BASE_URL")
    or OPENAI_API_BASE
)

MOCK_LLM = os.getenv("MOCK_LLM", "false").lower() in {"1", "true", "yes"}

# 教案审核通过后暂停，等老师确认/改稿再并行生成下游（设为 false 可关闭）
PLAN_CONFIRM_GATE = os.getenv("PLAN_CONFIRM_GATE", "true").lower() in {"1", "true", "yes"}

CHUNK_SIZE = 800
CHUNK_OVERLAP = 120
RETRIEVE_K = 6

CURRICULUM_META = {
    "stage": "义务教育",
    "subject": "数学",
    "curriculum_year": "2022",
    "title": "义务教育数学课程标准（2022年版）",
}


def find_curriculum_pdf() -> Path:
    """Locate the math curriculum PDF under doc/."""
    if not DOC_DIR.exists():
        raise FileNotFoundError(f"未找到课标目录: {DOC_DIR}")

    pdfs = sorted(DOC_DIR.glob("*.pdf"))
    if not pdfs:
        raise FileNotFoundError(f"doc/ 下没有 PDF，请放入课标文件: {DOC_DIR}")

    preferred = [p for p in pdfs if "数学" in p.name or "math" in p.name.lower()]
    return preferred[0] if preferred else pdfs[0]
