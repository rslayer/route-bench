"""HTML to PDF rendering via WeasyPrint."""

from __future__ import annotations

import structlog

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


def render_pdf(html: str) -> bytes:
    """Render an HTML string to a PDF document.

    Args:
        html: Complete HTML document as a string.

    Returns:
        PDF content as bytes.
    """
    from weasyprint import HTML

    logger.info("rendering_pdf")
    pdf_bytes: bytes = HTML(string=html).write_pdf()
    logger.info("pdf_rendered", size_bytes=len(pdf_bytes))
    return pdf_bytes
