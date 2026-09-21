# =============================================================================
# Module: Formatter
# Path: utilitybot/utils/formatter.py
# Description: Advanced Formatting Module – Enhanced with Full Markdown Support
#              Handles: In-App Markdown, Syntax Highlighting, Quotes, Collapsible
#              Quotes.
# =============================================================================


import random
import re
import html
from typing import Any, Dict, List, Optional, Tuple
from aiogram.types import Chat, InlineKeyboardButton, InlineKeyboardMarkup, User, MessageEntity
from . import settings_cache


def _make_button(text: str, style: Optional[str] = None, **kwargs) -> InlineKeyboardButton:
    """
    Factory for InlineKeyboardButton that correctly injects the ``style`` field
    introduced in Bot API 9.4.

    Aiogram's Pydantic model does not yet declare ``style`` as a typed field, so
    passing it as a normal kwarg raises a validation error.  Instead we build the
    button normally and then write the value into ``model_extra`` – the dict that
    aiogram serialises verbatim into the outgoing JSON payload – so Telegram
    receives it without aiogram complaining.

    Args:
        text:   Button label.
        style:  One of ``"danger"`` (red), ``"success"`` (green),
                ``"primary"`` (blue), or ``None`` (app default).
        **kwargs: Any other InlineKeyboardButton fields (``url``,
                  ``callback_data``, etc.).
    """
    btn = InlineKeyboardButton(text=text, **kwargs)
    if style in BUTTON_STYLES:
        # model_extra holds arbitrary extra fields that Pydantic passes through
        # to the serialised output unchanged, which is exactly what we need for
        # fields not yet typed in the installed aiogram version.
        btn.model_extra["style"] = style  # type: ignore[index]
    return btn

class FormattedResult:
    def __init__(self, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None, api_flags: Optional[Dict[str, Any]] = None):
        self.text = text
        self.reply_markup = reply_markup
        self.api_flags = api_flags or {}

def chunk_html(html_text: str, max_len: int = 4096) -> list[str]:
    """
    Safely chunks HTML text into segments up to max_len.
    Avoids splitting inside HTML tags, and ensures open tags are closed
    at the end of a chunk and reopened at the start of the next chunk.
    """
    if len(html_text) <= max_len:
        return [html_text]

    chunks = []
    current_chunk = ""
    open_tags = []
    tag_pattern = re.compile(r'(</?[a-zA-Z0-9]+[^>]*>)')
    parts = tag_pattern.split(html_text)
    
    def _get_closing_tag(open_tag: str) -> str:
        tag_name = open_tag[1:].split()[0].replace('>', '')
        return f"</{tag_name}>"

    def _close_all_open_tags(chunk: str, tags: list[str]) -> str:
        for t in reversed(tags):
            chunk += _get_closing_tag(t)
        return chunk
        
    for part in parts:
        if not part: continue
        is_tag = part.startswith('<') and part.endswith('>')
        if is_tag:
            if part.startswith('</'):
                # Closing tag — pop the matching opener before budget calc
                tag_name = part[2:-1].split()[0]
                for i in range(len(open_tags)-1, -1, -1):
                    if open_tags[i][1:].startswith(tag_name):
                        open_tags.pop(i)
                        break
            elif not part.endswith('/>'):
                # Opening tag — register it NOW so the closing budget below
                # accounts for this tag in the current iteration, not the next.
                open_tags.append(part)

        closing_length = sum(len(_get_closing_tag(t)) for t in open_tags)
        open_str = "".join([t for t in open_tags if not t.startswith('</')])

        if len(current_chunk) + len(part) + closing_length > max_len and current_chunk and current_chunk != open_str:
            if is_tag or len(open_str) + len(part) + closing_length <= max_len:
                current_chunk = _close_all_open_tags(current_chunk, open_tags)
                chunks.append(current_chunk.strip())
                current_chunk = open_str + part
            else:
                words = [w for w in re.split(r'(\s+)', part) if w]
                for i, word in enumerate(words):
                    added_len = len(word)
                    if len(current_chunk) + added_len + closing_length > max_len and current_chunk and current_chunk != open_str:
                        current_chunk = _close_all_open_tags(current_chunk, open_tags)
                        chunks.append(current_chunk.strip())
                        current_chunk = open_str + word
                    else:
                        current_chunk += word
        else:
            current_chunk += part

    open_str = "".join([t for t in open_tags if not t.startswith('</')])
    if current_chunk and current_chunk != open_str:
        current_chunk = _close_all_open_tags(current_chunk, open_tags)
        chunks.append(current_chunk.strip())
    return chunks


# ===================================================
# MAIN FORMATTER
# ===================================================

async def format_message(
    text: str,
    user: Optional[User],
    chat: Optional[Chat],
    settings: Optional[Dict[str, Any]] = None,
    bot: Any = None
) -> FormattedResult:
    
    settings = settings or {}
    
    # 1. Random Content (%%% blocks)
    text = _handle_random_content(text)
    
    # 2. Fillings {first}, {id}, etc.
    text = await _handle_fillings(text, user, chat, settings, bot)
    
    # 3. Extract flags
    text, api_flags = _extract_flags(text)
    
    # 4. Extract buttons
    text, markup = _extract_buttons(text)
    
    # 5. Handle Quote + Collapsible Quote + Syntax Blocks
    text, entities = _parse_all_markdown(text)
    
    api_flags["entities"] = entities
    
    return FormattedResult(text, markup, api_flags)


# ===================================================
# UNPARSE – REBUILD HTML FROM ENTITIES
# ===================================================

def unparse(text: str, entities: List[MessageEntity] = None) -> str:
    """Convert text with entities back to HTML format"""
    if not entities or not text:
        return html.escape(text or "")
    
    text_utf16 = text.encode('utf-16-le', 'surrogatepass')
    insertions = {}
    
    for entity in entities:
        start = entity.offset * 2
        end = (entity.offset + entity.length) * 2
        
        prefix, suffix = "", ""
        
        if entity.type == 'bold': 
            prefix, suffix = "<b>", "</b>"
        elif entity.type == 'italic': 
            prefix, suffix = "<i>", "</i>"
        elif entity.type == 'underline': 
            prefix, suffix = "<u>", "</u>"
        elif entity.type == 'strikethrough': 
            prefix, suffix = "<s>", "</s>"
        elif entity.type == 'spoiler': 
            prefix, suffix = "<tg-spoiler>", "</tg-spoiler>"
        elif entity.type == 'code': 
            prefix, suffix = "<code>", "</code>"
        elif entity.type == 'pre':
            lang = getattr(entity, 'language', '')
            prefix = f'<pre><code class="language-{lang}">' if lang else '<pre>'
            suffix = "</code></pre>" if lang else "</pre>"
        elif entity.type == 'blockquote':
            prefix, suffix = "<blockquote>", "</blockquote>"
        elif entity.type == 'expandable_blockquote':
            prefix, suffix = "<blockquote expandable>", "</blockquote>"
        elif entity.type == 'text_link':
            prefix, suffix = f'<a href="{entity.url}">', "</a>"
        elif entity.type == 'text_mention':
            prefix, suffix = f'<a href="tg://user?id={entity.user.id}">', "</a>"
        
        if prefix:
            insertions.setdefault(start, []).append(prefix)
            insertions.setdefault(end, []).insert(0, suffix)
    
    # Identify all split points (start, end of entities, plus 0 and total length)
    split_points = sorted(set(insertions.keys()) | {0, len(text_utf16)})
    
    output = []
    
    for i in range(len(split_points) - 1):
        current = split_points[i]
        next_point = split_points[i+1]

        # 1. Append tags at current position
        if current in insertions:
            output.append("".join(insertions[current]))

        # 2. Get the text segment between points
        segment_bytes = text_utf16[current:next_point]
        segment_str = segment_bytes.decode('utf-16-le', errors='surrogatepass')

        # 3. Escape and append
        output.append(html.escape(segment_str))

    # Append tags at the very end if any
    last_point = split_points[-1]
    if last_point in insertions:
        output.append("".join(insertions[last_point]))

    return "".join(output)


# ===================================================
# PRIVATE HELPERS
# ===================================================

def _handle_random_content(text: str) -> str:
    """Handle %%% random content blocks"""
    if "%%%" not in text:
        return text
    parts = [p.strip() for p in text.split("%%%") if p.strip()]
    return random.choice(parts) if parts else text


# ===================================================
# FILLINGS {first} {id} etc.
# ===================================================


_FILLINGS_PATTERN = re.compile(r"\{first\}|\{last\}|\{fullname\}|\{username\}|\{mention\}|\{id\}|\{chatname\}|\{name\}|\{count\}|\{date\}|\{pinned\}|\{rules\}|\{channels\}")

async def _handle_fillings(text: str, user, chat, settings, bot):
    """Handle all variable replacements"""
    # Fast-fail: skip processing if no format brackets exist
    if "{" not in text or not _FILLINGS_PATTERN.search(text):
        return text

    from datetime import datetime
    
    user_id = user.id if user else 0
    chat_id = chat.id if chat else 0
    
    # Evaluate replacements lazily to avoid CPU overhead on unused variables
    def _get_first(): return html.escape(getattr(user, "first_name", "") or "")
    def _get_last(): return html.escape(getattr(user, "last_name", "") or "")
    def _get_fullname(): return html.escape(getattr(user, "full_name", "") or "")
    def _get_username(): return f"@{html.escape(user.username)}" if user and user.username else "N/A"
    
    def _get_chatname(): return html.escape(getattr(chat, "title", "") or "")
    
    def _get_mention(): return f'<a href="tg://user?id={user_id}">{_get_first()}</a>' if user else _get_chatname()
    def _get_name(): return _get_fullname() if user else _get_chatname()
    def _get_id(): return str(user_id if user else chat_id)
    
    async def _get_count():
        if bot and chat:
            try:
                _cached_mc = settings_cache.get_member_count(chat_id)
                if _cached_mc is not None:
                    return str(_cached_mc)
                _raw = await bot.get_chat_member_count(chat_id)
                settings_cache.set_member_count(chat_id, _raw)
                return str(_raw)
            except Exception:
                pass
        return "N/A"

    async def _get_pinned():
        if bot and chat:
            try:
                full = settings_cache.get_chat(chat.id)
                if full is None:
                    full = await bot.get_chat(chat.id)
                    settings_cache.set_chat(chat.id, full)
                if full.pinned_message:
                    if chat.username:
                        return f"https://t.me/{chat.username}/{full.pinned_message.message_id}"
                    else:
                        cid = str(chat.id).replace("-100", "")
                        return f"https://t.me/c/{cid}/{full.pinned_message.message_id}"
            except Exception:
                pass
        return ""
    
    def _get_date(): return datetime.now().strftime("%Y-%m-%d %H:%M")
    
    def _get_rules(): return settings.get("rules_url", "")
    def _get_channels():
        chans = settings.get("channels", [])
        return html.escape(", ".join(map(str, chans))) if isinstance(chans, list) else ""

    # Since 'count' and 'pinned' require async operations, we pre-evaluate them if present in text
    count_val = await _get_count() if "{count}" in text else "N/A"
    pinned_val = await _get_pinned() if "{pinned}" in text else ""

    replacements = {
        "{first}": lambda: _get_first(),
        "{last}": lambda: _get_last(),
        "{fullname}": lambda: _get_fullname(),
        "{username}": lambda: _get_username(),
        "{mention}": lambda: _get_mention(),
        "{id}": lambda: _get_id(),
        "{chatname}": lambda: _get_chatname(),
        "{name}": lambda: _get_name(),
        "{count}": lambda: count_val,
        "{date}": lambda: _get_date(),
        "{pinned}": lambda: pinned_val,
        "{rules}": lambda: _get_rules(),
        "{channels}": lambda: _get_channels()
    }
    
    # Evaluate lambdas only for matched keys and cache them
    evaluated = {}
    def _replacer(m):
        key = m.group(0)
        if key not in evaluated:
            evaluated[key] = replacements[key]()
        return evaluated[key]

    return _FILLINGS_PATTERN.sub(_replacer, text)


# ===================================================
# FLAGS {preview} {pin} etc.
# ===================================================

_API_FLAGS_PATTERN = re.compile(r'\{(preview(:top|:bottom)?|nonotif|protect|mediaspoiler|pin|nomarginals|noheader|nofooter|nogap)\}')

def _extract_flags(text: str):
    """Extract special flags from text"""
    # Fast-fail: skip processing if no format brackets exist
    if "{" not in text or not _API_FLAGS_PATTERN.search(text):
        return text, {}

    flags = {}
    lines = text.split("\n")
    out = []
    
    flag_map = {
        "{preview}": ("disable_web_page_preview", False),
        "{nonotif}": ("disable_notification", True),
        "{protect}": ("protect_content", True),
        "{mediaspoiler}": ("has_spoiler", True),
        "{pin}": ("pin_message", True),
        "{nomarginals}": ("disable_marginals", True),
        "{noheader}": ("disable_header", True),
        "{nofooter}": ("disable_footer", True),
        "{nogap}": ("trim_gap", True),
    }
    
    for line in lines:
        s = line.strip()
        
        if s in flag_map:
            key, val = flag_map[s]
            flags[key] = val
            continue
        
        if s == "{preview:top}":
            flags["disable_web_page_preview"] = False
            flags["link_preview_options"] = {"is_above_text": True}
            continue
        
        out.append(line)
    
    return "\n".join(out), flags


# ===================================================
# BUTTONS - FIXED VERSION
# ===================================================

# Valid button styles per Telegram Bot API spec
BUTTON_STYLES = {"danger", "success", "primary"}


def _parse_button_token(raw: str):
    """
    Parse the full token captured inside ``(buttonurl…://…)`` and return the
    button's URL, style, and row-placement flag.

    New syntax (Bot API 9.4):
        buttonurl://example.com              → no style
        buttonurl#danger://example.com       → red
        buttonurl#success://example.com      → green
        buttonurl#primary://example.com      → blue
        buttonurl#primary://example.com:same → blue, same row

    The style is encoded in the *scheme fragment* (``#style``) so it is
    completely separated from the URL and the only permitted trailing modifier
    is ``:same`` for row placement.

    Args:
        raw:  Everything captured after the opening ``(`` up to the closing
              ``)``, e.g. ``"buttonurl#danger://example.com:same"``.

    Returns:
        tuple: (url, style, same_row)
            url      (str)      – the bare destination URL or ``#note-name``
            style    (str|None) – one of ``"danger"``, ``"success"``,
                                  ``"primary"``, or ``None``
            same_row (bool)     – True when ``:same`` was present
    """
    # --- 1. Peel the optional :same suffix first --------------------------------
    same_row = False
    if raw.endswith(":same"):
        same_row = True
        raw = raw[:-5]   # strip ":same"

    # --- 2. Split scheme from URL -----------------------------------------------
    # raw is now one of:
    #   "buttonurl://example.com"
    #   "buttonurl#danger://example.com"
    scheme_sep = raw.find("://")
    if scheme_sep == -1:
        # Malformed – return as-is with no style
        return raw, None, same_row

    scheme = raw[:scheme_sep]          # e.g. "buttonurl" or "buttonurl#danger"
    url    = raw[scheme_sep + 3:]      # everything after "://"

    # --- 3. Extract optional style from scheme fragment ------------------------
    style = None
    if "#" in scheme:
        fragment = scheme.split("#", 1)[1].lower()
        if fragment in BUTTON_STYLES:
            style = fragment

    return url, style, same_row


_BTN_RE = re.compile(r'\[([^\]]+)\]\((buttonurl(?:#[^:)]+)?://[^)]+)\)')

def _extract_buttons(text: str):
    """
    Extract inline button markup from text.

    Syntax
    ------
    No style (app default)::

        [Visit](buttonurl://example.com)

    Coloured buttons (Bot API 9.4 ``style`` field)::

        [Delete](buttonurl#danger://example.com)       ← red
        [Confirm](buttonurl#success://example.com)     ← green
        [Learn](buttonurl#primary://example.com)       ← blue

    Row placement – append ``:same`` to put the button beside the previous one::

        [A](buttonurl://one.com)  [B](buttonurl#primary://two.com:same)

    Note buttons (callback)::

        [My Note](buttonurl://#notename)
        [My Note](buttonurl#danger://#notename)
    """
    # Fast-fail: skip processing if no brackets or buttonurl exist
    if "[" not in text or "buttonurl" not in text or not _BTN_RE.search(text):
        return text, None

    lines = text.split("\n")
    cleaned = []
    rows = []
    current_row = []

    # Match any buttonurl variant: buttonurl://  OR  buttonurl#<style>://
    for line in lines:
        matches = list(_BTN_RE.finditer(line))
        if not matches:
            # No buttons on this line – flush pending row
            if current_row:
                rows.append(current_row)
                current_row = []
            cleaned.append(line)
            continue

        # Keep any plain text that appears before the first button
        line_text = line[:matches[0].start()].strip()
        if line_text:
            cleaned.append(line_text)

        for m in matches:
            label     = m.group(1)
            raw_token = m.group(2)   # e.g. "buttonurl#danger://example.com:same"

            url, style, same_row = _parse_button_token(raw_token)

            # Build the InlineKeyboardButton.
            # Style is injected via _make_button which uses model_extra so that
            # the Bot API 9.4 ``style`` field is serialised correctly without
            # aiogram's Pydantic model rejecting an unknown kwarg.
            if url.startswith("#"):
                # Note-reference button: buttonurl://#notename or buttonurl#style://#notename
                note = url[1:][:40]
                btn = _make_button(
                    text=label,
                    style=style,
                    callback_data=f"get_note:{note}",
                )
            else:
                btn = _make_button(
                    text=label,
                    style=style,
                    url=url,
                )

            # Determine row placement
            if same_row and current_row:
                current_row.append(btn)
            else:
                if current_row:
                    rows.append(current_row)
                current_row = [btn]

        # Keep any plain text that appears after the last button on this line
        remaining_text = line[matches[-1].end():].strip()
        if remaining_text:
            cleaned.append(remaining_text)

    # Flush the final row
    if current_row:
        rows.append(current_row)

    markup = InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
    return "\n".join(cleaned), markup


# ===================================================
# UNIFIED MARKDOWN PARSER
# ===================================================

# ⚡ Bolt Optimization: Fast-fail for block markdown (including blockquotes) and inline formats
_ALL_MARKDOWN_FAST_FAIL_PATTERN = re.compile(r'[*_~|`\[>]')

def _parse_all_markdown(text: str) -> Tuple[str, List[MessageEntity]]:
    """
    Master parser that handles all markdown formats:
    - Code blocks with syntax highlighting (```language)
    - Blockquotes (> lines)
    - Collapsible quotes (>! lines or auto for long quotes)
    - Inline formatting (bold, italic, underline, etc.)
    - Links
    """
    
    entities: List[MessageEntity] = []
    plain_text = ""
    current_offset = 0
    
    # ⚡ Bolt Optimization: Fast-fail if no markdown characters exist (including blockquotes)
    if not _ALL_MARKDOWN_FAST_FAIL_PATTERN.search(text):
        return text, entities

    def u16_len(s: str) -> int:
        """Calculate UTF-16 length for Telegram entities"""
        return len(s.encode("utf-16-le", "surrogatepass")) // 2
    
    # Split into lines for processing
    lines = text.split("\n")
    i = 0
    
    while i < len(lines):
        line = lines[i]
        
        # 1. CODE BLOCKS (```language)
        if line.strip().startswith("```"):
            code_lines = []
            lang = line.strip()[3:].strip()
            i += 1
            
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            
            code_content = "\n".join(code_lines)
            
            # Create pre entity
            entities.append(MessageEntity(
                type="pre",
                offset=current_offset,
                length=u16_len(code_content),
                language=lang if lang else None
            ))
            
            plain_text += code_content
            current_offset += u16_len(code_content)
            
            # Add newline after code block if not at end
            if i < len(lines) - 1:
                plain_text += "\n"
                current_offset += 1
            
            i += 1
            continue
        
        # 2. COLLAPSIBLE QUOTES (>!)
        if line.startswith(">!"):
            quote_lines = []
            while i < len(lines) and lines[i].startswith(">!"):
                quote_lines.append(lines[i][2:].lstrip())
                i += 1
            
            quote_text = "\n".join(quote_lines)
            
            entities.append(MessageEntity(
                type="expandable_blockquote",
                offset=current_offset,
                length=u16_len(quote_text)
            ))
            
            plain_text += quote_text
            current_offset += u16_len(quote_text)
            
            if i < len(lines):
                plain_text += "\n"
                current_offset += 1
            continue
        
        # 3. REGULAR QUOTES (>)
        if line.startswith(">"):
            quote_lines = []
            while i < len(lines) and lines[i].startswith(">") and not lines[i].startswith(">!"):
                quote_lines.append(lines[i][1:].lstrip())
                i += 1
            
            quote_text = "\n".join(quote_lines)
            quote_len = u16_len(quote_text)
            
            # Auto-expand if quote is very long (>400 chars)
            quote_type = "expandable_blockquote" if quote_len > 400 else "blockquote"
            
            entities.append(MessageEntity(
                type=quote_type,
                offset=current_offset,
                length=quote_len
            ))
            
            plain_text += quote_text
            current_offset += quote_len
            
            if i < len(lines):
                plain_text += "\n"
                current_offset += 1
            continue
        
        # 4. REGULAR LINE - Process inline markdown
        processed_line, line_entities = _parse_inline_markdown(line, current_offset)
        entities.extend(line_entities)
        
        plain_text += processed_line
        current_offset += u16_len(processed_line)
        
        # Add newline if not last line
        if i < len(lines) - 1:
            plain_text += "\n"
            current_offset += 1
        
        i += 1
    
    return plain_text, entities


def normalize_url(url: str) -> str:
    """Normalize URL to include protocol if missing"""
    url = url.strip()
    if not url.startswith(('http://', 'https://', 'tg://', 'ton://')):
        # Add https:// if no protocol
        return f'https://{url}'
    return url

# ⚡ Bolt Optimization: Pre-compiled inline markdown patterns at module level
# This avoids compiling regexes on every message format invocation
_INLINE_FAST_FAIL_PATTERN = re.compile(r'[*_~|`\[]')

_INLINE_PATTERNS = [
    # Inline code (highest priority) - using backticks
    (re.compile(r'`([^`]+)`'), 'code', None),
    # Links - works with both [text](url) and Telegram's [text](URL) format
    # Matches any URL format except buttonurl://
    (re.compile(r'\[([^\]]+)\]\((?!buttonurl://)([^)]+)\)'), 'text_link', lambda m: {'url': normalize_url(m.group(2))}),
    # Spoilers
    (re.compile(r'\|\|(.+?)\|\|'), 'spoiler', None),
    # Underline (double underscore - check first)
    (re.compile(r'__(.+?)__'), 'underline', None),
    # Italic (single underscore)
    (re.compile(r'_(.+?)_'), 'italic', None),
    # Bold (single asterisk)
    (re.compile(r'\*(.+?)\*'), 'bold', None),
    # Strikethrough (single tilde)
    (re.compile(r'~(.+?)~'), 'strikethrough', None),
]

def _parse_inline_markdown(text: str, base_offset: int) -> Tuple[str, List[MessageEntity]]:
    """
    Parse inline markdown formats:
    *bold*, _italic_, __underline__, ~strikethrough~, ||spoiler||, `code`, [link](url)
    Supports both syntax markdown [text](url) and Telegram in-app markdown [text](URL)
    """
    
    entities: List[MessageEntity] = []
    plain = text
    
    # ⚡ Bolt Optimization: Fast-fail if no markdown characters exist
    if not _INLINE_FAST_FAIL_PATTERN.search(plain):
        return plain, entities

    def u16_len(s: str) -> int:
        return len(s.encode("utf-16-le", "surrogatepass")) // 2
    
    # Process in specific order to avoid conflicts
    for pattern, entity_type, extra_func in _INLINE_PATTERNS:
        offset_adjustment = 0
        
        while True:
            match = pattern.search(plain)
            if not match:
                break
            
            # Get the content (group 1 for most, special handling for links)
            if entity_type == 'text_link':
                content = match.group(1)
            else:
                content = match.group(1)
            
            # Calculate position
            match_start = match.start()
            offset = base_offset + u16_len(plain[:match_start])
            length = u16_len(content)
            
            # Create entity
            entity_kwargs = {'type': entity_type, 'offset': offset, 'length': length}
            if extra_func:
                entity_kwargs.update(extra_func(match))
            
            entities.append(MessageEntity(**entity_kwargs))
            
            # Replace in plain text
            plain = plain[:match.start()] + content + plain[match.end():]
    
    return plain, entities


# ===================================================
# LEGACY MARKDOWN → HTML (for backwards compatibility)
# ===================================================

def _markdown_to_html(text: str) -> str:
    """
    Simplified markdown → Telegram HTML conversion
    (Kept for backwards compatibility)
    """
    
    # 1) Code Blocks
    def repl_pre(m):
        lang = m.group(1)
        body = html.escape(m.group(2))
        if lang:
            return f'<pre><code class="language-{lang}">{body}</code></pre>'
        return f"<pre>{body}</pre>"
    
    text = re.sub(r"```(\w*)\n(.*?)\n```", repl_pre, text, flags=re.DOTALL)
    
    # 2) Inline Code
    text = re.sub(r"`([^`]+)`", lambda m: f"<code>{html.escape(m.group(1))}</code>", text)
    
    # 3) Links
    text = re.sub(r'\[([^\]]+)\]\((?!buttonurl://)([^)]+)\)', r'<a href="\2">\1</a>', text)
    
    # 4) Spoilers
    text = re.sub(r"\|\|(.*?)\|\|", r"<tg-spoiler>\1</tg-spoiler>", text)
    
    # 5) Underline first (greedy __)
    text = re.sub(r"__([^_]+)__", r"<u>\1</u>", text)
    
    # 6) Italic _
    text = re.sub(r"_(.+?)_", r"<i>\1</i>", text)
    
    # 7) Bold *
    text = re.sub(r"\*(.+?)\*", r"<b>\1</b>", text)
    
    # 8) Strikethrough ~
    text = re.sub(r"~(.+?)~", r"<s>\1</s>", text)
    
    return text


# ── Caption splitting ─────────────────────────────────────────────────────────
# Sentence-boundary characters tried in priority order when splitting captions.
# Includes Devanagari danda (।), Arabic full stop (۔), CJK ideographic period (。).
_SPLIT_BOUNDARIES = [
    "\n\n", "\n", "।", "۔", "。",
    ". ", "! ", "? ", "; ", ", ", " ", "\u200b",
]


def split_caption(text: str, limit: int) -> int:
    """
    Return the character index at which *text* should be split so the
    first portion is at most *limit* characters.

    Tries sentence/word boundaries in priority order so the split never
    occurs mid-word or mid-sentence. Handles Unicode text (Hindi Devanagari,
    Arabic, CJK) by checking language-specific punctuation before falling
    back to whitespace. Never cuts inside an HTML tag.

    Returns ``limit`` as a hard fallback when no boundary is found.
    """
    if len(text) <= limit:
        return len(text)

    # Only scan the 200 chars immediately before the limit — enough to find
    # any reasonable boundary without walking the entire string.
    window_start = max(0, limit - 200)
    window = text[window_start:limit]

    for boundary in _SPLIT_BOUNDARIES:
        pos = window.rfind(boundary)
        if pos == -1:
            continue
        abs_pos = window_start + pos + len(boundary)
        # Skip if this position falls inside an unclosed HTML tag.
        tag_open  = text.rfind("<", 0, abs_pos)
        tag_close = text.rfind(">", 0, abs_pos)
        if tag_open > tag_close:
            continue
        return abs_pos

    return limit  # hard fallback — no boundary found
