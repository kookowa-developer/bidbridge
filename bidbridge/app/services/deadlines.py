"""Deadline manager: expiring passport documents, tender deadlines and overdue tasks."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from app.domain import DOC_LABELS_UZ, DocKind
from app.models import Company

Severity = Literal["critical", "warning", "info"]


@dataclass
class Alert:
    severity: Severity
    kind: str  # document_expiry | tender_deadline | task_overdue
    message: str
    due: date | None
    ref_id: int | None = None


def build_alerts(company: Company, today: date, warn_days: int = 30) -> list[Alert]:
    alerts: list[Alert] = []

    for d in company.documents:
        if not d.expires_date:
            continue
        left = (d.expires_date - today).days
        try:
            label = DOC_LABELS_UZ[DocKind(d.kind)]
        except ValueError:
            label = d.kind
        if left < 0:
            alerts.append(Alert("critical", "document_expiry", f"{label} ({d.title}) muddati {-left} kun oldin tugagan.", d.expires_date, d.id))
        elif left <= warn_days:
            alerts.append(Alert("warning", "document_expiry", f"{label} ({d.title}) muddati {left} kundan so'ng tugaydi.", d.expires_date, d.id))

    for t in company.tenders:
        if t.deadline:
            left = (t.deadline - today).days
            if 0 <= left <= 7:
                alerts.append(Alert("critical" if left <= 3 else "warning", "tender_deadline",
                                    f"'{t.title}' topshirish muddatiga {left} kun qoldi.", t.deadline, t.id))
            elif left > 7 and left <= 30:
                alerts.append(Alert("info", "tender_deadline", f"'{t.title}': muddatga {left} kun.", t.deadline, t.id))
        for task in t.tasks:
            if not task.done and task.due and task.due < today:
                alerts.append(Alert("critical", "task_overdue", f"Vazifa muddati o'tgan: {task.title} ('{t.title}').", task.due, task.id))

    order = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: (order[a.severity], a.due or date.max))
    return alerts
