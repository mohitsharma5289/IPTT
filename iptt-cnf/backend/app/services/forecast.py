"""Forecasting: where each node lands if it carries on as it has been.

This reproduces the legacy `/project/{id}/forecast-dashboard` calculation so the
figures can be compared like for like, with the corrections the rest of the
rebuild applies:

  * The projection runs over **working days**, not calendar days. The legacy
    version did `date.today() + timedelta(days=forecast_remaining)`, which
    silently added weekends and holidays to a duration measured in working
    days - inflating every forecast by roughly 40% and by more across a
    festival period. Decision 1 made working days the unit everywhere else;
    applying it here too is what makes the forecast comparable with the delay
    figures on the same screen.

  * Elapsed time per activity is measured in working days for the same reason,
    so the performance factor is a ratio of like to like. The legacy version
    divided calendar-day elapsed by working-day planned, which made every node
    that spanned a weekend look slower than it was.

  * Nodes with no execution rows are reported as unstarted rather than skipped.
    The legacy loop `continue`d past them, so a project where nothing had begun
    produced an empty dashboard rather than one saying nothing had begun.

Everything else - the clamp on the performance factor, the risk bands, the
confidence formula, the five delay drivers - is the legacy behaviour kept
deliberately, because the point of this screen is continuity of reporting.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.calendar import WorkingCalendar
from app.models import Project, Scope, Task, TaskExecution
from app.models.enums import ExecutionStatus

#: The legacy clamp. A node that has taken three times its planned effort is
#: already so far gone that a larger multiplier adds noise, not information.
MIN_PERFORMANCE_FACTOR = 1.0
MAX_PERFORMANCE_FACTOR = 3.0

#: Legacy risk bands, in working days of forecast slip.
RISK_LOW_MAX = 5
RISK_MEDIUM_MAX = 15

#: A node more than this far adrift is called out separately as critical.
CRITICAL_DELAY_DAYS = 30


@dataclass(slots=True)
class DelayDriver:
    activity: str
    remaining_days: int


@dataclass(slots=True)
class NodeForecast:
    scope_id: int
    node_id: str
    facility_name: str
    circle: str
    progress: float
    performance_factor: float
    remaining_days: int
    forecast_remaining_days: int
    forecast_go_live: date | None
    planned_finish: date | None
    forecast_delay_days: int
    risk: str
    confidence: int
    confidence_band: str
    started: bool
    delay_drivers: list[DelayDriver] = field(default_factory=list)


@dataclass(slots=True)
class CircleForecast:
    circle: str
    nodes: int
    average_delay_days: float
    average_progress: float
    forecast_go_live: date | None
    high_risk: int


@dataclass(slots=True)
class ProjectForecast:
    project_id: int
    project_name: str
    total_nodes: int
    started_nodes: int
    high_risk: int
    medium_risk: int
    low_risk: int
    critical_nodes: int
    forecast_go_live: date | None
    nodes: list[NodeForecast] = field(default_factory=list)
    circles: list[CircleForecast] = field(default_factory=list)
    insights: list[str] = field(default_factory=list)


def _risk_band(forecast_delay: int) -> str:
    if forecast_delay <= RISK_LOW_MAX:
        return "Low"
    if forecast_delay <= RISK_MEDIUM_MAX:
        return "Medium"
    return "High"


def _confidence(forecast_delay: int, pending: int, progress: float) -> tuple[int, str]:
    """The legacy weighting, unchanged.

    It is a heuristic, not a probability - named "confidence" because that is
    what the screen has always called it. Anchored at 100 and pulled down by
    slip and by how much work is left, pushed up by progress already made.
    """
    score = 100 - forecast_delay * 0.5 - pending * 0.3 + progress * 0.2
    score = max(20, min(100, round(score)))
    band = "High" if score >= 85 else "Medium" if score >= 60 else "Low"
    return int(score), band


def project_forecast(db: Session, project_id: int) -> ProjectForecast | None:
    project = db.get(Project, project_id)
    if project is None:
        return None

    scopes = list(
        db.scalars(
            select(Scope).where(Scope.project_id == project_id).order_by(Scope.node_id)
        ).all()
    )
    circles = sorted({s.circle for s in scopes})
    calendar = WorkingCalendar.from_session(db, circles)
    today = date.today()

    rows = db.execute(
        select(
            TaskExecution.scope_id,
            TaskExecution.status,
            TaskExecution.actual_start,
            TaskExecution.actual_finish,
            Task.name,
            Task.duration_days,
            Task.planned_finish,
        )
        .join(Task, Task.id == TaskExecution.task_id)
        .where(TaskExecution.project_id == project_id)
    ).all()

    by_scope: dict[int, list] = {}
    for row in rows:
        by_scope.setdefault(row[0], []).append(row)

    forecasts: list[NodeForecast] = []
    for scope in scopes:
        activities = by_scope.get(scope.id, [])
        if not activities:
            # Unstarted rather than invisible: the legacy screen dropped these.
            forecasts.append(
                NodeForecast(
                    scope_id=scope.id,
                    node_id=scope.node_id,
                    facility_name=scope.facility_name,
                    circle=scope.circle,
                    progress=0.0,
                    performance_factor=1.0,
                    remaining_days=0,
                    forecast_remaining_days=0,
                    forecast_go_live=None,
                    planned_finish=None,
                    forecast_delay_days=0,
                    risk="Low",
                    confidence=20,
                    confidence_band="Low",
                    started=False,
                )
            )
            continue

        total_duration = sum(a[5] or 0 for a in activities)
        completed_duration = sum(
            a[5] or 0 for a in activities if a[1] == ExecutionStatus.COMPLETED
        )
        progress = (
            round(completed_duration / total_duration * 100, 1) if total_duration else 0.0
        )

        planned_elapsed = 0
        actual_elapsed = 0
        for _sid, _status, actual_start, actual_finish, _name, duration, _pf in activities:
            planned_elapsed += duration or 0
            if actual_start:
                end = actual_finish or today
                # Working days, to match the unit `duration_days` is measured in.
                actual_elapsed += max(
                    0, calendar.working_days_between(actual_start, end, scope.circle)
                )

        factor = (actual_elapsed / planned_elapsed) if planned_elapsed else 1.0
        factor = max(MIN_PERFORMANCE_FACTOR, min(round(factor, 2), MAX_PERFORMANCE_FACTOR))

        open_activities = [a for a in activities if a[1] != ExecutionStatus.COMPLETED]
        remaining = sum(a[5] or 0 for a in open_activities)
        forecast_remaining = round(remaining * factor)

        go_live = (
            calendar.add_working_days(today, forecast_remaining, scope.circle)
            if open_activities
            else max((a[3] for a in activities if a[3]), default=None)
        )

        planned_finish = max((a[6] for a in activities if a[6]), default=None)
        forecast_delay = 0
        if go_live and planned_finish and go_live > planned_finish:
            forecast_delay = calendar.working_days_between(
                planned_finish, go_live, scope.circle
            )

        confidence, band = _confidence(forecast_delay, len(open_activities), progress)
        drivers = sorted(
            (DelayDriver(activity=a[4], remaining_days=a[5] or 0) for a in open_activities),
            key=lambda d: d.remaining_days,
            reverse=True,
        )[:5]

        forecasts.append(
            NodeForecast(
                scope_id=scope.id,
                node_id=scope.node_id,
                facility_name=scope.facility_name,
                circle=scope.circle,
                progress=progress,
                performance_factor=factor,
                remaining_days=remaining,
                forecast_remaining_days=forecast_remaining,
                forecast_go_live=go_live,
                planned_finish=planned_finish,
                forecast_delay_days=forecast_delay,
                risk=_risk_band(forecast_delay),
                confidence=confidence,
                confidence_band=band,
                started=any(a[2] for a in activities),
                delay_drivers=drivers,
            )
        )

    forecasts.sort(key=lambda f: (f.forecast_delay_days, f.progress), reverse=True)

    circle_rows: list[CircleForecast] = []
    for circle in circles:
        members = [f for f in forecasts if f.circle == circle]
        if not members:
            continue
        dates = [f.forecast_go_live for f in members if f.forecast_go_live]
        circle_rows.append(
            CircleForecast(
                circle=circle,
                nodes=len(members),
                average_delay_days=round(
                    sum(f.forecast_delay_days for f in members) / len(members), 1
                ),
                average_progress=round(
                    sum(f.progress for f in members) / len(members), 1
                ),
                forecast_go_live=max(dates) if dates else None,
                high_risk=sum(1 for f in members if f.risk == "High"),
            )
        )
    circle_rows.sort(key=lambda c: c.average_delay_days, reverse=True)

    started = [f for f in forecasts if f.started]
    all_dates = [f.forecast_go_live for f in forecasts if f.forecast_go_live]
    high = sum(1 for f in forecasts if f.risk == "High")
    medium = sum(1 for f in forecasts if f.risk == "Medium")
    low = sum(1 for f in forecasts if f.risk == "Low")
    critical = sum(1 for f in forecasts if f.forecast_delay_days > CRITICAL_DELAY_DAYS)

    return ProjectForecast(
        project_id=project_id,
        project_name=project.name,
        total_nodes=len(forecasts),
        started_nodes=len(started),
        high_risk=high,
        medium_risk=medium,
        low_risk=low,
        critical_nodes=critical,
        forecast_go_live=max(all_dates) if all_dates else None,
        nodes=forecasts,
        circles=circle_rows,
        insights=_insights(forecasts, circle_rows, critical),
    )


def _insights(
    nodes: list[NodeForecast], circles: list[CircleForecast], critical: int
) -> list[str]:
    """Plain sentences a reader can act on, rather than a wall of figures."""
    if not nodes:
        return ["This project has no nodes in scope yet."]

    started = [n for n in nodes if n.started]
    if not started:
        return ["No work has been recorded yet, so there is nothing to forecast from."]

    out: list[str] = []
    worst = nodes[0]
    if worst.forecast_delay_days > 0:
        out.append(
            f"{worst.node_id} at {worst.facility_name} is the furthest adrift, "
            f"forecast {worst.forecast_delay_days} working days late."
        )
    else:
        out.append("No node is currently forecast to finish late.")

    if circles and circles[0].average_delay_days > 0:
        weakest = circles[0]
        out.append(
            f"{weakest.circle} is the weakest circle, averaging "
            f"{weakest.average_delay_days} working days of forecast slip across "
            f"{weakest.nodes} node{'' if weakest.nodes == 1 else 's'}."
        )

    if critical:
        out.append(
            f"{critical} node{'' if critical == 1 else 's'} "
            f"{'is' if critical == 1 else 'are'} more than {CRITICAL_DELAY_DAYS} "
            "working days adrift and will not recover without intervention."
        )

    blocking = Counter(
        driver.activity for node in started for driver in node.delay_drivers
    )
    if blocking:
        activity, count = blocking.most_common(1)[0]
        out.append(
            f"'{activity}' is outstanding on {count} "
            f"node{'' if count == 1 else 's'} - the most common blocker."
        )

    return out
