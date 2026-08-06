"""Where a number came from, and who gets to overrule whom.

v1 could say one honest thing about heights: a floor plan states none of them,
so all of them are yours. That is true of a floor *plan* and false of a drawing
*set*. A section states a slab thickness, an elevation states a floor level, a
beam schedule states a beam depth. As readers for those sheets arrive, some of
the numbers on the heights page stop being guesses — and the interface has to
be able to say which, or the improvement is invisible and untrustworthy.

The number itself must not change shape when that happens. :mod:`build3d` wants
a float and should go on wanting a float, so provenance travels *beside* the
value rather than replacing it: a reader emits :class:`Reading` s, :func:`resolve`
picks one winner per field, writes the plain float exactly where it always went,
and files the losers where they can be shown instead of averaged away.

Precedence is ``user > measured > derived > default``. A number you typed is
never overwritten by a later reading, because you may be correcting the drawing;
the reading is kept beside it so the disagreement stays visible.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, Literal

from pydantic import BaseModel, Field

from .units import fmt_ft

if TYPE_CHECKING:  # pragma: no cover - import cycle: models imports Quantity
    from .models import BuildParams

Source = Literal["measured", "derived", "user", "default"]
Confidence = Literal["high", "medium", "low"]

#: who beats whom when two readers answer for the same field
PRIORITY: dict[str, int] = {"default": 0, "derived": 1, "measured": 2, "user": 3}
_CONF: dict[str, int] = {"low": 0, "medium": 1, "high": 2}

SOURCE_LABEL: dict[str, str] = {
    "measured": "measured",
    "derived": "derived",
    "user": "yours",
    "default": "assumed",
}

#: heights that live on one storey
LEVEL_FIELDS = (
    "floor_to_floor_ft",
    "wall_height_ft",
    "window_sill_ft",
    "window_head_ft",
    "door_head_ft",
    "slab_thickness_ft",
    "ffl_ft",
)
#: heights that belong to the building as a whole
BUILDING_FIELDS = (
    "plinth_ft",
    "parapet_ft",
    "parapet_thickness_ft",
    "railing_ft",
    "roof_slab_thickness_ft",
)

#: two readings this close are the same reading, not a disagreement
SAME_FT = 0.01


class Quantity(BaseModel):
    """A length, and the story of how it got here."""

    ft: float
    source: Source = "default"
    method: str = ""  # room_labels_pooled | elevation_ffl | tie_beam_schedule | ...
    confidence: Confidence = "low"
    evidence: str = ""  # the literal text or measurement that decided it
    sheet_id: str | None = None
    samples: int = 0
    spread_pct: float = 0.0
    #: readings that lost, kept verbatim. A conflict is reported, never averaged.
    alternatives: list[str] = Field(default_factory=list)

    def describe(self) -> str:
        """One line a person can read, e.g. ``10'-6" — measured on 03-section``."""
        bits = [f"{fmt_ft(self.ft)} — {SOURCE_LABEL.get(self.source, self.source)}"]
        if self.method:
            bits.append(f"by {self.method}")
        if self.sheet_id:
            bits.append(f"on {self.sheet_id}")
        if self.evidence:
            bits.append(f"({self.evidence})")
        return " ".join(bits)


class Reading(BaseModel):
    """One reader's answer for one field.

    ``key`` is the field's address: ``building.plinth_ft`` for the whole
    building, ``level.1.floor_to_floor_ft`` for a single storey.
    """

    key: str
    q: Quantity


# --------------------------------------------------------------------------- #
# addressing
# --------------------------------------------------------------------------- #
def building_key(field: str) -> str:
    return f"building.{field}"


def level_key(level: int, field: str) -> str:
    return f"level.{level}.{field}"


def _target(params: "BuildParams", key: str):
    """Resolve ``key`` to the object holding it and the attribute name."""
    parts = key.split(".")
    if len(parts) == 2 and parts[0] == "building" and parts[1] in BUILDING_FIELDS:
        return params, parts[1]
    if len(parts) == 3 and parts[0] == "level" and parts[2] in LEVEL_FIELDS:
        try:
            lp = params.level(int(parts[1]))
        except ValueError:
            return None
        if lp is not None:
            return lp, parts[2]
    return None


def _rank(r: Reading) -> tuple[int, int, int]:
    return (
        PRIORITY.get(r.q.source, 0),
        _CONF.get(r.q.confidence, 0),
        r.q.samples,
    )


# --------------------------------------------------------------------------- #
# resolution
# --------------------------------------------------------------------------- #
def resolve(params: "BuildParams", readings: Iterable[Reading]) -> list[str]:
    """Apply ``readings`` to ``params`` in place; return notes worth showing.

    A field the user has pinned is never written. Readings for it are still
    recorded as alternatives, because "the drawing says 9'-6" and you said 10'"
    is exactly the kind of thing a person needs to see.
    """
    notes: list[str] = []
    grouped: dict[str, list[Reading]] = {}
    for r in readings:
        grouped.setdefault(r.key, []).append(r)

    for key in sorted(grouped):
        group = grouped[key]
        found = _target(params, key)
        if found is None:
            notes.append(f"Reading for unknown field “{key}” ignored.")
            continue
        obj, field = found
        best = max(group, key=_rank)

        if field in obj.user_set:
            held = obj.provenance.get(field)
            for r in group:
                if held is None or abs(r.q.ft - getattr(obj, field, r.q.ft)) > SAME_FT:
                    line = r.q.describe()
                    if held is not None and line not in held.alternatives:
                        held.alternatives.append(line)
                    notes.append(
                        f"{_pretty(key)}: kept your {fmt_ft(getattr(obj, field))} "
                        f"over {line}."
                    )
            continue

        q = best.q.model_copy(deep=True)
        for r in group:
            if r is best:
                continue
            if abs(r.q.ft - q.ft) > SAME_FT:
                line = r.q.describe()
                if line not in q.alternatives:
                    q.alternatives.append(line)
        setattr(obj, field, q.ft)
        obj.provenance[field] = q
        if q.alternatives:
            notes.append(
                f"{_pretty(key)}: took {q.describe()}; also read "
                + "; ".join(q.alternatives)
                + ". Nothing was averaged — check which sheet is right."
            )
    return notes


def _pretty(key: str) -> str:
    parts = key.split(".")
    field = parts[-1].removesuffix("_ft").replace("_", " ")
    if parts[0] == "level":
        return f"level {parts[1]} {field}"
    return field


# --------------------------------------------------------------------------- #
# seeding and pinning
# --------------------------------------------------------------------------- #
def seed_defaults(params: "BuildParams") -> None:
    """Give every unattributed height a ``default`` provenance.

    Called once when a job's parameters are created, so that the interface can
    say "assumed" about a number from the very first render rather than showing
    a blank where the story should be.
    """
    for field in BUILDING_FIELDS:
        _seed(params, field, "the tool's default")
    for lp in params.levels:
        for field in LEVEL_FIELDS:
            _seed(lp, field, "the tool's default")


def _seed(obj, field: str, why: str) -> None:
    if field in obj.provenance:
        return
    value = getattr(obj, field, None)
    if value is None:  # None means "inherit"; there is no number to attribute
        return
    obj.provenance[field] = Quantity(
        ft=float(value), source="default", method="default", evidence=why
    )


def pin_user_edits(old: "BuildParams", new: "BuildParams") -> list[str]:
    """Mark every height that changed between ``old`` and ``new`` as the user's.

    Done by diffing on the server rather than trusting the browser to say so,
    which keeps API clients honest too and means the pin cannot drift out of
    step with the value it is pinning.
    """
    pinned: list[str] = []
    for field in BUILDING_FIELDS:
        if _pin_if_changed(old, new, field, building_key(field)):
            pinned.append(building_key(field))
    by_level = {lp.level: lp for lp in old.levels}
    for lp in new.levels:
        prev = by_level.get(lp.level)
        if prev is None:
            continue
        for field in LEVEL_FIELDS:
            if _pin_if_changed(prev, lp, field, level_key(lp.level, field)):
                pinned.append(level_key(lp.level, field))
    return pinned


def _pin_if_changed(old, new, field: str, key: str) -> bool:
    a, b = getattr(old, field, None), getattr(new, field, None)
    if a == b:
        # carry the story forward: the payload the browser sends back does not
        # contain it, so without this every save would forget where a number came from
        if field in getattr(old, "provenance", {}) and field not in new.provenance:
            new.provenance[field] = old.provenance[field].model_copy(deep=True)
        if field in getattr(old, "user_set", []) and field not in new.user_set:
            new.user_set.append(field)
        return False
    if b is None:
        new.provenance.pop(field, None)
        if field in new.user_set:
            new.user_set.remove(field)
        return False
    if field not in new.user_set:
        new.user_set.append(field)
    new.provenance[field] = Quantity(
        ft=float(b),
        source="user",
        method="typed",
        confidence="high",
        evidence=f"you changed this from {fmt_ft(float(a))}" if a is not None else "you set this",
    )
    return True


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #
def rows(params: "BuildParams") -> list[tuple[str, str, str]]:
    """``(what, value, where it came from)`` for every height, for the README."""
    out: list[tuple[str, str, str]] = []
    for field in BUILDING_FIELDS:
        q = params.provenance.get(field)
        if q is not None:
            out.append((_pretty(building_key(field)), fmt_ft(q.ft), _story(q)))
    for lp in params.levels:
        for field in LEVEL_FIELDS:
            q = lp.provenance.get(field)
            if q is not None:
                out.append(
                    (f"{lp.name} — {field.removesuffix('_ft').replace('_', ' ')}",
                     fmt_ft(q.ft),
                     _story(q))
                )
    return out


def _story(q: Quantity) -> str:
    bits = [SOURCE_LABEL.get(q.source, q.source)]
    if q.source != "default" and q.method:
        bits.append(f"({q.method})")
    if q.sheet_id:
        bits.append(f"from {q.sheet_id}")
    if q.alternatives:
        bits.append("— also read " + "; ".join(q.alternatives))
    return " ".join(bits)
