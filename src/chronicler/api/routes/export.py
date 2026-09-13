"""Chronicle export route (ck3_chronicler-441).

POST /api/campaigns/{name}/export/markdown — returns a zip containing
the closing chronicle, per-tracked-character vitae + memories + event
rolls, composed-SVG heraldry shields, and an overview with warnings
for any missing data."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import zipfile
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from chronicler.api.dependencies import (
    get_campaign_including_archived,
    get_engine_cache,
)
from chronicler.db.registry import (
    Campaign,
    get_data_dir,
    list_tracked_characters,
)
from chronicler.export import build_export_bundle
from chronicler.util.slug import slugify

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/campaigns/{name}", tags=["export"])


def _load_palette(heraldry_dir) -> dict[str, list[int]]:
    palette_file = heraldry_dir / "palette.json"
    if not palette_file.is_file():
        return {}
    try:
        return json.loads(palette_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("failed to load heraldry palette from %s", palette_file)
        return {}


def _build_markdown_zip_bytes(
    *,
    factory,
    campaign_name,
    campaign_slug,
    ck3_version,
    closing_chronicle_body,
    closing_chronicle_generated_at,
    bookmark_date,
    current_in_game_date,
    tracked_character_ids,
    tracked_metadata,
    heraldry_dir,
    palette,
):
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
    return buf.getvalue(), result


@router.post("/export/markdown")
async def export_chronicle_markdown(
    request: Request,
    campaign: Campaign = Depends(get_campaign_including_archived),
) -> Response:
    """audit F-04 / ck3_chronicler-shen: was sync def — markdown export
    builds a multi-megabyte zip with synchronous gzip + heraldry SVG
    rendering. Now async, with the heavy work offloaded via
    asyncio.to_thread so a long export doesn't pin a threadpool worker
    + block other requests on the same loop."""
    if not campaign.closing_chronicle:
        raise HTTPException(
            status_code=409,
            detail=(
                "campaign has no closing chronicle; complete the campaign first "
                "via POST /api/campaigns/{name}/complete"
            ),
        )

    cache = get_engine_cache(request)
    factory = cache.factory_for(campaign)

    tracked_rows = list_tracked_characters(campaign.id, registry=cache.registry_path)
    tracked_ids = [r.character_id for r in tracked_rows]
    tracked_metadata = {
        r.character_id: {"role": r.role, "added_at": r.added_at} for r in tracked_rows
    }

    heraldry_dir = get_data_dir() / "heraldry"
    palette = _load_palette(heraldry_dir)

    slug = slugify(campaign.name) or "chronicler-export"
    zip_bytes, result = await asyncio.to_thread(
        _build_markdown_zip_bytes,
        factory=factory,
        campaign_name=campaign.name,
        campaign_slug=slug,
        ck3_version=campaign.ck3_version,
        closing_chronicle_body=campaign.closing_chronicle,
        closing_chronicle_generated_at=campaign.closing_chronicle_generated_at or "",
        bookmark_date=campaign.bookmark_date,
        current_in_game_date=campaign.current_in_game_date,
        tracked_character_ids=tracked_ids,
        tracked_metadata=tracked_metadata,
        heraldry_dir=heraldry_dir,
        palette=palette,
    )

    log.info(
        "exported %s: %d files, %d warnings",
        campaign.name,
        result.files_written,
        len(result.warnings),
    )

    filename = f"{slug}-chronicle.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"
            ),
        },
    )


@router.post("/export/pdf")
async def export_chronicle_pdf(
    request: Request,
    campaign: Campaign = Depends(get_campaign_including_archived),
) -> Response:
    """Render a single PDF mirroring the markdown bundle (r8i).

    audit F-04 / ck3_chronicler-shen: now async + offloaded; PDF
    generation can take seconds on a large chronicle (cairo + SVG
    composition + reportlab assembly), and pinning a threadpool
    worker makes other requests queue."""
    if not campaign.closing_chronicle:
        raise HTTPException(
            status_code=409,
            detail=(
                "campaign has no closing chronicle; complete the campaign first "
                "via POST /api/campaigns/{name}/complete"
            ),
        )

    cache = get_engine_cache(request)
    factory = cache.factory_for(campaign)

    tracked_rows = list_tracked_characters(campaign.id, registry=cache.registry_path)
    tracked_ids = [r.character_id for r in tracked_rows]
    tracked_metadata = {
        r.character_id: {"role": r.role, "added_at": r.added_at} for r in tracked_rows
    }

    heraldry_dir = get_data_dir() / "heraldry"
    palette = _load_palette(heraldry_dir)

    # Imported lazily: the PDF stack is the optional `pdf` extra (bpb8/knne),
    # so the app loads and every non-PDF route works without it installed.
    from chronicler.export.pdf import PdfExportUnavailable, build_pdf_export

    slug = slugify(campaign.name) or "chronicler-export"
    try:
        pdf_bytes, result = await asyncio.to_thread(
            build_pdf_export,
            factory=factory,
            campaign_name=campaign.name,
            campaign_slug=slug,
            ck3_version=campaign.ck3_version,
            closing_chronicle_body=campaign.closing_chronicle,
            closing_chronicle_generated_at=campaign.closing_chronicle_generated_at or "",
            bookmark_date=campaign.bookmark_date,
            current_in_game_date=campaign.current_in_game_date,
            tracked_character_ids=tracked_ids,
            tracked_metadata=tracked_metadata,
            heraldry_dir=heraldry_dir,
            palette=palette,
        )
    except PdfExportUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    log.info(
        "exported %s as PDF: %d bytes, %d warnings",
        campaign.name,
        len(pdf_bytes),
        len(result.warnings),
    )

    filename = f"{slug}-chronicle.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"
            ),
        },
    )
