"""PDF chronicle export (ck3_chronicler-r8i).

Builds a single self-contained PDF mirroring the markdown bundle: an
overview page, the closing chronicle prose, and one page per
tracked-character vita with its composed heraldry shield inlined.

Uses xhtml2pdf so the renderer is pure-Python and bundles cleanly into
the PyInstaller .exe (6jh) without GTK/cairo system dependencies.
"""

from __future__ import annotations

import base64
import io
import zipfile
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt

from chronicler.export.markdown import BundleResult, build_export_bundle


class PdfExportUnavailable(RuntimeError):
    """Raised when the optional PDF stack (the ``pdf`` extra) is not installed."""


_PDF_CSS = """
@page {
    size: A4;
    margin: 18mm;
}
body {
    font-family: Garamond, Georgia, serif;
    font-size: 11pt;
    line-height: 1.45;
    color: #2a2a2a;
}
h1, h2, h3 {
    font-family: Cormorant, Georgia, serif;
    color: #1a1a1a;
}
h1 { font-size: 22pt; margin-top: 0; }
h2 { font-size: 16pt; margin-top: 14pt; }
h3 { font-size: 13pt; margin-top: 10pt; }
em { font-style: italic; }
strong { font-weight: bold; }
img.shield { display: block; margin: 8pt 0 12pt 0; }
table { border-collapse: collapse; margin: 6pt 0; width: 100%; font-size: 10pt; }
th, td { border: 1px solid #aaa; padding: 4pt 6pt; text-align: left; vertical-align: top; }
th { background-color: #ebe6dc; }
.character-page { page-break-before: always; }
.warnings { color: #7a3a3a; font-size: 9pt; margin-top: 16pt; }
"""


def _zip_to_html(buf: io.BytesIO, *, campaign_slug: str) -> str:
    """Pull the markdown + SVG payloads out of an in-memory bundle zip
    and weave them into one HTML document for the PDF renderer.

    Exposed (rather than inlined into ``build_pdf_export``) so unit
    tests can assert on HTML structure without spinning the renderer.
    """
    root = f"{campaign_slug}-chronicle"
    md_renderer = MarkdownIt("commonmark", {"breaks": True}).enable("table")

    buf.seek(0)
    with zipfile.ZipFile(buf, "r") as zf:
        names = sorted(zf.namelist())
        overview_md = zf.read(f"{root}/overview.md").decode("utf-8")
        chronicle_md = zf.read(f"{root}/chronicle.md").decode("utf-8")
        shields: dict[int, str] = {}
        for name in names:
            if name.startswith(f"{root}/assets/heraldry/") and name.endswith(".svg"):
                cid = int(Path(name).stem)
                shields[cid] = zf.read(name).decode("utf-8")
        char_md_bodies: dict[int, str] = {}
        for name in names:
            if name.startswith(f"{root}/characters/") and name.endswith(".md"):
                cid = int(Path(name).stem.split("-", 1)[0])
                char_md_bodies[cid] = zf.read(name).decode("utf-8")

    parts: list[str] = [
        "<html><head>",
        f"<style>{_PDF_CSS}</style>",
        "</head><body>",
        md_renderer.render(overview_md),
        '<div class="character-page">',
        "<h1>The Closing Chronicle</h1>",
        md_renderer.render(chronicle_md),
        "</div>",
    ]

    for cid in sorted(char_md_bodies.keys()):
        body_md = char_md_bodies[cid]
        # Strip the markdown image reference; the relative path won't
        # resolve in the PDF context. We re-inject the SVG inline below
        # as an <img> with a data URI so the PDF embeds the shield.
        stripped = "\n".join(
            line for line in body_md.splitlines() if not line.lstrip().startswith("![Arms of ")
        )
        body_html = md_renderer.render(stripped)
        if cid in shields:
            data_uri = "data:image/svg+xml;base64," + base64.b64encode(
                shields[cid].encode("utf-8")
            ).decode("ascii")
            shield_img = (
                f'<img src="{data_uri}" class="shield" width="120" height="138" alt="Arms" />'
            )
            body_html = body_html.replace("</h1>", "</h1>\n" + shield_img, 1)
        parts.append('<div class="character-page">')
        parts.append(body_html)
        parts.append("</div>")

    parts.append("</body></html>")
    return "".join(parts)


def build_pdf_export(
    *,
    factory,
    campaign_name: str,
    campaign_slug: str,
    ck3_version: str | None,
    closing_chronicle_body: str,
    closing_chronicle_generated_at: str,
    bookmark_date: str | None,
    current_in_game_date: str | None,
    tracked_character_ids: list[int],
    tracked_metadata: dict[int, dict[str, Any]],
    heraldry_dir: Path,
    palette: dict[str, list[int]],
) -> tuple[bytes, BundleResult]:
    """Build a chronicle PDF. Returns ``(pdf_bytes, bundle_result)``.

    Internally delegates to :func:`build_export_bundle` to gather all
    markdown + SVG payloads into an in-memory zip, then converts each
    .md to HTML via markdown-it and inlines each .svg as a data URI
    img tag before handing the assembled HTML to xhtml2pdf.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        result = build_export_bundle(
            zf=zf,
            factory=factory,
            campaign_name=campaign_name,
            campaign_slug=campaign_slug,
            ck3_version=ck3_version,
            closing_chronicle_body=closing_chronicle_body,
            closing_chronicle_generated_at=closing_chronicle_generated_at,
            bookmark_date=bookmark_date,
            current_in_game_date=current_in_game_date,
            tracked_character_ids=tracked_character_ids,
            tracked_metadata=tracked_metadata,
            heraldry_dir=heraldry_dir,
            palette=palette,
        )

    html = _zip_to_html(buf, campaign_slug=campaign_slug)

    # Imported lazily so the API/CLI/tests load without the PDF stack
    # (xhtml2pdf -> ... -> pycairo, the `pdf` extra). See bpb8/knne.
    try:
        from xhtml2pdf import pisa
    except ImportError as exc:  # pragma: no cover - exercised via the route's 503
        raise PdfExportUnavailable(
            "PDF export is not installed. Install the optional stack with "
            "`uv sync --extra pdf` (needs system cairo + cmake on Linux)."
        ) from exc

    pdf_buf = io.BytesIO()
    pisa_result = pisa.CreatePDF(html, dest=pdf_buf, encoding="utf-8")
    if pisa_result.err:
        raise RuntimeError(f"xhtml2pdf reported {pisa_result.err} errors during PDF generation")
    return pdf_buf.getvalue(), result
