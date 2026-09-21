# =============================================================================
# Module: Watermark Engine
# Path: utilitybot/utils/watermark.py
# Description: Global utility for manipulating Images and PDFs. Overlays transparent
#              logos, applies custom URI hyperlinked footernotes, and extracts PDF
#              thumbnails dynamically utilizing Pillow and PyMuPDF (fitz).
# =============================================================================

import io
import os
import tempfile
import re
import html
import threading
import concurrent.futures
import collections
from PIL import Image, UnidentifiedImageError
from utilitybot.utils.logger import log

# ---------------------------------------------------------------------------
# Dedicated thread pool for CPU-bound watermark work.
# Caps concurrency so many simultaneous RSS feeds don't spin up dozens of
# threads and contend on the GIL / I-O bandwidth at the same time.
# ---------------------------------------------------------------------------
_WATERMARK_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="watermark"
)

# ---------------------------------------------------------------------------
# Module-level logo cache: logo_bytes → (resized_rgba_image, png_bytes)
# Keyed by (hash(logo_bytes), target_width) so the same logo at the same
# size is only decoded + resized once per process lifetime.
# ---------------------------------------------------------------------------
_logo_cache: collections.OrderedDict = collections.OrderedDict()
_logo_cache_lock = threading.Lock()
_MAX_CACHE_SIZE = 32

def _get_prepared_logo(logo_bytes: bytes, target_w: int, opacity: int) -> Image.Image:
    """Return a cached RGBA logo resized to target_w, with opacity applied."""
    key = (hash(logo_bytes), target_w, opacity)
    with _logo_cache_lock:
        if key in _logo_cache:
            _logo_cache.move_to_end(key)
            return _logo_cache[key].copy()  # copy so callers can modify freely

    logo_img = Image.open(io.BytesIO(logo_bytes)).convert("RGBA")
    orig_w, orig_h = logo_img.size
    if orig_w > target_w:
        new_h = int(orig_h * target_w / orig_w)
        logo_img = logo_img.resize((target_w, new_h), Image.Resampling.BILINEAR)

    if opacity < 100:
        alpha = logo_img.split()[3]
        alpha = alpha.point(lambda p: int(p * opacity / 100))
        logo_img.putalpha(alpha)

    with _logo_cache_lock:
        _logo_cache[key] = logo_img
        if len(_logo_cache) > _MAX_CACHE_SIZE:
            _logo_cache.popitem(last=False)
    return logo_img.copy()


def process_image_sync(image_path: str, logo_bytes: bytes, opacity: int = 100) -> str:
    """
    Synchronously overlays a transparent PNG logo onto an image.
    Centers the logo and scales it to max 30% of the image width.
    Preserves GIF/WebP/PNG format where possible; falls back to JPEG.
    """
    try:
        main_img = Image.open(image_path)
    except UnidentifiedImageError as e:
        log.warning(f"Pillow could not read image, returning original: {e}")
        return image_path

    if not logo_bytes:
        return image_path

    try:
        ext = os.path.splitext(image_path)[1].lower()
        is_animated = getattr(main_img, "is_animated", False)

        # Animated GIFs: paste logo onto every frame
        if is_animated and ext == ".gif":
            frames = []
            for frame_idx in range(main_img.n_frames):
                main_img.seek(frame_idx)
                frame = main_img.convert("RGBA")
                fw, fh = frame.size
                logo = _get_prepared_logo(logo_bytes, int(fw * 0.3), opacity)
                lw, lh = logo.size
                frame.paste(logo, ((fw - lw) // 2, (fh - lh) // 2), logo)
                frames.append(frame.convert("P", palette=Image.ADAPTIVE, dither=0))

            fd, output_path = tempfile.mkstemp(suffix=".gif")
            os.close(fd)
            frames[0].save(
                output_path, format="GIF", save_all=True,
                append_images=frames[1:], loop=0, optimize=True
            )
            return output_path

        # Static images
        main_rgba = main_img.convert("RGBA")
        mw, mh = main_rgba.size
        logo = _get_prepared_logo(logo_bytes, int(mw * 0.3), opacity)
        lw, lh = logo.size
        main_rgba.paste(logo, ((mw - lw) // 2, (mh - lh) // 2), logo)

        # Preserve PNG/WebP transparency; everything else → JPEG
        if ext in (".png", ".webp"):
            suffix = ext
            save_kwargs = {"format": ext.lstrip(".").upper()}
        else:
            suffix = ".jpg"
            save_kwargs = {"format": "JPEG", "quality": 85, "optimize": True}

        fd, output_path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        img_to_save = main_rgba if suffix != ".jpg" else main_rgba.convert("RGB")
        img_to_save.save(output_path, **save_kwargs)
        return output_path

    except Exception as e:
        log.warning(f"process_image_sync error, returning original: {e}")
        return image_path


def process_pdf_sync(pdf_path: str, logo_bytes: bytes, footer_note: str, custom_name: str, opacity: int = 100) -> tuple:
    """
    Applies a logo watermark to the centre of every PDF page,
    injects a dynamically coloured URI hyperlink footernote, and extracts a
    visual thumbnail from the first processed page.
    Returns: (processed_pdf_path, thumbnail_jpg_path)
    """
    import uuid
    fd, output_pdf_path = tempfile.mkstemp(suffix=f"_{uuid.uuid4().hex}.pdf")
    os.close(fd)

    # Pre-process logo once outside the page loop
    processed_logo_bytes = None
    logo_ratio = None
    if logo_bytes:
        try:
            img = Image.open(io.BytesIO(logo_bytes)).convert("RGBA")
            logo_ratio = img.height / img.width
            if opacity < 100:
                alpha = img.split()[3]
                alpha = alpha.point(lambda p: int(p * opacity / 100))
                img.putalpha(alpha)
            tmp_io = io.BytesIO()
            img.save(tmp_io, format="PNG")
            processed_logo_bytes = tmp_io.getvalue()
        except Exception as e:
            log.warning(f"Logo pre-processing failed: {e}")

    url_match = None
    if footer_note:
        match = re.search(r'https?://\S+', footer_note)
        if match:
            url_match = match.group(0)

    try:
        import fitz
        doc = fitz.open(pdf_path)
        if doc.needs_pass:
            log.warning(f"PDF is password protected, skipping watermark: {pdf_path}")
            return pdf_path, None

        for i in range(len(doc)):
            page = doc.load_page(i)
            rect = page.rect

            if processed_logo_bytes and logo_ratio is not None:
                try:
                    logo_width  = rect.width * 0.3
                    logo_height = logo_width * logo_ratio
                    x0 = (rect.width  - logo_width)  / 2
                    y0 = (rect.height - logo_height) / 2
                    logo_rect = fitz.Rect(x0, y0, x0 + logo_width, y0 + logo_height)
                    page.insert_image(logo_rect, stream=processed_logo_bytes, keep_proportion=True)
                except Exception as e:
                    log.warning(f"Failed to insert logo into PDF page {i}: {e}")

            if footer_note:
                # Calculate dynamic sizes based on page width
                # A standard A4 page is ~595pt wide, where an 11pt font is ~1.8% of the width.
                dynamic_fontsize = max(6, int(rect.width * 0.018))
                
                # Dynamic margins (5% of width on each side)
                margin_x = rect.width * 0.05
                # Give the box enough height to hold the text (approx 2.5x font size)
                box_height = dynamic_fontsize * 2.5
                # Bottom margin (1.5% of height, minimum 5pt)
                margin_bottom = max(5, rect.height * 0.015)
                
                text_rect = fitz.Rect(
                    margin_x, 
                    rect.height - box_height - margin_bottom, 
                    rect.width - margin_x, 
                    rect.height - margin_bottom
                )

                if url_match:
                    safe_note = html.escape(footer_note)
                    safe_url  = html.escape(url_match)
                    # Underline + blue colour for the URL portion
                    linked_span = f"<span style='color:#1a73e8;text-decoration:underline'>{safe_url}</span>"
                    html_text = safe_note.replace(safe_url, linked_span)
                    page.insert_htmlbox(
                        text_rect, html_text,
                        css=f"* {{ text-align: center; font-size: {dynamic_fontsize}pt; font-family: sans-serif; }}"
                    )
                    page.insert_link({"kind": fitz.LINK_URI, "from": text_rect, "uri": url_match})
                else:
                    page.insert_textbox(text_rect, footer_note, fontsize=dynamic_fontsize, align=fitz.TEXT_ALIGN_CENTER)

        # Thumbnail: render first page for thumbnail
        thumb_path = None
        if len(doc) > 0:
            page = doc.load_page(0)
            
            # Telegram expects max 320x320 for thumbnails. Optimize rendering 
            # by asking PyMuPDF to scale it down *during* the C-level render,
            # saving significant RAM and CPU compared to generating a full 1.0x image.
            rect = page.rect
            zoom = 320 / max(rect.width, rect.height)
            if zoom > 1.0: zoom = 1.0  # don't upscale
            matrix = fitz.Matrix(zoom, zoom)
            
            pix = page.get_pixmap(matrix=matrix)
            fd_img, thumb_path = tempfile.mkstemp(suffix=f"_{uuid.uuid4().hex}.jpg")
            os.close(fd_img)
            pix.save(thumb_path)

            # Strict Telegram limits: 320x320, max 200KB.
            # Pillow is just a safety pass to ensure compression is tight enough for API bounds.
            try:
                with Image.open(thumb_path) as thumb_img:
                    thumb_img.thumbnail((320, 320), Image.Resampling.BILINEAR)
                    thumb_img.save(thumb_path, "JPEG", quality=80, optimize=True)
            except Exception as resize_e:
                log.warning(f"Failed to resize PDF thumbnail: {resize_e}")

        doc.save(output_pdf_path, garbage=4, deflate=True)  # compact output
        doc.close()

    except Exception as e:
        log.error(f"PyMuPDF error: {e}")
        import shutil
        shutil.copy(pdf_path, output_pdf_path)
        thumb_path = None

    return output_pdf_path, thumb_path