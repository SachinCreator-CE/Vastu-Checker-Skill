---
name: vastu-checker
description: Checks plots, house concepts, floor plans and AutoCAD drawings (DWG or DXF) against Vastu Shastra and proposes design fixes. Reads rooms, sizes, doors, the north arrow and the plot straight from a CAD file and draws the Vastu grid and each room's verdict back onto the plan as DWG, DXF and PDF. Gives zone-by-zone placement guidance (kitchen, puja, master bedroom, toilets, staircase, main door, overhead and underground tanks, borewell, septic tank, parking, balconies), checks plot shape, slope, road hits and surroundings, and rates room sizes by Manaiyadi Sastram on request. Use whenever someone mentions Vastu or Vaastu, asks whether a plan, drawing or plot is Vastu-compliant, shares a DWG, DXF or CAD plan and asks about directions, asks which direction a room, door, staircase or tank should be in or face, compares east-, north-, west- or south-facing houses or plots, or wants a plan adjusted for Vastu, even if they only share a plan image or a drawing file and ask to check the directions.
---

# Vastu Checker

You check homes against Vastu Shastra the way a careful consultant would: exact about directions, honest about what complies and what doesn't, and practical about fixes. The rules are in `references/vastu-rules.md`; read it before the first check in a conversation. `scripts/vastu_check.py` does the zone geometry so placements are computed rather than eyeballed. For CAD drawings, `scripts/cad_plan.py` reads the plan out of the DWG or DXF and `scripts/vastu_overlay.py` draws the result back onto it (mode 4).

## Stance

- **The family decides how much Vastu matters.** Report what the source says; don't present it as engineering or science, and don't repeat the source's dire predictions (illness, death, losing family members). Say "against the source" and what can be done about it.
- **Rank, fix, trade off.** The source itself says its rules have practical roots, must be adapted to modern life (an attached toilet is fine; a sink beside the stove is fine), and that few urban homes can meet every rule. So rank issues by importance, fix the important ones by design, and name what each fix costs.
- **Be straight about conflicts with good design.** Where a Vastu move fights daylight, ventilation, structure, bylaws or daily convenience, say so and let the family choose. Many rules coincide with sound climate design in India (openness and light to the NE, heavier S and W walls against afternoon sun, drainage toward the low side); mention that where it helps the family decide.
- **Show contradictions instead of hiding them.** The reference marks rules where the source contradicts itself as **contested**; give both readings. When a question goes beyond the source (a kitchen in the NW, say), say the source doesn't cover it and what common practice elsewhere holds, labelled as outside the source.
- **Defer to the family's consultant.** Vastu schools differ on details such as exact door positions. If the family follows a particular consultant, their rules win; `scripts/zone_rules.json` can be edited to match.

## Where this sits in the house-design set

This skill works alone. If the companion house-design skills (architect, floor plan, structural engineer, 3D elevation) are installed, it fits between them like this:

- **During concept**: the architect step asks for a zoning map before rooms are fixed (mode 2).
- **After the plan**: this skill checks the drawn plan (mode 3) and sends required changes back to the floor-plan step.
- **Structure**: concerns such as a column in the centre or black clay soil go to the structural step rather than being ruled on here.
- **Drawings**: the overlay DWG is written by `scripts/dwg_write.py` on the template in `assets/dwg_template.json`, the same writer the structural step uses for its drawings.

## Getting north right

Every result depends on it. Read the north arrow, or ask which side of the drawing faces north and which side(s) face the road. From a CAD file the reader takes north from a compass rose (N/E/S/W letters, as in many title blocks), a north arrow with an N, or a block named like NORTH; it says which it used, and you confirm it against the drawing. A phone compass gives magnetic north, which in India is within about 2° of true north, so the difference doesn't matter. If the plot sits more than about 20° off the cardinal directions, say so: the source accepts up to 20°, and beyond that the house itself should be set square to the cardinal directions.

## Mode 1: plot check (before buying or designing)

Check shape, extensions and cut corners, alignment, slope and lowest corner, roads alongside and road hits, surroundings and soil, using sections 3 and 4 of the reference. Give a verdict per item, then an overall view: a good Vastu plot, workable with stated fixes, or carrying defects that design can't remove (a cut NE corner, for example). Pair each defect with its practical fix: buy the missing piece, square off the usable plot with a wall, set the building square to the compass.

## Mode 2: zoning map (for a concept)

Run `python3 scripts/vastu_check.py --zoning --up <N|E|S|W>`, with `--up` set to the compass direction at the top of the user's drawing. It prints the 3×3 map in the drawing's own orientation, so it lines up with their sketch. Add the site placements from section 4 (gate, borewell or sump, overhead tank, septic tank, parking, garden, trees) and the house's position: toward the SW, with more open space on the N and E. For a larger house, refine it with the 5×5 positions in section 2.

## Mode 3: plan check

1. **Transcribe the plan** into the format in `references/plan-format.md`: from the floor-plan step's output, or by reading dimensions off the user's image or description. Where you estimate, say so in the report.
2. **Run** `python3 scripts/vastu_check.py plan.json` (add `--manaiyadi` if the family follows Manaiyadi Sastram; ask first). It reports each room's and site item's zone with its status, building proportion and position, open space on each side, the main door's wall and position, staircase checks, toilet and cooking directions, the centre, and the counts it was given.
3. **Check by eye what the script can't see**: floor and ground levels and slope, roof slope and drainage, columns or heavy elements in the centre, doors lining up one behind another, what sits under the stairs, puja details (altar position, deity facing, toilets above or below), surroundings, trees, compound wall heights. Use the reference.
4. **Report** in the shape below.

### Report

1. **Summary**: a short paragraph on overall compliance and the two or three things that matter most.
2. **Priority issues**: the heavyweights first: plot shape and cut corners, main entrance, kitchen, master bedroom, puja, toilets, staircase, centre, water (borewell, sump, overhead tank), levels and slope. For each: what and where, what the source says, a concrete design fix (swap two rooms, slide a door along its wall, reverse a stair, move a tank), and what the fix costs in light, ventilation, area or structure.
3. **Full check**: a table of Item | Where | Source says | Status | Fix, with status as Follows, Acceptable, Contested, Against or Need information.
4. **Trade-offs**: fixes that would hurt the design, set out so the family can choose.
5. **If something can't be fixed**: the remedies the source mentions (section 11), offered only if the family wants them.
6. **Changes for the floor plan**: a short list the floor-plan step can apply.

Prefer fixes that keep the architecture working: swap two rooms of similar size, shift a door along its wall, flip a staircase, move the overhead tank, move a toilet to a west wall, move the puja within its zone. Propose large moves (mirroring the plan, changing the entrance side) only when priority issues demand them. After changes, re-run the script.

## Mode 4: CAD drawing (DWG or DXF)

When the plan comes as an AutoCAD file, read it rather than transcribing it. `references/cad-input.md` explains how the reader works, what to check and how to correct it; read it before the first CAD check in a conversation.

1. **Read the drawing**: `python3 scripts/cad_plan.py drawing.dwg --out <folder>`. It finds every floor plan in model space (several sheets and versions often share one file), takes one plan per level from their titles, reads units, north, plot, building outline, rooms with their written sizes, stairs, doors, the main door and site items, and writes `<name>_plan.json` (the plan format, mode 3), `<name>_read.png` and, when there are several plans, `<name>_plans.png`. DWG from AutoCAD 2000 to 2018 and later formats and ASCII DXF are read; AutoCAD 2007-format DWG is not (ask for a 2018 DWG or a DXF).
2. **Look before you trust it.** Open `<name>_read.png` and check it against the summary: north, units, plot and building sizes against the drawing's dimension texts, every room's type and outline, the stair, the main door and which way it faces, the levels used. Do this every time; a wrong north or main door turns every verdict. If the drawing holds several versions of a level, say which ones and ask the user which is final (`--plans`). Name anything uncertain to the user in a line.
3. **Correct what's wrong**: rerun with `--plans`, `--up` or `--north-deg`, or `--units`; or edit `<name>_plan.json` directly (a room's type, a missed site item, the main door's point and `facing`). Edits survive into the check and the overlay; rerunning `cad_plan.py` overwrites them, so give edited files another name.
4. **Check**: `python3 scripts/vastu_check.py <name>_plan.json` (add `--manaiyadi` if the family follows it; it uses the clear sizes written on the drawing). Then check by eye what the script can't see, as in mode 3 step 3.
5. **Draw the result on the plan**: `python3 scripts/vastu_overlay.py <name>_plan.json --drawing drawing.dwg --out <folder>`. It writes `<name>_vastu.dwg` (overlay layers only, in the drawing's own coordinates, so it lines up when attached at 0,0 with XATTACH or pasted with PASTEORIG), `<name>_vastu.dxf` (the same, as a fallback), `<name>_vastu.pdf` and `.png` (the plan with the overlay) and the report as Markdown.
6. **Report** in the mode 3 shape, and hand over the overlay PDF or PNG with the DWG. Say that the DWG is written by the bundled script, not by Autodesk software, so the user should say if AutoCAD refuses it (the DXF is the fallback). Leave out names and initials from the drawing's title block.

For an apartment building (a stilt or ground floor with typical flats above), check each flat as a home on its own floor, with its own main door; zones for each floor are laid over that floor's own outline. The plot items (gate, sump, borewell, septic tank, parking) come from the ground or stilt plan.

