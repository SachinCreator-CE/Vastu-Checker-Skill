# Reading a CAD drawing

How `scripts/cad_plan.py` turns a DWG or DXF house plan into the plan format, what to check in what it read, and how to correct it. The reader is written for the way Indian residential plans are usually drafted in AutoCAD (feet-and-inch or metric, room names with their clear sizes, door tags such as D, D1 and MD), and was built and tested on a real office drawing set.

## Contents
1. Files it reads
2. How it reads a plan
3. Checking what was read
4. Correcting it
5. The overlay in AutoCAD
6. Drafting habits that read well
7. Limits

## 1. Files it reads

- **DWG** saved in AutoCAD 2000, 2004, 2010, 2013 or 2018 format (version strings AC1015, AC1018, AC1024, AC1027, AC1032). AutoCAD 2018 and later save in the 2018 format by default, and offices often save down to 2004 or 2010; all of these work. The reader is pure Python (`scripts/dwg_read.py`), tested on 2004 and 2010 files.
- **DXF** in ASCII, any version from R12 (`scripts/cad_read.py`).
- **Not read**: AutoCAD 2007-format DWG (AC1021) and anything older than 2000, binary DXF. Ask the user to save a copy as AutoCAD 2018 DWG or as DXF (SAVEAS, or DXFOUT). External references (xrefs) are not followed: if the plan is attached into a sheet file, send the plan file itself.
- Only model space is read; layouts (paper space) and their title blocks are ignored. Frozen and off layers are ignored.

## 2. How it reads a plan

**Texts first.** Every TEXT, MTEXT and block attribute is classified (`scripts/cad_labels.py`): room names (POOJA, KITCHEN, M.BED ROOM, TOILET, SIT-OUT, STAIRCASE...), their written sizes (12'0"X14'6", 3.0X3.6, 3600X3000, on the same text or as a separate text just below), site items (GATE, SUMP, BORE WELL, SEPTIC TANK, OHT, CAR PARK), roads (ROAD, R O A D, 30'-0" WIDE ROAD), door tags (D, D1, DP; MD, DM, MAIN DOOR for the main door), stair arrows (UP, DN), plan titles (GROUND FLOOR PLAN, PROPOSED STILT FLOOR PLAN, GF+3 FLOORS (TYPICAL)) and compass letters. Furniture notes (BED UNIT, STUDY TABLE) and title-block words are ignored. Names the office uses that aren't in the table can be added to `VOCAB` in `cad_labels.py`.

**Floor plans.** Room labels that sit together form one plan; titles near a plan give its level (stilt 0, then ground or GF, first and so on; a typical floor counts once; terrace on top). A drawing set often holds several sheets and versions of the same level: they are numbered in reading order (`<name>_plans.png`), and the first plan of each level is used unless `--plans` says otherwise. The summary names the versions that differ from the one used.

**Units** come from the DXF's INSUNITS, else from the plan's size and text heights (inches, feet, mm, cm or m), and are confirmed by the room sizes fitting the walls.

**Walls.** All lines, polylines, arcs and blocks are drawn into a fine grid (about an inch a pixel), except layers whose names mark furniture, fixtures, interiors, cars, dimensions, hatching, grids, centre lines, structure (beams, columns, footings, steel), title blocks and frames. Furniture drawn on wall layers just makes small holes in a room, which are filled.

**Rooms.** A room whose label gives its size is placed by sliding a rectangle of exactly that size (either way round) around the label until its four edges sit on drawn lines; the summary gives the share of its edges on lines (80% or more is a sure fit). Labels sometimes count a wall thickness in the size; the fit then lands on the outer face, which doesn't change the zone. A room without a size takes the space around its label up to the walls, split from its neighbours at door openings; an irregular space becomes up to three rectangles. A staircase is taken from its tread lines (evenly spaced parallel lines of equal length near the label or the UP arrows), stretched to the landing wall. Gates, sumps, borewells, septic tanks and similar items are points at their labels.

**Plot and building.** Space reachable from the edge of the sheet (and from a ROAD label) is outside. If the plot line is closed, the plot is the building plus the open strips that touch it, and the building outline is the plot minus those strips, trimmed of window boxes and sunshades. If the plot line is open (a gate gap to the road), the plot is the first long line round the building on each side, and the outline is the rooms out to their outer wall faces. A closed polyline round the building on any layer is taken as the plot first.

**Doors.** Arcs of 18 to 60 inches radius sweeping 60 to 110 degrees are doors. The leaf is the radius with a line drawn along it; the opening is the other. A door faces the way you look when walking out through it (away from its swing), and the summary says which spaces it joins. The main door is the door tagged MD, DM or MAIN DOOR; failing that, the widest door from outside into the home, flagged as a guess.

**North.** From a compass rose (N, E, S, W and corner letters together, often in the title block), an N beside an arrow, or a block named like NORTH; the plan uses the one nearest it. The top of the drawing is read as the nearest of N, E, S, W, with the tilt reported.

## 3. Checking what was read

Open `<name>_read.png` every time. In it the black box is the building outline, the green dashed box the plot, the blue dashed lines the 3x3 zones with their names, coloured boxes the rooms by type, orange arrows the doors pointing the way they face, red the main door, and the red arrow north. Check:

1. **North** matches the drawing's arrow or compass, and the road side makes sense.
2. **Units and sizes**: the building and plot sizes in the summary against the drawing's dimension texts.
3. **Every room**: the right type (a STORE read as a toilet, a DRESS or LIFT with no rule), an outline on the walls, nothing leaking across a big open area (the summary warns when a space is open to a larger area and falls back to the label point).
4. **Stair** and its extent; **main door**, its facing and the spaces it joins.
5. **Levels**: the plans used are the final versions, and the level numbers are right (a stilt floor is level 0; a typical floor stands for all the floors it covers).
6. **Site items**: gate, sump, borewell, septic tank, overhead tank (usually on the terrace plan), parking.

## 4. Correcting it

- `--plans 3,4`: use these plan numbers, lowest floor first.
- `--up S` or `--north-deg 270`: north, when the drawing has no usable arrow or it was read wrong (`--north-deg` is the direction north points in the drawing, in degrees anticlockwise from the +X axis; 90 is up the sheet).
- `--units in|ft|mm|cm|m`: when sizes come out 12 or 25.4 times off.
- **Edit the plan file** for anything else: change a room's `type` or `name`, move or resize a rectangle (plan units, origin at the plot's top-left corner, y down the drawing), add a site item the drawing doesn't label (`{"name": "Borewell", "type": "borewell", "x": 2, "y": 1}`), set or move `main_door` (`x`, `y` at the door's middle, `level`, `facing`), add `seat_facing` or `cook_facing`. Keep the `cad` block as it is: it places each floor back on the drawing for the overlay. Save edited files under a new name, since running `cad_plan.py` again overwrites the plan file.

## 5. The overlay in AutoCAD

`<name>_vastu.dwg` holds only the overlay, at the drawing's own coordinates, on layers VASTU-GRID, VASTU-ZONE, VASTU-STATUS (green follows, yellow acceptable, orange contested, red against, grey no rule), VASTU-DOOR, VASTU-NORTH, VASTU-PLOT and VASTU-NOTES. To see it on the plan: open the user's drawing, XATTACH the overlay at 0,0,0 (scale 1, rotation 0), or open both and copy the overlay across with PASTEORIG. The layers can then be frozen or plotted. `<name>_vastu.dxf` is the same overlay for programs that won't open the DWG; the PDF and PNG show the plan and overlay together for sharing.

## 6. Drafting habits that read well

Worth passing on to the office:
- Label every room with its name and clear size in one text, as most plans already do (KITCHEN / 8'6"X7'2").
- Tag the main door MD (or write MAIN DOOR beside it).
- Keep a north arrow with an N, or a compass rose, near each plan or in its title block.
- Draw the plot boundary as one closed polyline, or as a continuous line with the gate drawn across its opening.
- Keep furniture, fixtures, dimensions and hatching on their own layers.
- Title each plan (GROUND FLOOR PLAN, FIRST FLOOR PLAN, TERRACE FLOOR PLAN), and keep superseded versions out of the file sent for checking, or say which is final.
- Label the sump, borewell, septic tank and overhead tank where they are.

## 7. Limits

- Rooms and outlines are rectangles; curved and angled walls are read as their bounding boxes. A plan drawn at an angle on the sheet should be rotated square before reading.
- Room names in other languages or scripts aren't recognised until added to `VOCAB`.
- Levels, slopes, roof falls, ceiling heights, columns and the count of doors and windows are not read; check them by eye or add them to the plan file (`floor_heights`, `openings_per_floor`, `columns`).
- What is read is only as good as the drafting: an unlabelled room stays unknown, and an untagged main door is a guess. Say so in the report rather than guessing silently.
