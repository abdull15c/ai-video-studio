import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple

import requests

from config import Config
from modules.ffmpeg_util import validate_stock_clip, validate_video_file

logger = logging.getLogger(__name__)
_HTTP_TIMEOUT = Config.PEXELS_HTTP_TIMEOUT_SEC
_DOWNLOAD_READ_TIMEOUT = int(getattr(Config, "STOCK_DOWNLOAD_TIMEOUT_SEC", 30))
_SLOW_WINDOW_SEC = 10.0
_SLOW_MIN_BYTES = 100_000

_STOCK_BYTES_PER_SEC = float(getattr(Config, "STOCK_MIN_BYTES_PER_SEC", 200_000))

_MIN_W_PORTRAIT = 720
_MIN_H_PORTRAIT = 1280
_MIN_W_LANDSCAPE = 1920
_MIN_H_LANDSCAPE = 1080

_STOCK_PER_PAGE = 15


@dataclass(frozen=True)
class _StockCandidate:
    url: str
    duration: float
    width: int
    height: int
    source: str  # "pexels" | "pixabay"
    pexels_quality: str
    pixabay_tier: str
    api_index: int


def download_video(url, save_path, *, min_duration_required: Optional[float] = None) -> bool:
    """
    Скачивание с таймаутом, анти-зависанием (мало данных за 10 с — прерывание).
    После сохранения — ffprobe: валидное видео, длительность > 1 с;
    если задан min_duration_required — клип не короче сцены + fps/кадры + байт/сек (анти-статика).
    """
    logger.info("Скачивание %s...", url[:60])
    for attempt in range(1, 4):
        try:
            if os.path.exists(save_path):
                try:
                    os.remove(save_path)
                except OSError:
                    pass
            response = requests.get(
                url,
                stream=True,
                timeout=(15, _DOWNLOAD_READ_TIMEOUT),
            )
            response.raise_for_status()
            t0 = time.monotonic()
            last_check = t0
            downloaded = 0
            with open(save_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=32768):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    now = time.monotonic()
                    if now - t0 > _SLOW_WINDOW_SEC and downloaded < _SLOW_MIN_BYTES:
                        raise IOError(
                            f"медленная загрузка: за {_SLOW_WINDOW_SEC:.0f}s < {_SLOW_MIN_BYTES} B"
                        )
                    if now - last_check > 5.0:
                        last_check = now
            if not os.path.exists(save_path) or os.path.getsize(save_path) < 50_000:
                raise IOError("файл пустой или слишком маленький")

            ok, err = validate_video_file(save_path, min_duration_sec=1.0, min_fps=0.0, min_bytes_per_sec=8_000.0)
            if not ok:
                raise IOError(err)

            if min_duration_required is not None and min_duration_required > 0:
                ok2, err2 = validate_stock_clip(
                    save_path,
                    min_duration_sec=float(min_duration_required),
                    min_fps=15.0,
                    min_bytes_per_sec=_STOCK_BYTES_PER_SEC,
                )
                if not ok2:
                    raise IOError(err2)

            logger.info("Файл сохранён и проверен: %s", save_path)
            return True
        except Exception as e:
            logger.warning("Попытка %s/3 не удалась: %s", attempt, e)
            if os.path.exists(save_path):
                try:
                    os.remove(save_path)
                except OSError:
                    pass
            time.sleep(2)
    logger.error("Скачивание не удалось: %s", url[:80])
    return False


def _pexels_quality_rank(q: str, is_vertical: bool) -> int:
    q = (q or "").lower()
    if is_vertical:
        order = ("uhd", "hd", "sd", "hls", "mobile")
    else:
        order = ("uhd", "full_hd", "hd", "sd", "hls", "mobile")
    try:
        return order.index(q)
    except ValueError:
        return len(order)


def _pexels_file_is_video(f: dict) -> bool:
    t = (f.get("type") or "").strip().lower()
    if t in ("photo", "image", "picture"):
        return False
    if t and ("photo" in t or "image" in t) and "video" not in t:
        return False
    ft = (f.get("file_type") or "").lower()
    if ft.startswith("image/"):
        return False
    return True


def _dims_ok_portrait_landscape(w: int, h: int, is_vertical: bool, strict: bool) -> bool:
    if is_vertical:
        ok = h >= _MIN_H_PORTRAIT and w >= _MIN_W_PORTRAIT
        if not strict and not ok:
            ok = h >= 1080 and w >= 540
    else:
        ok = w >= _MIN_W_LANDSCAPE and h >= _MIN_H_LANDSCAPE
        if not strict and not ok:
            ok = w >= 1280 and h >= 720
    return ok


def _pexels_pick_file(
    video_files: List[dict], is_vertical: bool, strict: bool
) -> Optional[Tuple[str, int, int, str]]:
    """Лучший video_file: сначала больше пикселей, затем выше quality (uhd > hd)."""
    vf = [f for f in video_files if _pexels_file_is_video(f)]
    if not vf:
        return None
    pool = [f for f in vf if _dims_ok_portrait_landscape(int(f.get("width") or 0), int(f.get("height") or 0), is_vertical, strict)]
    if not pool:
        pool = [] if strict else vf
    if not pool:
        return None
    pool.sort(
        key=lambda f: (
            -int(f.get("width") or 0) * int(f.get("height") or 0),
            _pexels_quality_rank(f.get("quality") or "", is_vertical),
        )
    )
    best = pool[0]
    w, h = int(best.get("width") or 0), int(best.get("height") or 0)
    link = best.get("link")
    if not link:
        return None
    return (str(link), w, h, str(best.get("quality") or ""))


def _pexels_video_is_real_video(video: dict) -> bool:
    t = (video.get("type") or "").strip().lower()
    if t in ("photo", "image"):
        return False
    return True


def _pixabay_tier_rank(key: str) -> int:
    return {"large": 0, "medium": 1, "small": 2, "tiny": 3}.get((key or "").lower(), 9)


def _pixabay_unified_score(tier_key: str) -> int:
    """Выше число — хуже; Pexels HD (ранг 2) должен быть лучше Pixabay medium (10)."""
    return {"large": 6, "medium": 10, "small": 14, "tiny": 18}.get((tier_key or "").lower(), 20)


def _pixabay_pick_variant(
    hit: dict, is_vertical: bool, strict: bool
) -> Optional[Tuple[str, int, int, str]]:
    """Лучший вариант: крупнее разрешение, затем large > medium > small."""
    vids = hit.get("videos") or {}
    blocks: List[Tuple[str, dict]] = []
    for key in ("large", "medium", "small", "tiny"):
        block = vids.get(key)
        if block and block.get("url"):
            blocks.append((key, block))
    if not blocks:
        return None
    ok_blocks = []
    for key, block in blocks:
        w = int(block.get("width") or 0)
        h = int(block.get("height") or 0)
        if _dims_ok_portrait_landscape(w, h, is_vertical, strict):
            ok_blocks.append((key, block, w, h))
    if ok_blocks:
        use = ok_blocks
    elif strict:
        use = []
    else:
        use = [(k, b, int(b.get("width") or 0), int(b.get("height") or 0)) for k, b in blocks]
    if not use:
        return None
    use.sort(
        key=lambda t: (
            -t[2] * t[3],
            _pixabay_tier_rank(t[0]),
        )
    )
    key, block, w, h = use[0]
    return (str(block["url"]), w, h, key)


def _unified_quality_score(c: _StockCandidate, is_vertical: bool) -> int:
    if c.source == "pexels":
        return _pexels_quality_rank(c.pexels_quality, is_vertical)
    return _pixabay_unified_score(c.pixabay_tier)


def _duration_sort_key(duration: float, target: float) -> Tuple[int, float]:
    """Меньше кортеж — лучше: не короче сцены, ближе по длине, штраф за слишком длинные."""
    d = float(duration or 0)
    if target <= 0:
        return (0, 0.0)
    if d < target:
        return (3, target - d)
    if d <= target * 2.2:
        return (0, abs(d - target))
    return (1, d - target)


def _candidate_sort_key(c: _StockCandidate, target_duration: float, is_vertical: bool) -> Tuple:
    dk = _duration_sort_key(c.duration, target_duration)
    qs = _unified_quality_score(c, is_vertical)
    pixels = -(c.width * c.height)
    src_tie = 0 if c.source == "pexels" else 1
    return (dk[0], dk[1], qs, pixels, src_tie, c.api_index)


def _fetch_pexels_videos(query: str, is_vertical: bool) -> List[dict]:
    if not Config.PEXELS_API_KEY:
        return []
    orient = "portrait" if is_vertical else "landscape"
    q = requests.utils.quote(query)
    url = (
        f"https://api.pexels.com/videos/search?query={q}&orientation={orient}"
        f"&per_page={_STOCK_PER_PAGE}&size=large"
    )
    try:
        res = requests.get(url, headers={"Authorization": Config.PEXELS_API_KEY}, timeout=_HTTP_TIMEOUT)
        res.raise_for_status()
        data = res.json()
        return list(data.get("videos") or [])
    except (
        requests.exceptions.RequestException,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as e:
        logger.warning("Pexels fetch: %s", e)
        return []


def _fetch_pixabay_hits(query: str, is_vertical: bool) -> List[dict]:
    if not Config.PIXABAY_API_KEY:
        return []
    if is_vertical:
        min_w, min_h = 720, 1280
    else:
        min_w, min_h = 1280, 720
    q = requests.utils.quote(query)
    url = (
        f"https://pixabay.com/api/videos/?key={Config.PIXABAY_API_KEY}&q={q}&per_page={_STOCK_PER_PAGE}"
        f"&video_type=film&min_width={min_w}&min_height={min_h}"
    )
    try:
        res = requests.get(url, timeout=_HTTP_TIMEOUT)
        res.raise_for_status()
        data = res.json()
        return list(data.get("hits") or [])
    except (
        requests.exceptions.RequestException,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as e:
        logger.warning("Pixabay fetch: %s", e)
        return []


def _pexels_to_candidates(
    videos: List[dict],
    is_vertical: bool,
    min_duration_sec: float,
    used_urls: Set[str],
    strict_res: bool,
) -> List[_StockCandidate]:
    out: List[_StockCandidate] = []
    for idx, video in enumerate(videos):
        if not _pexels_video_is_real_video(video):
            continue
        dur = float(video.get("duration") or 0)
        if min_duration_sec > 0 and dur < min_duration_sec:
            continue
        picked = _pexels_pick_file(list(video.get("video_files") or []), is_vertical, strict_res)
        if not picked:
            continue
        url, w, h, qual = picked
        if url in used_urls:
            continue
        out.append(
            _StockCandidate(
                url=url,
                duration=dur,
                width=w,
                height=h,
                source="pexels",
                pexels_quality=qual,
                pixabay_tier="",
                api_index=idx,
            )
        )
    return out


def _pixabay_to_candidates(
    hits: List[dict],
    is_vertical: bool,
    min_duration_sec: float,
    used_urls: Set[str],
    strict_res: bool,
) -> List[_StockCandidate]:
    out: List[_StockCandidate] = []
    for idx, hit in enumerate(hits):
        dur = float(hit.get("duration") or 0)
        if min_duration_sec > 0 and dur < min_duration_sec:
            continue
        picked = _pixabay_pick_variant(hit, is_vertical, strict_res)
        if not picked:
            continue
        url, w, h, tier = picked
        if url in used_urls:
            continue
        out.append(
            _StockCandidate(
                url=url,
                duration=dur,
                width=w,
                height=h,
                source="pixabay",
                pexels_quality="",
                pixabay_tier=tier,
                api_index=idx,
            )
        )
    return out


def _merged_candidates_for_query(
    query: str,
    is_vertical: bool,
    min_duration_sec: float,
    used_urls: Set[str],
    strict_res: bool,
) -> List[_StockCandidate]:
    """Параллельно Pexels + Pixabay, общий пул, без скачивания."""
    q = (query or "").strip()
    if len(q) < 2:
        return []
    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(_fetch_pexels_videos, q, is_vertical)
        f2 = ex.submit(_fetch_pixabay_hits, q, is_vertical)
        try:
            pexels_videos = f1.result()
        except Exception as e:
            logger.warning("Pexels: %s", e)
            pexels_videos = []
        try:
            pixabay_hits = f2.result()
        except Exception as e:
            logger.warning("Pixabay: %s", e)
            pixabay_hits = []

    merged: List[_StockCandidate] = []
    merged.extend(_pexels_to_candidates(pexels_videos, is_vertical, min_duration_sec, used_urls, strict_res))
    merged.extend(_pixabay_to_candidates(pixabay_hits, is_vertical, min_duration_sec, used_urls, strict_res))
    return merged


def _sort_candidates(
    candidates: List[_StockCandidate], target_duration: float, is_vertical: bool
) -> List[_StockCandidate]:
    return sorted(
        candidates,
        key=lambda c: _candidate_sort_key(c, target_duration, is_vertical),
    )


def get_smart_stock(
    visual_plan,
    is_vertical,
    target_path,
    min_duration_sec: Optional[float] = None,
    used_urls: Optional[Set[str]] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Подбор стока: для каждого запроса объединяются Pexels и Pixabay, кандидаты сортируются,
    скачивание по очереди до первого успеха.
    used_urls — URL уже использованных в проекте клипов (не повторять).
    Возвращает (успех, url_источника).
    """
    if used_urls is None:
        used_urls = set()

    queries_to_try = (
        [visual_plan["primary_query"]]
        + list(visual_plan.get("alt_queries") or [])
        + list(visual_plan.get("fallback_queries") or [])
    )

    required_sec = float(min_duration_sec) if min_duration_sec is not None else 8.0
    required_sec = max(1.5, required_sec)
    api_thresholds = [
        required_sec,
        max(required_sec * 0.88, 4.0),
        max(required_sec * 0.75, 3.0),
    ]

    def try_download_sorted(candidates: List[_StockCandidate]) -> Optional[str]:
        for c in candidates:
            if c.url in used_urls:
                continue
            if download_video(c.url, target_path, min_duration_required=required_sec):
                return c.url
        return None

    for min_api in api_thresholds:
        for q in queries_to_try:
            if not q or len(str(q).strip()) < 2:
                continue
            logger.info(
                "Поиск стока (Pexels+Pixabay) по %r: API duration≥%.1fs, ffprobe≥%.1fs",
                q,
                min_api,
                required_sec,
            )
            for strict in (True, False):
                pool = _merged_candidates_for_query(
                    str(q).strip(), is_vertical, min_api, used_urls, strict
                )
                ranked = _sort_candidates(pool, required_sec, is_vertical)
                url = try_download_sorted(ranked)
                if url:
                    return True, url

    logger.info("Резервный поиск фона (оба API)")
    for min_api in api_thresholds:
        for strict in (True, False):
            pool = _merged_candidates_for_query(
                "dark abstract background", is_vertical, min_api, used_urls, strict
            )
            ranked = _sort_candidates(pool, required_sec, is_vertical)
            url = try_download_sorted(ranked)
            if url:
                return True, url
    return False, None
