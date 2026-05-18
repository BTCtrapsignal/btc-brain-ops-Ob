"""
api/weekly.py

GET  /weekly/              — list all exported weeks
GET  /weekly/{week}        — get markdown export
GET  /weekly/{week}/obsidian — get obsidian-formatted export
POST /weekly/{week}/generate — generate (or regenerate) export
"""

from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.database import WeeklyExport, get_session
from app.weekly_intelligence_exporter.exporter import generate_weekly_markdown
from app.obsidian_export.formatter import format_for_obsidian

router = APIRouter(prefix="/weekly", tags=["weekly"])


@router.get("/")
def list_exports(session: Session = Depends(get_session)):
    exports = session.exec(select(WeeklyExport).order_by(WeeklyExport.week.desc())).all()
    return [{"week": e.week, "generated_at": e.generated_at} for e in exports]


@router.get("/{week}")
def get_export(week: str, session: Session = Depends(get_session)):
    export = session.exec(select(WeeklyExport).where(WeeklyExport.week == week)).first()
    if not export:
        raise HTTPException(status_code=404, detail=f"No export for {week}. POST to /weekly/{week}/generate first.")
    return {"week": week, "markdown": export.markdown_content}


@router.get("/{week}/obsidian")
def get_obsidian_export(week: str, session: Session = Depends(get_session)):
    export = session.exec(select(WeeklyExport).where(WeeklyExport.week == week)).first()
    if not export or not export.obsidian_content:
        raise HTTPException(status_code=404, detail=f"No Obsidian export for {week}.")
    return {"week": week, "obsidian": export.obsidian_content}


@router.post("/{week}/generate", status_code=201)
def generate_export(week: str, session: Session = Depends(get_session)):
    markdown = generate_weekly_markdown(week=week, session=session)
    obsidian = format_for_obsidian(week=week, markdown=markdown)

    existing = session.exec(select(WeeklyExport).where(WeeklyExport.week == week)).first()
    if existing:
        existing.markdown_content = markdown
        existing.obsidian_content = obsidian
        existing.generated_at = datetime.utcnow()
        session.add(existing)
    else:
        session.add(WeeklyExport(week=week, markdown_content=markdown, obsidian_content=obsidian))

    session.commit()
    return {"week": week, "status": "generated", "length": len(markdown)}
