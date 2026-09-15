"""Executive PDF packs for project, programme and circle.

Rendered with ReportLab. Charts are drawn as native ReportLab vector graphics
rather than rasterised through Matplotlib: the legacy exporter spun up a
headless Matplotlib backend and pushed PNGs through BytesIO for every chart,
which is a lot of machinery and a large dependency for four bar charts, and it
produced blurry output at print resolution.

Numbers come from the same rollup functions the dashboards use, so a PDF and the
screen can never disagree.
"""
from __future__ import annotations

from datetime import date, datetime
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.delay import AT_RISK_THRESHOLD_DAYS
from app.domain.stages import StageModel, health_status, resolve_many, summarise
from app.models import LeadershipAction, Programme, Project, Scope, Task, TaskExecution
from app.services.rollup import (
    ProgrammeRollup,
    circle_rollups,
    programme_rollup,
    project_narrative,
)

# Palette carried over from the legacy PDF spec so the output still looks like
# IPTT to the people who read it.
INK = colors.HexColor("#1F2933")
ACCENT = colors.HexColor("#0E6D7D")
MUTED = colors.HexColor("#59646F")
RULE = colors.HexColor("#D3DAE1")
BAND = colors.HexColor("#F2F4F6")
OK = colors.HexColor("#1C6B4A")
WARN = colors.HexColor("#8A6410")
RISK = colors.HexColor("#A4231C")

PAGE = landscape(A4)
MARGIN = 14 * mm


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title", parent=base["Title"], fontName="Helvetica-Bold",
            fontSize=19, leading=23, textColor=INK, alignment=TA_LEFT, spaceAfter=2,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", parent=base["Normal"], fontName="Helvetica",
            fontSize=9.5, leading=13, textColor=MUTED, spaceAfter=10,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=11, leading=14, textColor=INK, spaceBefore=10, spaceAfter=5,
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"], fontName="Helvetica",
            fontSize=9, leading=13, textColor=INK,
        ),
        "note": ParagraphStyle(
            "note", parent=base["Normal"], fontName="Helvetica-Oblique",
            fontSize=8, leading=11, textColor=MUTED,
        ),
        "cell": ParagraphStyle(
            "cell", parent=base["Normal"], fontName="Helvetica",
            fontSize=7.5, leading=9.5, textColor=INK,
        ),
        "th": ParagraphStyle(
            "th", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=7.5, leading=9.5, textColor=colors.white,
        ),
    }


def esc(value) -> str:
    """Escape for ReportLab's Paragraph markup.

    Stage and task names contain ampersands - "HW Handover to Application I&C",
    "Application I & C" - and an unescaped one is read as the start of an XML
    entity, which silently mangles the text.
    """
    return escape("" if value is None else str(value))


def _tone(health: float):
    return OK if health >= 80 else WARN if health >= 50 else RISK


class _Doc(BaseDocTemplate):
    """Adds a footer with the page number and generation stamp."""

    def __init__(self, buffer, title: str, **kwargs):
        super().__init__(buffer, pagesize=PAGE, title=title, **kwargs)
        frame = Frame(
            MARGIN, MARGIN + 8 * mm,
            PAGE[0] - 2 * MARGIN, PAGE[1] - 2 * MARGIN - 8 * mm,
            id="body", showBoundary=0,
        )
        self.addPageTemplates(PageTemplate(id="main", frames=[frame], onPage=self._chrome))
        self._doc_title = title

    def _chrome(self, canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.5)
        canvas.line(MARGIN, MARGIN + 6 * mm, PAGE[0] - MARGIN, MARGIN + 6 * mm)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(
            MARGIN, MARGIN + 1.5 * mm,
            f"IPTT · {self._doc_title} · generated {datetime.now():%d %b %Y %H:%M}",
        )
        canvas.drawRightString(
            PAGE[0] - MARGIN, MARGIN + 1.5 * mm, f"Page {canvas.getPageNumber()}"
        )
        canvas.restoreState()


# --- building blocks --------------------------------------------------------


def _kpi_band(items: list[tuple[str, str, colors.Color | None]]) -> Table:
    """A row of big figures. Used once per document, at the top."""
    labels = [
        Paragraph(f"<font size=7 color='#59646F'>{esc(label).upper()}</font>",
                  _styles()["cell"])
        for label, _, _ in items
    ]
    values = []
    for _, value, tone in items:
        colour = (tone or INK).hexval()[2:]
        values.append(
            Paragraph(f"<font size=15 color='#{colour}'><b>{esc(value)}</b></font>",
                      _styles()["cell"])
        )

    width = (PAGE[0] - 2 * MARGIN) / len(items)
    table = Table([labels, values], colWidths=[width] * len(items), rowHeights=[11, 21])
    table.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("BACKGROUND", (0, 0), (-1, -1), BAND),
            ("LINEAFTER", (0, 0), (-2, -1), 0.5, colors.white),
            ("BOX", (0, 0), (-1, -1), 0.5, RULE),
        ])
    )
    return table


def _bar_chart(rows: list[tuple[str, float]], *, width: float, label_width: float = 120,
               value_suffix: str = "", tone=None) -> Table:
    """A horizontal bar chart drawn as a table with coloured cells.

    Vector, selectable, and it reflows with the frame — which a PNG does not.
    """
    if not rows:
        return Table([[Paragraph("No data.", _styles()["note"])]], colWidths=[width])

    peak = max((v for _, v in rows), default=0) or 1
    bar_area = width - label_width - 42
    data = []
    style = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("TEXTCOLOR", (0, 0), (0, -1), INK),
        ("ALIGN", (2, 0), (2, -1), "RIGHT"),
        ("TEXTCOLOR", (2, 0), (2, -1), MUTED),
    ]
    for index, (label, value) in enumerate(rows):
        filled = max(1.0, bar_area * (value / peak))
        inner = Table([[""]], colWidths=[filled], rowHeights=[7])
        inner.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), tone(value) if callable(tone) else (tone or ACCENT)),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        data.append([
            Paragraph(esc(label)[:120], _styles()["cell"]),
            inner,
            f"{value:g}{value_suffix}",
        ])
        del index

    table = Table(data, colWidths=[label_width, bar_area, 42])
    table.setStyle(TableStyle(style))
    return table


def _grid(header: list[str], rows: list[list], widths: list[float],
          aligns: dict[int, str] | None = None) -> Table:
    styles = _styles()
    body = [[Paragraph(esc(h), styles["th"]) for h in header]]
    for row in rows:
        body.append([
            cell if hasattr(cell, "wrap") else Paragraph(esc(cell), styles["cell"])
            for cell in row
        ])

    table = Table(body, colWidths=widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BAND]),
    ]
    for column, align in (aligns or {}).items():
        style.append(("ALIGN", (column, 0), (column, -1), align))
    table.setStyle(TableStyle(style))
    return table


def _header(title: str, subtitle: str) -> list:
    styles = _styles()
    return [
        Paragraph(esc(title), styles["title"]),
        # subtitle carries deliberate &nbsp; markup, so it is composed escaped
        # by its callers rather than escaped wholesale here.
        Paragraph(subtitle, styles["subtitle"]),
    ]


def _render(title: str, story: list) -> bytes:
    buffer = BytesIO()
    _Doc(buffer, title).build(story)
    return buffer.getvalue()


# --- project ----------------------------------------------------------------


def project_pack(db: Session, project_id: int) -> bytes:
    project = db.get(Project, project_id)
    if project is None:
        raise ValueError("Project not found")

    styles = _styles()
    model = StageModel.from_session(db)
    rows = db.execute(
        select(Scope.id, Task.template_task_number, TaskExecution.status)
        .join(TaskExecution, TaskExecution.scope_id == Scope.id)
        .join(Task, Task.id == TaskExecution.task_id)
        .where(Scope.project_id == project_id)
    ).all()
    node_stages = resolve_many(model, rows)
    summary = summarise(node_stages.values())

    at_risk, total_delay = db.execute(
        select(
            func.count(func.distinct(TaskExecution.scope_id)).filter(
                TaskExecution.delay_days > AT_RISK_THRESHOLD_DAYS
            ),
            func.coalesce(func.sum(TaskExecution.delay_days), 0),
        ).where(TaskExecution.project_id == project_id)
    ).one()

    programme_name = db.scalar(
        select(Programme.name).where(Programme.id == project.programme_id)
    )
    width = PAGE[0] - 2 * MARGIN

    story: list = []
    story += _header(
        project.name,
        f"{esc(programme_name)} &nbsp;·&nbsp; kickoff "
        f"{project.project_start_date or 'not set'} &nbsp;·&nbsp; baseline v"
        f"{project.baseline_version}",
    )
    story.append(_kpi_band([
        ("Health", f"{summary.health:g}", _tone(summary.health)),
        ("Nodes", str(summary.total_nodes), None),
        ("Live", f"{summary.live_nodes} ({summary.progress:g}%)", None),
        ("At risk", str(at_risk or 0), RISK if at_risk else OK),
        ("Delay (wd)", str(int(total_delay or 0)), WARN if total_delay else OK),
        ("Assessment", health_status(summary.health), _tone(summary.health)),
    ]))
    story.append(Spacer(1, 9))
    story.append(Paragraph(esc(project_narrative(db, project_id, model)), styles["body"]))

    # --- stage mix ---------------------------------------------------------
    ordered = {s.name: s.sequence_position for s in model.ordered}
    mix = sorted(
        summary.stage_mix.items(), key=lambda kv: ordered.get(kv[0], 0)
    )
    story.append(Paragraph("Stage mix", styles["h2"]))
    story.append(Paragraph(
        "Nodes by the furthest gate they have actually passed. Only completed "
        "activities count.", styles["note"]))
    story.append(Spacer(1, 4))
    story.append(_bar_chart([(k, v) for k, v in mix], width=width, label_width=190))

    # --- circles -----------------------------------------------------------
    circles = circle_rollups(db, project_id, model)
    if circles:
        story.append(Paragraph("Circle performance", styles["h2"]))
        story.append(Paragraph("Weakest first.", styles["note"]))
        story.append(Spacer(1, 4))
        story.append(_grid(
            ["Circle", "Nodes", "Live", "Health", "At risk", "Delay (wd)", "Assessment"],
            [
                [c.circle, c.nodes, c.live_nodes, f"{c.health:g}",
                 c.at_risk_nodes, c.total_delay_days, c.status]
                for c in circles
            ],
            [110, 90, 80, 90, 90, 110, 140],
            aligns={1: "RIGHT", 2: "RIGHT", 3: "RIGHT", 4: "RIGHT", 5: "RIGHT"},
        ))

    # --- worst nodes -------------------------------------------------------
    worst = db.execute(
        select(
            Scope.id, Scope.node_id, Scope.circle, Scope.facility_name,
            func.coalesce(func.max(TaskExecution.delay_days), 0),
            func.coalesce(func.sum(TaskExecution.delay_days), 0),
        )
        .join(TaskExecution, TaskExecution.scope_id == Scope.id)
        .where(Scope.project_id == project_id)
        .group_by(Scope.id)
        .having(func.max(TaskExecution.delay_days) > 0)
        .order_by(func.max(TaskExecution.delay_days).desc())
        .limit(15)
    ).all()
    if worst:
        story.append(PageBreak())
        story.append(Paragraph("Nodes needing attention", styles["h2"]))
        story.append(Paragraph(
            f"Ranked by the single worst late activity. A node is at risk beyond "
            f"{AT_RISK_THRESHOLD_DAYS} working days.", styles["note"]))
        story.append(Spacer(1, 4))
        story.append(_grid(
            ["Node", "Circle", "Facility", "Stage", "Worst delay (wd)", "Total (wd)"],
            [
                [
                    node_id, circle, facility[:40],
                    node_stages[scope_id].stage_name if scope_id in node_stages else "-",
                    int(w), int(t),
                ]
                for scope_id, node_id, circle, facility, w, t in worst
            ],
            [90, 50, 170, 180, 90, 70],
            aligns={4: "RIGHT", 5: "RIGHT"},
        ))

    # --- leadership actions -------------------------------------------------
    actions = db.scalars(
        select(LeadershipAction)
        .where(LeadershipAction.project_id == project_id)
        .order_by(LeadershipAction.priority_sort, LeadershipAction.target_date)
    ).all()
    if actions:
        today = date.today()
        story.append(Paragraph("Leadership actions", styles["h2"]))
        story.append(Spacer(1, 4))
        story.append(_grid(
            ["Priority", "Action", "Owner", "Target", "Status", "Overdue"],
            [
                [
                    a.priority,
                    Paragraph(esc(a.action_required or "")[:260], styles["cell"]),
                    a.owner or "-",
                    a.target_date.isoformat() if a.target_date else "-",
                    a.status,
                    (today - a.target_date).days
                    if a.target_date and a.status != "Closed" and a.target_date < today
                    else "-",
                ]
                for a in actions
            ],
            [55, 300, 90, 65, 65, 55],
            aligns={5: "RIGHT"},
        ))

    return _render(f"{project.name} — executive pack", story)


# --- programme --------------------------------------------------------------


def programme_pack(db: Session, programme_id: int) -> bytes:
    rollup: ProgrammeRollup | None = programme_rollup(db, programme_id)
    if rollup is None:
        raise ValueError("Programme not found")

    styles = _styles()
    model = StageModel.from_session(db)
    width = PAGE[0] - 2 * MARGIN

    story: list = []
    story += _header(
        rollup.programme_name,
        f"Programme executive pack &nbsp;·&nbsp; {rollup.total_projects} project"
        f"{'' if rollup.total_projects == 1 else 's'} &nbsp;·&nbsp; "
        f"{rollup.total_nodes} node{'' if rollup.total_nodes == 1 else 's'}",
    )
    story.append(_kpi_band([
        ("Health", f"{rollup.health:g}", _tone(rollup.health)),
        ("Projects", str(rollup.total_projects), None),
        ("Nodes", str(rollup.total_nodes), None),
        ("Live", f"{rollup.live_nodes} ({rollup.progress:g}%)", None),
        ("At risk", str(rollup.at_risk_nodes), RISK if rollup.at_risk_nodes else OK),
        ("Delay (wd)", str(rollup.total_delay_days),
         WARN if rollup.total_delay_days else OK),
    ]))
    story.append(Spacer(1, 9))
    story.append(Paragraph(esc(rollup.narrative), styles["body"]))

    if rollup.projects:
        story.append(Paragraph("Projects", styles["h2"]))
        story.append(Paragraph(
            "Health is the mean stage weight across that project's nodes.",
            styles["note"]))
        story.append(Spacer(1, 4))
        story.append(_grid(
            ["Project", "Nodes", "Health", "Live", "At risk", "Delay (wd)",
             "Dominant stage", "Assessment"],
            [
                [
                    Paragraph(esc(p.project_name)[:80], styles["cell"]), p.nodes,
                    f"{p.health:g}", p.live_nodes, p.at_risk_nodes,
                    p.total_delay_days, p.dominant_stage[:34], p.status,
                ]
                for p in sorted(rollup.projects, key=lambda p: p.health)
            ],
            [175, 45, 50, 40, 50, 60, 165, 70],
            aligns={1: "RIGHT", 2: "RIGHT", 3: "RIGHT", 4: "RIGHT", 5: "RIGHT"},
        ))

    if rollup.circles:
        story.append(Paragraph("Circles", styles["h2"]))
        story.append(Paragraph(
            "Counted by node, not by project.", styles["note"]))
        story.append(Spacer(1, 4))
        story.append(_bar_chart(
            [(c.circle, c.health) for c in rollup.circles],
            width=width, label_width=70, tone=_tone,
        ))

    if rollup.stage_mix:
        ordered = {s.name: s.sequence_position for s in model.ordered}
        story.append(Paragraph("Stage mix across the programme", styles["h2"]))
        story.append(Spacer(1, 4))
        story.append(_bar_chart(
            sorted(rollup.stage_mix.items(), key=lambda kv: ordered.get(kv[0], 0)),
            width=width, label_width=190,
        ))

    return _render(f"{rollup.programme_name} — programme pack", story)


# --- circle -----------------------------------------------------------------


def circle_pack(db: Session, project_id: int, circle: str) -> bytes:
    project = db.get(Project, project_id)
    if project is None:
        raise ValueError("Project not found")

    styles = _styles()
    model = StageModel.from_session(db)
    rows = db.execute(
        select(Scope.id, Task.template_task_number, TaskExecution.status)
        .join(TaskExecution, TaskExecution.scope_id == Scope.id)
        .join(Task, Task.id == TaskExecution.task_id)
        .where(Scope.project_id == project_id, Scope.circle == circle)
    ).all()
    if not rows:
        raise ValueError(f"No nodes in circle '{circle}' for this project")

    node_stages = resolve_many(model, rows)
    summary = summarise(node_stages.values())

    at_risk, total_delay = db.execute(
        select(
            func.count(func.distinct(TaskExecution.scope_id)).filter(
                TaskExecution.delay_days > AT_RISK_THRESHOLD_DAYS
            ),
            func.coalesce(func.sum(TaskExecution.delay_days), 0),
        )
        .join(Scope, TaskExecution.scope_id == Scope.id)
        .where(Scope.project_id == project_id, Scope.circle == circle)
    ).one()

    story: list = []
    story += _header(
        f"{circle} — {project.name}",
        f"Circle executive pack &nbsp;·&nbsp; {summary.total_nodes} node"
        f"{'' if summary.total_nodes == 1 else 's'}",
    )
    story.append(_kpi_band([
        ("Health", f"{summary.health:g}", _tone(summary.health)),
        ("Nodes", str(summary.total_nodes), None),
        ("Live", f"{summary.live_nodes} ({summary.progress:g}%)", None),
        ("At risk", str(at_risk or 0), RISK if at_risk else OK),
        ("Delay (wd)", str(int(total_delay or 0)), WARN if total_delay else OK),
        ("Assessment", health_status(summary.health), _tone(summary.health)),
    ]))

    detail = db.execute(
        select(
            Scope.id, Scope.node_id, Scope.facility_name, Scope.num_servers,
            func.coalesce(func.max(TaskExecution.delay_days), 0),
            func.coalesce(func.sum(TaskExecution.delay_days), 0),
        )
        .join(TaskExecution, TaskExecution.scope_id == Scope.id)
        .where(Scope.project_id == project_id, Scope.circle == circle)
        .group_by(Scope.id)
        .order_by(func.max(TaskExecution.delay_days).desc(), Scope.node_id)
    ).all()

    story.append(Paragraph("Nodes", styles["h2"]))
    story.append(Spacer(1, 4))
    story.append(_grid(
        ["Node", "Facility", "Servers", "Stage", "Completed", "Worst delay (wd)", "Total (wd)"],
        [
            [
                node_id, facility[:44], servers,
                node_stages[scope_id].stage_name if scope_id in node_stages else "-",
                f"{node_stages[scope_id].completed_tasks}/{node_stages[scope_id].total_tasks}"
                if scope_id in node_stages else "-",
                int(worst), int(total),
            ]
            for scope_id, node_id, facility, servers, worst, total in detail
        ],
        [85, 175, 55, 175, 70, 90, 70],
        aligns={2: "RIGHT", 4: "RIGHT", 5: "RIGHT", 6: "RIGHT"},
    ))

    return _render(f"{circle} — {project.name}", story)
