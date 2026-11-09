"""Translate Garmin's workout DTOs to Intervals.icu workout builder text."""

import math

STEP_FORMATS = {
    "warmup": ("Warm Up", "warmup"),
    "recovery": ("Rest", "recovery"),
    "rest": ("Rest", "recovery"),
    "cooldown": ("Cool Down", "cooldown"),
}
SECTION_TITLES = {"warmup": "Warmup", "cooldown": "Cooldown"}
ZONE_TARGETS = {
    "speed.zone": " Pace",
    "pace.zone": " Pace",
    "heart.rate.zone": " HR",
    "power.zone": "",
}


class UnsupportedStep(ValueError):
    """The Garmin step cannot be represented in workout builder text."""


def positive(value):
    number = float(value) if value is not None else 0
    if not math.isfinite(number) or number <= 0:
        raise ValueError("expected a positive step duration, distance or target")
    return number


def pace(speed):
    minutes, seconds = divmod(round(1000 / positive(speed)), 60)
    return f"{minutes}:{seconds:02d}"


def target(step, secondary=False):
    type_field = "secondaryTargetType" if secondary else "targetType"
    kind = (step.get(type_field) or {}).get("workoutTargetTypeKey")
    if kind in (None, "no.target"):
        return ""

    zone = step.get("secondaryZoneNumber" if secondary else "zoneNumber")
    if zone and kind in ZONE_TARGETS:
        return f"Z{zone}{ZONE_TARGETS[kind]}"

    value_prefix = "secondaryTarget" if secondary else "target"
    values = [
        positive(step[field])
        for field in (f"{value_prefix}ValueOne", f"{value_prefix}ValueTwo")
        if step.get(field) is not None
    ]
    if not values:
        raise ValueError(f"missing values for target {kind}")

    if kind in ("speed.zone", "pace.zone"):
        bounds = [pace(value) for value in sorted(set(values), reverse=True)]
        return "-".join(dict.fromkeys(bounds)) + " Pace"
    if kind in ("cadence", "power.zone"):
        unit = "rpm" if kind == "cadence" else "w"
        return "-".join(f"{value:g}" for value in sorted(set(values))) + unit
    raise UnsupportedStep(f"unsupported target {kind}")


def render_step(step):
    kind = step["stepType"]["stepTypeKey"]
    label, intensity = STEP_FORMATS.get(kind, ("Run", "active"))
    condition = step["endCondition"]["conditionTypeKey"]
    value = step.get("endConditionValue")
    flags = []
    if condition == "time":
        minutes, seconds = divmod(positive(value), 60)
        parts = []
        if minutes:
            parts.append(f"{int(minutes)}m")
        if seconds:
            parts.append(f"{seconds:g}s")
        duration = " ".join(parts)
    elif condition == "distance":
        duration = f"{positive(value) / 1000:g}km"
    elif condition in ("lap.button", "lap_button"):
        duration = "1s"
        flags.append("press lap")
    else:
        raise UnsupportedStep(f"unsupported end condition {condition}")

    primary = target(step)
    secondary = target(step, secondary=True)
    if primary and secondary and not (primary.endswith("rpm") or secondary.endswith("rpm")):
        raise UnsupportedStep("two simultaneous intensity targets")
    targets = sorted(filter(None, (primary, secondary)), key=lambda item: item.endswith("rpm"))
    return " ".join([f"- {label}", duration, *targets, *flags, f"intensity={intensity}"])


def render_steps(steps, *, inside_repeat=False):
    if not steps:
        raise ValueError("empty workout step list")
    lines = []
    section = None
    for step in sorted(steps, key=lambda item: item.get("stepOrder", 0)):
        if step.get("type") != "RepeatGroupDTO" and "workoutSteps" not in step:
            if not inside_repeat:
                title = SECTION_TITLES.get(step["stepType"]["stepTypeKey"], "Main set")
                if title != section:
                    if lines and lines[-1]:
                        lines.append("")
                    lines.append(title)
                    section = title
            lines.append(render_step(step))
            continue
        count = positive(step["numberOfIterations"])
        if not count.is_integer():
            raise ValueError("repeat count must be a whole number")
        children = step["workoutSteps"]
        if inside_repeat:
            lines.extend(render_steps(children, inside_repeat=True) * int(count))
        else:
            if lines and lines[-1]:
                lines.append("")
            lines.extend([f"Main set {int(count)}x", *render_steps(children, inside_repeat=True), ""])
            section = None
    return lines


def workout_text(workout):
    segments = sorted(workout["workoutSegments"], key=lambda item: item.get("segmentOrder", 0))
    blocks = ["\n".join(render_steps(segment["workoutSteps"])).strip() for segment in segments]
    if not blocks:
        raise ValueError("workout has no segments")
    return "\n\n".join(blocks)
