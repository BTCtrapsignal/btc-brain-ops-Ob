"""
api/weekly.py

GET  /weekly/              — list all exported weeks
GET  /weekly/{week}        — get markdown export
GET  /weekly/{week}/obsidian — get obsidian-formatted export
POST /weekly/{week}/generate — generate (or regenerate) export
GET  /weekly/{week}/status — reliability check
GET  /weekly/{week}/engineering-package — full engineering package (REQ-W27-002)
"""

from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.database import WeeklyExport, get_session
from app.weekly_intelligence_exporter.exporter import (
    generate_weekly_markdown,
    get_generation_metadata,
)
from app.obsidian_export.formatter import format_for_obsidian

# REQ-W27-002: engineering generators
from app.engineering_exporter import (
    generate_engineering_index,
    generate_timeline,
    generate_event_bundle,
    generate_runtime_statistics,
    generate_eo_register,
    generate_er_register,
    generate_engineering_summary,
)

router = APIRouter(prefix="/weekly", tags=["weekly"])


# ─────────────────────────────────────────────────────────────
# Existing endpoints — unchanged
# ─────────────────────────────────────────────────────────────

@router.get("/")
def list_exports(session: Session = Depends(get_session)):
    exports = session.exec(select(WeeklyExport).order_by(WeeklyExport.week.desc())).all()
    return [{"week": e.week, "generated_at": e.generated_at} for e in exports]


@router.get("/{week}")
def get_export(week: str, session: Session = Depends(get_session)):
    export = session.exec(select(WeeklyExport).where(WeeklyExport.week == week)).first()
    if not export:
        raise HTTPException(
            status_code=404,
            detail=f"No export for {week}. POST to /weekly/{week}/generate first.",
        )
    return {"week": week, "markdown": export.markdown_content}


@router.get("/{week}/obsidian")
def get_obsidian_export(week: str, session: Session = Depends(get_session)):
    export = session.exec(select(WeeklyExport).where(WeeklyExport.week == week)).first()
    if not export or not export.obsidian_content:
        raise HTTPException(
            status_code=404,
            detail=f"No Obsidian export for {week}.",
        )
    return {"week": week, "obsidian": export.obsidian_content}


@router.get("/{week}/status")
def export_status(week: str, session: Session = Depends(get_session)):
    """Reliability check — lets Engineering Review verify report completeness."""
    export = session.exec(select(WeeklyExport).where(WeeklyExport.week == week)).first()
    if not export:
        return {
            "week":     week,
            "exported": False,
            "message":  f"No export found. POST to /weekly/{week}/generate first.",
        }
    return {
        "week":                       week,
        "exported":                   True,
        "generated_at":               export.generated_at,
        "is_complete":                export.is_complete,
        "signal_count_at_generation": export.signal_count_at_generation,
        "missed_count_at_generation": export.missed_count_at_generation,
        "event_count_at_generation":  export.event_count_at_generation,
        "markdown_length":            len(export.markdown_content),
        # REQ-W27-002: versioning fields — None for pre-REQ-W27-002 exports
        "package_version":    export.package_version,
        "schema_version":     export.schema_version,
        "engineering_ready":  export.engineering_index_content is not None,
    }


# ─────────────────────────────────────────────────────────────
# Extended generate endpoint — REQ-W27-002
# ─────────────────────────────────────────────────────────────

@router.post("/{week}/generate", status_code=201)
def generate_export(week: str, session: Session = Depends(get_session)):
    """
    Generate or regenerate the full weekly export.

    Produces:
    - Weekly intelligence Markdown (existing)
    - Obsidian export (existing)
    - Engineering Package (REQ-W27-002): index, timeline, event bundle,
      runtime statistics, EO register, ER register, engineering summary.
    """
    # ── Existing generators (unchanged) ──────────────────────
    markdown = generate_weekly_markdown(week=week, session=session)
    obsidian = format_for_obsidian(week=week, markdown=markdown)
    meta     = get_generation_metadata(week=week, session=session)

    # ── REQ-W27-002: Engineering generators ──────────────────
    eng_index    = generate_engineering_index(week=week, session=session)
    timeline     = generate_timeline(week=week, session=session)
    event_bundle = generate_event_bundle(week=week, session=session)
    runtime_stats = generate_runtime_statistics(week=week, session=session)
    eo_register  = generate_eo_register(week=week, session=session)
    er_register  = generate_er_register(week=week, session=session)
    eng_summary  = generate_engineering_summary(week=week, session=session)

    # ── Write to WeeklyExport ─────────────────────────────────
    existing = session.exec(select(WeeklyExport).where(WeeklyExport.week == week)).first()

    if existing:
        # Update path — all fields assigned explicitly (Source Review #3 finding)
        existing.markdown_content            = markdown
        existing.obsidian_content            = obsidian
        existing.generated_at                = datetime.utcnow()
        existing.signal_count_at_generation  = meta["signal_count_at_generation"]
        existing.missed_count_at_generation  = meta["missed_count_at_generation"]
        existing.event_count_at_generation   = meta["event_count_at_generation"]
        existing.is_complete                 = meta["is_complete"]
        # REQ-W27-002: versioning
        existing.package_version             = meta["package_version"]
        existing.schema_version              = meta["schema_version"]
        existing.generator_version           = meta["generator_version"]
        existing.compatible_runtime          = meta["compatible_runtime"]
        # REQ-W27-002: engineering content
        existing.engineering_index_content   = eng_index
        existing.timeline_content            = timeline
        existing.event_bundle_json           = event_bundle
        existing.runtime_stats_json          = runtime_stats
        existing.eo_register_content         = eo_register
        existing.er_register_content         = er_register
        existing.engineering_summary_content = eng_summary
        session.add(existing)
    else:
        # Create path — **meta unpacks existing + versioning keys
        session.add(WeeklyExport(
            week=week,
            markdown_content=markdown,
            obsidian_content=obsidian,
            # existing reliability + new versioning via **meta
            **meta,
            # REQ-W27-002: engineering content
            engineering_index_content=eng_index,
            timeline_content=timeline,
            event_bundle_json=event_bundle,
            runtime_stats_json=runtime_stats,
            eo_register_content=eo_register,
            er_register_content=er_register,
            engineering_summary_content=eng_summary,
        ))

    session.commit()
    return {
        "week":    week,
        "status":  "generated",
        "length":  len(markdown),
        "metadata": meta,
        "engineering_package": {
            "index_length":   len(eng_index),
            "timeline_note":  "Partial — Reflex correlation pending Source Review #5",
            "event_count":    len(event_bundle),
            "eo_count":       meta.get("eo_count_at_generation", 0),
            "er_count":       meta.get("er_count_at_generation", 0),
        },
    }


# ─────────────────────────────────────────────────────────────
# New engineering package endpoint — REQ-W27-002
# ─────────────────────────────────────────────────────────────

@router.get("/{week}/engineering-package")
def get_engineering_package(week: str, session: Session = Depends(get_session)):
    """
    Return the complete Engineering Package for a week.

    Assembles stored content fields from WeeklyExport into one structured
    response. No re-generation — reads stored content only.

    Returns 404 if no export exists.
    Returns 'not_generated' status if export exists but predates REQ-W27-002.
    """
    export = session.exec(select(WeeklyExport).where(WeeklyExport.week == week)).first()
    if not export:
        raise HTTPException(
            status_code=404,
            detail=f"No export for {week}. POST to /weekly/{week}/generate first.",
        )

    # Pre-REQ-W27-002 export — engineering fields are NULL
    if export.engineering_index_content is None:
        return {
            "week":    week,
            "status":  "not_generated",
            "message": (
                f"Export for {week} predates REQ-W27-002 or engineering package "
                f"was not generated. POST to /weekly/{week}/generate to regenerate."
            ),
            "generated_at": export.generated_at,
        }

    return {
        "week":             week,
        "status":           "ready",
        "generated_at":     export.generated_at,
        "package_version":  export.package_version,
        "schema_version":   export.schema_version,
        "generator_version": export.generator_version,
        "compatible_runtime": export.compatible_runtime,
        "documents": {
            "engineering_index":   export.engineering_index_content,
            "timeline":            export.timeline_content,
            "eo_register":         export.eo_register_content,
            "er_register":         export.er_register_content,
            "engineering_summary": export.engineering_summary_content,
        },
        "data": {
            "event_bundle":  export.event_bundle_json,
            "runtime_stats": export.runtime_stats_json,
        },
    }
