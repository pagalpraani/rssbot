# =============================================================================
# Module: Content Pipeline
# Path: utilitybot/utils/content_pipeline.py
# Description: Utility functions and helpers for operations related to Content
#              Pipeline.
# =============================================================================

from aiogram import Bot
from aiogram.types import Message, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo, InputMediaAudio, InputMediaDocument
from ..database.mongodb import db
from ..utils.formatter import unparse, format_message, split_caption as _split_caption
from ..utils.logger import get_logger
import html
import re
import asyncio

log = get_logger(__name__)

# Telegram API hard limits
_TG_CAPTION_LIMIT = 1024   # maximum characters in any media caption
_TG_MESSAGE_LIMIT = 4096   # maximum characters in a text message


# Module-level cache: logo_id → bytes
# Avoids re-downloading the same logo from Telegram on every watermark call.
import collections
_logo_bytes_cache: collections.OrderedDict = collections.OrderedDict()
_LOGO_BYTES_CACHE_MAX_SIZE = 32

class ContentPipeline:
    PIPELINE_MARKER = "\u200b"
    HEADER_MARKER = "\u200c"
    FOOTER_MARKER = "\u200d"

    _replacement_cache = {}

    @classmethod
    def invalidate_replacement_cache(cls, chat_id: int):
        if chat_id in cls._replacement_cache:
            del cls._replacement_cache[chat_id]

    @staticmethod
    def _safe_replace_text_nodes(html_content: str, pattern: re.Pattern, replacement: str) -> str:
        """
        Performs regex replacement only on text nodes within HTML content.
        Preserves tags.
        """
        # Split by tags (keeping delimiters)
        parts = re.split(r'(<[^>]+>)', html_content)
        new_parts = []
        for part in parts:
            if part.startswith('<'):
                new_parts.append(part)
            else:
                new_parts.append(pattern.sub(replacement, part))
        return "".join(new_parts)

    # ⚡ Bolt Optimization: Pre-compile marginal strip regexes at class level
    _MARGINALS_FAST_FAIL_PATTERN = re.compile(rf"(?:{re.escape(HEADER_MARKER)}|{re.escape(FOOTER_MARKER)}|%%|\{{\{{|\[\[|<<|---)")

    _MARGINALS_PATTERNS = [
        re.compile(rf"{HEADER_MARKER}.*?{HEADER_MARKER}", re.DOTALL),
        re.compile(rf"{FOOTER_MARKER}.*?{FOOTER_MARKER}", re.DOTALL),
        re.compile(r"%{2,}.*?%{2,}", re.DOTALL),        # %%header%%
        re.compile(r"\{\{.*?\}\}", re.DOTALL),          # {{header}}
        re.compile(r"\[\[.*?\]\]", re.DOTALL),          # [[header]]
        re.compile(r"<<.*?>>", re.DOTALL),              # <<header>>
        re.compile(r"%%.*?%%", re.DOTALL),              # %HEADER%
        re.compile(r"---.*?---", re.DOTALL)             # --- footer ---
    ]

    @staticmethod
    def strip_existing_marginals(text: str) -> str:
        """
        Removes any previously-added marginals.
        Works for:
        - Marked marginals (HEADER_MARKER / FOOTER_MARKER)
        - Random formatted blocks (%%...%%, {{...}}, <<...>>, etc)
        """

        if not text or not ContentPipeline._MARGINALS_FAST_FAIL_PATTERN.search(text):
            return text

        cleaned = text
        for p in ContentPipeline._MARGINALS_PATTERNS:
            cleaned = p.sub("", cleaned).strip()

        return cleaned

    @classmethod
    async def process_replacements(cls, text: str, chat_id: int, media_type: str = "text", is_album: bool = False, settings: dict = None) -> str:
        """
        Stage 3: Replacements
        Rewrites text based on configured rules.
        Supports case-sensitivity, HTML-safety, and media type filtering.
        """
        if not text:
            return text

        if settings is None:
            settings = await db.get_settings(chat_id) or {}
        if not settings.get("replacements_active", True):
            return text

        # Check Cache
        if chat_id in cls._replacement_cache:
            repls_compiled = cls._replacement_cache[chat_id]
        else:
            # Build Cache
            raw_repls = settings.get("replacements_map", [])
            if not raw_repls:
                if len(cls._replacement_cache) >= 500:
                    cls._replacement_cache.pop(next(iter(cls._replacement_cache)))
                cls._replacement_cache[chat_id] = []
                return text

            compiled_list = []
            for r in raw_repls:
                src = r.get('src')
                dest = r.get('dest')
                if not src:
                    continue

                ignore_case = r.get('ignore_case', True)
                whole_word = r.get('whole_word', False)
                apply_on = r.get('apply_on', ["text", "caption", "media_group"])

                flags = re.IGNORECASE if ignore_case else 0
                escaped_src = re.escape(src)

                if whole_word:
                    # Enforce whole word boundaries (robust lookarounds)
                    regex_pattern = fr"(?<!\w){escaped_src}(?!\w)"
                else:
                    regex_pattern = escaped_src

                pattern = re.compile(regex_pattern, flags)

                # Sanitize destination
                safe_dest = html.escape(dest)

                compiled_list.append({
                    "pattern": pattern,
                    "replacement": safe_dest,
                    "apply_on": apply_on
                })

            if len(cls._replacement_cache) >= 500:
                cls._replacement_cache.pop(next(iter(cls._replacement_cache)))
            cls._replacement_cache[chat_id] = compiled_list
            repls_compiled = compiled_list

        if not repls_compiled:
            return text

        current_text = text
        check_key = "text" if media_type == "text" else "caption"

        for r in repls_compiled:
            # Check Album constraint
            if is_album and "media_group" not in r["apply_on"]:
                continue

            # Check Media Type constraint
            if check_key not in r["apply_on"]:
                continue

            # Apply Safe Replacement
            current_text = cls._safe_replace_text_nodes(current_text, r["pattern"], r["replacement"])

        return current_text

    @staticmethod
    async def process_marginals(text: str, chat_id: int, bot: Bot, user=None, chat=None, flag_overrides: dict = None, settings: dict = None) -> (str, InlineKeyboardMarkup):
        """
        Stage 5: Marginals
        Adds headers and footers.
        flag_overrides: optional dict with keys 'skip_header' / 'skip_footer' (bool).
        """
        flag_overrides = flag_overrides or {}
        if settings is None:
            settings = await db.get_settings(chat_id) or {}
        if not settings.get("marginals_enabled"):
            return text, None

        header = settings.get("header_content", "")
        footer = settings.get("footer_content", "")

        if not header and not footer:
            return text, None

        header_formatted = None
        if header and not flag_overrides.get("skip_header"):
            header_formatted = await format_message(header, user, chat, settings, bot=bot)
            if header_formatted.api_flags.get("disable_header"):
                header_formatted = None

        footer_formatted = None
        if footer and not flag_overrides.get("skip_footer"):
            footer_formatted = await format_message(footer, user, chat, settings, bot=bot)
            if footer_formatted.api_flags.get("disable_footer"):
                footer_formatted = None

        parts = []

        # Strip any existing marginals (prevents duplication)
        clean_body = ContentPipeline.strip_existing_marginals(
            text.replace(ContentPipeline.PIPELINE_MARKER, "")
        ).strip()

        # Final cleaned body (no previous marginals)
        body = clean_body

        # Add header if available
        if header_formatted:
            parts.append(f"{ContentPipeline.HEADER_MARKER}{header_formatted.text}{ContentPipeline.HEADER_MARKER}")

        # Add body
        if body:
            parts.append(body)

        # Add footer if available
        if footer_formatted:
            parts.append(f"{ContentPipeline.FOOTER_MARKER}{footer_formatted.text}{ContentPipeline.FOOTER_MARKER}")

        final_text = "\n\n".join(parts)

        # Merge buttons
        rows = []
        if header_formatted and header_formatted.reply_markup:
            rows.extend(header_formatted.reply_markup.inline_keyboard)
        # Body buttons? (Usually passed separately, but for now we only handle marginal buttons)
        if footer_formatted and footer_formatted.reply_markup:
            rows.extend(footer_formatted.reply_markup.inline_keyboard)

        markup = InlineKeyboardMarkup(inline_keyboard=rows) if rows else None

        return final_text, markup

    @classmethod
    async def apply_watermark(cls, bot: Bot, file_id: str, chat_id: int, media_type: str, settings: dict, cached_logo_bytes: bytes = None, original_file_name: str = None, force_thumbnail: bool = False, skip_watermark: bool = False) -> tuple:
        """
        Downloads the media, applies the channel's global watermark via the watermark engine,
        and returns the local file paths to the processed media and thumbnail (if any).

        force_thumbnail: generate thumbnail even when no logo/footer is set (for PDFs).
        skip_watermark:  skip logo/footer overlay but still generate thumbnail.
        Returns: (processed_media_path, thumb_path, filename)
        """
        if not settings.get('watermark_enabled', True) and not force_thumbnail:
            return None, None, None

        logo_id = settings.get('logo_id')
        footer_note_enabled = settings.get('footer_note_enabled', True)
        custom_name_enabled = settings.get('custom_name_enabled', True)
        try:
            watermark_opacity = int(settings.get('watermark_opacity', 100))
        except (ValueError, TypeError):
            watermark_opacity = 100

        footer_note = settings.get('footer_note') if footer_note_enabled else None
        custom_name = settings.get('custom_name', '') if custom_name_enabled else ''

        # When skip_watermark is True, clear the overlay content but still run
        # process_pdf_sync so the thumbnail gets generated.
        if skip_watermark:
            logo_id = None
            footer_note = None
            custom_name = ''

        if not logo_id and not footer_note and not custom_name_enabled and not force_thumbnail:
            if media_type != "document":
                return None, None, None

        # EARLY BYPASS: Do not process non-JPG photos or non-PDF documents to avoid downloading them unnecessarily.
        # We can check the original file name or the file_id string (if it's a URL or path).
        # Standard Telegram file_ids don't have extensions, so we must assume they are valid and check magic bytes later.
        is_definitely_not_pdf = False
        is_definitely_not_jpg = False

        name_to_check = original_file_name or (getattr(file_id, 'filename', None) if hasattr(file_id, 'filename') else None)
        if not name_to_check and isinstance(file_id, str) and (file_id.startswith('http') or '/' in file_id or '.' in file_id):
            name_to_check = file_id

        if name_to_check and '.' in name_to_check:
            name_lower = name_to_check.lower()
            if media_type == "document" and not name_lower.endswith('.pdf'):
                is_definitely_not_pdf = True
            if media_type == "photo" and not name_lower.endswith(('.jpg', '.jpeg')):
                is_definitely_not_jpg = True

        if media_type == "document" and is_definitely_not_pdf:
            return None, None, None

        if media_type == "photo" and is_definitely_not_jpg:
            return None, None, None

        import tempfile
        import os
        import io
        from utilitybot.utils.watermark import process_image_sync, process_pdf_sync

        try:
            from aiogram.types import FSInputFile
            if isinstance(file_id, FSInputFile):
                temp_file_path = file_id.path
            elif isinstance(file_id, str) and os.path.exists(file_id):
                temp_file_path = file_id
            else:
                file_info = await bot.get_file(file_id)
                fd, temp_file_path = tempfile.mkstemp()
                os.close(fd)
                await bot.download_file(file_info.file_path, temp_file_path)

            logo_bytes = cached_logo_bytes
            if logo_id and logo_bytes is None:
                # Check module-level cache before hitting Telegram API
                if logo_id in _logo_bytes_cache:
                    _logo_bytes_cache.move_to_end(logo_id)
                    logo_bytes = _logo_bytes_cache[logo_id]
            if logo_id and logo_bytes is None:
                try:
                    logo_file_info = await bot.get_file(logo_id)
                    logo_io = io.BytesIO()
                    await bot.download_file(logo_file_info.file_path, logo_io)
                    logo_bytes = logo_io.getvalue()
                    _logo_bytes_cache[logo_id] = logo_bytes  # cache for next call
                    if len(_logo_bytes_cache) > _LOGO_BYTES_CACHE_MAX_SIZE:
                        _logo_bytes_cache.popitem(last=False)
                except Exception as e:
                    log.warning(f"Watermark Engine: Invalid logo_id '{logo_id}' configured for chat {chat_id}: {e}")
                    logo_bytes = None

            import asyncio
            from utilitybot.utils.watermark import _WATERMARK_POOL
            loop = asyncio.get_event_loop()

            if media_type == "photo":
                # Only process actual JPEG/JPG images. Skip PNG, GIF, WebP completely.
                is_jpg = False
                if original_file_name:
                    is_jpg = original_file_name.lower().endswith(('.jpg', '.jpeg'))
                elif temp_file_path:
                    is_jpg = temp_file_path.lower().endswith(('.jpg', '.jpeg'))

                # Ultimate fallback: check file signature if extension is missing/wrong
                if not is_jpg and temp_file_path and os.path.exists(temp_file_path):
                    try:
                        with open(temp_file_path, 'rb') as f_sig:
                            header = f_sig.read(3)
                            if header == b'\xff\xd8\xff':
                                is_jpg = True
                    except Exception:
                        pass

                if not logo_bytes or not is_jpg:
                    return temp_file_path, None, None

                out_path = await loop.run_in_executor(_WATERMARK_POOL, process_image_sync, temp_file_path, logo_bytes, watermark_opacity)
                if out_path != temp_file_path and not isinstance(file_id, (str, FSInputFile)):
                    os.remove(temp_file_path)
                return out_path, None, None

            elif media_type == "document":
                # Only process actual PDF files — other document types (docx, zip, mp4
                # sent as document, etc.) must pass through untouched to avoid corruption.
                is_pdf = False
                if isinstance(file_id, FSInputFile):
                    is_pdf = (file_id.filename or '').lower().endswith('.pdf') or (getattr(file_id, 'path', '') or '').lower().endswith('.pdf')
                elif isinstance(file_id, str) and os.path.exists(file_id):
                    is_pdf = file_id.lower().endswith('.pdf')
                elif original_file_name:
                    is_pdf = original_file_name.lower().endswith('.pdf')
                else:
                    # Telegram file — check the path returned by get_file
                    is_pdf = 'temp_file_path' in dir() and temp_file_path.lower().endswith('.pdf')
                    if not is_pdf and 'file_info' in dir():
                        is_pdf = (getattr(file_info, 'file_path', '') or '').lower().endswith('.pdf')

                # Ultimate fallback: check file signature if extension is missing/wrong
                if not is_pdf and temp_file_path and os.path.exists(temp_file_path):
                    try:
                        with open(temp_file_path, 'rb') as f_sig:
                            header = f_sig.read(4)
                            if header == b'%PDF':
                                is_pdf = True
                    except Exception:
                        pass

                if not is_pdf:
                    # Not a PDF — return as-is, no processing, no rename
                    log.debug(f"apply_watermark: not a PDF (filename={getattr(file_id, 'filename', None)}, path={getattr(file_id, 'path', file_id)}), skipping")
                    return temp_file_path, None, original_file_name or None

                log.debug(f"apply_watermark: running process_pdf_sync on {temp_file_path}, logo={bool(logo_bytes)}, footer={bool(footer_note)}")
                out_pdf, out_thumb = await loop.run_in_executor(_WATERMARK_POOL, process_pdf_sync, temp_file_path, logo_bytes, footer_note, custom_name, watermark_opacity)
                log.debug(f"apply_watermark: process_pdf_sync returned out_pdf={out_pdf}, out_thumb={out_thumb}")

                import urllib.parse
                import shutil
                import uuid

                if original_file_name:
                    original_filename = os.path.splitext(original_file_name)[0]
                else:
                    original_filename = "document"
                    if isinstance(file_id, FSInputFile) and file_id.filename:
                        # Prefer the explicitly provided filename from the FSInputFile wrapper
                        original_filename = os.path.splitext(file_id.filename)[0]
                    elif not isinstance(file_id, (str, FSInputFile)):
                        original_filename = os.path.splitext(os.path.basename(urllib.parse.urlparse(file_info.file_path).path))[0]
                    else:
                        # Fallback for local strings or FSInputFile without filename
                        original_filename = os.path.splitext(os.path.basename(temp_file_path))[0]

                    if not original_filename or original_filename.startswith("tmp"):
                        original_filename = "document"

                import re
                if custom_name:
                    safe_custom_name = re.sub(r'[^\w\-_\.\@ ]', '_', custom_name)
                    final_name_disk = f"{original_filename}_{safe_custom_name}_{uuid.uuid4().hex[:8]}.pdf".replace(" ", "_")
                    final_name_tg = f"{original_filename}_{safe_custom_name}.pdf"
                else:
                    final_name_disk = f"{original_filename}_{uuid.uuid4().hex[:8]}.pdf".replace(" ", "_")
                    final_name_tg = f"{original_filename}.pdf"

                final_pdf_path = os.path.join(tempfile.gettempdir(), final_name_disk)
                shutil.copy(out_pdf, final_pdf_path)

                if not isinstance(file_id, (str, FSInputFile)) and os.path.exists(temp_file_path):
                    os.remove(temp_file_path)
                if out_pdf != temp_file_path and os.path.exists(out_pdf):
                    os.remove(out_pdf)

                if not settings.get('thumbnail_enabled', True):
                    if out_thumb and os.path.exists(out_thumb):
                        os.remove(out_thumb)
                    out_thumb = None

                return final_pdf_path, out_thumb, final_name_tg

        except Exception as e:
            log.error(f"Watermark Engine error on {file_id}: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path) and not isinstance(file_id, (str, FSInputFile)):
                os.remove(temp_file_path)

        return None, None, None

    @classmethod
    async def send_via_pipeline(cls, bot: Bot, chat_id: int, content: str, media_type: str = "text", media_id: str = None, reply_markup=None, user=None, chat=None, use_replacements=True, use_marginals=True, use_watermark=True, use_blocklist=False, use_antiforward=False, original_file_name: str = None, **kwargs):
        """
        The Universal Send Function for Single Messages.
        """


        # Shared settings dict — fetched lazily once and reused by every stage
        # that needs it (blocklist, replacements, marginals, watermark).
        settings: dict | None = None

        async def _get_settings() -> dict:
            nonlocal settings
            if settings is None:
                settings = await db.get_settings(chat_id) or {}
            return settings

        # 1. Blocklist
        if use_blocklist and content:
            s = await _get_settings()
            blocklist_active = s.get("blocklist_active", True)
            if blocklist_active:
                words = s.get("blocklist_words", [])
                text_lower = content.lower()
                for word in words:
                    if word.lower() in text_lower:
                        return None # Blocked

        # 3. Replacements
        current_text = content
        if use_replacements:
            current_text = await cls.process_replacements(current_text, chat_id, media_type=media_type, is_album=False, settings=await _get_settings())

        # 5. Caption length pre-check — split BEFORE marginals so marginals are
        #    applied only to the last message (the overflow), not to the media caption.
        _caption_overflow_raw = None
        if media_type != "text" and current_text and len(current_text) > _TG_CAPTION_LIMIT:
            _split_idx = _split_caption(current_text, _TG_CAPTION_LIMIT)
            _caption_overflow_raw = current_text[_split_idx:].strip()
            current_text = current_text[:_split_idx].strip()

        # 5b. Marginals — applied to the caption only when there is NO overflow.
        #     When the message splits into two parts, the media caption is part 1
        #     and the follow-up text is part 2 (the last message).  Marginals must
        #     appear on the last message, so we skip them on the caption and pass
        #     use_marginals=True to the overflow send below.
        final_markup = reply_markup.model_copy(deep=True) if reply_markup else None
        if use_marginals and not _caption_overflow_raw:
            # Single message — apply marginals normally.
            current_text, marg_markup = await cls.process_marginals(current_text, chat_id, bot, user, chat, settings=await _get_settings())
            if marg_markup:
                if final_markup:
                    final_markup.inline_keyboard.extend(marg_markup.inline_keyboard)
                else:
                    final_markup = marg_markup
        # When _caption_overflow_raw is set, marginals are intentionally deferred
        # to the overflow (last) message — see the overflow send block below.

        # Add Marker
        if current_text:
            current_text += cls.PIPELINE_MARKER

        # 8. Post-marginals caption guard — marginals themselves could push
        #    the caption over 1024 even after the pre-split above (e.g. a very
        #    long footer). If so, split again and the overflow gets no footer
        #    (it was already added to current_text; don't duplicate it).
        _caption_overflow = None
        if media_type != "text" and current_text and len(current_text) > _TG_CAPTION_LIMIT:
            _split_idx2 = _split_caption(current_text, _TG_CAPTION_LIMIT)
            _caption_overflow = current_text[_split_idx2:].strip()
            current_text = current_text[:_split_idx2].strip()

        # 6. Watermark + Thumbnail
        processed_media = media_id
        thumb_path = None
        cleanup_paths = []
        if media_type in ("photo", "document") and media_id:
            s = await _get_settings()
            thumbnail_enabled = s.get('thumbnail_enabled', True)
            has_wm_content = any([s.get('logo_id'), s.get('footer_note')])

            need_thumbnail = media_type == "document" and thumbnail_enabled
            need_watermark = use_watermark and (
                (media_type == "photo" and has_wm_content) or
                (media_type == "document" and has_wm_content)
            )

            if need_thumbnail or need_watermark:
                log.debug(f"RSS watermark: need_thumbnail={need_thumbnail}, need_watermark={need_watermark}, media_type={media_type}, file_id={getattr(media_id, 'filename', media_id)}")
                out_path, thumb, final_tg_name = await cls.apply_watermark(
                    bot, media_id, chat_id, media_type, s,
                    original_file_name=original_file_name,
                    force_thumbnail=need_thumbnail,
                    skip_watermark=not need_watermark,
                )
                log.debug(f"RSS watermark result: out_path={out_path}, thumb={thumb}, final_tg_name={final_tg_name}")
                if out_path:
                    from aiogram.types import FSInputFile
                    processed_media = FSInputFile(out_path, filename=final_tg_name) if final_tg_name else FSInputFile(out_path)
                    cleanup_paths.append(out_path)
                    if thumb:
                        thumb_path = FSInputFile(thumb)
                        cleanup_paths.append(thumb)


        # 7. Antiforward
        if use_antiforward:
            kwargs['protect_content'] = True

        # Send — retry on timeout/network errors (large files like audio/video often need it)
        from aiogram.exceptions import TelegramRetryAfter, TelegramNetworkError
        import aiohttp as _aiohttp
        _MAX_RETRIES = 3
        _last_exc = None
        _sent_msg = None
        try:
            for _attempt in range(_MAX_RETRIES):
                try:
                    if media_type == "text":
                        _sent_msg = await bot.send_message(chat_id, current_text, reply_markup=final_markup, parse_mode="HTML", **kwargs)
                    elif media_type == "photo":
                        _sent_msg = await bot.send_photo(chat_id, processed_media, caption=current_text, reply_markup=final_markup, parse_mode="HTML", **kwargs)
                    elif media_type == "video":
                        _sent_msg = await bot.send_video(chat_id, media_id, caption=current_text, reply_markup=final_markup, parse_mode="HTML", **kwargs)
                    elif media_type == "audio":
                        _sent_msg = await bot.send_audio(chat_id, media_id, caption=current_text, reply_markup=final_markup, parse_mode="HTML", **kwargs)
                    elif media_type == "voice":
                        _sent_msg = await bot.send_voice(chat_id, media_id, caption=current_text, reply_markup=final_markup, parse_mode="HTML", **kwargs)
                    elif media_type == "document":
                        if thumb_path:
                            _sent_msg = await bot.send_document(chat_id, processed_media, thumbnail=thumb_path, caption=current_text, reply_markup=final_markup, parse_mode="HTML", **kwargs)
                        else:
                            _sent_msg = await bot.send_document(chat_id, processed_media, caption=current_text, reply_markup=final_markup, parse_mode="HTML", **kwargs)
                    elif media_type == "animation":
                        _sent_msg = await bot.send_animation(chat_id, media_id, caption=current_text, reply_markup=final_markup, parse_mode="HTML", **kwargs)
                    break  # success — exit retry loop
                except TelegramRetryAfter as e:
                    wait = e.retry_after + 1
                    log.warning(f"Pipeline Send rate-limited, retrying in {wait}s (attempt {_attempt+1}/{_MAX_RETRIES})")
                    await asyncio.sleep(wait)
                    _last_exc = e
                except (TelegramNetworkError, asyncio.TimeoutError, _aiohttp.ClientError) as e:
                    if _attempt < _MAX_RETRIES - 1:
                        wait = 5 * (2 ** _attempt)  # 5s, 10s, 20s
                        log.warning(f"Pipeline Send network error, retrying in {wait}s (attempt {_attempt+1}/{_MAX_RETRIES}): {e}")
                        await asyncio.sleep(wait)
                        _last_exc = e
                    else:
                        log.error(f"Pipeline Send Error: {e}")
                        raise
                except Exception as e:
                    log.error(f"Pipeline Send Error: {e}")
                    raise
            if _last_exc and _sent_msg is None:
                log.error(f"Pipeline Send Error after {_MAX_RETRIES} retries: {_last_exc}")
                raise _last_exc
        finally:
            import os
            if 'cleanup_paths' in locals():
                for p in cleanup_paths:
                    if isinstance(p, str) and os.path.exists(p):
                        try: os.remove(p)
                        except Exception: pass

        # Send overflows as replies to the primary message so they thread together.
        # _caption_overflow_raw: content that didn't fit in the media caption.
        #   → This is the LAST message, so marginals belong here (use_marginals=use_marginals).
        #     Replacements already ran on the full text before the split, so skip them.
        #     Forward reply_markup and antiforward so buttons/protection land on the last msg.
        # _caption_overflow: content that overflowed AFTER marginals (rare, e.g. very long footer).
        #   → send direct — marginals already applied; don't duplicate.
        if _caption_overflow_raw and _sent_msg is not None:
            try:
                await cls.send_via_pipeline(
                    bot, chat_id, _caption_overflow_raw,
                    media_type="text",
                    reply_markup=reply_markup,
                    use_replacements=False,
                    use_marginals=use_marginals,
                    use_blocklist=False,
                    use_antiforward=use_antiforward,
                    reply_to_message_id=_sent_msg.message_id,
                )
            except Exception as e:
                log.warning(f"Pipeline: failed to send caption overflow: {e}")

        if _caption_overflow and _sent_msg is not None:
            try:
                await bot.send_message(
                    chat_id,
                    _caption_overflow,
                    parse_mode="HTML",
                    reply_to_message_id=_sent_msg.message_id,
                )
            except Exception as e:
                log.warning(f"Pipeline: failed to send post-marginals overflow: {e}")

        return _sent_msg

    @classmethod
    async def send_media_group_via_pipeline(cls, bot: Bot, chat_id: int, media_list: list, user=None, chat=None, use_replacements=True, use_marginals=True, reply_markup: InlineKeyboardMarkup = None, use_watermark=False, use_blocklist=False, use_antiforward=False, **kwargs):
        """
        Send a Media Group (Album) via the pipeline.
        media_list: List of dicts or tuples containing (type, media_id, caption, caption_entities)
        Structure expected: [{"type": "photo", "media": "id", "caption": "txt", "entities": [...]}, ...]
        """

        if not media_list:
            return


        # 1. Blocklist
        if use_blocklist:
            settings = await db.get_settings(chat_id) or {}
            blocklist_active = settings.get("blocklist_active", True)
            if blocklist_active:
                words = settings.get("blocklist_words", [])
                for item in media_list:
                    cap = item.get('caption', '')
                    if cap:
                        text_lower = cap.lower()
                        for word in words:
                            if word.lower() in text_lower:
                                return None # Blocked

        input_media_list = []
        album_markup = reply_markup.model_copy(deep=True) if reply_markup else None
        marginals_processed = False

        # ⚡ Bolt Optimization: Fetch settings once for the entire album
        settings = None
        if use_replacements or use_marginals:
            settings = await db.get_settings(chat_id) or {}

        # Iterate to process captions
        for i, item in enumerate(media_list):
            m_type = item.get("type")
            m_media = item.get("media")
            m_caption = item.get("caption", "") or ""
            # Entities not directly used if we assume raw text, but usually we unparse before calling this?
            # Assuming caller passes raw text or unparsed HTML in 'caption'.
            # If 'entities' are passed, we might need to unparse here.
            # Let's assume input 'caption' is already HTML or plain text suitable for processing.



        # 3. Replacements (Per Caption)
            if use_replacements:
                m_caption = await cls.process_replacements(m_caption, chat_id, media_type=m_type, is_album=True, settings=settings)

            # 5. Marginals
            # Rule: Apply marginals to the "Main" caption.
            # Logic: If this item has a caption, and we haven't applied marginals yet?
            # Or apply to ALL items? Telegram albums usually show one caption at bottom if it's mixed?
            # Actually, every item can have a caption (up to 1024 chars).
            # But usually channels want one unified look.
            # Let's apply marginals to EVERY item that has a caption?
            # Or just the first one?
            # Let's stick to "Apply to any caption found".

            if use_marginals and m_caption:
                # Process Marginals
                # Note: Marginals logic adds headers/footers.
                # We capture the markup (buttons) here but can't attach it to the media group.
                # We will save it to send separately if it exists.
                m_caption, markup = await cls.process_marginals(m_caption, chat_id, bot, user, chat, settings=settings)
                if markup and not marginals_processed:
                    if album_markup:
                        album_markup.inline_keyboard.extend(markup.inline_keyboard)
                    else:
                        album_markup = markup
                    marginals_processed = True

            # Add Marker
            if m_caption:
                m_caption += cls.PIPELINE_MARKER

            # Build InputMedia
            if m_type == "photo":
                input_media_list.append(InputMediaPhoto(media=m_media, caption=m_caption, parse_mode="HTML"))
            elif m_type == "video":
                input_media_list.append(InputMediaVideo(media=m_media, caption=m_caption, parse_mode="HTML"))
            elif m_type == "audio":
                input_media_list.append(InputMediaAudio(media=m_media, caption=m_caption, parse_mode="HTML"))
            elif m_type == "document":
                input_media_list.append(InputMediaDocument(media=m_media, caption=m_caption, parse_mode="HTML"))
            # Voice/Animation not typically in mixed albums in same way, or handled as Audio/Video.
            # But InputMediaAnimation exists? No, mostly Photo/Video/Audio/Document.


        # 6. Watermark & Thumbnails
        cleanup_paths = []
        settings_for_wm = settings if settings else await db.get_settings(chat_id) or {}
        thumbnail_enabled = settings_for_wm.get('thumbnail_enabled', True)
        has_wm_content = any([settings_for_wm.get('logo_id'), settings_for_wm.get('footer_note'), settings_for_wm.get('custom_name')])

        global_logo_bytes = None
        logo_id = settings_for_wm.get('logo_id')
        if logo_id and use_watermark and settings_for_wm.get('watermark_enabled', True):
            try:
                logo_file_info = await bot.get_file(logo_id)
                import io
                logo_io = io.BytesIO()
                await bot.download_file(logo_file_info.file_path, logo_io)
                global_logo_bytes = logo_io.getvalue()
            except Exception as e:
                log.warning(f"Watermark Engine Album: Invalid logo_id '{logo_id}': {e}")

        for i, item in enumerate(input_media_list):
            m_type = getattr(item, 'type', None)
            m_type_str = m_type.value if hasattr(m_type, 'value') else str(m_type)
            if m_type_str in ('photo', 'document') and hasattr(item, 'media') and (isinstance(item.media, str) or type(item.media).__name__ == 'FSInputFile'):
                need_thumbnail = m_type_str == "document" and thumbnail_enabled
                need_watermark = use_watermark and (
                    (m_type_str == "photo" and has_wm_content) or
                    (m_type_str == "document" and has_wm_content)
                )

                if need_thumbnail or need_watermark:
                    item_file_name = media_list[i].get('file_name') if i < len(media_list) else None
                    out_path, thumb, final_tg_name = await cls.apply_watermark(
                        bot, item.media, chat_id, m_type_str, settings_for_wm,
                        cached_logo_bytes=global_logo_bytes,
                        original_file_name=item_file_name,
                        force_thumbnail=need_thumbnail,
                        skip_watermark=not need_watermark
                    )

                    if out_path:
                        from aiogram.types import FSInputFile
                        # In Pydantic v2, mutating fields directly may not update validation or may fail silently.
                        # Best practice is to replace the item entirely with a copy or dict dump.
                        update_dict = {"media": FSInputFile(out_path, filename=final_tg_name) if final_tg_name else FSInputFile(out_path)}
                        cleanup_paths.append(out_path)
                        if thumb and m_type_str == 'document':
                            update_dict["thumbnail"] = FSInputFile(thumb)
                            cleanup_paths.append(thumb)

                        input_media_list[i] = item.model_copy(update=update_dict)

        if not input_media_list:
            return


        # Retry send_media_group on timeout or Telegram rate-limit errors
        from aiogram.exceptions import TelegramRetryAfter, TelegramNetworkError
        import aiohttp

        _MAX_RETRIES = 3
        sent_messages = None
        try:
            for _attempt in range(_MAX_RETRIES):
                try:
                    sent_messages = await bot.send_media_group(chat_id, media=input_media_list)
                    break  # success
                except TelegramRetryAfter as e:
                    wait = e.retry_after + 1
                    log.warning(f"Album send rate-limited, retrying in {wait}s (attempt {_attempt+1}/{_MAX_RETRIES})")
                    await asyncio.sleep(wait)
                except (TelegramNetworkError, asyncio.TimeoutError, aiohttp.ClientError) as e:
                    if _attempt < _MAX_RETRIES - 1:
                        wait = 5 * (2 ** _attempt)  # 5s, 10s, 20s
                        log.warning(f"Album send network error, retrying in {wait}s (attempt {_attempt+1}/{_MAX_RETRIES}): {e}")
                        await asyncio.sleep(wait)
                    else:
                        log.error(f"Pipeline Album Send Error: {e}")
                        raise
                except Exception as e:
                    log.error(f"Pipeline Album Send Error: {e}")
                    raise

            # UX Fix: Send buttons as a separate message if they were generated
            if album_markup and sent_messages:
                last_msg_id = sent_messages[-1].message_id
                try:
                    await bot.send_message(
                        chat_id,
                        f"👇 <b>Actions</b>{cls.PIPELINE_MARKER}",
                        reply_to_message_id=last_msg_id,
                        reply_markup=album_markup,
                        parse_mode="HTML"
                    )
                except Exception as ex:
                    log.warning(f"Failed to send album buttons: {ex}")
        finally:
            import os
            if 'cleanup_paths' in locals():
                for p in cleanup_paths:
                    if isinstance(p, str) and os.path.exists(p):
                        try: os.remove(p)
                        except Exception: pass

        # Send caption overflow as a follow-up text message
        if _caption_overflow:
            try:
                await bot.send_message(
                    chat_id,
                    _caption_overflow,
                    parse_mode="HTML",
                    reply_markup=None,
                )
            except Exception as e:
                log.warning(f"Pipeline: failed to send caption overflow text: {e}")
