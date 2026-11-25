#!/usr/bin/env python3
"""Vastu zone checker for house plans.

Usage:
  python3 vastu_check.py plan.json [--manaiyadi] [--json results.json]   check a plan, print a Markdown report
  python3 vastu_check.py --zoning --up N           print the ideal 3x3 zoning map

The plan format is described in ../references/plan-format.md.
Rules are read from zone_rules.json next to this script; edit that file to follow another school.
"""
import argparse
import json
import sys
from pathlib import Path

RULES = json.loads(Path(__file__).with_name("zone_rules.json").read_text())
TYPES, ALIASES = RULES["types"], RULES["aliases"]
FAV = set(RULES["manaiyadi"]["favourable"])
NEU = set(RULES["manaiyadi"]["neutral"])
CW = {"N": "E", "E": "S", "S": "W", "W": "N"}
OPP = {"N": "S", "S": "N", "E": "W", "W": "E"}
EPS = 0.03  # share of a wall's length counted as "at the centre"
M_TO_FT = 3.28084


def drawing_dirs(up):
    """Compass direction at the drawing's top, right, bottom and left."""
    return up, CW[up], OPP[up], OPP[CW[up]]


def compose(a, b):
    parts = [d for d in (a, b) if d]
    if not parts:
        return "C"
    if len(parts) == 1:
        return parts[0]
    return next(d for d in parts if d in "NS") + next(d for d in parts if d in "EW")


def grid(ref, up):
    """The 9 zones of a rectangle as {label: (x0, y0, x1, y1)} in drawing coordinates."""
    x, y, w, h = ref["x"], ref["y"], ref["width"], ref["depth"]
    top, right, bottom, left = drawing_dirs(up)
    zones = {}
    for r, v in enumerate((top, None, bottom)):
        for c, hz in enumerate((left, None, right)):
            zones[compose(v, hz)] = (x + c * w / 3, y + r * h / 3, x + (c + 1) * w / 3, y + (r + 1) * h / 3)
    return zones


def box(item):
    return (item["x"], item["y"], item["x"] + item.get("width", 0), item["y"] + item.get("depth", 0))


def overlap(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def locate(parts, zones):
    """Dominant zone of a list of rectangles or points, with its share of the item's area."""
    areas = {z: 0.0 for z in zones}
    points = []
    for p in parts:
        b = box(p)
        if b[2] > b[0] and b[3] > b[1]:
            for z, zr in zones.items():
                areas[z] += overlap(b, zr)
        else:
            points.append(b[:2])
    total = sum(areas.values())
    if total > 0:
        best = max(areas, key=areas.get)
        return best, areas[best] / total
    for px, py in points:
        for z, (x0, y0, x1, y1) in zones.items():
            if x0 <= px <= x1 and y0 <= py <= y1:
                return z, 1.0
    return None, 0.0


def clamp_zone(parts, zones):
    """Zone of an item lying wholly outside the reference rectangle, by direction: its centre pulled back
    onto the rectangle (a portico beyond the east wall's middle third reads as E)."""
    xs0 = min(z[0] for z in zones.values())
    ys0 = min(z[1] for z in zones.values())
    xs1 = max(z[2] for z in zones.values())
    ys1 = max(z[3] for z in zones.values())
    bx = [box(p) for p in parts]
    cx = (min(b[0] for b in bx) + max(b[2] for b in bx)) / 2
    cy = (min(b[1] for b in bx) + max(b[3] for b in bx)) / 2
    cx = min(max(cx, xs0 + 1e-6), xs1 - 1e-6)
    cy = min(max(cy, ys0 + 1e-6), ys1 - 1e-6)
    for z, (x0, y0, x1, y1) in zones.items():
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            return z
    return None


def norm_type(t):
    t = (t or "").strip().lower().replace(" ", "_").replace("-", "_")
    return ALIASES.get(t, t)


def zone_status(rule, zone):
    if zone is None:
        return "Outside its reference area"
    for key, label in (("best", "Follows"), ("ok", "Acceptable"), ("contested", "Contested"), ("avoid", "Against")):
        if zone in rule.get(key, []):
            return label
    return "Not listed"


def num(n):
    return f"{n:.1f}".rstrip("0").rstrip(".")


def ns_ew(item, up):
    """Lengths along N-S and E-W."""
    return (item["depth"], item["width"]) if up in ("N", "S") else (item["width"], item["depth"])


def proportion_lines(label, item, up, ratio_rule):
    ns, ew = ns_ew(item, up)
    out = []
    if ratio_rule:
        long_side, short_side = max(ns, ew), min(ns, ew)
        ratio = long_side / short_side if short_side else float("inf")
        if ratio <= 1.05:
            verdict = "Follows (square)"
        elif ratio <= 1.5:
            verdict = "Follows (within 1:1.5)"
        elif ratio <= 2.0:
            verdict = "Acceptable (within 1:2)"
        else:
            verdict = "Against (longer than 1:2)"
        out.append(f"| {label} proportion | 1:{ratio:.2f} | {verdict} |")
    if abs(ns - ew) < 1e-9:
        axis = "Follows (square)"
    elif ns > ew:
        axis = "Follows (longer side runs N-S)"
    else:
        axis = "Against (longer side runs E-W; the source prefers N-S)"
    out.append(f"| {label} long axis | N-S {num(ns)}, E-W {num(ew)} | {axis} |")
    return out


def setback_lines(plot, b, up):
    top, right, bottom, left = drawing_dirs(up)
    gap = {
        top: b["y"] - plot["y"],
        bottom: plot["y"] + plot["depth"] - (b["y"] + b["depth"]),
        left: b["x"] - plot["x"],
        right: plot["x"] + plot["width"] - (b["x"] + b["width"]),
    }
    ns = "Follows" if gap["N"] >= gap["S"] else "Against (more open space should be on the N)"
    ew = "Follows" if gap["E"] >= gap["W"] else "Against (more open space should be on the E)"
    return [
        f"| Open space N vs S | N {num(gap['N'])}, S {num(gap['S'])} | {ns} |",
        f"| Open space E vs W | E {num(gap['E'])}, W {num(gap['W'])} | {ew} |",
    ]


def door_lines(door, b, up):
    top, right, bottom, left = drawing_dirs(up)
    x0, y0, x1, y1 = b["x"], b["y"], b["x"] + b["width"], b["y"] + b["depth"]
    px, py = door["x"], door["y"]
    facing = (door.get("facing") or "").upper()
    if facing in (top, bottom, left, right):
        # the door faces this way, so it counts as being on that side, wherever it sits along it
        wall, horizontal = facing, facing in (top, bottom)
    else:
        _, wall, horizontal = min(
            (abs(py - y0), top, True), (abs(py - y1), bottom, True), (abs(px - x0), left, False),
            (abs(px - x1), right, False)
        )
    if horizontal:
        f, start = (px - x0) / (x1 - x0), left
    else:
        f, start = (py - y0) / (y1 - y0), top
    f = min(max(f, 0.0), 1.0)
    want_start = "W" if wall in ("N", "S") else "N"
    if start != want_start:
        f = 1 - f  # N and S walls measured from the W end, E and W walls from the N end
    side = {"N": "Follows", "E": "Follows", "W": "Acceptable", "S": "Against (the source advises against a south entrance)"}[wall]
    how = f"{wall} wall" if not facing else f"faces {wall}"
    if door.get("from") or door.get("into"):
        how += f" (from {door.get('from') or '?'} into {door.get('into') or '?'})"
    lines = [f"| Main door wall | {how} | {side} |"]
    where = f"{round(f * 100)}% along from the {want_start} end"
    if f <= 1 / 9 or f >= 8 / 9:
        pos = "Against (too close to a corner)"
    elif abs(f - 0.5) <= EPS:
        pos = "Against (exactly at the centre of the wall)"
    else:
        want_upper_half = {"N": True, "S": True, "E": False, "W": True}[wall]
        fix = {"N": "move it east of centre", "S": "move it east of centre", "E": "move it north of centre",
               "W": "move it south of centre (some authorities centre a west door)"}[wall]
        pos = "Follows" if (f > 0.5) == want_upper_half else f"Against ({fix})"
    lines.append(f"| Main door position | {where} | {pos} |")
    return lines


ROAD_SETS = [
    ({"N", "E", "S", "W"}, "Follows (roads on all four sides: best)"),
    ({"N", "E"}, "Follows (roads on E and N: good)"),
    ({"N", "W"}, "Acceptable (roads on N and W: average)"),
    ({"N", "W", "E"}, "Against (roads on N, W and E: not so good)"),
    ({"N", "S", "W"}, "Against (roads on N, S and W: not so good)"),
    ({"E", "S", "W"}, "Against (roads on E, S and W: bad)"),
]


def road_line(roads):
    rs = {r.upper() for r in roads}
    for combo, verdict in ROAD_SETS:
        if rs == combo:
            return f"| Roads | {', '.join(sorted(rs, key='NESW'.index))} | {verdict} |"
    if len(rs) == 1:
        r = next(iter(rs))
        v = "Follows (a road on the E or N is better than on the S or W)" if r in "NE" else \
            "Acceptable (no facing is bad in itself; a road on the E or N is the source's preference)"
        return f"| Road | {r} | {v} |"
    return f"| Roads | {', '.join(sorted(rs, key='NESW'.index))} | Not listed |"


def steps_verdict(n):
    odd, mod3, zero = n % 2 == 1, n % 3 == 2, n % 10 == 0
    if odd and mod3 and not zero:
        return "Follows (odd and leaves 2 when divided by 3)"
    nearest = sorted((k for k in range(5, 60, 6)), key=lambda k: (abs(k - n), k))[:2]
    issues = []
    if not odd:
        issues.append("even")
    if zero:
        issues.append("ends in zero")
    if not mod3:
        issues.append("doesn't leave 2 when divided by 3, as one passage asks")
    status = "Against" if (not odd or zero) else "Contested"
    return f"{status}: {', '.join(issues)}. Counts meeting both rules nearby: {', '.join(map(str, sorted(nearest)))}"


def stair_lines(name, parts, room, b, up):
    top, right, bottom, left = drawing_dirs(up)
    x0, y0, x1, y1 = b["x"], b["y"], b["x"] + b["width"], b["y"] + b["depth"]
    tol = 0.02 * max(b["width"], b["depth"])
    touching = set()
    for p in parts:
        bx = box(p)
        if abs(bx[1] - y0) <= tol:
            touching.add(top)
        if abs(bx[3] - y1) <= tol:
            touching.add(bottom)
        if abs(bx[0] - x0) <= tol:
            touching.add(left)
        if abs(bx[2] - x1) <= tol:
            touching.add(right)
    bad = sorted(touching & {"N", "E"})
    out = [f"| {name}: outer walls touched | {', '.join(sorted(touching)) or 'none'} | "
           f"{'Against (keep stairs off the E and N outer walls)' if bad else 'Follows'} |"]
    if "steps" in room:
        out.append(f"| {name}: steps | {room['steps']} | {steps_verdict(int(room['steps']))} |")
    if room.get("ascends_toward"):
        d = room["ascends_toward"].upper()
        v = "Follows" if d in ("W", "S") else "Against (climb toward W or S)"
        out.append(f"| {name}: climbs toward | {d} | {v} |")
    if room.get("turn"):
        t = room["turn"].lower()
        v = "Follows" if t.startswith("clock") else "Against (turn clockwise going up)"
        out.append(f"| {name}: turns | {t} | {v} |")
    return out


def manaiyadi(value, units):
    ft = value * M_TO_FT if units == "m" else value
    n = round(ft)
    shown = f"{num(ft)} ft"
    if n < 6 or n > 120:
        return shown, "Not covered (the table runs 6-120 ft)"
    halfway = abs(ft - int(ft) - 0.5) < 0.1
    note = " (close to halfway; check both neighbours)" if halfway else ""
    if n in FAV:
        return shown, f"Favourable at {n} ft{note}"
    if n in NEU:
        return shown, f"Neutral at {n} ft{note}"
    lower = max((k for k in FAV if k < n), default=None)
    upper = min((k for k in FAV if k > n), default=None)
    near = " or ".join(str(k) for k in (lower, upper) if k)
    return shown, f"Unfavourable at {n} ft (nearest favourable: {near} ft){note}"


def level_label(plan, level):
    names = plan.get("levels") or {}
    nm = names.get(str(level))
    return f"{level} ({nm})" if nm else str(level)


def check(plan, use_manaiyadi, results=None):
    """Markdown report; if `results` is a dict, it is filled with the statuses for drawing an overlay."""
    up = plan.get("up", "N").upper()
    if up not in CW:
        sys.exit("'up' must be N, E, S or W: the compass direction at the top of the drawing.")
    units = plan.get("units", "ft")
    plot = {"x": 0, "y": 0, "width": plan["plot"]["width"], "depth": plan["plot"]["depth"]}
    building = plan.get("building", plot)
    floors = {f.get("level", 0): f for f in plan.get("floors", [])}
    res = results if results is not None else {}
    res.update(up=up, items=[], checks=[], centre=[], door=None)
    out = ["# Vastu check", ""]
    top, right, bottom, left = drawing_dirs(up)
    out.append(f"Drawing orientation: top faces {top}, right faces {right}. Units: {units}.")
    tilt = plan.get("tilt_deg")
    if tilt is not None and abs(tilt) > 20:
        out.append(f"Warning: the plot is {num(abs(tilt))} degrees off the cardinal directions; the source accepts up to 20. "
                   "Set the house square to the cardinal directions.")
    out += ["", "## Plot and building", "", "| Check | Value | Status |", "|---|---|---|"]
    rows = proportion_lines("Plot", plot, up, ratio_rule=False)
    rows += proportion_lines("Building", building, up, ratio_rule=True)
    if "building" in plan and plan["plot"].get("drawn", True):
        rows += setback_lines(plot, building, up)
    elif "building" in plan:
        rows.append("| Open space around the building | plot line not in the drawing | Need information |")
    if plan.get("roads"):
        rows.append(road_line(plan["roads"]))
    out += rows
    res["checks"] += rows

    # Group rooms by name and level so L-shaped rooms given as several rectangles count once.
    groups = {}
    for item in plan.get("rooms", []):
        groups.setdefault(("room", item.get("name", item.get("type", "?")), item.get("level", 0)), []).append(item)
    for item in plan.get("site", []):
        groups.setdefault(("site", item.get("name", item.get("type", "?")), item.get("level", 0)), []).append(item)

    out += ["", "## Rooms and site features", "", "| Item | Type | Level | Zone | Status | Note |", "|---|---|---|---|---|---|"]
    centre_hits, extra, sizes = [], [], []
    for (kind, name, level), parts in groups.items():
        t = norm_type(parts[0].get("type"))
        rule = TYPES.get(t)
        if rule:
            ref = plot if rule.get("ref") == "plot" else floors.get(level, building)
        else:
            ref = plot if kind == "site" else floors.get(level, building)
        zones = grid(ref, up)
        zone, share = locate(parts, zones)
        outside_note = ""
        if zone is None:
            zone = clamp_zone(parts, zones)
            share = 1.0
            outside_note = " (outside the outline; zoned by direction)" if zone else ""
        if rule:
            status, note = zone_status(rule, zone), rule.get("note", "")
        else:
            status, note = "No rule in the source", ""
        z = f"{zone} ({round(share * 100)}%)" if zone and share < 0.995 else (zone or "-")
        lv = level_label(plan, level) if kind == "room" else "site"
        out.append(f"| {name} | {t} | {lv} | {z}{outside_note} | {status} | {note} |")
        res["items"].append({"name": name, "type": t, "kind": kind, "level": level, "zone": zone,
                             "share": round(share, 2), "status": status.split(" (")[0], "note": note})
        centre_area = sum(overlap(box(p), zones["C"]) for p in parts)
        if centre_area > 0 or zone == "C":
            centre_hits.append((name, t, centre_area))
        if t == "staircase":
            extra += stair_lines(f"{name} (level {level_label(plan, level)})" if len(plan.get("levels") or {}) > 1 else name,
                                 parts, parts[0], floors.get(level, building), up)
        if t == "toilet" and parts[0].get("seat_facing"):
            d = parts[0]["seat_facing"].upper()
            extra.append(f"| {name}: user faces | {d} | {'Follows' if d in ('N', 'S') else 'Against (face N or S)'} |")
        if t == "kitchen" and parts[0].get("cook_facing"):
            d = parts[0]["cook_facing"].upper()
            v = {"E": "Follows", "W": "Acceptable", "S": "Against (never face S while cooking)"}.get(d, "Not listed")
            extra.append(f"| {name}: cook faces | {d} | {v} |")
        if kind == "room" and len(parts) == 1 and parts[0].get("width") and parts[0].get("depth"):
            clear = parts[0].get("clear")
            w, d_ = (clear[0], clear[1]) if clear else (parts[0]["width"], parts[0]["depth"])
            sizes.append((name, w, d_))

    if plan.get("main_door") or extra:
        out += ["", "## Entrance, stairs and directions", "", "| Check | Value | Status |", "|---|---|---|"]
        if plan.get("main_door"):
            door = plan["main_door"]
            dl = door_lines(door, floors.get(door.get("level", 0), building), up)
            out += dl
            res["door"] = {"rows": dl, "status": "Against" if any("| Against" in r for r in dl) else "Follows"}
        out += extra
        res["checks"] += extra

    out += ["", "## Centre (Brahmasthan)", ""]
    avoid = set(RULES["centre_avoid"])
    flagged = [(n, t) for n, t, _ in centre_hits if t in avoid]
    open_ok = [n for n, t, _ in centre_hits if t == "courtyard"]
    if flagged:
        out.append("Against: " + "; ".join(f"{n} ({t})" for n, t in flagged) + " reaches into the centre. Keep it open.")
    if open_ok:
        out.append("Follows: " + ", ".join(open_ok) + " keeps the centre open.")
    if not flagged and not open_ok:
        others = ", ".join(n for n, _, _ in centre_hits) or "nothing"
        out.append(f"No restricted use in the centre (occupied by: {others}). Check for columns there by eye.")
    res["centre"] = [n for n, _ in flagged]

    counts = []
    openings = plan.get("openings_per_floor")
    if openings:
        levels = sorted(openings, key=lambda k: int(k))
        for k in levels:
            n = int(openings[k])
            v = "Follows" if n % 2 == 0 and n % 10 != 0 else "Against (use an even total that doesn't end in zero)"
            counts.append(f"| Doors + windows, level {k} | {n} | {v} |")
        ground = int(openings[levels[0]])
        for k in levels[1:]:
            n = int(openings[k])
            if n >= ground:
                counts.append(f"| Upper level {k} vs ground | {n} vs {ground} | Against (fewer openings upstairs) |")
    heights = plan.get("floor_heights")
    if heights and len(heights) > 1:
        ok = all(a > b for a, b in zip(heights, heights[1:]))
        counts.append(f"| Ceiling heights upward | {', '.join(map(num, heights))} | "
                      f"{'Follows' if ok else 'Against (upper floors lower than the ground floor)'} |")
    cols = plan.get("columns")
    if cols:
        n = len(cols) if isinstance(cols, list) else int(cols)
        counts.append(f"| Number of columns | {n} | {'Follows' if n % 2 == 0 else 'Against (even number; minor rule)'} |")
        if isinstance(cols, list):
            centre = grid(building, up)["C"]
            inside = [c.get("id", "?") for c in cols
                      if centre[0] < c["x"] < centre[2] and centre[1] < c["y"] < centre[3]]
            if inside:
                counts.append(f"| Columns in the centre | {', '.join(inside)} | "
                              "Against (keep the Brahmasthan clear; ask the structural step whether the grid can shift) |")
    if counts:
        out += ["", "## Counts", "", "| Check | Value | Status |", "|---|---|---|"] + counts

    if use_manaiyadi and sizes:
        out += ["", "## Room sizes (Manaiyadi Sastram, optional)", "",
                "| Room | Length | Breadth |", "|---|---|---|"]
        for name, w, d in sizes:
            a, b = manaiyadi(w, units), manaiyadi(d, units)
            out.append(f"| {name} | {a[0]}: {a[1]} | {b[0]}: {b[1]} |")
        out.append("")
        out.append("Sizes are the clear sizes written on the drawing where it gives them, otherwise as measured; "
                   "the family's practice decides inside clear or centre-line dimensions.")
    return "\n".join(out)


def zoning(up):
    top, right, bottom, left = drawing_dirs(up)
    lines = [f"Ideal zoning, drawn the way your plan is oriented (top of the drawing faces {top}).", "",
             f"| {left} side | middle | {right} side |", "|---|---|---|"]
    for v in (top, None, bottom):
        cells = []
        for hz in (left, None, right):
            z = compose(v, hz)
            cells.append(f"**{z}**: {RULES['zoning'][z]}")
        lines.append("| " + " | ".join(cells) + " |")
    lines += ["", "Place the house toward the SW of the plot and leave more open space on the N and E.",
              "Site: gate on N, NE or E; borewell or sump NE; overhead tank SW; septic tank N-NW or E-SE; parking NW."]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan", nargs="?", help="plan JSON file")
    ap.add_argument("--manaiyadi", action="store_true", help="also rate room sizes by Manaiyadi Sastram")
    ap.add_argument("--zoning", action="store_true", help="print the ideal zoning map instead of checking a plan")
    ap.add_argument("--up", default="N", help="with --zoning: compass direction at the top of the drawing (N, E, S, W)")
    ap.add_argument("--json", help="also write the statuses to this file (for vastu_overlay.py)")
    args = ap.parse_args()
    if args.zoning:
        up = args.up.upper()
        if up not in CW:
            sys.exit("--up must be N, E, S or W")
        print(zoning(up))
        return
    if not args.plan:
        ap.error("give a plan JSON file, or use --zoning")
    plan = json.loads(Path(args.plan).read_text())
    results = {}
    print(check(plan, args.manaiyadi, results))
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
