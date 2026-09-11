"""Reference data seeded at migration time.

Everything here was a Python constant in the legacy app. It now lives in the
database so an admin can correct a weight, reorder a stage or retune a capacity
rule without a redeploy.

Three corrections are baked into the seed, each flagged in the audit:

1. "Labeling WIP" is present. The legacy STAGE_SEQUENCE omitted it while both
   the weight table and the task map referenced it, so any node sitting at that
   stage disappeared from the governance matrix (H5).

2. "Security Clearance" moves from position 24 to position 17. It is template
   task 39 of 50, with eleven activities after it including all testing, ATP and
   IDC/NOC handover. Ranking it just below RFS meant one completed activity
   could report a node as ~98% done (H6). >>> SEE THE NOTE ON WEIGHTS BELOW -
   this one needs your confirmation.

3. Weights are derived from the template's critical path rather than assigned by
   hand, so they are monotonic with the sequence and mean something concrete:
   the share of the 46-working-day critical path completed on reaching a stage.
"""
from __future__ import annotations

from datetime import date

# ---------------------------------------------------------------------------
# Reporting stages
# ---------------------------------------------------------------------------
# (sequence_position, name, weight, is_terminal)
#
# `weight` = round(critical-path working days elapsed / 46 * 100), smoothed to
# stay non-decreasing along the sequence. The legacy weights were hand-assigned
# and non-monotonic against the real task order - completing task 19 scored 20
# while tasks 20-25, which come later, scored 13-15.
REPORTING_STAGES: list[tuple[int, str, int, bool]] = [
    (1, "Ordering Completed", 2, False),
    (2, "Planning Completed", 4, False),
    (3, "Infra Allocated", 6, False),
    (4, "Material Available", 8, False),
    (5, "Material in Transit", 22, False),
    (6, "Material at Site", 26, False),
    (7, "IP Readiness WIP", 28, False),
    (8, "IP Readiness Completed", 30, False),
    (9, "HW Deployment Completed", 33, False),
    (10, "Cabling WIP", 37, False),
    (11, "Labeling WIP", 39, False),
    (12, "Under HW Configuration", 41, False),
    (13, "HW Configuration completed", 43, False),
    (14, "HW Handover to Application I&C", 45, False),
    (15, "Application Deployment & Integration WIP", 60, False),
    (16, "Integration Readiness", 72, False),
    (17, "Security Clearance", 76, False),
    (18, "Testing Readiness", 78, False),
    (19, "Under Testing", 80, False),
    (20, "Validation & Testing Completed", 87, False),
    (21, "Under ATP", 89, False),
    (22, "ATP Acceptance", 93, False),
    (23, "IDC & NOC Handover WIP", 95, False),
    (24, "IDC & NOC Handover Completed", 97, False),
    (25, "RFS", 99, False),
    (26, "Live", 100, True),
]

# ---------------------------------------------------------------------------
# Template task -> stage
# ---------------------------------------------------------------------------
# Keyed on template_task_number, not on the display name. The legacy map matched
# `task.name` literally and only achieved full coverage because its keys
# reproduced the source typos exactly - "IRM disptatched", "Lebeling",
# "Cabaling Service PO Available", "P2P Availibility", "TOR  HW I & C" with a
# double space. Fixing any spelling silently removed that task from reporting
# (M17). The names below are retained verbatim for display and for matching
# legacy spreadsheets; the *number* is what binds.
#
# (template_task_number, task_name, stage_name)
TASK_STAGE_MAP: list[tuple[int, str, str]] = [
    (1, "H/W Order Completed", "Ordering Completed"),
    (2, "IRM Ordering Completed", "Ordering Completed"),
    (3, "Cabaling Service PO Available", "Ordering Completed"),
    (4, "Application service PO Availibility", "Ordering Completed"),
    (5, "Scope defined and available", "Planning Completed"),
    (6, "Facility Readiness and allocation", "Infra Allocated"),
    (7, "Rack Layout availibility", "Infra Allocated"),
    (8, "Cable length to be available", "Infra Allocated"),
    (9, "Project Initiation", "Planning Completed"),
    (10, "H/W at NWH", "Material Available"),
    (11, "IRM at NWH", "Material Available"),
    (12, "NSIP ID Creation", "Material in Transit"),
    (13, "DMTO/SO1 Creation for HW", "Material in Transit"),
    (14, "HW @SWH", "Material in Transit"),
    (15, "HW @Site", "Material at Site"),
    (16, "IRM disptatched", "Material in Transit"),
    (17, "IRM @SWH", "Material in Transit"),
    (18, "IRM @Site", "Material at Site"),
    (19, "HW Rack-stack & Power ON", "HW Deployment Completed"),
    (20, "P2P Availibility", "IP Readiness WIP"),
    (21, "Final P2P Upload", "IP Readiness WIP"),
    (22, "P2P Approved", "IP Readiness WIP"),
    (23, "ToR Site Survey", "IP Readiness WIP"),
    (24, "TOR  HW I & C", "IP Readiness WIP"),
    (25, "IP readiness", "IP Readiness Completed"),
    (26, "Ilo Reachability check", "HW Deployment Completed"),
    (27, "Server to ToR Cabaling", "Cabling WIP"),
    (28, "Lebeling", "Labeling WIP"),
    (29, "HW HOTO Checklist Imp.", "Under HW Configuration"),
    (30, "SO2 Completion", "HW Configuration completed"),
    (31, "HW Handover to Application Team", "HW Handover to Application I&C"),
    (32, "OS Installation", "Application Deployment & Integration WIP"),
    (33, "Application I & C", "Application Deployment & Integration WIP"),
    (34, "SO3 Submission", "Integration Readiness"),
    (35, "SO4 Completion", "Application Deployment & Integration WIP"),
    (36, "NEID Availibility", "Integration Readiness"),
    (37, "Nw Integration", "Application Deployment & Integration WIP"),
    (38, "OSS Integration", "Application Deployment & Integration WIP"),
    (39, "Security Clearance", "Security Clearance"),
    (40, "NIT Clearance", "Testing Readiness"),
    (41, "Testing Offered", "Under Testing"),
    (42, "Testing Completion", "Validation & Testing Completed"),
    (43, "ATP Offer", "Under ATP"),
    (44, "ATP Acceptance", "ATP Acceptance"),
    (45, "IDC - Node Offer", "IDC & NOC Handover WIP"),
    (46, "IDC -  Node Acceptance", "IDC & NOC Handover WIP"),
    (47, "NOC - Node offer", "IDC & NOC Handover WIP"),
    (48, "NOC - Node Acceptance", "IDC & NOC Handover Completed"),
    (49, "Ready for Service", "RFS"),
    (50, "Go Live", "Live"),
]

# ---------------------------------------------------------------------------
# Scheduling constraints (decision 8 - the two legacy sets merged)
# ---------------------------------------------------------------------------
# dict keys mirror SchedulingConstraint columns.
#
# Where the two sources agreed, the shared value is used. Two genuine conflicts
# are resolved in favour of planner.py - the set that actually produced the live
# schedule - and are flagged in `notes` for confirmation.
SCHEDULING_CONSTRAINTS: list[dict] = [
    {
        "template_task_number": 13,
        "label": "DMTO / SO1 creation",
        "rule_type": "starts_per_day",
        "partition_by": "global",
        "max_count": 10,
        "notes": "planner.py only; constraints.py had no rule for task 13.",
    },
    {
        "template_task_number": 16,
        "label": "IRM dispatch",
        "rule_type": "starts_per_day",
        "partition_by": "global",
        "max_count": 5,
        "notes": "planner.py only.",
    },
    {
        "template_task_number": 20,
        "label": "P2P availability",
        "rule_type": "starts_per_day",
        "partition_by": "global",
        "max_count": 5,
        "notes": "planner.py only.",
    },
    {
        "template_task_number": 40,
        "label": "NIT clearance",
        "rule_type": "starts_per_day",
        "partition_by": "global",
        "max_count": 2,
        "notes": "Both sources agree on 2.",
    },
    {
        "template_task_number": 25,
        "label": "IP readiness - distinct facilities in a rolling window",
        "rule_type": "distinct_in_window",
        "partition_by": "global",
        "count_distinct_by": "facility",
        "max_count": 2,
        "window_working_days": 2,
        "notes": (
            "Both sources agree: cap 2, grouped by facility. The rolling window "
            "was inoperative in the legacy code and is enforced for the first "
            "time here (audit C8) - expect this plan to differ from the old one."
        ),
    },
    {
        "template_task_number": 32,
        "label": "OS installation - global crew cap",
        "rule_type": "concurrent",
        "partition_by": "global",
        "max_count": 3,
        "pool_key": "onsite_install",
        "notes": "CONFLICT: planner.py said 3 global; constraints.py said 4 by circle. Using 3.",
    },
    {
        "template_task_number": 32,
        "label": "OS installation - one per circle",
        "rule_type": "concurrent",
        "partition_by": "circle",
        "max_count": 1,
        "pool_key": "onsite_install",
        "notes": "planner.py. Shares the onsite_install pool with task 33.",
    },
    {
        "template_task_number": 33,
        "label": "Application I&C - global crew cap",
        "rule_type": "concurrent",
        "partition_by": "global",
        "max_count": 3,
        "pool_key": "onsite_install",
        "notes": "Counted against the same crew pool as task 32.",
    },
    {
        "template_task_number": 33,
        "label": "Application I&C - one per circle",
        "rule_type": "concurrent",
        "partition_by": "circle",
        "max_count": 1,
        "pool_key": "onsite_install",
    },
    {
        "template_task_number": 41,
        "label": "Testing offered - one node per circle",
        "rule_type": "concurrent",
        "partition_by": "circle",
        "max_count": 1,
        "pool_key": "testing",
        "notes": (
            "CONFLICT: planner.py said 1 per circle; constraints.py said "
            "unlimited, grouped by facility. Using 1 per circle."
        ),
    },
    {
        "template_task_number": 42,
        "label": "Testing completion - one node per circle",
        "rule_type": "concurrent",
        "partition_by": "circle",
        "max_count": 1,
        "pool_key": "testing",
    },
]

# ---------------------------------------------------------------------------
# Holidays
# ---------------------------------------------------------------------------
# The legacy set was eight Maharashtra 2026 dates applied to all 20 circles and
# empty outside 2026 (M16). National holidays are seeded with circle=None; the
# Maharashtra-specific entry is scoped to MH. Circles operate across India, so
# this table needs extending per circle before planning into 2027.
HOLIDAYS: list[tuple[date, str | None, str]] = [
    (date(2026, 1, 26), None, "Republic Day"),
    (date(2026, 5, 1), "MH", "Maharashtra Day"),
    (date(2026, 8, 15), None, "Independence Day"),
    (date(2026, 9, 14), None, "Ganesh Chaturthi"),
    (date(2026, 10, 2), None, "Gandhi Jayanti"),
    (date(2026, 10, 20), None, "Dussehra"),
    (date(2026, 11, 9), None, "Diwali"),
    (date(2026, 11, 10), None, "Diwali - Bali Pratipada"),
    (date(2026, 11, 11), None, "Diwali - Bhai Dooj"),
    (date(2027, 1, 26), None, "Republic Day"),
    (date(2027, 8, 15), None, "Independence Day"),
    (date(2027, 10, 2), None, "Gandhi Jayanti"),
]
