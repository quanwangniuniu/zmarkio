import logging
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from PIL import Image, ImageDraw, ImageFont

from comments.attachment_previews import get_comment_attachment_file_category
from comments.models import CommentAttachment


logger = logging.getLogger(__name__)


class PreviewGenerationError(Exception):
    """Raised for expected preview generation failures that should be persisted."""


OFFICE_PREVIEW_CATEGORIES = {"document", "presentation", "spreadsheet"}
TEXT_PREVIEW_EXTENSIONS = {"csv", "md", "txt"}
DEFAULT_MAX_PAGES = 20
DEFAULT_MAX_INPUT_BYTES = 50 * 1024 * 1024
DEFAULT_LIBREOFFICE_TIMEOUT_SECONDS = 60
DEFAULT_LIBREOFFICE_MAX_ATTEMPTS = 2
DEFAULT_LIBREOFFICE_RETRY_DELAY_SECONDS = 1
DEFAULT_PDF_RENDER_TIMEOUT_SECONDS = 60
DEFAULT_PDF_RENDER_DPI = 144
DEFAULT_IMAGE_FORMAT = "webp"
DEFAULT_TEXT_RENDER_WIDTH = 1190
DEFAULT_TEXT_RENDER_HEIGHT = 1684
DEFAULT_TEXT_RENDER_MARGIN = 96
DEFAULT_TEXT_RENDER_FONT_SIZE = 24
DEFAULT_TEXT_RENDER_TITLE_FONT_SIZE = 32
DEFAULT_TEXT_RENDER_LINE_SPACING = 8


def generate_comment_attachment_preview(attachment: CommentAttachment) -> CommentAttachment:
    """
    Generate or refresh preview metadata for one attachment.

    The stored preview pages are the API contract for the frontend overlay, so
    failures are persisted on the attachment instead of bubbling to callers.
    """

    category = get_comment_attachment_file_category(attachment)

    if category in {"video", "zip"}:
        # ZIP and video attachments are intentionally downloadable-only, not preview-rendered.
        return _mark_preview(
            attachment=attachment,
            status=CommentAttachment.PREVIEW_UNSUPPORTED,
            preview_pages=[],
            error="",
        )

    if not attachment.file:
        return _mark_preview(
            attachment=attachment,
            status=CommentAttachment.PREVIEW_FAILED,
            preview_pages=[],
            error="Attachment file is missing.",
        )

    max_input_bytes = _get_setting("COMMENT_PREVIEW_MAX_INPUT_BYTES", DEFAULT_MAX_INPUT_BYTES)
    if attachment.size > max_input_bytes:
        # The input limit prevents expensive conversions from tying up workers.
        return _mark_preview(
            attachment=attachment,
            status=CommentAttachment.PREVIEW_FAILED,
            preview_pages=[],
            error="Attachment is too large to generate a preview.",
        )

    _mark_preview(
        attachment=attachment,
        status=CommentAttachment.PREVIEW_PROCESSING,
        preview_pages=[],
        error="",
    )

    try:
        if category == "image":
            preview_pages = [_get_image_preview_page(attachment)]
        else:
            preview_pages = _generate_document_preview_pages(
                attachment=attachment,
                category=category,
            )
    except Exception as exc:
        return _mark_preview(
            attachment=attachment,
            status=CommentAttachment.PREVIEW_FAILED,
            preview_pages=[],
            error=_format_preview_error(exc),
        )

    if not preview_pages:
        return _mark_preview(
            attachment=attachment,
            status=CommentAttachment.PREVIEW_FAILED,
            preview_pages=[],
            error="Preview generation produced no pages.",
        )

    return _mark_preview(
        attachment=attachment,
        status=CommentAttachment.PREVIEW_READY,
        preview_pages=preview_pages,
        error="",
    )


def _generate_document_preview_pages(*, attachment, category):
    # Every non-image preview becomes page images first. PDFs render directly,
    # Office files convert to PDF, and text-like files draw lightweight pages.
    with tempfile.TemporaryDirectory(prefix=f"comment-preview-{attachment.id}-") as tmpdir:
        tmp_path = Path(tmpdir)
        input_path = _copy_attachment_to_temp(attachment=attachment, tmp_path=tmp_path)

        if _is_text_like_attachment(attachment):
            return _render_text_attachment_to_preview_pages(
                input_path=input_path,
                attachment=attachment,
                tmp_path=tmp_path,
            )
        elif category in OFFICE_PREVIEW_CATEGORIES:
            pdf_path = _convert_office_file_to_pdf(input_path=input_path, tmp_path=tmp_path)
        elif category == "pdf":
            pdf_path = input_path
        else:
            raise PreviewGenerationError("Preview generation is not supported for this file type.")

        return _render_pdf_to_preview_pages(
            pdf_path=pdf_path,
            attachment=attachment,
            tmp_path=tmp_path,
        )


def _copy_attachment_to_temp(*, attachment, tmp_path):
    suffix = Path(attachment.filename).suffix
    input_path = tmp_path / f"input{suffix}"
    with attachment.file.open("rb") as source, open(input_path, "wb") as target:
        shutil.copyfileobj(source, target)
    return input_path


def _convert_office_file_to_pdf(*, input_path, tmp_path):
    executable = _find_executable(["libreoffice", "soffice"])
    if not executable:
        raise PreviewGenerationError("LibreOffice is not installed.")

    output_dir = tmp_path / "office-pdf"
    output_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = tmp_path / "libreoffice-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    timeout = _get_setting(
        "COMMENT_PREVIEW_LIBREOFFICE_TIMEOUT_SECONDS",
        DEFAULT_LIBREOFFICE_TIMEOUT_SECONDS,
    )
    max_attempts = max(
        1,
        _get_setting(
            "COMMENT_PREVIEW_LIBREOFFICE_MAX_ATTEMPTS",
            DEFAULT_LIBREOFFICE_MAX_ATTEMPTS,
        ),
    )
    retry_delay = max(
        0,
        _get_setting(
            "COMMENT_PREVIEW_LIBREOFFICE_RETRY_DELAY_SECONDS",
            DEFAULT_LIBREOFFICE_RETRY_DELAY_SECONDS,
        ),
    )
    last_error = ""

    for attempt in range(1, max_attempts + 1):
        _clear_generated_pdfs(output_dir)
        try:
            # Use an isolated user profile so concurrent conversions do not share
            # LibreOffice locks, caches, or first-run state.
            result = subprocess.run(
                [
                    executable,
                    f"-env:UserInstallation={profile_dir.as_uri()}",
                    "--headless",
                    "--nologo",
                    "--nofirststartwizard",
                    "--norestore",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(output_dir),
                    str(input_path),
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            last_error = f"timed out after {timeout}s"
            logger.warning(
                "LibreOffice preview conversion timed out for %s on attempt %s/%s: %s",
                input_path.name,
                attempt,
                max_attempts,
                exc,
            )
        else:
            stdout = _decode_process_output(result.stdout)
            stderr = _decode_process_output(result.stderr)
            if result.returncode == 0:
                pdf_paths = sorted(output_dir.glob("*.pdf"))
                if pdf_paths:
                    return pdf_paths[0]
                last_error = "LibreOffice exited successfully but produced no PDF."
                logger.warning(
                    "LibreOffice produced no PDF for %s on attempt %s/%s. stdout=%r stderr=%r",
                    input_path.name,
                    attempt,
                    max_attempts,
                    stdout,
                    stderr,
                )
            else:
                last_error = stderr or stdout or f"exit code {result.returncode}"
                logger.warning(
                    "LibreOffice failed for %s on attempt %s/%s with exit code %s. stdout=%r stderr=%r",
                    input_path.name,
                    attempt,
                    max_attempts,
                    result.returncode,
                    stdout,
                    stderr,
                )

        if attempt < max_attempts and retry_delay:
            time.sleep(retry_delay)

    logger.error("LibreOffice conversion failed for %s: %s", input_path.name, last_error)
    raise PreviewGenerationError("LibreOffice failed to convert the file.")


def _render_text_attachment_to_preview_pages(*, input_path, attachment, tmp_path):
    text = _read_text_preview(input_path)
    max_pages = _get_setting("COMMENT_PREVIEW_MAX_PAGES", DEFAULT_MAX_PAGES)
    width = _get_setting("COMMENT_PREVIEW_TEXT_RENDER_WIDTH", DEFAULT_TEXT_RENDER_WIDTH)
    height = _get_setting("COMMENT_PREVIEW_TEXT_RENDER_HEIGHT", DEFAULT_TEXT_RENDER_HEIGHT)
    margin = _get_setting("COMMENT_PREVIEW_TEXT_RENDER_MARGIN", DEFAULT_TEXT_RENDER_MARGIN)
    font_size = _get_setting("COMMENT_PREVIEW_TEXT_RENDER_FONT_SIZE", DEFAULT_TEXT_RENDER_FONT_SIZE)
    title_font_size = _get_setting(
        "COMMENT_PREVIEW_TEXT_RENDER_TITLE_FONT_SIZE",
        DEFAULT_TEXT_RENDER_TITLE_FONT_SIZE,
    )
    line_spacing = _get_setting(
        "COMMENT_PREVIEW_TEXT_RENDER_LINE_SPACING",
        DEFAULT_TEXT_RENDER_LINE_SPACING,
    )

    body_font = _load_preview_font(font_size)
    title_font = _load_preview_font(title_font_size, bold=True)
    measure_image = Image.new("RGB", (width, height), "white")
    measure_draw = ImageDraw.Draw(measure_image)
    max_text_width = max(width - (margin * 2), 120)
    body_lines = _wrap_preview_text(
        text=text,
        draw=measure_draw,
        font=body_font,
        max_width=max_text_width,
    )
    title_lines = _wrap_preview_text(
        text=attachment.filename,
        draw=measure_draw,
        font=title_font,
        max_width=max_text_width,
    )[:2]

    body_line_height = _get_font_line_height(draw=measure_draw, font=body_font) + line_spacing
    title_line_height = _get_font_line_height(draw=measure_draw, font=title_font) + 8

    pages = []
    line_index = 0
    page_number = 1
    while page_number <= max_pages and (line_index < len(body_lines) or not pages):
        image = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(image)
        y = margin

        if page_number == 1:
            for title_line in title_lines:
                draw.text((margin, y), title_line, fill="#172b4d", font=title_font)
                y += title_line_height
            y += 12
            draw.line((margin, y, width - margin, y), fill="#dfe1e6", width=2)
            y += 32

        bottom = height - margin
        while line_index < len(body_lines) and y + body_line_height <= bottom:
            draw.text((margin, y), body_lines[line_index], fill="#172b4d", font=body_font)
            y += body_line_height
            line_index += 1

        if page_number == max_pages and line_index < len(body_lines):
            _draw_truncation_notice(
                draw=draw,
                x=margin,
                y=min(y, bottom - body_line_height),
                font=body_font,
                max_pages=max_pages,
            )

        rendered_path = tmp_path / f"text-page-{page_number:03d}.png"
        image.save(rendered_path, format="PNG")
        pages.append(
            _store_rendered_page(
                rendered_path=rendered_path,
                attachment=attachment,
                page=page_number,
            )
        )
        page_number += 1

    return pages


def _read_text_preview(input_path):
    max_text_bytes = _get_setting("COMMENT_PREVIEW_MAX_TEXT_BYTES", 2 * 1024 * 1024)
    with open(input_path, "rb") as handle:
        data = handle.read(max_text_bytes + 1)
    if len(data) > max_text_bytes:
        data = data[:max_text_bytes] + b"\n\n[Preview truncated]"
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _clear_generated_pdfs(output_dir):
    for pdf_path in output_dir.glob("*.pdf"):
        try:
            pdf_path.unlink()
        except OSError:
            logger.warning("Could not remove stale LibreOffice output %s", pdf_path)


def _decode_process_output(output):
    return (output or b"").decode("utf-8", errors="replace").strip()


def _wrap_preview_text(*, text, draw, font, max_width):
    wrapped_lines = []
    lines = text.splitlines() or [""]
    for line in lines:
        expanded_line = line.expandtabs(4)
        if not expanded_line:
            wrapped_lines.append("")
            continue
        wrapped_lines.extend(
            _wrap_preview_line(
                line=expanded_line,
                draw=draw,
                font=font,
                max_width=max_width,
            )
        )
    return wrapped_lines


def _wrap_preview_line(*, line, draw, font, max_width):
    wrapped = []
    remaining = line.rstrip()
    while remaining:
        if draw.textlength(remaining, font=font) <= max_width:
            wrapped.append(remaining)
            break

        split_at = _find_preview_line_split(
            line=remaining,
            draw=draw,
            font=font,
            max_width=max_width,
        )
        wrapped.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()

    return wrapped or [""]


def _find_preview_line_split(*, line, draw, font, max_width):
    low = 1
    high = len(line)
    best = 1
    while low <= high:
        mid = (low + high) // 2
        if draw.textlength(line[:mid], font=font) <= max_width:
            best = mid
            low = mid + 1
        else:
            high = mid - 1

    natural_breaks = [
        line.rfind(" ", 0, best + 1),
        line.rfind("\t", 0, best + 1),
        line.rfind(",", 0, best + 1),
    ]
    natural_break = max(natural_breaks)
    if natural_break >= max(1, best // 2):
        return natural_break + 1
    return max(best, 1)


def _load_preview_font(size, *, bold=False):
    font_paths = (
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        )
        if bold
        else (
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/System/Library/Fonts/Monaco.ttf",
        )
    )
    for font_path in font_paths:
        if Path(font_path).exists():
            return ImageFont.truetype(font_path, size=size)
    return ImageFont.load_default()


def _get_font_line_height(*, draw, font):
    bbox = draw.textbbox((0, 0), "Mg", font=font)
    return bbox[3] - bbox[1]


def _draw_truncation_notice(*, draw, x, y, font, max_pages):
    notice = f"[Preview truncated after {max_pages} pages]"
    draw.text((x, y), notice, fill="#626f86", font=font)


def _render_pdf_to_preview_pages(*, pdf_path, attachment, tmp_path):
    executable = _find_executable(["pdftoppm"])
    if not executable:
        raise PreviewGenerationError("Poppler pdftoppm is not installed.")

    max_pages = _get_setting("COMMENT_PREVIEW_MAX_PAGES", DEFAULT_MAX_PAGES)
    dpi = _get_setting("COMMENT_PREVIEW_PDF_RENDER_DPI", DEFAULT_PDF_RENDER_DPI)
    timeout = _get_setting(
        "COMMENT_PREVIEW_PDF_RENDER_TIMEOUT_SECONDS",
        DEFAULT_PDF_RENDER_TIMEOUT_SECONDS,
    )
    output_prefix = tmp_path / "pdf-page"

    # Page limits protect preview generation from very large PDFs and decks.
    # Requires Poppler's pdftoppm on PATH for PDF page rasterization.
    result = subprocess.run(
        [
            executable,
            "-f",
            "1",
            "-l",
            str(max_pages),
            "-r",
            str(dpi),
            "-png",
            str(pdf_path),
            str(output_prefix),
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise PreviewGenerationError("PDF renderer failed to create preview pages.")

    rendered_paths = sorted(tmp_path.glob("pdf-page-*.png"), key=_page_sort_key)
    if not rendered_paths:
        raise PreviewGenerationError("PDF renderer produced no pages.")

    pages = []
    for index, rendered_path in enumerate(rendered_paths[:max_pages], start=1):
        pages.append(
            _store_rendered_page(
                rendered_path=rendered_path,
                attachment=attachment,
                page=index,
            )
        )
    return pages


def _store_rendered_page(*, rendered_path, attachment, page):
    image_format = _get_str_setting("COMMENT_PREVIEW_IMAGE_FORMAT", DEFAULT_IMAGE_FORMAT).lower()
    if image_format not in {"png", "webp"}:
        image_format = DEFAULT_IMAGE_FORMAT

    with Image.open(rendered_path) as image:
        width, height = image.size
        if image_format == "webp":
            storage_path = f"comments/previews/{attachment.id}/page-{page:03d}.webp"
            payload = _encode_image(image=image, image_format="WEBP")
        else:
            storage_path = f"comments/previews/{attachment.id}/page-{page:03d}.png"
            with open(rendered_path, "rb") as handle:
                payload = handle.read()

    if default_storage.exists(storage_path):
        default_storage.delete(storage_path)
    saved_path = default_storage.save(storage_path, ContentFile(payload))
    return {
        "page": page,
        "image_url": default_storage.url(saved_path),
        "width": width,
        "height": height,
    }


def _encode_image(*, image, image_format):
    from io import BytesIO

    buffer = BytesIO()
    image.save(buffer, format=image_format, quality=85)
    return buffer.getvalue()


def _get_image_preview_page(attachment):
    page = {
        "page": 1,
        "image_url": attachment.file.url,
    }
    try:
        with attachment.file.open("rb") as handle:
            with Image.open(handle) as image:
                page["width"], page["height"] = image.size
    except Exception as exc:
        # Width/height are optional hints; the image is still served from its original
        # URL. Expected for formats Pillow cannot decode (e.g. SVG). A FileNotFoundError
        # here usually means this worker cannot see MEDIA_ROOT.
        logger.warning(
            "Comment attachment preview: could not read image dimensions "
            "attachment_id=%s content_type=%s: %r",
            attachment.id, attachment.content_type, exc,
        )
    return page


def _mark_preview(*, attachment, status, preview_pages, error):
    attachment.preview_status = status
    attachment.preview_error = error
    attachment.preview_metadata = {"preview_pages": preview_pages}
    attachment.save(update_fields=["preview_status", "preview_error", "preview_metadata"])
    return attachment


def _format_preview_error(exc):
    message = str(exc).strip() or exc.__class__.__name__
    return message[:240]


def _find_executable(names):
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    return None


def _page_sort_key(path):
    stem = path.stem
    suffix = stem.rsplit("-", 1)[-1]
    try:
        return int(suffix)
    except ValueError:
        return 0


def _is_text_like_attachment(attachment):
    extension = Path(attachment.filename).suffix.lower().lstrip(".")
    content_type = (attachment.content_type or "").lower()
    return extension in TEXT_PREVIEW_EXTENSIONS or content_type.startswith("text/")


def _get_setting(name, default):
    value = getattr(settings, name, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _get_str_setting(name, default):
    value = getattr(settings, name, default)
    return str(value or default)
