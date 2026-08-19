"""Type what you want the front to do, and it does it.

The composition in :mod:`app.facade` is driven entirely by
:class:`~app.models.FacadeParams` — an arrangement, a handful of switches and a
dozen dimensions. That is a *small, closed* vocabulary, and it is what makes a
chat over it tractable without a model behind it: "make the screen wider" has
exactly one meaning here, because there is exactly one screen and one width.

So this is a parser, not a language model. It reads an instruction against the
real parameter names, applies bounded edits, and says what it changed. Three
consequences worth being clear about:

* it runs offline, in-process, with no API key and no network;
* it can be tested exhaustively, which a model cannot;
* it understands its own vocabulary and nothing else, and says so plainly
  rather than guessing — a wrong guess silently redesigns the elevation.

:func:`interpret` is the whole seam. Anything that can turn a sentence and a
:class:`FacadeParams` into a :class:`Reply` can replace it, a model included,
without the API or the UI knowing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import FacadeParams
from .units import fmt_ft, parse_length_ft


# --------------------------------------------------------------------------- #
# what can be said
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Control:
    """One thing on the facade the instruction can name.

    ``flag`` turns it on and off; ``size`` is the dimension "wider" and "6 feet"
    act on. ``lo``/``hi`` are what the geometry can actually take — a screen
    forty feet wide is not a screen, and clamping is kinder than composing
    something absurd and letting the user wonder what happened.
    """

    name: str
    words: tuple[str, ...]
    flag: str | None = None
    size: str | None = None
    lo: float = 0.0
    hi: float = 0.0
    #: the dimension's own noun, for the reply — "wider" reads better than
    #: "changed screen_width_ft"
    measure: str = "size"
    #: True when the element is a row of things, so "more" means more of them
    #: (a tighter pitch) rather than a bigger one
    countable_pitch: str | None = None


CONTROLS: tuple[Control, ...] = (
    Control(
        "screen", ("screen", "fins", "fin", "blades", "blade", "louvres",
                   "louvers", "slats", "battens"),
        flag="fins", size="screen_width_ft", lo=1.0, hi=20.0, measure="width",
        countable_pitch="fin_pitch_ft",
    ),
    Control(
        "clad bay", ("bay", "mass", "clad bay", "projecting bay", "block",
                     "box", "cladding", "clad"),
        flag="mass", size="mass_projection_ft", lo=0.2, hi=4.0,
        measure="projection",
    ),
    Control(
        "balcony", ("balcony", "recess", "void", "terrace", "loggia"),
        flag="void", size="void_depth_ft", lo=0.3, hi=5.0, measure="depth",
    ),
    Control(
        "floor bands", ("band", "bands", "string course", "floor band",
                        "banding"),
        flag="bands", size="band_height_ft", lo=0.2, hi=3.0, measure="height",
    ),
    Control(
        "box frames", ("frame", "frames", "box frame", "box frames",
                       "surround", "surrounds"),
        flag="box_frames", size="frame_margin_ft", lo=0.1, hi=2.0,
        measure="margin",
    ),
    Control(
        "canopy", ("canopy", "overhang", "roof canopy", "eave", "eaves",
                   "brise soleil"),
        flag="canopy", size="canopy_projection_ft", lo=0.3, hi=8.0,
        measure="projection",
    ),
)

#: dimensions that are not an element's headline size but can still be named
EXTRAS: dict[str, tuple[str, float, float, str]] = {
    # phrase                 field              lo    hi   noun
    "fin spacing": ("fin_pitch_ft", 0.3, 3.0, "spacing"),
    "fin pitch": ("fin_pitch_ft", 0.3, 3.0, "spacing"),
    "fin width": ("fin_width_ft", 0.15, 1.5, "width"),
    "canopy thickness": ("canopy_thickness_ft", 0.2, 3.0, "thickness"),
    "canopy overhang": ("canopy_side_ft", 0.0, 6.0, "overhang"),
}

ARRANGEMENT_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("quiet", ("quiet", "quieter", "simple", "simpler", "plain", "plainer",
               "minimal", "restrained", "calm", "clean")),
    ("framed", ("framed", "symmetrical", "symmetric", "mirrored", "formal",
                "both sides", "either side of the centre")),
    ("layered", ("layered", "asymmetric", "asymmetrical", "dynamic")),
)

ON_WORDS = ("add", "put", "include", "give", "want", "need", "show", "bring",
            "restore", "enable", "turn on", "with", "back")
OFF_WORDS = ("remove", "delete", "drop", "without", "hide", "lose", "off",
             "get rid of", "take out", "take off", "no ", "not ", "don't",
             "dont", "disable", "kill", "clear")

BIGGER = ("wider", "bigger", "larger", "deeper", "thicker", "taller",
          "longer", "increase", "extend", "bolder", "stronger", "heavier",
          "more prominent", "stand out", "proud", "double", "twice")
SMALLER = ("narrower", "smaller", "thinner", "shallower", "shorter",
           "reduce", "subtle", "subtler", "tighter", "lighter", "slimmer",
           "pull back", "less prominent", "flatter", "half", "halve")

#: how far a comparative moves the number
STEPS: tuple[tuple[tuple[str, ...], float], ...] = (
    (("a bit", "a little", "slightly", "a touch", "marginally"), 1.15),
    (("much", "a lot", "way", "far", "significantly", "considerably"), 1.8),
    (("double", "twice"), 2.0),
    (("half", "halve"), 0.5),
)
DEFAULT_STEP = 1.35

RESET_WORDS = ("reset", "start again", "start over", "default", "defaults",
               "from scratch", "revert")


# --------------------------------------------------------------------------- #
# what comes back
# --------------------------------------------------------------------------- #
@dataclass
class Change:
    field: str
    before: object
    after: object
    said: str


@dataclass
class Reply:
    params: FacadeParams
    changes: list[Change] = field(default_factory=list)
    message: str = ""
    understood: bool = False

    def as_dict(self) -> dict:
        return {
            "understood": self.understood,
            "message": self.message,
            "changes": [
                {"field": c.field, "before": c.before, "after": c.after,
                 "said": c.said}
                for c in self.changes
            ],
        }


HELP = (
    "I can change the parts the composition is made of. Try: "
    "“make the screen wider”, “no canopy”, "
    "“put the screen on the left”, “clad bay 2 feet deep”, "
    "“more fins”, “keep it simple”, or “reset”."
)


# --------------------------------------------------------------------------- #
# reading an instruction
# --------------------------------------------------------------------------- #
def interpret(text: str, params: FacadeParams) -> Reply:
    """Read one message and return the parameters it asks for.

    The input is never trusted to be a command: anything not in the vocabulary
    comes back ``understood=False`` with the parameters untouched, because
    quietly doing nothing and quietly doing the wrong thing look identical from
    the outside and one of them ruins an elevation.
    """
    out = params.model_copy(deep=True)
    said = (text or "").strip()
    if not said:
        return Reply(out, message=HELP)

    low = _normalise(said)
    if any(w in low for w in RESET_WORDS) and len(low) < 40:
        fresh = FacadeParams(enabled=out.enabled)
        return Reply(
            fresh, [Change("*", "edited", "defaults", "back to the default composition")],
            "Reset the fasād to the composition the plans give on their own.",
            True,
        )

    changes: list[Change] = []
    last: Control | None = None
    for clause in _clauses(low):
        got, last = _clause(clause, out, last)
        changes += got

    if not changes:
        return Reply(out, message="I did not follow that. " + HELP)
    return Reply(out, changes, _sentence(changes), True)


def _normalise(text: str) -> str:
    text = text.lower().replace("’", "'").replace("”", '"')
    return re.sub(r"\s+", " ", text).strip()


def _clauses(low: str) -> list[str]:
    """Split a message into instructions, keeping short ones together.

    "remove the canopy and make the screen wider" is two instructions; "a bit
    wider and deeper" is one element with two, which is why the caller carries
    the last element it saw across the split.
    """
    parts = re.split(r"\s*(?:,|;|\.|\band\b|\balso\b|\bthen\b)\s*", low)
    return [p for p in (p.strip() for p in parts) if p]


def _clause(clause: str, fp: FacadeParams,
            last: Control | None) -> tuple[list[Change], Control | None]:
    changes: list[Change] = []

    # an arrangement is a whole-composition word and wins over anything else
    for name, words in ARRANGEMENT_WORDS:
        if any(_has(clause, w) for w in words):
            if fp.arrangement != name:
                changes.append(Change("arrangement", fp.arrangement, name,
                                      f"switched to the {name} arrangement"))
                fp.arrangement = name
            return changes, last

    # which side the screen stands
    side = _side(clause)
    if side and (_names(clause, _control("screen")) or last
                 and last.name == "screen" or "side" in clause):
        if fp.screen_side != side:
            where = "wherever there is room" if side == "auto" else f"the {side}"
            changes.append(Change("screen_side", fp.screen_side, side,
                                  f"moved the screen to {where}"))
            fp.screen_side = side
        return changes, _control("screen")

    # a named extra dimension: "fin spacing", "canopy thickness"
    for phrase, (fieldname, lo, hi, noun) in EXTRAS.items():
        if phrase in clause:
            c = _number(clause, fp, fieldname, lo, hi, noun, phrase)
            if c:
                changes.append(c)
            return changes, last

    control = _find(clause) or last
    if control is None:
        return changes, last

    # on or off
    want = _switch(clause)
    if want is not None and control.flag:
        now = getattr(fp, control.flag)
        if now != want:
            verb = "added" if want else "removed"
            changes.append(Change(control.flag, now, want,
                                  f"{verb} the {control.name}"))
            setattr(fp, control.flag, want)
        return changes, control

    # "more fins" means more of them, which is a tighter pitch, not a wider one
    if control.countable_pitch and _has(clause, "more") and "prominent" not in clause:
        c = _scale(fp, control.countable_pitch, 1 / DEFAULT_STEP, 0.3, 3.0,
                   f"packed the {control.name} closer together")
        return ([c] if c else []), control
    if control.countable_pitch and (_has(clause, "fewer") or _has(clause, "less")):
        c = _scale(fp, control.countable_pitch, DEFAULT_STEP, 0.3, 3.0,
                   f"spaced the {control.name} further apart")
        return ([c] if c else []), control

    if control.size:
        c = _number(clause, fp, control.size, control.lo, control.hi,
                    control.measure, control.name)
        if c:
            changes.append(c)
    return changes, control


def _find(clause: str) -> Control | None:
    """The element this clause is about, longest name first so "clad bay" wins."""
    best: tuple[int, Control] | None = None
    for control in CONTROLS:
        for word in control.words:
            if _has(clause, word) and (best is None or len(word) > best[0]):
                best = (len(word), control)
    return best[1] if best else None


def _names(clause: str, control: Control | None) -> bool:
    return bool(control) and any(_has(clause, w) for w in control.words)


def _control(name: str) -> Control:
    return next(c for c in CONTROLS if c.name == name)


def _has(text: str, word: str) -> bool:
    """Whole-word match, so "band" does not fire on "abandon"."""
    return re.search(r"(?<![a-z])" + re.escape(word) + r"(?![a-z])", text) is not None


def _switch(clause: str) -> bool | None:
    for word in OFF_WORDS:
        if word.endswith(" ") and word in clause + " ":
            return False
        if not word.endswith(" ") and _has(clause, word):
            return False
    for word in ON_WORDS:
        if _has(clause, word):
            return True
    return None


def _side(clause: str) -> str | None:
    if _has(clause, "auto") or "either end" in clause or "wherever" in clause:
        return "auto"
    if _has(clause, "left"):
        return "left"
    if _has(clause, "right"):
        return "right"
    return None


def _number(clause: str, fp: FacadeParams, fieldname: str, lo: float, hi: float,
            noun: str, what: str) -> Change | None:
    """An absolute size if the clause states one, otherwise a comparative."""
    absolute = _length(clause)
    if absolute is not None:
        return _set(fp, fieldname, absolute, lo, hi,
                    lambda v: f"set the {what} {noun} to {fmt_ft(v)}")

    up = any(_has(clause, w) or w in clause for w in BIGGER)
    down = any(_has(clause, w) or w in clause for w in SMALLER)
    if not (up or down):
        return None
    step = DEFAULT_STEP
    for words, value in STEPS:
        if any(w in clause for w in words):
            step = value
            break
    if step == 0.5:            # "half" is absolute, not a direction
        factor = 0.5
    elif step == 2.0:
        factor = 2.0
    else:
        factor = step if up else 1 / step
    verb = "increased" if factor > 1 else "reduced"
    return _scale(fp, fieldname, factor, lo, hi,
                  lambda v: f"{verb} the {what} {noun} to {fmt_ft(v)}")


def _scale(fp: FacadeParams, fieldname: str, factor: float, lo: float,
           hi: float, said) -> Change | None:
    return _set(fp, fieldname, getattr(fp, fieldname) * factor, lo, hi, said)


def _set(fp: FacadeParams, fieldname: str, value: float, lo: float, hi: float,
         said) -> Change | None:
    before = getattr(fp, fieldname)
    after = round(min(max(value, lo), hi), 3)
    if abs(after - before) < 0.005:
        return None
    setattr(fp, fieldname, after)
    text = said(after) if callable(said) else said
    if abs(value - after) > 0.01:
        text += f" (as far as it goes; {fmt_ft(lo)}–{fmt_ft(hi)})"
    return Change(fieldname, before, after, text)


_UNITS = re.compile(
    r"(\d+(?:\.\d+)?)\s*(feet|foot|ft|inches|inch|in|metres|meters|metre|meter|m|cm|mm)"
    r"(?![a-z])"
)
_TO_FT = {"feet": 1.0, "foot": 1.0, "ft": 1.0, "inches": 1 / 12, "inch": 1 / 12,
          "in": 1 / 12, "metres": 3.280839895, "meters": 3.280839895,
          "metre": 3.280839895, "meter": 3.280839895, "m": 3.280839895,
          "cm": 0.032808399, "mm": 0.0032808399}


def _length(clause: str) -> float | None:
    """A stated size, in whatever the user wrote it in."""
    drawing = parse_length_ft(clause)          # 6'  6'-8"  10"
    if drawing:
        return drawing
    m = _UNITS.search(clause)
    if m:
        return float(m.group(1)) * _TO_FT[m.group(2)]
    return None


def _sentence(changes: list[Change]) -> str:
    said = [c.said for c in changes]
    if len(said) == 1:
        body = said[0]
    else:
        body = ", ".join(said[:-1]) + " and " + said[-1]
    return body[0].upper() + body[1:] + "."
