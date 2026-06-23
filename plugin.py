"""
Pixiv image search and fetch plugin.

The plugin uses the Lolicon API v2 as a Pixiv artwork index and image proxy.
It downloads selected artwork pages into Nekro Agent's per-chat upload
directory and returns sandbox-visible file paths for follow-up sending.
"""

from __future__ import annotations

import hashlib
import io
import re
import time
import urllib.parse
import zipfile
from typing import Any, Dict, List

from pydantic import Field

from nekro_agent.api import core, i18n
from nekro_agent.api.plugin import ConfigBase, ExtraField, NekroPlugin, SandboxMethodType, dynamic_import_pkg
from nekro_agent.api.schemas import AgentCtx
from nekro_agent.core import logger
from nekro_agent.tools.common_util import download_file_from_bytes
from nekro_agent.tools.path_convertor import convert_filename_to_sandbox_upload_path

httpx = dynamic_import_pkg("httpx")

PIXIV_APP_USER_AGENT = "PixivAndroidApp/5.0.234 (Android 11; Pixel 5)"
PIXIV_CLIENT_ID = "MOBrBDS8blbauoSck0ZfDbtuzpyT"
PIXIV_CLIENT_SECRET = "lsACyCD94FhDUtGTXi3QzcFE2uU1hqtDaKeqrdwj"
PIXIV_AUTH_TOKEN_URL = "https://oauth.secure.pixiv.net/auth/token"
PIXIV_APP_API_BASE = "https://app-api.pixiv.net"
_token_cache: Dict[str, Any] = {"access_token": "", "expires_at": 0.0, "refresh_token": ""}


plugin = NekroPlugin(
    name="Pixiv图片搜索获取",
    module_name="nekro_plugin_pixiv_fetcher",
    description="按标签、关键词、作者UID等条件检索 Pixiv 插画，并下载为可发送图片文件",
    version="1.0.0",
    author="Akiyo_dayo",
    url="https://github.com/Akiyo-dayo/nekro-plugin-pixiv-fetcher",
    i18n_name=i18n.i18n_text(
        zh_CN="Pixiv图片搜索获取",
        en_US="Pixiv Image Search and Fetch",
    ),
    i18n_description=i18n.i18n_text(
        zh_CN="按条件检索 Pixiv 插画并下载图片文件，默认过滤 R18 内容",
        en_US="Search Pixiv artworks and download image files; R18 content is filtered by default",
    ),
    allow_sleep=True,
    sleep_brief="用于用户明确要求搜索、获取或发送 Pixiv/P站插画时调用。",
)


@plugin.mount_config()
class PixivFetcherConfig(ConfigBase):
    """Pixiv fetcher configuration."""

    API_URL: str = Field(
        default="https://api.lolicon.app/setu/v2",
        title="Lolicon API v2 地址",
        description="用于检索 Pixiv 作品元数据的 API 地址。",
    )
    TIMEOUT_SECONDS: int = Field(
        default=30,
        title="请求超时秒数",
        description="API 检索和图片下载的单次请求超时时间。",
        ge=5,
        le=120,
    )
    MAX_RESULTS: int = Field(
        default=3,
        title="单次最大图片数",
        description="限制单次工具调用最多下载的图片数量，防止刷屏。",
        ge=1,
        le=10,
    )
    ALLOW_R18: bool = Field(
        default=False,
        title="允许 R18 内容",
        description="关闭时强制只搜索全年龄作品；开启后仍需调用参数明确指定 r18='adult' 或 'mixed'。",
        json_schema_extra=ExtraField(
            i18n_title=i18n.i18n_text(zh_CN="允许 R18 内容", en_US="Allow R18 Content"),
            i18n_description=i18n.i18n_text(
                zh_CN="关闭时强制只搜索全年龄作品；开启后仍需调用参数明确指定。",
                en_US="When disabled, safe-only search is enforced.",
            ),
        ).model_dump(),
    )
    EXCLUDE_AI_BY_DEFAULT: bool = Field(
        default=True,
        title="默认排除 AI 作品",
        description="开启后默认请求 excludeAI=true；调用参数 include_ai=True 可临时允许。",
    )
    DEFAULT_SIZE: str = Field(
        default="regular",
        title="默认图片尺寸",
        description="可选 regular 或 original。regular 更稳定，original 体积更大。",
    )
    DEFAULT_MODE: str = Field(
        default="search",
        title="默认获取模式",
        description="search 表示按条件搜索；ranking 表示默认获取 Pixiv 排行榜。",
    )
    DEFAULT_RANKING_MODE: str = Field(
        default="daily",
        title="默认排行榜类型",
        description="支持 daily、weekly、monthly、rookie、original、male、female。",
    )
    DEFAULT_RANKING_POSITION: int = Field(
        default=1,
        title="默认排行榜起始名次",
        description="1 表示排行榜第一；2 表示从第二名开始。",
        ge=1,
        le=500,
    )
    DEFAULT_R18_MODE: str = Field(
        default="safe",
        title="默认 R18 策略",
        description="safe 为全年龄；adult 只取 R18；mixed 混合。ALLOW_R18=false 时会强制 safe。",
    )
    DEFAULT_INCLUDE_AI: bool = Field(
        default=False,
        title="默认允许 AI 作品",
        description="开启后工具参数未显式传入 include_ai 时允许 AI 作品。",
    )
    PIXIV_REFRESH_TOKEN: str = Field(
        default="",
        title="Pixiv OAuth refresh_token",
        description="可选。填写后优先使用 Pixiv 登录态 App API；留空则使用免登录公开接口。",
        json_schema_extra=ExtraField(is_secret=True).model_dump(),
    )
    ORIGINAL_ZIP_THRESHOLD_MB: int = Field(
        default=20,
        title="原图自动打包阈值(MB)",
        description="size=original 且 delivery=auto 时，超过该大小会打包为 zip 文件发送，避免平台按图片消息压缩或拦截。",
        ge=1,
        le=200,
    )
    DEFAULT_DELIVERY: str = Field(
        default="auto",
        title="默认交付方式",
        description="auto 自动判断；image 直接作为图片文件；zip 打包原图为 zip 文件。",
    )


config: PixivFetcherConfig = plugin.get_config(PixivFetcherConfig)

SIZE_ALIASES = {
    "regular": "regular",
    "original": "original",
    "small": "regular",
    "large": "original",
    "原图": "original",
    "大图": "original",
    "普通": "regular",
}

R18_ALIASES = {
    "safe": 0,
    "全年龄": 0,
    "normal": 0,
    "off": 0,
    "false": 0,
    "adult": 1,
    "r18": 1,
    "only": 1,
    "mixed": 2,
    "all": 2,
    "both": 2,
    "混合": 2,
}

RANKING_MODES = {
    "daily",
    "weekly",
    "monthly",
    "rookie",
    "original",
    "male",
    "female",
}

R18_TAGS = {"r-18", "r18", "r-18g", "r18g"}


def _clean_tags(tags: List[str], keyword: str) -> List[str]:
    cleaned = []
    for tag in tags:
        normalized = str(tag).strip()
        if normalized and normalized not in cleaned:
            cleaned.append(normalized)
    if keyword.strip() and keyword.strip() not in cleaned:
        cleaned.append(keyword.strip())
    return cleaned[:5]


def _normalize_size(size: str) -> str:
    normalized = SIZE_ALIASES.get((size or "").strip().lower(), "")
    if normalized:
        return normalized
    return SIZE_ALIASES.get(config.DEFAULT_SIZE.strip().lower(), "regular")


def _normalize_r18(r18: str) -> int:
    requested = R18_ALIASES.get((r18 or "safe").strip().lower(), 0)
    if requested and not config.ALLOW_R18:
        return 0
    return requested


def _proxy() -> str | None:
    proxy = core.config.DEFAULT_PROXY
    if not proxy:
        return None
    return proxy if proxy.startswith(("http://", "https://")) else f"http://{proxy}"


def _proxy_candidates() -> List[str | None]:
    proxy = _proxy()
    return [proxy, None] if proxy else [None]


def _pixiv_app_headers(access_token: str | None = None) -> Dict[str, str]:
    headers = {
        "User-Agent": PIXIV_APP_USER_AGENT,
        "App-OS": "android",
        "App-OS-Version": "11",
        "App-Version": "5.0.234",
        "Accept-Language": "zh_CN",
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    return headers


def _safe_filename_part(value: Any, fallback: str) -> str:
    text = re.sub(r"[^0-9A-Za-z._-]+", "_", str(value or "")).strip("._-")
    return text[:80] or fallback


def _has_r18_tag(item: Dict[str, Any]) -> bool:
    return any(str(tag).strip().lower() in R18_TAGS for tag in item.get("tags", []) or [])


def _is_safe_item(item: Dict[str, Any]) -> bool:
    return not bool(item.get("r18")) and not _has_r18_tag(item)


def _config_mode(value: str) -> str:
    return (value or config.DEFAULT_MODE or "search").strip().lower()


def _config_ranking_mode(value: str) -> str:
    return (value or config.DEFAULT_RANKING_MODE or "daily").strip().lower()


def _config_r18(value: str) -> str:
    return (value or config.DEFAULT_R18_MODE or "safe").strip().lower()


def _config_include_ai(value: bool | None) -> bool:
    return config.DEFAULT_INCLUDE_AI if value is None else value


def _config_ranking_position(value: int) -> int:
    return value if value > 0 else max(1, config.DEFAULT_RANKING_POSITION)


def _config_delivery(value: str) -> str:
    delivery = (value or config.DEFAULT_DELIVERY or "auto").strip().lower()
    return delivery if delivery in {"auto", "image", "zip"} else "auto"


def _should_zip_original(*, delivery: str, size: str, image_bytes: bytes) -> bool:
    if delivery == "zip":
        return True
    if delivery == "image":
        return False
    threshold = max(1, config.ORIGINAL_ZIP_THRESHOLD_MB) * 1024 * 1024
    return _normalize_size(size) == "original" and len(image_bytes) >= threshold


def _zip_image_bytes(image_bytes: bytes, image_name: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(image_name, image_bytes)
    return buffer.getvalue()


async def _request_json(method: str, url: str, *, headers: Dict[str, str], **kwargs: Any) -> Dict[str, Any]:
    last_error: Exception | None = None
    for proxy in _proxy_candidates():
        try:
            async with httpx.AsyncClient(timeout=config.TIMEOUT_SECONDS, proxy=proxy, follow_redirects=True) as client:
                response = await client.request(method, url, headers=headers, **kwargs)
                response.raise_for_status()
                return response.json()
        except Exception as exc:
            last_error = exc
            if proxy:
                logger.warning(f"Pixiv 登录态 API 代理请求失败，改用直连重试: {exc!s}")
                continue
            raise
    raise RuntimeError(str(last_error) if last_error else "Pixiv 登录态 API 请求失败")


async def _pixiv_access_token() -> str:
    refresh_token = config.PIXIV_REFRESH_TOKEN.strip()
    if not refresh_token:
        raise RuntimeError("未配置 PIXIV_REFRESH_TOKEN")
    now = time.time()
    if (
        _token_cache.get("access_token")
        and _token_cache.get("refresh_token") == refresh_token
        and float(_token_cache.get("expires_at") or 0) - 60 > now
    ):
        return str(_token_cache["access_token"])
    payload = {
        "client_id": PIXIV_CLIENT_ID,
        "client_secret": PIXIV_CLIENT_SECRET,
        "grant_type": "refresh_token",
        "include_policy": "true",
        "refresh_token": refresh_token,
    }
    data = await _request_json(
        "POST",
        PIXIV_AUTH_TOKEN_URL,
        headers=_pixiv_app_headers(),
        data=payload,
    )
    response = data.get("response") or data
    access_token = response.get("access_token")
    if not access_token:
        raise RuntimeError(f"Pixiv access_token 刷新失败: {data}")
    expires_in = int(response.get("expires_in") or 3600)
    _token_cache.update(
        {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": time.time() + expires_in,
        },
    )
    return str(access_token)


def _pixiv_app_image_url(illust: Dict[str, Any], size: str) -> str:
    if _normalize_size(size) == "original":
        original = (illust.get("meta_single_page") or {}).get("original_image_url")
        if original:
            return str(original)
        pages = illust.get("meta_pages") or []
        if pages:
            return str(((pages[0].get("image_urls") or {}).get("original")) or "")
    image_urls = illust.get("image_urls") or {}
    return str(image_urls.get("large") or image_urls.get("medium") or image_urls.get("square_medium") or "")


def _pixiv_app_item(illust: Dict[str, Any], *, size: str, rank: int | None = None) -> Dict[str, Any]:
    tags = []
    for tag in illust.get("tags") or []:
        name = tag.get("name") if isinstance(tag, dict) else str(tag)
        translated = (tag.get("translated_name") if isinstance(tag, dict) else "") or ""
        if name:
            tags.append(str(name))
        if translated and translated not in tags:
            tags.append(str(translated))
    image_url = _pixiv_app_image_url(illust, size)
    ext = (urllib.parse.urlparse(image_url).path.rsplit(".", 1)[-1] or "jpg").lower()
    user = illust.get("user") or {}
    return {
        "pid": illust.get("id"),
        "p": 0,
        "uid": user.get("id"),
        "title": illust.get("title"),
        "author": user.get("name"),
        "r18": bool(illust.get("x_restrict")),
        "width": illust.get("width"),
        "height": illust.get("height"),
        "tags": tags,
        "ext": ext if len(ext) <= 5 else "jpg",
        "aiType": illust.get("illust_ai_type"),
        "rank": rank if rank is not None else "N/A",
        "rating_count": illust.get("total_bookmarks"),
        "view_count": illust.get("total_view"),
        "urls": {"regular": image_url, "original": image_url},
    }


def _api_payload(
    *,
    tags: List[str],
    keyword: str,
    uid: int,
    count: int,
    r18: str,
    size: str,
    include_ai: bool,
) -> Dict[str, Any]:
    normalized_r18 = _normalize_r18(r18)
    requested_count = max(1, min(count, config.MAX_RESULTS))
    payload: Dict[str, Any] = {
        "r18": normalized_r18,
        "num": min(20, requested_count * 3) if normalized_r18 == 0 else requested_count,
        "size": [_normalize_size(size)],
        "excludeAI": config.EXCLUDE_AI_BY_DEFAULT and not include_ai,
    }
    clean_tags = _clean_tags(tags, keyword)
    if clean_tags:
        payload["tag"] = clean_tags
    if uid > 0:
        payload["uid"] = [uid]
    return payload


async def _search_pixiv(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    headers = {
        "User-Agent": "NekroAgent-PixivFetcher/1.0",
        "Accept": "application/json",
    }
    last_error: Exception | None = None
    for proxy in _proxy_candidates():
        try:
            async with httpx.AsyncClient(timeout=config.TIMEOUT_SECONDS, proxy=proxy) as client:
                response = await client.post(config.API_URL, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
            break
        except Exception as exc:
            last_error = exc
            if proxy:
                logger.warning(f"Pixiv API 代理请求失败，改用直连重试: {exc!s}")
                continue
            raise
    else:
        raise RuntimeError(str(last_error) if last_error else "Pixiv API 请求失败")
    if data.get("error"):
        raise RuntimeError(str(data["error"]))
    items = list(data.get("data") or [])
    if payload.get("r18") == 0:
        items = [item for item in items if _is_safe_item(item)]
    return items


async def _search_pixiv_app(
    *,
    tags: List[str],
    keyword: str,
    uid: int,
    count: int,
    r18: str,
    size: str,
    include_ai: bool,
) -> List[Dict[str, Any]]:
    access_token = await _pixiv_access_token()
    headers = _pixiv_app_headers(access_token)
    requested_count = max(1, min(count, config.MAX_RESULTS))
    if uid > 0:
        url = f"{PIXIV_APP_API_BASE}/v1/user/illusts"
        params = {
            "user_id": uid,
            "type": "illust",
            "filter": "for_ios",
        }
    else:
        word = " ".join(_clean_tags(tags, keyword)).strip()
        if not word:
            raise RuntimeError("登录态搜索仍需标签、关键词或作者 UID")
        url = f"{PIXIV_APP_API_BASE}/v1/search/illust"
        params = {
            "word": word,
            "search_target": "partial_match_for_tags",
            "sort": "date_desc",
            "filter": "for_ios",
            "include_translated_tag_results": "true",
        }
    data = await _request_json("GET", url, headers=headers, params=params)
    items = [_pixiv_app_item(illust, size=size) for illust in data.get("illusts") or []]
    requested_r18 = _normalize_r18(r18)
    if requested_r18 == 0:
        items = [item for item in items if _is_safe_item(item)]
    elif requested_r18 == 1:
        items = [item for item in items if bool(item.get("r18")) or _has_r18_tag(item)]
    if not include_ai:
        items = [item for item in items if item.get("aiType") in (0, None, "unknown")]
    return items[:requested_count]


async def _search_pixiv_ranking(ranking_mode: str, ranking_position: int, count: int) -> List[Dict[str, Any]]:
    mode = (ranking_mode or "daily").strip().lower()
    if mode == "week":
        mode = "weekly"
    if mode == "month":
        mode = "monthly"
    if mode not in RANKING_MODES:
        mode = "daily"

    start = max(1, ranking_position)
    page = ((start - 1) // 50) + 1
    wanted = max(1, min(count, config.MAX_RESULTS))
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
        ),
        "Referer": "https://www.pixiv.net/ranking.php",
        "Accept": "application/json,text/plain,*/*",
    }
    params = {
        "mode": mode,
        "content": "illust",
        "p": page,
        "format": "json",
    }
    last_error: Exception | None = None
    for proxy in _proxy_candidates():
        try:
            async with httpx.AsyncClient(timeout=config.TIMEOUT_SECONDS, proxy=proxy, follow_redirects=True) as client:
                response = await client.get("https://www.pixiv.net/ranking.php", params=params, headers=headers)
                response.raise_for_status()
                data = response.json()
            break
        except Exception as exc:
            last_error = exc
            if proxy:
                logger.warning(f"Pixiv 排行榜代理请求失败，改用直连重试: {exc!s}")
                continue
            raise
    else:
        raise RuntimeError(str(last_error) if last_error else "Pixiv 排行榜请求失败")

    ranked_items: List[Dict[str, Any]] = []
    for item in data.get("contents") or []:
        rank = int(item.get("rank") or 0)
        if rank < start:
            continue
        content_type = item.get("illust_content_type") or {}
        is_r18 = bool(content_type.get("sexual"))
        if (is_r18 or _has_r18_tag(item)) and not config.ALLOW_R18:
            continue
        ranked_items.append(
            {
                "pid": item.get("illust_id"),
                "p": 0,
                "uid": item.get("user_id"),
                "title": item.get("title"),
                "author": item.get("user_name"),
                "r18": is_r18,
                "width": item.get("width"),
                "height": item.get("height"),
                "tags": item.get("tags") or [],
                "ext": "jpg",
                "aiType": "unknown",
                "rank": rank,
                "rating_count": item.get("rating_count"),
                "view_count": item.get("view_count"),
                "urls": {"regular": item.get("url"), "original": item.get("url")},
            },
        )
        if len(ranked_items) >= wanted:
            break
    return ranked_items


async def _search_pixiv_ranking_app(ranking_mode: str, ranking_position: int, count: int, size: str) -> List[Dict[str, Any]]:
    mode = (ranking_mode or "daily").strip().lower()
    app_modes = {
        "daily": "day",
        "day": "day",
        "weekly": "week",
        "week": "week",
        "monthly": "month",
        "month": "month",
        "rookie": "week_rookie",
        "original": "week_original",
        "male": "day_male",
        "female": "day_female",
    }
    app_mode = app_modes.get(mode, "day")
    start = max(1, ranking_position)
    wanted = max(1, min(count, config.MAX_RESULTS))
    access_token = await _pixiv_access_token()
    headers = _pixiv_app_headers(access_token)
    params = {
        "mode": app_mode,
        "filter": "for_ios",
    }
    data = await _request_json("GET", f"{PIXIV_APP_API_BASE}/v1/illust/ranking", headers=headers, params=params)
    items = []
    for index, illust in enumerate(data.get("illusts") or [], start=1):
        if index < start:
            continue
        item = _pixiv_app_item(illust, size=size, rank=index)
        if not config.ALLOW_R18 and not _is_safe_item(item):
            continue
        items.append(item)
        if len(items) >= wanted:
            break
    return items


async def _download_image(url: str) -> bytes:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
        ),
        "Referer": "https://www.pixiv.net/",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    }
    last_error: Exception | None = None
    for proxy in _proxy_candidates():
        try:
            async with httpx.AsyncClient(timeout=config.TIMEOUT_SECONDS, proxy=proxy, follow_redirects=True) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                content = response.content
            break
        except Exception as exc:
            last_error = exc
            if proxy:
                logger.warning(f"Pixiv 图片代理下载失败，改用直连重试: {exc!s}")
                continue
            raise
    else:
        raise RuntimeError(str(last_error) if last_error else "Pixiv 图片下载失败")
    if len(content) < 1024 or not content_type.startswith("image/"):
        raise RuntimeError(f"下载内容不是有效图片: content-type={content_type or 'unknown'}, bytes={len(content)}")
    return content


def _format_result(index: int, item: Dict[str, Any], sandbox_path: str) -> str:
    tags = ", ".join([str(tag) for tag in item.get("tags", [])[:8]])
    pixiv_url = f"https://www.pixiv.net/artworks/{item.get('pid')}"
    return (
        f"{index}. {item.get('title') or 'Untitled'}\n"
        f"   pid: {item.get('pid')} | page: {item.get('p', 0)} | author: {item.get('author')} ({item.get('uid')})\n"
        f"   size: {item.get('width')}x{item.get('height')} | r18: {bool(item.get('r18'))} | aiType: {item.get('aiType')}\n"
        f"   rank: {item.get('rank', 'N/A')} | views: {item.get('view_count', 'N/A')} | ratings: {item.get('rating_count', 'N/A')}\n"
        f"   tags: {tags or '无'}\n"
        f"   pixiv: {pixiv_url}\n"
        f"   file: {sandbox_path}"
    )


@plugin.mount_sandbox_method(
    SandboxMethodType.TOOL,
    name="pixiv_search_and_fetch",
    description=(
        "按用户要求搜索并获取 Pixiv/P站插画。支持标签、关键词、作者 UID、排行榜、数量、尺寸、R18 策略和 AI 作品策略；"
        "返回可发送的 /app/uploads 图片路径。仅在用户明确要求 P站/Pixiv 图片、插画、壁纸或同人图时调用。"
    ),
)
async def pixiv_search_and_fetch(
    _ctx: AgentCtx,
    tags: List[str],
    keyword: str = "",
    uid: int = 0,
    count: int = 1,
    mode: str = "",
    ranking_mode: str = "",
    ranking_position: int = 0,
    r18: str = "",
    size: str = "",
    include_ai: bool | None = None,
    delivery: str = "",
) -> str:
    """搜索并下载 Pixiv/P站图片。

    Args:
        tags: 搜索标签列表，例如 ["初音ミク"]、["明日方舟", "能天使"]。不知道标签时可传空列表。
        keyword: 额外关键词，会作为标签参与搜索，例如 "风景"、"水着"。
        uid: Pixiv 作者 UID。传 0 表示不限作者。
        count: 需要获取的图片数量，实际不会超过插件配置 MAX_RESULTS。
        mode: 获取模式。search 表示按条件搜索；ranking 表示获取 Pixiv 排行榜。
        ranking_mode: 排行榜类型。支持 daily、weekly、monthly、rookie、original、male、female。mode=ranking 时生效。
        ranking_position: 排行榜起始名次。1 表示排行榜第一。mode=ranking 时生效。
        r18: 内容策略。safe/全年龄 只搜索全年龄；adult/r18 只搜索 R18；mixed/all 混合。插件配置 ALLOW_R18=false 时会强制 safe。
        size: 图片尺寸，regular 或 original。默认 regular；original 可能更慢且体积更大。
        include_ai: 是否允许 AI 作品。默认按配置排除。
        delivery: 交付方式。auto 自动判断；image 直接作为图片文件；zip 打包原图为 zip 文件，适合用户必须要原图但平台图片消息传不过去的场景。

    Returns:
        str: 搜索和下载结果。每条结果包含作品元数据、Pixiv 链接和可发送文件路径。
    """
    active_mode = _config_mode(mode)
    active_ranking_mode = _config_ranking_mode(ranking_mode)
    active_ranking_position = _config_ranking_position(ranking_position)
    active_r18 = _config_r18(r18)
    active_size = _normalize_size(size or config.DEFAULT_SIZE)
    active_include_ai = _config_include_ai(include_ai)
    active_delivery = _config_delivery(delivery)
    if active_mode == "ranking":
        try:
            if config.PIXIV_REFRESH_TOKEN.strip():
                try:
                    items = await _search_pixiv_ranking_app(active_ranking_mode, active_ranking_position, count, active_size)
                except Exception as login_exc:
                    logger.warning(f"Pixiv 登录态排行榜失败，退回免登录公开排行榜: {login_exc!s}")
                    items = await _search_pixiv_ranking(active_ranking_mode, active_ranking_position, count)
            else:
                items = await _search_pixiv_ranking(active_ranking_mode, active_ranking_position, count)
        except Exception as exc:
            logger.exception("Pixiv 排行榜获取失败")
            return f"[Pixiv] 排行榜获取失败: {exc!s}"
        payload = {
            "mode": "ranking",
            "ranking_mode": active_ranking_mode,
            "ranking_position": active_ranking_position,
            "num": max(1, min(count, config.MAX_RESULTS)),
        }
    elif not tags and not keyword.strip() and uid <= 0:
        return "[Pixiv] 请至少提供一个标签、关键词或作者 UID。"
    else:
        payload = _api_payload(
            tags=tags,
            keyword=keyword,
            uid=uid,
            count=count,
            r18=active_r18,
            size=active_size,
            include_ai=active_include_ai,
        )
        logger.info(f"Pixiv fetch payload: {payload}")

        try:
            if config.PIXIV_REFRESH_TOKEN.strip():
                try:
                    items = await _search_pixiv_app(
                        tags=tags,
                        keyword=keyword,
                        uid=uid,
                        count=count,
                        r18=active_r18,
                        size=active_size,
                        include_ai=active_include_ai,
                    )
                except Exception as login_exc:
                    logger.warning(f"Pixiv 登录态搜索失败，退回免登录公开搜索: {login_exc!s}")
                    items = await _search_pixiv(payload)
            else:
                items = await _search_pixiv(payload)
        except Exception as exc:
            logger.exception("Pixiv 搜索失败")
            return f"[Pixiv] 搜索失败: {exc!s}"

    if not items:
        return f"[Pixiv] 未找到匹配作品。搜索条件: {payload}"

    results: List[str] = []
    failures: List[str] = []
    target_size = active_size

    desired_count = max(1, min(count, config.MAX_RESULTS))
    for item in items[:desired_count]:
        url = (item.get("urls") or {}).get(target_size) or next(iter((item.get("urls") or {}).values()), "")
        if not url:
            failures.append(f"pid={item.get('pid')}: API 未返回可下载图片 URL")
            continue
        try:
            image_bytes = await _download_image(url)
            ext = item.get("ext") or "jpg"
            seed = f"{item.get('pid')}_{item.get('p', 0)}_{hashlib.md5(url.encode()).hexdigest()[:8]}"
            image_name = f"pixiv_{_safe_filename_part(seed, 'artwork')}.{_safe_filename_part(ext, 'jpg')}"
            if _should_zip_original(delivery=active_delivery, size=target_size, image_bytes=image_bytes):
                image_bytes = _zip_image_bytes(image_bytes, image_name)
                file_name = f"{image_name}.zip"
                use_suffix = ".zip"
            else:
                file_name = image_name
                use_suffix = f".{ext}"
            _, saved_name = await download_file_from_bytes(
                image_bytes,
                file_name=file_name,
                use_suffix=use_suffix,
                from_chat_key=_ctx.chat_key,
            )
            sandbox_path = str(convert_filename_to_sandbox_upload_path(saved_name))
            results.append(_format_result(len(results) + 1, item, sandbox_path))
        except Exception as exc:
            logger.exception(f"Pixiv 图片下载失败: pid={item.get('pid')}")
            failures.append(f"pid={item.get('pid')}: {exc!s}")

    if not results:
        return "[Pixiv] 找到了作品，但图片下载全部失败。\n" + "\n".join(failures)

    summary = [
        "[Pixiv] 已获取图片，可直接将 file 路径交给 send_msg_file 发送。",
        f"搜索条件: mode={active_mode}, tags={payload.get('tag', [])}, uid={uid or '不限'}, r18={payload.get('r18', 'N/A')}, size={target_size}, delivery={active_delivery}, count={len(results)}",
        "",
        "\n\n".join(results),
    ]
    if failures:
        summary.extend(["", "部分失败:", "\n".join(failures)])
    return "\n".join(summary)


@plugin.mount_cleanup_method()
async def clean_up():
    """Clean plugin resources."""
    logger.info("Pixiv图片搜索获取插件资源已清理")
