# Plan format

A plain JSON description of a house plan. The Vastu checker reads it; the floor-plan skill and `scripts/cad_plan.py` (from a DWG or DXF) write it. Everything is a rectangle: split an L-shaped room into two rectangles with the same `name`.

**Coordinates**: the origin is the top-left corner of the plot as drawn, `x` runs to the right and `y` runs down the drawing. All lengths use the same unit, feet (`"ft"`) or metres (`"m"`).

## Example

```json
{
  "units": "ft",
  "up": "N",
  "tilt_deg": 0,
  "plot": {"width": 30, "depth": 40},
  "building": {"x": 0, "y": 6, "width": 26, "depth": 32},
  "floors": [{"level": 1, "x": 0, "y": 6, "width": 26, "depth": 24}],
  "rooms": [
    {"name": "Kitchen", "type": "kitchen", "level": 0, "x": 17.5, "y": 28, "width": 8.5, "depth": 10, "cook_facing": "E"},
    {"name": "Toilet 1", "type": "toilet", "level": 0, "x": 0, "y": 18, "width": 5, "depth": 8, "seat_facing": "N"},
    {"name": "Stair", "type": "staircase", "level": 0, "x": 10, "y": 30, "width": 7, "depth": 8, "steps": 17, "ascends_toward": "W", "turn": "clockwise"},
    {"name": "Living", "type": "living", "level": 0, "x": 9, "y": 6, "width": 11, "depth": 10},
    {"name": "Living", "type": "living", "level": 0, "x": 9, "y": 16, "width": 5, "depth": 4}
  ],
  "main_door": {"level": 0, "x": 26, "y": 12},
  "site": [
    {"name": "Borewell", "type": "borewell", "x": 28, "y": 2},
    {"name": "Sump", "type": "sump", "x": 24, "y": 1, "width": 4, "depth": 3},
    {"name": "Gate", "type": "gate", "x": 30, "y": 5}
  ],
  "openings_per_floor": {"0": 18, "1": 14},
  "floor_heights": [3.2, 3.0],
  "columns": 16
}
```

## Fields

| Field | Required | Meaning |
|---|---|---|
| `units` | no | `"ft"` (default) or `"m"` |
| `up` | yes | Compass direction at the top of the drawing: `N`, `E`, `S` or `W` |
| `tilt_deg` | no | How far true north sits off that direction, in degrees; over 20 triggers a warning |
| `plot` | yes | `width` (left to right) and `depth` (top to bottom) of the plot |
| `building` | no | Ground-floor footprint; rooms are zoned over this rectangle. Without it, the plot is used |
| `floors` | no | Footprints of upper floors that differ from the ground floor, each with its `level` |
| `rooms[]` | yes | `name`, `type`, `level` (0 = ground), `x`, `y`, `width`, `depth` |
| `rooms[].cook_facing` | no | Kitchen: the direction the cook faces at the stove |
| `rooms[].seat_facing` | no | Toilet: the direction the user faces when seated |
| `rooms[].steps`, `ascends_toward`, `turn` | no | Staircase: number of steps, direction of climb, `clockwise` or `anticlockwise` |
| `main_door` | no | A point at the door's centre, with its `level`; on the building's outer wall unless `facing` is given |
| `main_door.facing` | no | Direction you face walking out through the door (`N`, `E`, `S`, `W`); the door then counts as on that side wherever it sits, as for a flat's door off a lobby or sit-out |
| `main_door.from`, `into` | no | Names of the spaces outside and inside the door, shown in the report |
| `levels` | no | Names for the levels, such as `{"0": "Stilt", "1": "Typical floor (GF+3)"}`, shown in the report |
| `roads` | no | Sides of the plot with a road, such as `["N"]` or `["N", "E"]` |
| `rooms[].clear` | no | Clear size `[width, depth]` written on the drawing; Manaiyadi uses it in place of the rectangle's size |
| `rooms[].label_size` | no | That size as written, for reference |
| `cad` | no | Written by `cad_plan.py`: the drawing's file, units, north direction and, for each level, where the plan sits on the drawing. The overlay needs it; leave it as it is |
| `site[]` | no | Plot-level items: `name`, `type`, `x`, `y`, and `width`/`depth` if not a point |
| `openings_per_floor` | no | Doors plus windows counted per level |
| `floor_heights` | no | Floor-to-ceiling heights, ground floor first |
| `columns` | no | Total number of columns, or the structural step's list of columns (`id`, `x`, `y`, and `b`, `D` in mm); with a list, columns standing in the centre are flagged |

## Types

Rooms: `puja`, `kitchen`, `master_bedroom`, `bedroom`, `children_bedroom`, `guest_bedroom`, `living`, `dining`, `toilet` (any room with a WC), `bath` (bathing only), `staircase`, `store`, `heavy_store`, `study`, `cash_room`, `laundry`, `basement`, `balcony`, `verandah`, `terrace`, `portico`, `courtyard`, `overhead_tank`. Rooms of any other type (foyer, corridor, lift) are listed with "No rule in the source".

Site: `gate`, `borewell`, `well`, `sump`, `septic_tank`, `parking`, `garage`, `outhouse`, `generator`, `pool`, `water_feature`, `lawn`, `tree_tall`, `tree_small`, `tulsi`, `swing`, `rock_garden`.

An item lying outside its reference rectangle (a portico or balcony beyond the outline) is zoned by direction from the outline and marked so in the report.

Common Indian names are accepted as aliases (`pooja`, `hall`, `sit_out`, `oht`, `ugt`, `car_porch` and so on); see `aliases` in `scripts/zone_rules.json`. The overhead tank is zoned over the building's roof even when listed under `site`; put it under `rooms` with the top floor's `level` to be explicit.
