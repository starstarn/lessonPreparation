"""课件配图：搜图下载与本地教学示意图生成。"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from lesson_prep.config import MEDIA_DIR, MEDIA_SEARCH_ENABLED, ROOT_DIR, UNSPLASH_ACCESS_KEY
from lesson_prep.logutil import safe_log
from lesson_prep.schemas import SlidePage, Slides

MEDIA_DIR.mkdir(parents=True, exist_ok=True)

_USER_AGENT = "LessonPrepBot/0.1 (K12 education lesson prep; local demo)"
_SEARCH_TIMEOUT = 8
_DOWNLOAD_TIMEOUT = 20
_BUNDLED_FONT = ROOT_DIR / "assets" / "fonts" / "wqy-microhei.ttc"


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        str(_BUNDLED_FONT),
        "/Library/Fonts/Arial Unicode.ttf",
        "msyh.ttc",
        "simhei.ttf",
        "arial.ttf",
    ]
    for name in candidates:
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


def _http_get_json(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: int = _SEARCH_TIMEOUT,
) -> tuple[dict | None, str | None]:
    """返回 (json, error)。error 非空表示网络/HTTP 失败。"""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def _download_url(url: str, dest: Path, timeout: int = _DOWNLOAD_TIMEOUT) -> bool:
    """下载图片；直连失败时经 wsrv.nl 代理再试（缓解 Flickr 等 CDN 502）。"""
    import time

    candidates = [url]
    if "wsrv.nl" not in url and "images.weserv.nl" not in url:
        proxied = (
            "https://wsrv.nl/?url="
            + urllib.parse.quote(url, safe="")
            + "&w=960&output=jpg"
        )
        candidates.append(proxied)

    last_err = ""
    for attempt_url in candidates:
        for attempt in range(2):
            req = urllib.request.Request(
                attempt_url,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = resp.read()
                if len(data) < 512:
                    last_err = "content too small"
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                return True
            except Exception as exc:  # noqa: BLE001
                last_err = str(exc)
                time.sleep(0.5 * (attempt + 1))
    safe_log(f"  下载图片失败: {last_err}")
    return False


def _search_wikimedia(query: str, limit: int = 5) -> tuple[list[dict], bool]:
    """返回 (hits, network_error)。"""
    params = urllib.parse.urlencode(
        {
            "action": "query",
            "generator": "search",
            "gsrsearch": query,
            "gsrlimit": str(limit),
            "gsrnamespace": "6",
            "prop": "imageinfo",
            "iiprop": "url|mime",
            "iiurlwidth": "800",
            "format": "json",
        }
    )
    url = f"https://commons.wikimedia.org/w/api.php?{params}"
    data, err = _http_get_json(url)
    if err:
        safe_log(f"  Wikimedia 检索失败: {err}")
        return [], True
    hits: list[dict] = []
    pages = (data.get("query") or {}).get("pages") or {}
    for page in pages.values():
        infos = page.get("imageinfo") or []
        if not infos:
            continue
        info = infos[0]
        mime = str(info.get("mime") or "")
        if not mime.startswith("image/"):
            continue
        # 优先缩略图；原图容易触发 upload.wikimedia.org 429
        thumb = info.get("thumburl") or info.get("url")
        if not thumb:
            continue
        title = str(page.get("title") or query).replace("File:", "")
        ext = "jpg" if "jpeg" in mime else mime.split("/")[-1][:4]
        hits.append({"url": thumb, "title": title, "ext": ext, "source": "wikimedia"})
    return hits, False


def _search_openverse(query: str) -> tuple[list[dict], bool]:
    params = urllib.parse.urlencode({"q": query, "page_size": "5"})
    url = f"https://api.openverse.org/v1/images/?{params}"
    data, err = _http_get_json(url)
    if err:
        safe_log(f"  Openverse 检索失败: {err}")
        return [], True
    hits: list[dict] = []
    for item in data.get("results") or []:
        title = str(item.get("title") or query)
        # 缩略图优先：比 Flickr 原图更稳；原图作为备选
        thumb = item.get("thumbnail")
        original = item.get("url")
        if thumb:
            hits.append({"url": thumb, "title": title, "ext": "jpg", "source": "openverse"})
        if original and original != thumb:
            hits.append({"url": original, "title": title, "ext": "jpg", "source": "openverse"})
    return hits, False


def _search_unsplash(query: str) -> tuple[list[dict], bool]:
    if not UNSPLASH_ACCESS_KEY:
        return [], False
    params = urllib.parse.urlencode({"query": query, "per_page": "3", "orientation": "landscape"})
    url = f"https://api.unsplash.com/search/photos?{params}"
    data, err = _http_get_json(
        url,
        headers={"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"},
    )
    if err:
        safe_log(f"  Unsplash 检索失败: {err}")
        return [], True
    hits: list[dict] = []
    for item in data.get("results") or []:
        urls = item.get("urls") or {}
        thumb = urls.get("regular") or urls.get("small")
        if not thumb:
            continue
        hits.append(
            {
                "url": thumb,
                "title": str(item.get("description") or item.get("alt_description") or query),
                "ext": "jpg",
                "source": "unsplash",
            }
        )
    return hits, False


def _expand_search_queries(query: str) -> list[str]:
    """为中文/课堂词补充更易命中的英文检索词。"""
    q = (query or "").strip()
    out: list[str] = []
    if q:
        out.append(q)

    mapping = [
        (("温度计", "气温", "温度", "thermometer", "celsius", "temperature"), "thermometer celsius temperature"),
        (("数轴", "有理数", "number line"), "number line mathematics"),
        (("坐标", "coordinate"), "cartesian coordinate plane math"),
        (("课堂", "教室", "education"), "classroom education students"),
        (("数学", "math"), "mathematics education diagram"),
    ]
    lower = q.lower()
    for keys, en in mapping:
        if any(k in q or k in lower for k in keys):
            out.append(en)

    # 避免默认塞 "classroom" 类图库图（黑板/教室库存照），优先保留情境词
    if re.search(r"[\u4e00-\u9fff]", q):
        ascii_q = re.sub(r"[\u4e00-\u9fff]+", " ", q).strip()
        if ascii_q:
            out.append(ascii_q)
        if any(k in q for k in ("导入", "情境", "生活", "游戏", "故事")):
            out.append("everyday life scene photography")

    seen: set[str] = set()
    unique: list[str] = []
    for item in out:
        key = item.lower()
        if key not in seen and "classroom" not in key and "blackboard" not in key:
            seen.add(key)
            unique.append(item)
    return unique[:4]


def _collect_image_candidates(query: str, max_candidates: int = 8) -> list[dict]:
    """汇总多源候选；Openverse 优先（下载更稳），Wikimedia 次之。"""
    # Openverse 优先：避免 Wikimedia 缩略图 429
    sources = (_search_openverse, _search_unsplash, _search_wikimedia)
    candidates: list[dict] = []
    seen_urls: set[str] = set()
    network_errors = 0

    for q in _expand_search_queries(query):
        for fn in sources:
            hits, net_err = fn(q)
            if net_err:
                network_errors += 1
            for hit in hits:
                url = str(hit.get("url") or "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                candidates.append(hit)
                if len(candidates) >= max_candidates:
                    return candidates
            if network_errors >= 3:
                safe_log("  外网搜图连续失败较多，停止继续检索")
                return candidates
    return candidates


def _search_external_image(query: str) -> dict | None:
    """兼容旧调用：返回第一个候选（实际下载在 search_images 里多候选重试）。"""
    candidates = _collect_image_candidates(query, max_candidates=1)
    if candidates:
        safe_log(f"  搜图命中 [{candidates[0]['source']}] query={query!r}")
        return candidates[0]
    return None


def _infer_illustration_kind(text: str) -> str:
    lower = text.lower()
    if any(k in text for k in ("温度计", "thermometer", "气温", "温度", "celsius", "temperature")):
        return "thermometer"
    if any(
        k in text
        for k in ("数轴", "numberline", "number line", "有理数", "同号", "异号", "正负数", "整数加减")
    ):
        return "number_line"
    if any(k in text for k in ("坐标", "平面直角", "coordinate", "xy", "函数图像", "一次函数", "二次函数")):
        return "coordinate"
    if any(k in text for k in ("流程", "步骤", "解题", "方法", "法则", "运算顺序")) or "→" in text:
        return "flowchart"
    if any(k in text for k in ("加法", "减法", "乘法", "除法", "运算")):
        return "number_line"
    if "math" in lower or "数学" in text:
        return "flowchart"
    return "flowchart"


def suggested_diagram_prompts(lesson_title: str, stage_names: list[str] | None = None) -> list[str]:
    """按课题生成 1～2 条本地示意图 prompt（不依赖 LLM）。"""
    title = (lesson_title or "").strip() or "本课"
    stages = stage_names or []
    prompts: list[str] = []

    if any(k in title for k in ("有理数", "正负数", "数轴", "整数")):
        prompts.append(f"{title} 数轴 同号异号加减示意")
        prompts.append(f"{title} 审题→定符号→计算→检验")
    elif any(k in title for k in ("温度", "气温")):
        prompts.append(f"{title} 温度计 正负气温变化")
        prompts.append(f"{title} 观察→列式→计算→检验")
    elif any(k in title for k in ("坐标", "函数", "图像")):
        prompts.append(f"{title} 平面直角坐标系示意")
        prompts.append(f"{title} 读题→画图→验证→结论")
    elif any(k in title for k in ("方程", "不等式")):
        prompts.append(f"{title} 审题→列式→求解→检验")
    else:
        prompts.append(f"{title} 核心概念示意图")
        prompts.append(f"{title} 观察→归纳→应用→检验")

    # 导入/情境环节优先温度计等生活示意
    joined = " ".join(stages)
    if any(k in joined for k in ("导入", "情境")) and not any("温度" in p for p in prompts):
        if any(k in title for k in ("有理数", "正负", "加减", "整数")):
            prompts.insert(0, f"{title} 温度计 气温变化引入")

    seen: set[str] = set()
    out: list[str] = []
    for p in prompts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out[:2]


def suggested_search_queries(lesson_title: str, stage_names: list[str] | None = None) -> list[str]:
    """导入/情境页的英文搜图词（避免 classroom 库存照）。"""
    title = lesson_title or ""
    queries: list[str] = []
    if any(k in title for k in ("有理数", "正负", "温度", "气温", "加减")):
        queries.append("thermometer celsius weather temperature")
    if any(k in title for k in ("分数", "比")):
        queries.append("pizza slices equal parts food")
    if any(k in title for k in ("几何", "三角形", "圆", "面积")):
        queries.append("geometry shapes architecture pattern")
    if any(k in title for k in ("统计", "概率")):
        queries.append("colorful bar chart data visualization")
    stages = " ".join(stage_names or [])
    if any(k in stages for k in ("导入", "情境", "游戏")) and not queries:
        queries.append("everyday life objects photography")
    return queries[:2]


def _draw_number_line(draw: ImageDraw.ImageDraw, w: int, h: int, caption: str = "") -> None:
    y = h // 2 + 20
    origin_x = w // 2
    step = 70
    draw.line((60, y, w - 60, y), fill=(30, 60, 55), width=4)
    draw.polygon([(w - 60, y), (w - 75, y - 8), (w - 75, y + 8)], fill=(30, 60, 55))
    for val in range(-5, 6):
        x = origin_x + val * step
        if x < 80 or x > w - 80:
            continue
        draw.line((x, y - 12, x, y + 12), fill=(30, 60, 55), width=2)
        draw.text((x - 8, y + 16), str(val), fill=(30, 60, 55), font=_font(16))
    # 原点加粗
    draw.ellipse((origin_x - 5, y - 5, origin_x + 5, y + 5), fill=(45, 125, 110))
    draw.text((origin_x - 18, y - 36), "O", fill=(45, 125, 110), font=_font(16))

    # 示例箭头：同号向右 / 异号向左（默认展示，便于课堂投影）
    show_demo = True
    if caption and not any(
        k in caption for k in ("数轴", "有理数", "同号", "异号", "加法", "减法", "整数", "number")
    ):
        show_demo = "运算" in caption or "法则" in caption
    if show_demo:
        # +3 → +8
        x1, x2 = origin_x + 3 * step, origin_x + 8 * step
        draw.line((x1, y - 48, x2, y - 48), fill=(200, 60, 60), width=3)
        draw.polygon([(x2, y - 48), (x2 - 12, y - 56), (x2 - 12, y - 40)], fill=(200, 60, 60))
        draw.text((x1, y - 78), "同号：+3+(+5) 向右 5", fill=(200, 60, 60), font=_font(17))
        # +3 → -2
        x3 = origin_x + (-2) * step
        draw.line((x1, y + 55, x3, y + 55), fill=(45, 125, 110), width=3)
        draw.polygon([(x3, y + 55), (x3 + 12, y + 47), (x3 + 12, y + 63)], fill=(45, 125, 110))
        draw.text((min(x1, x3), y + 68), "异号：+3+(-5) 向左 2", fill=(45, 125, 110), font=_font(17))


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
    raw = re.split(r"[→\->、，,;；\n]", prompt)
    steps = [s.strip() for s in raw if s.strip()]
    # 去掉课题长标题，只留短步骤词
    cleaned: list[str] = []
    for s in steps:
        if len(s) > 10 and any(k in s for k in ("有理数", "课题", "示意", "核心")):
            continue
        cleaned.append(s[:8])
    steps = cleaned[:4] or ["审题", "定符号", "计算", "检验"]
    if len(steps) < 2:
        steps = ["观察", "归纳", "应用", "检验"]

    box_w, box_h, gap = 160, 64, 36
    start_x = max(40, (w - len(steps) * box_w - (len(steps) - 1) * gap) // 2)
    y = h // 2 - box_h // 2
    palette = [(45, 125, 110), (60, 140, 160), (200, 120, 60), (120, 90, 160)]
    for i, step in enumerate(steps):
        x = start_x + i * (box_w + gap)
        color = palette[i % len(palette)]
        draw.rounded_rectangle((x, y, x + box_w, y + box_h), radius=12, outline=color, width=3)
        draw.text((x + 18, y + 20), step, fill=(30, 60, 55), font=_font(18))
        if i < len(steps) - 1:
            ax = x + box_w + 6
            mid_y = y + box_h // 2
            draw.line((ax, mid_y, ax + gap - 14, mid_y), fill=color, width=3)
            draw.polygon(
                [(ax + gap - 14, mid_y), (ax + gap - 26, mid_y - 7), (ax + gap - 26, mid_y + 7)],
                fill=color,
            )


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
    """搜索配图；优先下载真图，多候选重试；全部失败才本地示意图。"""
    import time

    query = (query or "").strip()
    if not query:
        return json.dumps({"ok": False, "error": "query 不能为空"}, ensure_ascii=False)

    safe_log(f"  [tool] search_images(query={query!r})")
    if MEDIA_SEARCH_ENABLED:
        candidates = _collect_image_candidates(query, max_candidates=max(3, min(int(limit or 1) * 3, 8)))
        safe_log(f"  搜图候选 {len(candidates)} 个")
        blocked_hosts: set[str] = set()
        for i, hit in enumerate(candidates):
            url = str(hit.get("url") or "")
            host = urllib.parse.urlparse(url).netloc
            if host in blocked_hosts:
                continue
            ext = hit.get("ext") if hit.get("ext") != "jpeg" else "jpg"
            media_id = _media_id("img", f"{query}:{i}:{url}", str(ext or "jpg"))
            dest = MEDIA_DIR / media_id
            # 已缓存成功文件则直接复用
            if dest.is_file() and dest.stat().st_size > 512:
                safe_log(f"  复用已下载图片 [{hit.get('source')}] {media_id}")
                return _save_result(
                    media_id=media_id,
                    path=dest,
                    title=str(hit.get("title") or query),
                    source=str(hit.get("source") or "search"),
                    query_or_prompt=query,
                )
            if _download_url(url, dest):
                safe_log(f"  下载成功 [{hit.get('source')}] {media_id}")
                return _save_result(
                    media_id=media_id,
                    path=dest,
                    title=str(hit.get("title") or query),
                    source=str(hit.get("source") or "search"),
                    query_or_prompt=query,
                )
            # 直连失败且带 429/502 的图源，同 host 后续少试几次
            if "429" in str(url) or "wikimedia" in host or "wikipedia" in host or "staticflickr" in host:
                blocked_hosts.add(host)
                safe_log(f"  暂时跳过不稳定图源: {host}")
            time.sleep(0.3)

    # 外网搜图关闭或失败 → 生成本地示意图
    if not MEDIA_SEARCH_ENABLED:
        safe_log("  外网搜图已关闭(MEDIA_SEARCH_ENABLED=false)，生成本地示意图")
    else:
        safe_log("  外网真图下载均失败，改生成本地示意图")
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


def _media_relevance(page: SlidePage, media: dict[str, str]) -> int:
    """页标题/环节与素材 query 的简单相关度。"""
    hay = f"{page.title} {page.linked_stage} {' '.join(page.visual_keywords)}".lower()
    needle = f"{media.get('query') or ''} {media.get('title') or ''}".lower()
    score = 0
    for token in ("数轴", "温度", "气温", "坐标", "流程", "导入", "情境", "法则", "同号", "异号", "thermometer", "number"):
        if token in hay and token in needle:
            score += 3
    for kw in page.visual_keywords or []:
        if kw and kw.lower() in needle:
            score += 2
    return score


def attach_media_to_slides(slides: Slides, manifest: list[dict[str, str]]) -> Slides:
    """将工具产出的真实 media_id 绑定到课件页。"""
    if not manifest:
        return slides

    generated = [m for m in manifest if m.get("source") == "generated"]
    searched = [m for m in manifest if m.get("source") in {"wikimedia", "unsplash", "openverse"}]
    ordered = generated + searched + [m for m in manifest if m not in generated and m not in searched]

    used: set[str] = set()

    def _take(pool: list[dict[str, str]], page: SlidePage | None = None) -> dict[str, str] | None:
        available = [item for item in pool if item["media_id"] not in used]
        if not available:
            return None
        if page is not None:
            available.sort(key=lambda m: _media_relevance(page, m), reverse=True)
        item = available[0]
        used.add(item["media_id"])
        return item

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

    # 优先给「需要图」的页配图：概念/导入/新授
    priority_pages = []
    normal_pages = []
    for page in slides.pages:
        if page.image_id and resolve_media_path(page.image_id):
            used.add(page.image_id)
            continue
        page.image_id = ""
        page.image_source = "none"
        hint = f"{page.title} {page.linked_stage} {' '.join(page.visual_keywords)}"
        needs = (
            page.media_type_suggestion in {"diagram", "image"}
            or any(k in hint for k in ("数轴", "流程", "示意图", "坐标", "结构", "温度", "气温", "情境", "导入", "新授", "法则", "概念"))
        )
        (priority_pages if needs else normal_pages).append(page)

    for page in priority_pages:
        hint = f"{page.title} {page.linked_stage} {' '.join(page.visual_keywords)}"
        if page.media_type_suggestion == "diagram" or any(
            k in hint for k in ("数轴", "流程", "示意图", "坐标", "结构", "法则", "概念", "运算")
        ):
            media = _take(generated, page) or _take(ordered, page)
        elif page.media_type_suggestion == "image" or any(k in hint for k in ("情境", "导入", "生活")):
            media = _take(searched, page) or _take(generated, page) or _take(ordered, page)
        else:
            media = _take(generated, page) or _take(searched, page) or _take(ordered, page)
        if media:
            _apply(page, media)

    for page in normal_pages:
        if page.image_id:
            continue
        media = _take(ordered, page)
        if media:
            _apply(page, media)

    return slides
