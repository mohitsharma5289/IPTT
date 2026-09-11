"""Security Clearance: weight 98 -> 76, position kept late in the ladder.

Confirmed by Mohit, 11 Sep 2026: change the weight, not the position.

The stage therefore sits between "IDC & NOC Handover Completed" and "RFS", where
the legacy sequence had it, and scores 76 instead of 98.

Note for whoever reads this later. Under decision 3 a node's stage is the
furthest *position* it has reached, so position and weight now disagree in a way
that has a visible effect: "Security Clearance" sits at position 24 but scores
lower (76) than positions 21-23 (89, 93, 95, 97). Two consequences follow, and
both are intended to be temporary:

  * A node that completes template task 39 jumps to position 24 and stays there
    until RFS, because everything between - testing, ATP, IDC/NOC handover - is
    at an earlier position and cannot advance it.
  * A node that has passed ATP Acceptance but not clearance scores 93; one that
    has passed both scores 76.

The underlying disagreement is that the stage is modelled as the last gate
before RFS while template task 39 places the activity immediately after network
integration, with eleven activities behind it. Resolving it properly means
renumbering the task template so the clearance activity really is late, at which
point position and weight agree again and this note can go.

Revision ID: 0003_sec_clearance
Revises: 0002_seed
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0003_sec_clearance"
down_revision: str | None = "0002_seed"
branch_labels = None
depends_on = None

STAGE = "Security Clearance"

# Ladder from position 15 down, as it must end up.
TARGET_ORDER = [
    (15, "Application Deployment & Integration WIP", 60),
    (16, "Integration Readiness", 72),
    (17, "Testing Readiness", 78),
    (18, "Under Testing", 80),
    (19, "Validation & Testing Completed", 87),
    (20, "Under ATP", 89),
    (21, "ATP Acceptance", 93),
    (22, "IDC & NOC Handover WIP", 95),
    (23, "IDC & NOC Handover Completed", 97),
    (24, STAGE, 76),
    (25, "RFS", 99),
    (26, "Live", 100),
]

PREVIOUS_ORDER = [
    (15, "Application Deployment & Integration WIP", 60),
    (16, "Integration Readiness", 72),
    (17, STAGE, 76),
    (18, "Testing Readiness", 78),
    (19, "Under Testing", 80),
    (20, "Validation & Testing Completed", 87),
    (21, "Under ATP", 89),
    (22, "ATP Acceptance", 93),
    (23, "IDC & NOC Handover WIP", 95),
    (24, "IDC & NOC Handover Completed", 97),
    (25, "RFS", 99),
    (26, "Live", 100),
]


def _apply(order: list[tuple[int, str, int]]) -> None:
    bind = op.get_bind()
    # sequence_position carries a UNIQUE constraint, so park the affected rows
    # out of range before writing the final values.
    bind.execute(
        sa.text(
            "UPDATE reporting_stage SET sequence_position = sequence_position + 1000 "
            "WHERE sequence_position >= 15"
        )
    )
    for position, name, weight in order:
        bind.execute(
            sa.text(
                "UPDATE reporting_stage SET sequence_position = :pos, weight = :weight "
                "WHERE name = :name"
            ),
            {"pos": position, "weight": weight, "name": name},
        )


def upgrade() -> None:
    _apply(TARGET_ORDER)


def downgrade() -> None:
    _apply(PREVIOUS_ORDER)
