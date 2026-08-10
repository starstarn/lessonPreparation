"""课件配图：搜图下载与本地教学示意图生成。"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from lesson_prep.config import MEDIA_DIR, MEDIA_SEARCH_ENABLED, UNSPLASH_ACCESS_KEY
from lesson_prep.logutil import safe_log
from lesson_prep.schemas import SlidePage, Slides

MEDIA_DIR.mkdir(parents=True, exist_ok=True)

_USER_AGENT = "LessonPrepBot/0.1 (education; contact: local)"
_SEARCH_TIMEOUT = 8


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("msyh.ttc", "simhei.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _media_id(prefix: str, seed: str, ext: str = "png") -> str:
    digest = hashlib.md5(seed.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{digest}.{ext}"


def _save_result(
    *,
    media_id: str,
    path: Path,
    title: str,
    source: str,
    query_or_prompt: str,
) -> str:
    rel = path.relative_to(MEDIA_DIR.parent.parent).as_posix()
    payload = {
        "ok": True,
        "media_id": media_id,
        "path": rel,
        "preview_url": f"/api/media/{media_id}",
        "title": title,
        "source": source,
        "query": query_or_prompt,
    }
    return json.dumps(payload, ensure_ascii=False)


def _download_url(url: str, dest: Path, timeout: int = 20) -> bool:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        if len(data) < 512:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return True
    except Exception as exc:  # noqa: BLE001
        safe_log(f"  下载图片失败: {exc}")
        return False


def _search_wikimedia(query: str, limit: int = 5) -> dict | None:
    params = urllib.parse.urlencode(
        {
            "action": "query",
            "generator": "search",
            "gsrsearch": query,
            "gsrlimit": str(limit),
            "gsrnamespace": "6",
            "prop": "imageinfo",
            "iiprop": "url|mime",
            "iiurlwidth": "960",
            "format": "json",
        }
    )
    url = f"https://commons.wikimedia.org/w/api.php?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_SEARCH_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        safe_log(f"  Wikimedia 检索失败: {exc}")
        return None

    pages = (data.get("query") or {}).get("pages") or {}
    for page in pages.values():
        infos = page.get("imageinfo") or []
        if not infos:
            continue
        info = infos[0]
        mime = str(info.get("mime") or "")
        if not mime.startswith("image/"):
            continue
        thumb = info.get("thumburl") or info.get("url")
        if not thumb:
            continue
        title = str(page.get("title") or query).replace("File:", "")
        ext = "jpg" if "jpeg" in mime else mime.split("/")[-1][:4]
        return {"url": thumb, "title": title, "ext": ext, "source": "wikimedia"}
    return None


def _search_openverse(query: str) -> dict | None:
    params = urllib.parse.urlencode({"q": query, "page_size": "1", "license_type": "commercial,modification"})
    url = f"https://api.openverse.org/v1/images/?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_SEARCH_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        safe_log(f"  Openverse 检索失败: {exc}")
        return None
    results = data.get("results") or []
    if not results:
        return None
    item = results[0]
    thumb = item.get("url") or item.get("thumbnail")
    if not thumb:
        return None
    return {
        "url": thumb,
        "title": str(item.get("title") or query),
        "ext": "jpg",
        "source": "openverse",
    }


def _search_unsplash(query: str) -> dict | None:
    if not UNSPLASH_ACCESS_KEY:
        return None
    params = urllib.parse.urlencode({"query": query, "per_page": "1", "orientation": "landscape"})
    url = f"https://api.unsplash.com/search/photos?{params}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}", "User-Agent": _USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=_SEARCH_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        safe_log(f"  Unsplash 检索失败: {exc}")
        return None
    results = data.get("results") or []
    if not results:
        return None
    item = results[0]
    urls = item.get("urls") or {}
    thumb = urls.get("regular") or urls.get("small")
    if not thumb:
        return None
    return {
        "url": thumb,
        "title": str(item.get("description") or item.get("alt_description") or query),
        "ext": "jpg",
        "source": "unsplash",
    }


def _search_external_image(query: str) -> dict | None:
    """多源搜图；网络不可用时快速返回 None。"""
    queries = [query]
    # 补一条更通用的英文检索
    if re.search(r"[\u4e00-\u9fff]", query):
        queries.append(re.sub(r"[\u4e00-\u9fff]+", " ", query).strip() or f"{query} education")
    for q in queries:
        for fn in (_search_wikimedia, _search_openverse, _search_unsplash):
            hit = fn(q)
            if hit:
                return hit
    return None


def _infer_illustration_kind(text: str) -> str:
    lower = text.lower()
    if any(k in text for k in ("温度计", "thermometer", "气温", "温度", "celsius", "temperature")):
        return "thermometer"
    if any(k in text for k in ("数轴", "numberline", "number line", "有理数", "同号", "异号", "加法", "减法")):
        return "number_line"
    if any(k in text for k in ("坐标", "平面直角", "coordinate", "xy", "函数图像")):
        return "coordinate"
    if any(k in text for k in ("流程", "步骤", "解题", "方法")) or "→" in text:
        return "flowchart"
    if "math" in lower or "数学" in text:
        return "number_line"
    return "flowchart"


def _draw_number_line(draw: ImageDraw.ImageDraw, w: int, h: int, caption: str = "") -> None:
    y = h // 2 + 30
    draw.line((60, y, w - 60, y), fill=(30, 60, 55), width=4)
    draw.polygon([(w - 60, y), (w - 75, y - 8), (w - 75, y + 8)], fill=(30, 60, 55))
    for val, x in zip(range(-5, 6), range(100, w - 100, 70)):
        draw.line((x, y - 10, x, y + 10), fill=(30, 60, 55), width=2)
        draw.text((x - 10, y + 14), str(val), fill=(30, 60, 55), font=_font(16))
    # 示例：同号 / 异号
    if any(k in caption for k in ("同号", "+3", "异号", "-5")):
        draw.text((100, y - 55), "+3 + (+5) = +8  → 向右 8 格", fill=(200, 60, 60), font=_font(18))
        draw.text((100, y + 45), "+3 + (-5) = -2  → 向左 2 格", fill=(45, 125, 110), font=_font(18))


def _draw_thermometer(draw: ImageDraw.ImageDraw, w: int, h: int) -> None:
    cx = w // 2 + 80
    top, bottom = 80, h - 120
    draw.rounded_rectangle((cx - 28, top, cx + 28, bottom), radius=20, outline=(45, 125, 110), width=3)
    draw.ellipse((cx - 38, bottom - 20, cx + 38, bottom + 56), fill=(200, 60, 60), outline=(45, 125, 110), width=2)
    draw.rectangle((cx - 10, top + 80, cx + 10, bottom + 10), fill=(200, 60, 60))
    for t, y in [(-10, bottom - 10), (-5, bottom - 70), (0, bottom - 130), (5, bottom - 190), (10, top + 40)]:
        draw.line((cx + 28, y, cx + 48, y), fill=(30, 60, 55), width=2)
        draw.text((cx + 52, y - 10), f"{t}°C", fill=(30, 60, 55), font=_font(16))
    draw.text((cx - 120, top), "气温变化", fill=(45, 125, 110), font=_font(22))
    draw.text((cx - 180, top + 40), "凌晨 -5°C → 中午 +5°C", fill=(30, 60, 55), font=_font(16))
    draw.text((cx - 180, top + 70), "中午 → 下午 -3°C", fill=(30, 60, 55), font=_font(16))


def _draw_coordinate(draw: ImageDraw.ImageDraw, w: int, h: int) -> None:
    cx, cy = w // 2, h // 2 + 40
    draw.line((80, cy, w - 80, cy), fill=(30, 60, 55), width=2)
    draw.line((cx, 60, cx, h - 60), fill=(30, 60, 55), width=2)
    draw.text((w - 70, cy + 8), "x", fill=(30, 60, 55), font=_font(18))
    draw.text((cx + 8, 50), "y", fill=(30, 60, 55), font=_font(18))


def _draw_flowchart(draw: ImageDraw.ImageDraw, w: int, h: int, prompt: str) -> None:
    steps = re.split(r"[→\->、，,;；\n]", prompt)
    steps = [s.strip() for s in steps if s.strip()][:4] or ["审题", "定符号", "计算", "检验"]
    box_w, box_h, gap = 160, 56, 36
    start_x = max(40, (w - len(steps) * box_w - (len(steps) - 1) * gap) // 2)
    y = h // 2 - box_h // 2
    for i, step in enumerate(steps):
        x = start_x + i * (box_w + gap)
        draw.rounded_rectangle((x, y, x + box_w, y + box_h), radius=10, outline=(45, 125, 110), width=2)
        draw.text((x + 16, y + 16), step[:8], fill=(30, 60, 55), font=_font(16))
        if i < len(steps) - 1:
            ax = x + box_w + 6
            draw.line((ax, y + box_h // 2, ax + gap - 12, y + box_h // 2), fill=(45, 125, 110), width=2)


def _render_illustration(query: str, dest: Path) -> str:
    """本地生成教学示意图（无外网时使用）。"""
    kind = _infer_illustration_kind(query)
    img = Image.new("RGB", (960, 540), color=(248, 252, 251))
    draw = ImageDraw.Draw(img)
    draw.rectangle((16, 16, 944, 524), outline=(200, 220, 215), width=2)

    if kind == "thermometer":
        _draw_thermometer(draw, 960, 540)
    elif kind == "number_line":
        _draw_number_line(draw, 960, 540, query)
    elif kind == "coordinate":
        _draw_coordinate(draw, 960, 540)
    else:
        _draw_flowchart(draw, 960, 540, query)

    draw.text((36, 24), query[:52], fill=(45, 125, 110), font=_font(18))
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, format="PNG")
    return kind


def search_images(query: str, limit: int = 1) -> str:
    """搜索配图；外网不可达时自动生成本地教学示意图。"""
    query = (query or "").strip()
    if not query:
        return json.dumps({"ok": False, "error": "query 不能为空"}, ensure_ascii=False)

    safe_log(f"  [tool] search_images(query={query!r})")
    hit = _search_external_image(query) if MEDIA_SEARCH_ENABLED else None

    if hit:
        ext = hit["ext"] if hit["ext"] != "jpeg" else "jpg"
        media_id = _media_id("img", query, ext)
        dest = MEDIA_DIR / media_id
        if _download_url(hit["url"], dest):
            return _save_result(
                media_id=media_id,
                path=dest,
                title=hit["title"],
                source=hit["source"],
                query_or_prompt=query,
            )

    # 外网搜图关闭或失败 → 生成本地示意图
    if not MEDIA_SEARCH_ENABLED:
        safe_log("  外网搜图已关闭(MEDIA_SEARCH_ENABLED=false)，生成本地示意图")
    else:
        safe_log("  外网搜图不可用，改生成本地示意图")
    media_id = _media_id("img", f"gen:{query}", "png")
    dest = MEDIA_DIR / media_id
    kind = _render_illustration(query, dest)
    return _save_result(
        media_id=media_id,
        path=dest,
        title=f"示意图({kind})",
        source="generated",
        query_or_prompt=query,
    )


def generate_diagram(prompt: str) -> str:
    """根据描述生成简易教学示意图（数轴/温度计/坐标系/流程图等）。"""
    prompt = (prompt or "").strip()
    if not prompt:
        return json.dumps({"ok": False, "error": "prompt 不能为空"}, ensure_ascii=False)

    safe_log(f"  [tool] generate_diagram(prompt={prompt!r})")
    media_id = _media_id("diagram", prompt, "png")
    dest = MEDIA_DIR / media_id
    kind = _render_illustration(prompt, dest)
    return _save_result(
        media_id=media_id,
        path=dest,
        title=f"示意图({kind})",
        source="generated",
        query_or_prompt=prompt,
    )


def resolve_media_path(media_id: str) -> Path | None:
    if not media_id or ".." in media_id or "/" in media_id or "\\" in media_id:
        return None
    path = MEDIA_DIR / media_id
    return path if path.is_file() else None


def parse_media_manifest(chunks: list[str] | str) -> list[dict[str, str]]:
    """从工具返回文本中解析 media_id 清单（仅保留磁盘上存在的文件）。"""
    if isinstance(chunks, str):
        parts = [p.strip() for p in chunks.split("\n\n") if p.strip()]
    else:
        parts = [c.strip() for c in chunks if c.strip()]

    manifest: list[dict[str, str]] = []
    seen: set[str] = set()
    for part in parts:
        if not part.startswith("{"):
            continue
        try:
            data = json.loads(part)
        except json.JSONDecodeError:
            continue
        if not data.get("ok"):
            continue
        source = str(data.get("source") or "")
        if source == "placeholder":
            continue
        media_id = str(data.get("media_id") or "").strip()
        if not media_id or media_id in seen or resolve_media_path(media_id) is None:
            continue
        seen.add(media_id)
        manifest.append(
            {
                "media_id": media_id,
                "source": source or "none",
                "title": str(data.get("title") or ""),
                "query": str(data.get("query") or ""),
                "preview_url": str(data.get("preview_url") or f"/api/media/{media_id}"),
            }
        )
    return manifest


def attach_media_to_slides(slides: Slides, manifest: list[dict[str, str]]) -> Slides:
    """将工具产出的真实 media_id 绑定到课件页。"""
    if not manifest:
        return slides

    generated = [m for m in manifest if m.get("source") == "generated"]
    searched = [m for m in manifest if m.get("source") in {"wikimedia", "unsplash", "openverse"}]
    ordered = generated + searched + [m for m in manifest if m not in generated and m not in searched]

    used: set[str] = set()

    def _take(pool: list[dict[str, str]]) -> dict[str, str] | None:
        for item in pool:
            mid = item["media_id"]
            if mid not in used:
                used.add(mid)
                return item
        return None

    def _apply(page: SlidePage, media: dict[str, str]) -> None:
        src = media.get("source") or "none"
        if src == "generated":
            page.image_source = "generated"
        elif src in {"wikimedia", "unsplash", "openverse"}:
            page.image_source = "search"
        else:
            page.image_source = "none"
        page.image_id = media["media_id"]
        if not page.image_caption:
            page.image_caption = media.get("title") or media.get("query") or ""

    for page in slides.pages:
        if page.image_id and resolve_media_path(page.image_id):
            used.add(page.image_id)
            continue
        page.image_id = ""
        page.image_source = "none"

        hint = f"{page.title} {page.linked_stage} {' '.join(page.visual_keywords)}"
        if page.media_type_suggestion == "diagram" or any(
            k in hint for k in ("数轴", "流程", "示意图", "坐标", "结构", "温度", "气温")
        ):
            media = _take(generated) or _take(ordered)
        elif page.media_type_suggestion == "image" or any(k in hint for k in ("情境", "导入", "生活")):
            media = _take(searched) or _take(generated) or _take(ordered)
        else:
            media = None
        if media:
            _apply(page, media)

    for page in slides.pages:
        if page.image_id:
            continue
        media = _take(ordered)
        if media:
            _apply(page, media)

    return slides
