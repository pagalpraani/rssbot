# =============================================================================
# Module: RSS Service
# Path: utilitybot/modules/rss/service.py
# Description: Core RSS background worker for fetching, parsing, and watermarking multimedia content.
# =============================================================================

import asyncio
import io
import aiohttp
import aiofiles
import aiofiles.os
import feedparser
from urllib.parse import urlparse
import urllib.parse
import tempfile
import os
import shutil
import re
import html
from collections import OrderedDict
from PIL import Image, ImageSequence, UnidentifiedImageError
from utilitybot.utils.watermark import process_image_sync, process_pdf_sync
from datetime import datetime, timezone, timedelta
from aiogram.types import FSInputFile, InputMediaPhoto, InputMediaDocument
from aiogram.exceptions import TelegramRetryAfter
from utilitybot.database.mongodb import db
from utilitybot.utils.logger import log
from utilitybot.utils.formatter import split_caption as _split_caption
from utilitybot import config

# ---------------------------------------------------------------------------
# Telegram bot upload limit is 50 MB.
TELEGRAM_MAX_UPLOAD_BYTES = 50 * 1024 * 1024

# User-Agent strings used for downloading.
# General RSS/image content — Feedly is whitelisted by most CDNs.
_UA_FEED = "Feedly/1.0 (+https://feedly.com/fetcher.html; like FeedFetcher-Google)"
# Audio/podcast — must look like a real podcast app to pass tracker CDNs
# (dts.podtrac.com, podtrac, chtbl, BBC mediaselector, Spreaker, etc.)
_UA_AUDIO = "AntennaPod/3.1.2 (Linux; Android 13) okhttp/4.12.0"

# Media relay (Cloudflare Workers) — handles images AND audio/video.
# URL format: {RELAY_BASE}/{media_url}  (no encoding needed)
_RELAY_BASE = config.RSS_RELAY_BASE

# Dedicated thread pool for feedparser (network I/O bound).
# Kept separate from the watermark CPU pool so feed fetches and image
# processing don't queue behind each other.
import concurrent.futures as _cf
_FEED_FETCH_POOL = _cf.ThreadPoolExecutor(max_workers=10, thread_name_prefix="feedfetch")

async def _async_empty_set() -> set:
    """Coroutine that immediately returns an empty set (used as no-op gather slot)."""
    return set()
# ---------------------------------------------------------------------------

# Shared SSL context — creating one per session adds ~40% CPU overhead (RSStT finding).
import ssl as _ssl
_SSL_CTX = _ssl.create_default_context()

# Per-host concurrency limiter — prevents hammering a single CDN with too many
# simultaneous connections and getting rate-limited or IP-banned.
# Cloudflare img-relay (future): when deployed, add its host here too.
_PER_HOST_LIMIT = 6   
_MAX_HOST_SEMAPHORES = 1000  # Cap the dictionary size to prevent memory leaks

_host_semaphores: OrderedDict = OrderedDict()
_host_sem_lock = asyncio.Lock()

async def _get_host_semaphore(url: str) -> asyncio.Semaphore:
    host = urllib.parse.urlparse(url).netloc
    async with _host_sem_lock:
        if host in _host_semaphores:
            # Move the accessed host to the end (most recently used)
            _host_semaphores.move_to_end(host)
            return _host_semaphores[host]
        
        # If we hit the cap, remove the oldest (least recently used) host
        if len(_host_semaphores) >= _MAX_HOST_SEMAPHORES:
            _host_semaphores.popitem(last=False)
            
        sem = asyncio.Semaphore(_PER_HOST_LIMIT)
        _host_semaphores[host] = sem
        return sem

# Telegram flood control semaphore — cap concurrent sends to avoid triggering
# Telegram's flood control before it happens rather than retrying after.
# RSStT uses this pattern to keep load average low even with many feeds.
_TELEGRAM_SEND_SEM = asyncio.Semaphore(8)  # max 8 concurrent Telegram API calls

# Feed processing concurrency cap — prevents memory spikes when many feeds are
# due simultaneously. Each feed task holds a parsed feedparser object + any
# downloaded media in flight, so unbounded parallelism can easily exhaust RAM.
# Reduced to 10 to prevent OOM crashes during bulk feed processing.
_FEED_SEM = asyncio.Semaphore(10)

# ---------------------------------------------------------------------------
# Transient-error backoff table.
# On each consecutive failure the feed is silenced for progressively longer
# windows.  After _BACKOFF_MAX_FAILS consecutive failures the feed stays
# silenced at the last (largest) window until it succeeds again.
# Delays are in seconds: 5 min, 15 min, 30 min, 1 h, 3 h, 6 h.
# ---------------------------------------------------------------------------
_BACKOFF_DELAYS = [300, 900, 1800, 3600, 10800, 21600]
_BACKOFF_MAX_FAILS = len(_BACKOFF_DELAYS)

# Errno strings that identify transient network resets (not permanent errors).
_TRANSIENT_ERRNOS = frozenset({"104", "110", "111", "32", "54"})  # ECONNRESET, ETIMEDOUT, ECONNREFUSED, EPIPE, ECONNRESET(macOS)

def _is_transient_error(exc: Exception) -> bool:
    """Return True for recoverable network errors that warrant backoff."""
    msg = str(exc)
    # aiohttp / asyncio connection-level failures
    if isinstance(exc, (
        aiohttp.ServerDisconnectedError,
        aiohttp.ClientConnectionError,
        asyncio.TimeoutError,
    )):
        return True
    # errno 104 = Connection reset by peer, 110 = ETIMEDOUT, etc.
    for token in _TRANSIENT_ERRNOS:
        if f"[Errno {token}]" in msg:
            return True
    # "Remote end closed connection without response"
    if "remote end closed connection" in msg.lower():
        return True
    return False

_http_session = None
_audio_session = None

def get_http_session():
    global _http_session
    if _http_session is None or _http_session.closed:
        timeout = aiohttp.ClientTimeout(total=60, connect=15, sock_read=30)
        connector = aiohttp.TCPConnector(ssl=_SSL_CTX, limit=64, limit_per_host=_PER_HOST_LIMIT)
        _http_session = aiohttp.ClientSession(timeout=timeout, connector=connector)
    return _http_session

def get_audio_session():
    global _audio_session
    if _audio_session is None or _audio_session.closed:
        timeout = aiohttp.ClientTimeout(total=None, connect=20, sock_read=300)
        connector = aiohttp.TCPConnector(ssl=_SSL_CTX, limit=16)
        _audio_session = aiohttp.ClientSession(timeout=timeout, connector=connector)
    return _audio_session


class SkipMediaError(Exception):
    """Raised when a media URL should be silently skipped (403, 401, 404, etc.)."""
    pass


async def close_http_session():
    """Close all aiohttp sessions and thread pools cleanly on bot shutdown."""
    global _http_session, _audio_session
    if _http_session and not _http_session.closed:
        await _http_session.close()
        _http_session = None
    if _audio_session and not _audio_session.closed:
        await _audio_session.close()
        _audio_session = None
    _host_semaphores.clear()
    _FEED_FETCH_POOL.shutdown(wait=False)


async def download_file(url: str, dest_path: str, is_audio: bool = False):
    """Download a remote file to dest_path.

    Uses a podcast-client User-Agent for audio so tracker CDNs
    (dts.podtrac.com, BBC mediaselector, Spreaker, etc.) let us through.
    Raises SkipMediaError for permanent HTTP errors (401/403/404/410).
    Raises Exception for transient network errors.
    Files over TELEGRAM_MAX_UPLOAD_BYTES are skipped with SkipMediaError.
    """
    session = get_audio_session() if is_audio else get_http_session()
    headers = {
        "User-Agent": _UA_AUDIO if is_audio else _UA_FEED,
        "Accept": "audio/*,*/*;q=0.8" if is_audio else "*/*",
    }
    try:
        # allow_redirects=True is aiohttp default; max_redirects covers tracker chains
        async with session.get(url, headers=headers, max_redirects=10) as response:
            if response.status == 200:
                # Guard against files too large for Telegram
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > TELEGRAM_MAX_UPLOAD_BYTES:
                    raise SkipMediaError(
                        f"Skipping {url}: file too large "
                        f"({int(content_length) // (1024*1024)} MB > 50 MB Telegram limit)"
                    )
                downloaded = 0
                async with aiofiles.open(dest_path, "wb") as f:
                    async for chunk in response.content.iter_chunked(65536):
                        downloaded += len(chunk)
                        if downloaded > TELEGRAM_MAX_UPLOAD_BYTES:
                            raise SkipMediaError(
                                f"Skipping {url}: file exceeds 50 MB Telegram limit mid-download"
                            )
                        await f.write(chunk)
            elif response.status in (401, 403, 404, 410):
                raise SkipMediaError(
                    f"Skipping {url}: HTTP {response.status} (access denied or not found)"
                )
            else:
                raise Exception(f"Failed to download {url}: HTTP {response.status}")
    except SkipMediaError:
        raise
    except (aiohttp.ServerDisconnectedError, aiohttp.ClientConnectionError,
            aiohttp.ClientResponseError, asyncio.TimeoutError) as e:
        raise Exception(f"Failed to download {url}: {e}") from e



# ---------------------------------------------------------------------------
# Per-entry constants — defined once at module level, not per loop iteration
# ---------------------------------------------------------------------------
_MIME_MAP = {
    'image/jpg':   'image/jpeg',
    'image/pjpeg': 'image/jpeg',
    'image/x-png': 'image/png',
}
_IMAGE_TYPES = frozenset({'image/jpeg', 'image/png', 'image/gif', 'image/webp'})
_AUDIO_TYPES = frozenset({'audio/mpeg', 'audio/mp3', 'audio/ogg', 'audio/aac',
                           'audio/m4a', 'audio/mp4', 'audio/x-m4a', 'audio/wav',
                           'audio/flac', 'audio/webm'})

# Pre-compiled HTML-cleaning regexes used in build_caption (avoids re-compiling
# the dynamic allowed_tags pattern on every entry).
_RE_BR          = re.compile(r'<br\s*/?>|</p>|</div>', re.IGNORECASE)
_RE_HR          = re.compile(r'<hr\s*/?>', re.IGNORECASE)
_RE_LI          = re.compile(r'<li[^>]*>(.*?)</li>', re.IGNORECASE | re.DOTALL)
_RE_UL          = re.compile(r'</?(ul|ol)[^>]*>', re.IGNORECASE)
_RE_H           = re.compile(r'<h[1-6][^>]*>(.*?)</h[1-6]>', re.IGNORECASE | re.DOTALL)
_RE_STRONG_O    = re.compile(r'<strong[^>]*>', re.IGNORECASE)
_RE_STRONG_C    = re.compile(r'</strong>', re.IGNORECASE)
_RE_EM_O        = re.compile(r'<em[^>]*>', re.IGNORECASE)
_RE_EM_C        = re.compile(r'</em>', re.IGNORECASE)
_RE_ALLOWED_TAGS = re.compile(
    r'<(?!/?(a|b|i|u|s|strike|del|code|pre|blockquote)\b)[^>]+>',
    re.IGNORECASE
)
_RE_MULTI_NL    = re.compile(r'\n{3,}')
# ---------------------------------------------------------------------------

def _build_caption(target_url: str, desc_raw: str, feed_title: str, post_title: str,
                   has_link: bool, source_format: str, author_text: str, hash_text: str) -> str:
    """Module-level caption builder — defined once, called per entry. Uses
    pre-compiled regexes so pattern compilation cost is paid only at import."""
    desc = desc_raw
    if desc:
        desc = html.unescape(desc)
        desc = _RE_BR.sub('\n', desc)
        desc = _RE_HR.sub('\n──────────\n', desc)
        desc = _RE_LI.sub(r'• \1\n', desc)
        desc = _RE_UL.sub('\n', desc)
        desc = _RE_H.sub(r'<b>\1</b>\n', desc)
        desc = _RE_STRONG_O.sub('<b>', desc)
        desc = _RE_STRONG_C.sub('</b>', desc)
        desc = _RE_EM_O.sub('<i>', desc)
        desc = _RE_EM_C.sub('</i>', desc)
        desc = _RE_ALLOWED_TAGS.sub('', desc)
        desc = _RE_MULTI_NL.sub('\n\n', desc)
        desc = desc.strip()
        # No truncation here — the pipeline caption guard handles splitting
        # at send time, preserving the full content for text-only RSS posts.

    desc_text = f"\n\n{desc}" if desc else ""
    b_feed    = f"<b>{feed_title}</b>" if feed_title else ""
    b_u_feed  = f"<b><u>{feed_title}</u></b>" if feed_title else ""
    b_post    = f"<b>{post_title}</b>" if post_title else ""
    b_u_post  = f"<b><u>{post_title}</u></b>" if post_title else ""

    active_mode = source_format
    if not has_link and active_mode in ('post_title_link', 'hyperlink_at_end', 'bare_url_at_end'):
        active_mode = 'disable'

    parts = []
    if active_mode == 'feed_title_and_link':
        if b_feed: parts.append(b_feed)
        if b_post: parts.append(b_post)
    elif active_mode == 'feed_title_and_post_title_link':
        if b_feed: parts.append(b_feed)
        if b_post: parts.append(f'<a href="{target_url}">{b_post}</a>')
        elif has_link: parts.append(f'<a href="{target_url}">Link</a>')
    elif active_mode == 'post_title_link':
        if b_u_post: parts.append(f'<a href="{target_url}">{b_u_post}</a>')
        elif has_link: parts.append(f'<a href="{target_url}">Link</a>')
    elif active_mode == 'feed_title_no_link':
        # Bold+underlined feed title, plain post title, no link anywhere
        # Description (if present) is separated by an empty line
        if b_u_feed: parts.append(b_u_feed)
        if post_title: parts.append(post_title)
    elif active_mode in ('hyperlink_at_end', 'bare_url_at_end', 'disable'):
        if not feed_title and b_u_post: parts.append(b_u_post)
        elif feed_title and b_post: parts.append(b_post)

    res = "\n".join(parts) + desc_text + author_text

    if active_mode == 'feed_title_and_link' and has_link:
        res += f'\n\n<a href="{target_url}">Source</a>'
    elif active_mode == 'hyperlink_at_end' and has_link:
        res += f'\n\n<a href="{target_url}">Source</a>'
    elif active_mode == 'bare_url_at_end' and has_link:
        res += f"\n\n{target_url}"
    # feed_title_no_link: intentionally no link appended

    res += hash_text
    return res.strip()


async def _process_single_feed(bot_instance, feed: dict, force_single: bool = False, settings: dict = None):
    chat_id = feed['chat_id']
    feed_url = feed['feed_url']

    # If settings were pre-fetched by the caller (scheduler bulk fetch), reuse them.
    # Otherwise fetch them now (force_single / send_latest path).
    # Both collections are merged so pipeline flags (settings) AND default_time_interval
    # (rss_settings) are always available in a single dict.
    if settings is None:
        gen_s = await db.get_settings(chat_id) or {}
        rss_s = await db.get_rss_settings(chat_id) or {}
        settings = {**gen_s, **rss_s}   # rss_settings wins on conflicts

    watermark_enabled = feed.get('watermark_enabled', True)
    media_mode = feed.get('media_mode', 'Enable')
    download_enabled = True if media_mode in ('Enable', 'Only media') else False
    newly_processed: set = set()  # collected then bulk-written after entry loop

    link_preview = feed.get('link_preview', True)
    post_title_enabled = feed.get('post_title_enabled', True)
    notification = feed.get('notification', 'Normal')
    disable_notification = notification == 'Muted'
    author_enabled = feed.get('author_enabled', True)
    custom_feed_title = feed.get('custom_title')
    custom_hashtags = feed.get('custom_hashtags')

    logo_id = settings.get('logo_id') if watermark_enabled else None
    footer_note = settings.get('footer_note') if watermark_enabled else None
    custom_name = settings.get('custom_name') or None

    try:

        # -----------------------------------------------------------------------
        # Circuit-breaker: skip feeds that are in a backoff window.
        # retry_after is set by the failure handler below.
        # -----------------------------------------------------------------------
        retry_after = feed.get('retry_after')
        if retry_after is not None:
            now_utc = datetime.now(timezone.utc)
            # MongoDB may return a naive datetime; normalise it.
            if retry_after.tzinfo is None:
                retry_after = retry_after.replace(tzinfo=timezone.utc)
            if now_utc < retry_after:
                log.debug(
                    f"Feed in backoff window, skipping until {retry_after.isoformat()}: {feed_url}"
                )
                return

        loop = asyncio.get_running_loop()
        etag     = feed.get('http_etag')     if not force_single else None
        modified = feed.get('http_modified') if not force_single else None
        
        # Build headers for native aiohttp caching
        headers = {"User-Agent": _UA_FEED}
        if etag: headers["If-None-Match"] = etag
        if modified: headers["If-Modified-Since"] = modified

        new_etag = None
        new_modified = None

        try:
            # 1. Fetch XML via aiohttp (respects async timeouts natively)
            session = get_http_session()
            async with session.get(feed_url, headers=headers, timeout=25.0) as resp:
                if resp.status == 304:
                    log.debug(f"Feed not modified (304), skipping: {feed_url}")
                    return
                    
                resp.raise_for_status()
                raw_xml = await resp.read()
                
                # Extract fresh headers for caching
                new_etag = resp.headers.get('ETag')
                new_modified = resp.headers.get('Last-Modified')

            # 2. Parse XML string and fetch DB processed IDs concurrently
            # Feedparser runs purely on CPU now, with no network I/O to hang the thread.
            parsed, processed_ids_prefetch = await asyncio.gather(
                loop.run_in_executor(_FEED_FETCH_POOL, lambda: feedparser.parse(raw_xml)),
                db.get_processed_item_ids(chat_id, feed_url) if not force_single else _async_empty_set(),
            )
            
        except asyncio.TimeoutError:
            log.warning(f"Feed fetch timed out (25s), skipping: {feed_url}")
            return
        except Exception as e:
            # Re-raise to let the transient error handler / backoff logic catch it
            raise e

        if not parsed.entries and parsed.bozo and getattr(parsed, 'bozo_exception', None):
            log.warning(f"Feed parse error for {feed_url}: {parsed.get('bozo_exception', 'unknown')}")
            return

        # Persist updated ETag / Last-Modified for next poll
        cache_updates = {}
        if new_etag and new_etag != etag:
            cache_updates['http_etag'] = new_etag
        if new_modified and new_modified != modified:
            cache_updates['http_modified'] = new_modified

        raw_feed_title = parsed.feed.get('title', 'Feed Title')
        if feed.get('feed_title') != raw_feed_title:
            cache_updates['feed_title'] = raw_feed_title
            
        if cache_updates:
            await db.update_feed_settings(chat_id, feed_url, cache_updates)

        # Clear any previous backoff state now that the feed responded successfully.
        if feed.get('fail_count'):
            await db.clear_feed_failures(chat_id, feed_url)

        feed_title = html.escape(custom_feed_title) if custom_feed_title else html.escape(raw_feed_title)
        from utilitybot.utils.content_pipeline import ContentPipeline

        # Bulk-fetch of processed IDs ran concurrently with the feed download above.
        # For force_single mode it's an empty set (we want to re-send regardless).
        processed_ids = processed_ids_prefetch

        # If sending the single latest post, read from the top down. 
        # Otherwise, process the feed from bottom up (oldest to newest) 
        # so they appear in correct chronological order in the chat.
        entries_to_process = parsed.entries if force_single else reversed(parsed.entries)

        def _compute_item_id(entry):
            # Priority: guid (most reliable) → id → link → hash fallback.
            # feedparser maps <guid> to entry.id and also exposes it as entry.guid.
            item_id = (
                entry.get('guid') or
                entry.get('id') or
                entry.get('link') or
                ''
            )
            # Fallback: hash title+date+content so entries without guid/id/link
            # are still uniquely identified (poorly formatted feeds).
            if not item_id:
                import hashlib
                _hash_src = (
                    (entry.get('title') or '') +
                    (entry.get('published') or entry.get('updated') or '') +
                    (entry.get('summary') or '')[:200]
                )
                item_id = 'hash:' + hashlib.md5(_hash_src.encode('utf-8', errors='replace')).hexdigest()
            return item_id

        id_entry_pairs = [(_compute_item_id(e), e) for e in entries_to_process]

        if force_single:
            final_pairs = id_entry_pairs
        else:
            # id_entry_pairs is oldest -> newest (see reversed() above).
            unprocessed_pairs = [(iid, e) for iid, e in id_entry_pairs if iid not in processed_ids]
            max_new = config.RSS_MAX_NEW_ITEMS_PER_CYCLE
            if max_new > 0 and len(unprocessed_pairs) > max_new:
                # Backlog too large (new feed, or a big gap since the last check).
                # Send only the newest `max_new` items; silently mark the older
                # overflow as processed so it's never sent later either — this
                # is what stops a 700-800 item feed from flooding the chat.
                overflow_pairs = unprocessed_pairs[:-max_new]
                final_pairs = unprocessed_pairs[-max_new:]
                for iid, _ in overflow_pairs:
                    newly_processed.add(iid)
                log.info(
                    f"Feed backlog capped for {feed_url}: {len(overflow_pairs)} older "
                    f"item(s) marked as seen without sending "
                    f"({len(unprocessed_pairs)} unseen, cap={max_new})."
                )
            else:
                final_pairs = unprocessed_pairs

        for item_id, entry in final_pairs:
            entry_title = html.escape(entry.title) if hasattr(entry, 'title') and entry.title else ''
            entry_author = html.escape(entry.author) if hasattr(entry, 'author') and entry.author else ''
            safe_hashtags = html.escape(custom_hashtags) if custom_hashtags else ''

            # Format Caption parts
            author_text = f"\n<i>By: {entry_author}</i>" if author_enabled and 'author' in entry else ""
            hash_text = f"\n\n{safe_hashtags}" if safe_hashtags else ""

            source_format = feed.get('source_format', 'feed_title_and_link')
            has_link = bool(entry.get('link', ''))
            post_title = entry_title if post_title_enabled else ""

            # build_caption is now a module-level function (_build_caption);
            # call it with explicit arguments instead of a per-entry closure.
            def build_caption(target_url, is_text_msg=False):
                return _build_caption(
                    target_url=target_url,
                    desc_raw=entry.get('summary') or entry.get('description') or '',
                    feed_title=feed_title,
                    post_title=post_title,
                    has_link=has_link,
                    source_format=source_format,
                    author_text=author_text,
                    hash_text=hash_text,
                )

            if media_mode == 'Only media':
                caption_text = None
            else:
                caption_text = build_caption(entry.get('link', ''), is_text_msg=False)

            # ----------------------------------------------------------------
            # Collect ALL media attachments from the entry, deduplicating
            # across the three places feedparser exposes them:
            #   1. entry.links  (rel=enclosure)  – standard RSS <enclosure>
            #   2. entry.enclosures              – feedparser convenience list
            #   3. entry.media_content           – <media:content> / <media:group>
            # ----------------------------------------------------------------
            _seen_urls: set = set()
            links_to_download: list = []  # list of (url, mime_type)

            def _add_link(href: str, ctype: str) -> None:
                if not href or href in _seen_urls:
                    return
                _seen_urls.add(href)
                links_to_download.append((href, ctype or 'application/octet-stream'))

            # 1. Standard <link rel="enclosure"> elements
            for link in entry.get('links', []):
                if link.get('rel') == 'enclosure':
                    _add_link(link.get('href', ''), link.get('type', ''))

            # 2. feedparser's convenience entry.enclosures list
            for enc in getattr(entry, 'enclosures', []) or []:
                _add_link(enc.get('href', '') or enc.get('url', ''), enc.get('type', ''))

            # 3. <media:content> / <media:group> tags (used by many CMS/social feeds)
            for mc in getattr(entry, 'media_content', []) or []:
                medium = mc.get('medium', '')
                if medium == 'image':
                    mc_type = 'image/jpeg'   # safest default for medium="image"
                else:
                    mc_type = mc.get('type', '') or 'application/octet-stream'
                _add_link(mc.get('url', ''), mc_type)

            # Normalise using module-level _MIME_MAP constant
            links_to_download = [
                (url, _MIME_MAP.get(ct, ct)) for url, ct in links_to_download
            ]

            # Audio files: podcast CDNs actively block bots and files are too
            # large for Telegram uploads. Strip audio links out of the download
            # queue and append the first audio URL to the caption instead so
            # listeners can open it directly in their podcast app / browser.
            audio_links = [(u, ct) for u, ct in links_to_download if ct in _AUDIO_TYPES or ct.startswith('audio/')]
            links_to_download = [(u, ct) for u, ct in links_to_download if ct not in _AUDIO_TYPES and not ct.startswith('audio/')]

            # No non-audio media OR downloads disabled: send as text.
            # But if audio_links exist, fall through to the audio download section below.
            if (not links_to_download or not download_enabled) and not audio_links:
                if media_mode == 'Only media':
                    if not force_single:
                        newly_processed.add(item_id)
                        processed_ids.add(item_id)
                    if force_single: break
                    continue

                target_url = entry.get('link', '')
                if links_to_download and not download_enabled:
                    target_url = links_to_download[0][0]

                # feed_title_no_link is designed for media items — when there is no
                # media, fall back to feed_title_and_post_title_link so the text
                # message still carries a usable link to the original article.
                effective_format = source_format
                if source_format == 'feed_title_no_link' and target_url:
                    effective_format = 'feed_title_and_post_title_link'

                msg_text = _build_caption(
                    target_url=target_url,
                    desc_raw=entry.get('summary') or entry.get('description') or '',
                    feed_title=feed_title,
                    post_title=post_title,
                    has_link=bool(target_url),
                    source_format=effective_format,
                    author_text=author_text,
                    hash_text=hash_text,
                )

                limit = feed.get('length_limit', 0)
                if limit == 0: limit = 4096
                else: limit = min(limit, 4096)

                from utilitybot.utils.content_pipeline import ContentPipeline
                from utilitybot.utils.formatter import chunk_html
                if len(msg_text) > limit:
                    chunks = chunk_html(msg_text, limit)
                    for chunk in chunks:
                        try:
                            await ContentPipeline.send_via_pipeline(bot_instance, chat_id, chunk, use_replacements=settings.get('rss_use_replacements', True), use_marginals=settings.get('rss_use_marginals', True), use_blocklist=settings.get('rss_use_blocklist', True), use_antiforward=False, disable_notification=disable_notification, disable_web_page_preview=not link_preview)
                        except Exception:
                            pass
                else:
                    await ContentPipeline.send_via_pipeline(bot_instance, chat_id, msg_text, use_replacements=settings.get('rss_use_replacements', True), use_marginals=settings.get('rss_use_marginals', True), use_blocklist=settings.get('rss_use_blocklist', True), use_antiforward=False, disable_notification=disable_notification, disable_web_page_preview=not link_preview)

                if not force_single:
                    newly_processed.add(item_id)
                    processed_ids.add(item_id)
                if force_single: break
                continue

            images_group = []
            documents_group = []
            audio_group = []  # list of (file_path, metadata_dict)
            files_to_cleanup = []

            # Download all non-audio media concurrently — each file is independent
            async def _download_one(url: str, ctype: str):
                fd, temp_file = tempfile.mkstemp()
                os.close(fd)
                files_to_cleanup.append(temp_file)

                async def _try_download(src_url: str) -> bool:
                    """Try downloading from src_url. Returns True on success."""
                    try:
                        await download_file(src_url, temp_file, is_audio=False)
                        return True
                    except SkipMediaError:
                        raise
                    except Exception:
                        return False

                try:
                    # Primary download attempt
                    ok = await _try_download(url)

                    # Relay fallback chain for images (only when primary fails):
                    #   1. Cloudflare relay — proxies any URL, bypasses anti-hotlinking,
                    #      handles images + video. Fast (edge network, no conversion).
                    #   2. wsrv.nl — converts WebP/SVG/AVIF→JPEG, resizes, public CDN.
                    if not ok and (ctype in _IMAGE_TYPES or ctype == 'application/pdf'):
                        relay_url = f"{_RELAY_BASE}/{url}"
                        log.info(f"Primary failed, trying relay: {url}")
                        ok = await _try_download(relay_url)

                    if not ok and ctype in _IMAGE_TYPES:
                        wsrv_url = f"https://wsrv.nl/?url={urllib.parse.quote(url, safe='')}&output=jpg&q=85"
                        log.info(f"Relay failed, trying wsrv.nl: {url}")
                        ok = await _try_download(wsrv_url)
                        if not ok:
                            log.warning(f"All fallbacks failed for: {url}")
                            return None
                    elif not ok:
                        return None

                    if ctype in _IMAGE_TYPES:
                        # ── Image dimension pre-check ───────────────────────
                        # Read just enough bytes to decode width/height from the
                        # image header — no need to load the whole file into memory.
                        img_w, img_h = 0, 0
                        try:
                            with Image.open(temp_file) as _img:
                                img_w, img_h = _img.size
                        except Exception:
                            pass  # if Pillow can't read it, send as-is

                        file_size = os.path.getsize(temp_file)

                        # ── 10 MB photo cap ─────────────────────────────────
                        # Telegram rejects photos > 10 MB — send as document instead.
                        TELEGRAM_PHOTO_MAX = 10 * 1024 * 1024
                        if file_size > TELEGRAM_PHOTO_MAX:
                            log.info(f"Image too large for photo ({file_size // (1024*1024)} MB), sending as document: {url}")
                            name = os.path.basename(urllib.parse.urlparse(url).path) or 'image.jpg'
                            final_path = os.path.join(tempfile.gettempdir(), name)
                            os.replace(temp_file, final_path)
                            files_to_cleanup.append(final_path)
                            return ('document', final_path)

                        # ── Long image → send as document ────────────────────
                        # Telegram heavily compresses images with h/w ratio > 3:1,
                        # making text-heavy tall images (infographics, screenshots)
                        # unreadable. Send as document to preserve quality.
                        if img_w > 0 and img_h > 0 and (img_h / img_w) > 3.0:
                            log.info(f"Long image detected ({img_w}×{img_h}), sending as document: {url}")
                            name = os.path.basename(urllib.parse.urlparse(url).path) or 'image.jpg'
                            final_path = os.path.join(tempfile.gettempdir(), name)
                            os.replace(temp_file, final_path)
                            files_to_cleanup.append(final_path)
                            return ('document', final_path)

                        return ('image', temp_file)

                    elif ctype == 'application/pdf':
                        name = os.path.splitext(urllib.parse.unquote(
                            os.path.basename(urllib.parse.urlparse(url).path)))[0] or 'document'
                        final_path = os.path.join(tempfile.gettempdir(), f"{name}.pdf")
                        os.replace(temp_file, final_path)
                        files_to_cleanup.append(final_path)
                        return ('document', final_path)
                    else:
                        name = os.path.basename(urllib.parse.urlparse(url).path) or 'document.bin'
                        final_path = os.path.join(tempfile.gettempdir(), name)
                        os.replace(temp_file, final_path)
                        files_to_cleanup.append(final_path)
                        return ('document', final_path)

                except SkipMediaError as e:
                    log.info(str(e))
                    return None
                except Exception as e:
                    log.error(f"Error downloading RSS media {url}: {e}")
                    return None

            # Run all downloads for this entry in parallel
            if links_to_download:
                dl_results = await asyncio.gather(
                    *[_download_one(u, ct) for u, ct in links_to_download],
                    return_exceptions=True
                )
                for result in dl_results:
                    if result is None or isinstance(result, Exception):
                        continue
                    kind, path = result
                    if kind == 'image':
                        images_group.append(path)
                    else:
                        documents_group.append((path, None))

            # Download audio — try the relay first (fast, no FFmpeg, runs on Cloudflare edge),
            # then fall back to a direct HTTP download for standard podcast URLs.
            # audio_group holds (file_path, metadata_dict) tuples.
            raw_title  = html.unescape(entry_title)  if entry_title  else ''
            raw_author = html.unescape(entry_author) if entry_author else ''

            for audio_url, _ in audio_links:
                tmp_dir = tempfile.mkdtemp()
                files_to_cleanup.append(tmp_dir)
                try:
                    audio_path = None

                    # ── Attempt 1: Cloudflare relay ──────────────────────────
                    # The relay streams the audio directly — no FFmpeg, no thread pool,
                    # just a proxied HTTP download from the edge.
                    relay_audio_url = f"{_RELAY_BASE}/{audio_url}"
                    fd, relay_tmp = tempfile.mkstemp(suffix='.mp3', dir=tmp_dir)
                    os.close(fd)
                    try:
                        log.info(f"Attempting audio via relay: {audio_url}")
                        await download_file(relay_audio_url, relay_tmp, is_audio=False)
                        if os.path.getsize(relay_tmp) > 0:
                            audio_path = relay_tmp
                            log.info(f"Audio relay OK: {audio_url}")
                        else:
                            log.info(f"Relay returned empty file, falling back to direct download: {audio_url}")
                    except Exception as relay_err:
                        log.info(f"Audio relay failed ({relay_err}), falling back to direct download: {audio_url}")

                    # ── Attempt 2: Direct Download ──────────────────────────
                    if not audio_path:
                        fd, direct_tmp = tempfile.mkstemp(suffix='.mp3', dir=tmp_dir)
                        os.close(fd)
                        try:
                            log.info(f"Attempting direct audio download: {audio_url}")
                            await download_file(audio_url, direct_tmp, is_audio=True)
                            if os.path.getsize(direct_tmp) > 0:
                                audio_path = direct_tmp
                                log.info(f"Direct audio download OK: {audio_url}")
                            else:
                                log.info(f"Direct download returned empty file: {audio_url}")
                                raise Exception("Direct download returned empty file")
                        except Exception as direct_err:
                            log.info(f"Direct audio download failed ({direct_err}): {audio_url}")
                            raise

                    file_size = os.path.getsize(audio_path)
                    if file_size == 0:
                        raise Exception(f"Downloaded audio is empty: {audio_url}")
                    if file_size > TELEGRAM_MAX_UPLOAD_BYTES:
                        raise SkipMediaError(f"Audio too large ({file_size // (1024*1024)} MB): {audio_url}")

                    audio_meta = {
                        'title':     raw_title  or 'Podcast',
                        'performer': raw_author or feed_title or '',
                        'duration':  None,
                    }

                    # ---- Thumbnail resolution ----
                    thumb_path = None
                    # 1. RSS entry-level image (itunes:image / media:thumbnail)
                    thumb_url = (
                        getattr(entry, 'image', {}).get('href') or
                        (entry.get('media_thumbnail') or [{}])[0].get('url') or
                        next((lnk.get('href') for lnk in entry.get('links', [])
                              if lnk.get('type', '').startswith('image/')), None)
                    )
                    # 2. Feed-level podcast artwork
                    if not thumb_url:
                        thumb_url = (
                            getattr(parsed.feed, 'image', {}).get('href') or
                            getattr(parsed.feed, 'itunes_image', {}).get('href') or
                            ''
                        )
                    if thumb_url:
                        try:
                            fd, thumb_tmp = tempfile.mkstemp(suffix='.jpg', dir=tmp_dir)
                            os.close(fd)
                            await download_file(thumb_url, thumb_tmp, is_audio=False)
                            if os.path.getsize(thumb_tmp) > 0:
                                thumb_path = thumb_tmp
                        except Exception as te:
                            log.warning(f'Thumbnail download failed: {te}')

                    audio_meta['thumbnail_path'] = thumb_path
                    log.info(f"Audio ready ({file_size // 1024} KB): {audio_url} — "
                             f"{audio_meta['title']} / {audio_meta['performer']}"
                             f"{' [thumb]' if thumb_path else ''}")
                    audio_group.append((audio_path, audio_meta))
                except SkipMediaError as e:
                    log.info(str(e))
                    if not caption_text or audio_url not in caption_text:
                        caption_text = (caption_text or '') + '\n\n\U0001f3a7 <a href="' + audio_url + '">Listen / Download</a>'
                except Exception as e:
                    log.error(f"Audio download failed [{type(e).__name__}]: {e} — URL: {audio_url}")
                    if not caption_text or audio_url not in caption_text:
                        caption_text = (caption_text or '') + '\n\n\U0001f3a7 <a href="' + audio_url + '">Listen / Download</a>'

            # No pre-split here — the pipeline caption guard in content_pipeline.py
            # handles splitting at the 1024-char boundary and sends the overflow
            # as a reply to the primary message.
            text_followup = None

            # Helper: send with flood-control semaphore
            _pipe_kwargs = dict(
                use_replacements=settings.get('rss_use_replacements', True),
                use_marginals=settings.get('rss_use_marginals', True),
                use_blocklist=settings.get('rss_use_blocklist', True),
                use_antiforward=False,
                disable_notification=disable_notification,
            )
            _wm_kwargs = dict(_pipe_kwargs, use_watermark=watermark_enabled and settings.get('rss_use_watermark', True))

            async def _send(coro):
                """Acquire Telegram flood semaphore then await the send coroutine."""
                async with _TELEGRAM_SEND_SEM:
                    return await coro

            async def _send_text_chunks(text: str, reply_to: int = None):
                """
                Send overflow/followup text as plain message(s).
                - use_marginals=False: overflow already comes from after the main
                  caption; adding headers/footers again would duplicate them.
                - reply_to: message_id to thread this message under (optional).
                """
                if not text:
                    return
                _limit = feed.get('length_limit', 0)
                if _limit == 0: _limit = 4096
                else: _limit = min(_limit, 4096)
                from utilitybot.utils.formatter import chunk_html
                chunks = chunk_html(text, _limit)
                _no_marginals_kwargs = dict(_pipe_kwargs, use_marginals=False)
                for chunk in chunks:
                    try:
                        extra = {}
                        if reply_to:
                            extra['reply_to_message_id'] = reply_to
                            reply_to = None  # only thread the first chunk
                        await _send(ContentPipeline.send_via_pipeline(
                            bot_instance, chat_id, chunk,
                            **_no_marginals_kwargs,
                            **extra,
                        ))
                    except Exception:
                        pass

            if images_group:
                if len(images_group) == 1:
                    _primary_msg = None
                    try:
                        _primary_msg = await _send(ContentPipeline.send_via_pipeline(bot_instance, chat_id, caption_text, media_type="photo", media_id=FSInputFile(images_group[0]), **_wm_kwargs))
                    except Exception:
                        pass
                    if text_followup:
                        _reply_id = _primary_msg.message_id if _primary_msg else None
                        await _send_text_chunks(text_followup, reply_to=_reply_id)
                else:
                    for i in range(0, len(images_group), 10):
                        batch = images_group[i:i + 10]
                        media = []
                        for idx, img_path in enumerate(batch):
                            cap = caption_text if i == 0 and idx == 0 else ""
                            media.append({'type': 'photo', 'media': FSInputFile(img_path), 'caption': cap, 'caption_entities': None})
                        try:
                            await _send(ContentPipeline.send_media_group_via_pipeline(bot_instance, chat_id, media, **_wm_kwargs))
                        except Exception:
                            pass
                    if text_followup:
                        await _send_text_chunks(text_followup)  # no reply_to for media groups

            if documents_group:
                for i in range(0, len(documents_group), 10):
                    batch = documents_group[i:i+10]
                    if len(batch) == 1:
                        doc_path, thumb_path = batch[0]
                        cap = caption_text if i == 0 and not images_group else ""
                        try:
                            await _send(ContentPipeline.send_via_pipeline(bot_instance, chat_id, cap, media_type="document", media_id=FSInputFile(doc_path, filename=os.path.basename(doc_path)), **_wm_kwargs))
                        except Exception:
                            pass
                    else:
                        media = []
                        doc_text_followup = None
                        for idx, (doc_path, thumb_path) in enumerate(batch):
                            cap = caption_text if i == 0 and idx == 0 and not images_group else ""
                            media.append({'type': 'document', 'media': FSInputFile(doc_path, filename=os.path.basename(doc_path)), 'caption': cap, 'caption_entities': None, 'file_name': os.path.basename(doc_path)})
                        try:
                            await _send(ContentPipeline.send_media_group_via_pipeline(bot_instance, chat_id, media, **_wm_kwargs))
                        except Exception:
                            pass
                        if doc_text_followup:
                            await _send_text_chunks(doc_text_followup)
            if audio_group:
                _primary_audio_msg = None
                for idx, (audio_path, audio_meta) in enumerate(audio_group):
                    cap = caption_text if idx == 0 and not images_group and not documents_group else ""
                    try:
                        extra = {}
                        if audio_meta.get('title'):     extra['title']     = audio_meta['title']
                        if audio_meta.get('performer'): extra['performer'] = audio_meta['performer']
                        if audio_meta.get('duration'):  extra['duration']  = audio_meta['duration']
                        if audio_meta.get('thumbnail_path') and os.path.exists(audio_meta['thumbnail_path']):
                            extra['thumbnail'] = FSInputFile(audio_meta['thumbnail_path'])
                        _sent = await _send(ContentPipeline.send_via_pipeline(
                            bot_instance, chat_id, cap,
                            media_type="audio",
                            media_id=FSInputFile(audio_path, filename=os.path.basename(audio_path)),
                            use_watermark=False,
                            **_pipe_kwargs,
                            **extra,
                        ))
                        if idx == 0 and _sent:
                            _primary_audio_msg = _sent
                    except Exception as e:
                        log.error(f"Error sending audio {audio_path}: {e}")
                # Send overflow text as a reply to the first audio message
                if text_followup:
                    _reply_id = _primary_audio_msg.message_id if _primary_audio_msg else None
                    await _send_text_chunks(text_followup, reply_to=_reply_id)

            for fpath in files_to_cleanup:
                if not fpath:
                    continue
                try:
                    if os.path.isdir(fpath):
                        shutil.rmtree(fpath, ignore_errors=True)
                    elif os.path.exists(fpath):
                        os.remove(fpath)
                except Exception as e:
                    log.warning(f"Failed to cleanup temp path {fpath}: {e}")

            if not force_single:
                newly_processed.add(item_id)
                # Also add to the in-memory set so concurrent tasks within this same
                # cycle don't re-send the same item before the bulk_write completes.
                processed_ids.add(item_id)
            if force_single:
                # Mark as processed so scheduler doesn't re-send on next cycle.
                # (force_single = Send Latest Post — user explicitly requested it,
                #  but the scheduler should still treat it as seen.)
                newly_processed.add(item_id)
                break

    except Exception as e:
        if _is_transient_error(e):
            fail_count = (feed.get('fail_count') or 0) + 1
            delay = _BACKOFF_DELAYS[min(fail_count - 1, _BACKOFF_MAX_FAILS - 1)]
            retry_after = datetime.now(timezone.utc).replace(microsecond=0)
            retry_after = retry_after + timedelta(seconds=delay)
            await db.record_feed_failure(chat_id, feed_url, fail_count, retry_after)
            log.warning(
                f"Transient error fetching feed (attempt {fail_count}, "
                f"backoff {delay // 60}m, retry after {retry_after.isoformat()}): "
                f"{feed_url} — {e}"
            )
        else:
            log.error(f"Error fetching feed {feed_url}: {e}")

    # Bulk-mark all newly processed items in one DB round-trip
    if newly_processed:
        from pymongo import UpdateOne
        ops = [
            UpdateOne(
                {'chat_id': chat_id, 'feed_url': feed_url, 'item_id': iid},
                {'$setOnInsert': {'chat_id': chat_id, 'feed_url': feed_url,
                                  'item_id': iid, 'processed_at': datetime.now(timezone.utc)}},
                upsert=True
            )
            for iid in newly_processed
        ]
        try:
            await db.db.rss_processed.bulk_write(ops, ordered=False)
        except Exception as e:
            log.warning(f"bulk mark_processed error for {feed_url}: {e}")

        try:
            await db.enforce_processed_cap(chat_id, feed_url, config.RSS_PROCESSED_CACHE_CAP)
        except Exception as e:
            log.warning(f"enforce_processed_cap error for {feed_url}: {e}")

async def send_latest_item(bot_instance, feed: dict):
    await _process_single_feed(bot_instance, feed, force_single=True)

_is_fetching = False

async def fetch_and_process_feeds(bot_instance):
    global _is_fetching
    if _is_fetching:
        log.warning("RSS fetch cycle already running, skipping this trigger to prevent memory leak.")
        return
    _is_fetching = True

    try:
        feeds = await db.get_rss_feeds()
        if not feeds:
            return
        now = datetime.now(timezone.utc)

        # Fetch all chat settings in one parallel gather instead of one await per feed.
        # Two collections are needed:
        #   - rss_settings : holds default_time_interval (set via RSS Dashboard)
        #   - settings     : holds pipeline flags (rss_use_watermark, rss_use_blocklist, …)
        # Merging them (rss_settings wins on conflicts) gives _process_single_feed a
        # single dict with every key it needs.
        chat_ids = list({f['chat_id'] for f in feeds})
        rss_settings_results, general_settings_results = await asyncio.gather(
            asyncio.gather(*[db.get_rss_settings(cid) for cid in chat_ids], return_exceptions=True),
            asyncio.gather(*[db.get_settings(cid)     for cid in chat_ids], return_exceptions=True),
        )
        settings_map = {}
        for cid, rss_s, gen_s in zip(chat_ids, rss_settings_results, general_settings_results):
            merged = {}
            if isinstance(gen_s, dict):
                merged.update(gen_s)
            if isinstance(rss_s, dict):
                merged.update(rss_s)   # rss_settings keys take precedence
            settings_map[cid] = merged

        due_feeds = []
        for feed in feeds:
            chat_id = feed['chat_id']
            if feed.get('status', 'Activated') == 'Deactivated':
                continue
            settings = settings_map.get(chat_id, {})
            default_interval = settings.get('default_time_interval', 300)
            interval = feed.get('time_interval') or default_interval
            last_checked = feed.get('last_checked', datetime.fromtimestamp(0, tz=timezone.utc))
            # Normalise naive datetimes stored by older bot versions
            if last_checked.tzinfo is None:
                last_checked = last_checked.replace(tzinfo=timezone.utc)
            if (now - last_checked).total_seconds() < interval:
                continue
            due_feeds.append(feed)

        if not due_feeds:
            return

        # Bulk-update last_checked for all due feeds in one round-trip
        from pymongo import UpdateOne
        ops = [
            UpdateOne(
                {'chat_id': f['chat_id'], 'feed_url': f['feed_url']},
                {'$set': {'last_checked': now}}
            )
            for f in due_feeds
        ]
        try:
            await db.db.rss_feeds.bulk_write(ops, ordered=False)
        except Exception as e:
            log.warning(f'fetch_and_process_feeds bulk_write error: {e}')

        # Process all due feeds concurrently, passing pre-fetched settings to avoid
        # a redundant db.get_settings call inside each _process_single_feed.
        # _FEED_SEM caps parallelism at 10 to prevent RAM spikes when many feeds
        # are due at the same time — each task holds a parsed feedparser object
        # plus any in-flight media, so unbounded concurrency risks OOM.
        async def _process_with_sem(feed):
            async with _FEED_SEM:
                await _process_single_feed(bot_instance, feed, settings=settings_map.get(feed['chat_id'], {}))

        tasks = [
            asyncio.create_task(_process_with_sem(feed))
            for feed in due_feeds
        ]
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        _is_fetching = False
