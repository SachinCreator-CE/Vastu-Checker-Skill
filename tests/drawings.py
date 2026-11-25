"""Synthetic house drawings for the tests: one small house written several ways.

The house (in mm, as drawn): plot 12 m x 9 m, building 10 m x 7 m, eight rooms including an L-shaped living room,
a puja in the living room's corner, a stair with treads, a tagged main door, windows, and site items. The same
geometry is written as:

  house_mm_block_north.dxf      metric DXF, INSUNITS set, north arrow block rotated so north points left,
                                plot as a closed polyline, sizes partly as separate texts, furniture block
  house_in_compass_north.dxf    feet-and-inch DXF, no INSUNITS, compass letters with north up the sheet,
                                plot as four lines with a gate gap to a road, sizes inside MTEXT
  house_mm_letters.dwg          metric DWG (AutoCAD 2010 format) written with scripts/dwg_write.py,
                                compass letters with north pointing left

Run:  python3 tests/drawings.py OUTDIR
"""
import math
import sys
from pathlib import Path

# ---------------------------------------------------------------- geometry (mm, y up)

T = 230
INNER = (1730, 730, 11270, 7270)
ROOMS = {
    'PUJA': (1730, 6000, 3000, 7270),
    'LIVING': (1730, 3615, 4500, 7270),
    'DINING': (4615, 3615, 8000, 7270),
    'KITCHEN': (8115, 3615, 11270, 7270),
    'GUEST BED ROOM': (1730, 730, 4500, 3500),
    'TOILET': (4615, 730, 6200, 3500),
    'STAIRCASE': (6315, 730, 8000, 3500),
    'MASTER BED ROOM': (8115, 730, 11270, 3500),
}
# hinge, rotation (deg), tag, opening box cut out of the wall faces
DOORS = [((3200, 7270), -90, 'MD', (3200, 7260, 4100, 7510)),
         ((8115, 4000), 0, 'D', (7990, 4000, 8125, 4800)),
         ((4800, 3500), -90, 'D1', (4800, 3490, 5550, 3625)),
         ((3200, 3500), -90, 'D', (3200, 3490, 4000, 3625)),
         ((9000, 3500), -90, 'D', (9000, 3490, 9800, 3625)),
         ((3000, 6900), 180, 'DP', (2990, 6200, 3125, 6900))]
OPENINGS_EXTRA = [(6500, 3490, 7800, 3625)]          # stair open to the dining room
WINDOWS = [(5500, 7270, 6700, 7500), (9300, 7270, 10500, 7500), (2500, 500, 3700, 730), (9300, 500, 10500, 730),
           (11270, 4800, 11500, 6000)]
LABELS = [  # name, size (mm), centre, height
    ('KITCHEN', (3150, 3650), (9700, 5450), 200),
    ('DINING', (3400, 3650), (6300, 5450), 200),
    ('LIVING', (2770, 3655), (2600, 4700), 200),
    ('POOJA', None, (2350, 6650), 150),
    ('GUEST BED ROOM', (2770, 2770), (3100, 2300), 200),
    ('TOILET', None, (5400, 2100), 200),
    ('STAIR CASE', None, (7150, 3200), 150),
    ('MASTER BED ROOM', (3155, 2770), (9700, 2700), 200),
]
SITE = [('SUMP', 'circle', (700, 8300), 450, (700, 7650)),
        ('SEPTIC TANK', 'rect', (11000, 100, 11850, 1300), None, (11400, 1500)),
        ('BORE WELL', 'circle', (500, 4500), 150, (600, 4100))]


def wall_lines():
    faces = []

    def rect(x0, y0, x1, y1):
        return [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]
    for nm, r in ROOMS.items():
        if nm == 'LIVING':
            x0, y0, x1, y1 = r
            pts = [(x0, y0), (x1, y0), (x1, y1), (3115, y1), (3115, 5885), (x0, 5885)]
            faces += [(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]
        else:
            faces += rect(*r)
    faces += rect(INNER[0] - T, INNER[1] - T, INNER[2] + T, INNER[3] + T)
    boxes = [d[3] for d in DOORS] + OPENINGS_EXTRA + \
        [(w[0], w[1] - 10, w[2], w[3] + 10) if w[2] - w[0] > w[3] - w[1] else (w[0] - 10, w[1], w[2] + 10, w[3]) for w in WINDOWS]

    def cut(seg, box):
        (x1, y1), (x2, y2) = seg
        bx0, by0, bx1, by1 = box
        if y1 == y2 and by0 <= y1 <= by1:
            a, b = sorted((x1, x2))
            if not (bx0 < b and bx1 > a):
                return [seg]
            res = []
            if bx0 > a:
                res.append(((a, y1), (min(bx0, b), y1)))
            if bx1 < b:
                res.append(((max(bx1, a), y1), (b, y1)))
            return res
        if x1 == x2 and bx0 <= x1 <= bx1:
            a, b = sorted((y1, y2))
            if not (by0 < b and by1 > a):
                return [seg]
            res = []
            if by0 > a:
                res.append(((x1, a), (x1, min(by0, b))))
            if by1 < b:
                res.append(((x1, max(by1, a)), (x1, b)))
            return res
        return [seg]
    for box in boxes:
        faces = [s for f in faces for s in cut(f, box)]
    out = [('A-WALL', a, b) for a, b in faces]
    for bx0, by0, bx1, by1 in boxes:                       # jamb caps
        if by1 - by0 < bx1 - bx0:
            out += [('A-WALL', (x, by0 + 10), (x, by1 - 10)) for x in (bx0, bx1)]
        else:
            out += [('A-WALL', (bx0 + 10, y), (bx1 - 10, y)) for y in (by0, by1)]
    for x0, y0, x1, y1 in WINDOWS:                         # three glazing lines
        if x1 - x0 > y1 - y0:
            out += [('A-GLAZ', (x0, y), (x1, y)) for y in (y0, (y0 + y1) / 2, y1)]
        else:
            out += [('A-GLAZ', (x, y0), (x, y1)) for x in (x0, (x0 + x1) / 2, x1)]
    for i in range(6):                                     # stair treads
        x = 6550 + i * 250
        out.append(('A-STAIR', (x, 900), (x, 3300)))
    return out


def door_geometry(hinge, rot, r=900):
    """Leaf line and swing arc of a door block placed at hinge with rotation (degrees)."""
    a = math.radians(rot)
    hx, hy = hinge
    tip = (hx + r * math.cos(a), hy + r * math.sin(a))
    return tip, (math.degrees(a), math.degrees(a) + 90)


def feet_inches(mm):
    inches = round(mm / 25.4)
    return f"{inches // 12}'{inches % 12}\""


# ---------------------------------------------------------------- DXF writing

class Dxf:
    def __init__(self, insunits=None):
        self.out, self.h, self.insunits = [], 0x100, insunits

    def g(self, c, v):
        self.out += [f'{c:>3}', str(v)]

    def hd(self):
        self.h += 1
        return format(self.h, 'X')

    def ent(self, t, layer, pairs, sub):
        self.g(0, t)
        self.g(5, self.hd())
        self.g(100, 'AcDbEntity')
        self.g(8, layer)
        if sub:
            self.g(100, sub)
        for c, v in pairs:
            self.g(c, v)

    def line(self, layer, a, b):
        self.ent('LINE', layer, [(10, a[0]), (20, a[1]), (30, 0.0), (11, b[0]), (21, b[1]), (31, 0.0)], 'AcDbLine')

    def arc(self, layer, c, r, a0, a1, extrusion_down=False):
        pairs = [(10, c[0]), (20, c[1]), (30, 0.0), (40, r)]
        if extrusion_down:
            pairs += [(210, 0.0), (220, 0.0), (230, -1.0)]
        self.ent('ARC', layer, pairs + [(100, 'AcDbArc'), (50, a0 % 360), (51, a1 % 360)], 'AcDbCircle')

    def circle(self, layer, c, r):
        self.ent('CIRCLE', layer, [(10, c[0]), (20, c[1]), (30, 0.0), (40, r)], 'AcDbCircle')

    def pline(self, layer, pts, closed):
        self.ent('LWPOLYLINE', layer, [(90, len(pts)), (70, 1 if closed else 0)] +
                 [p for x, y in pts for p in ((10, x), (20, y))], 'AcDbPolyline')

    def text(self, layer, s, c, h, rot=0.0):
        self.ent('TEXT', layer, [(10, c[0]), (20, c[1]), (30, 0.0), (40, h), (1, s), (50, rot), (72, 1),
                                 (11, c[0]), (21, c[1]), (31, 0.0), (100, 'AcDbText'), (73, 2)], 'AcDbText')

    def mtext(self, layer, s, c, h):
        self.ent('MTEXT', layer, [(10, c[0]), (20, c[1]), (30, 0.0), (40, h), (41, 3000.0), (71, 5), (1, s)], 'AcDbMText')

    def insert(self, layer, name, p, rot):
        self.ent('INSERT', layer, [(2, name), (10, p[0]), (20, p[1]), (30, 0.0), (50, rot)], 'AcDbBlockReference')

    def save(self, path, blocks):
        head = ['  0', 'SECTION', '  2', 'HEADER', '  9', '$ACADVER', '  1', 'AC1015']
        if self.insunits is not None:
            head += ['  9', '$INSUNITS', ' 70', str(self.insunits)]
        head += ['  0', 'ENDSEC', '  0', 'SECTION', '  2', 'TABLES', '  0', 'TABLE', '  2', 'LAYER', ' 70', '12']
        for nm, col in (('0', 7), ('A-WALL', 1), ('A-DOOR', 3), ('A-GLAZ', 4), ('A-PLOT', 2), ('A-TEXT', 7),
                        ('A-FURN', 8), ('A-DIMS', 8), ('A-ANNO-NORTH', 1), ('A-STAIR', 7), ('A-HATCH', 9), ('A-SITE', 6)):
            head += ['  0', 'LAYER', '  2', nm, ' 70', '0', ' 62', str(col), '  6', 'CONTINUOUS']
        head += ['  0', 'ENDTAB', '  0', 'ENDSEC', '  0', 'SECTION', '  2', 'BLOCKS']
        body = []
        for name, ents in blocks.items():
            b = Dxf()
            b.h = self.h + 1000
            for f, args in ents:
                getattr(b, f)(*args)
            body += ['  0', 'BLOCK', '  8', '0', '  2', name, ' 70', '0', ' 10', '0.0', ' 20', '0.0', ' 30', '0.0',
                     '  3', name] + b.out + ['  0', 'ENDBLK', '  8', '0']
        tail = ['  0', 'ENDSEC', '  0', 'SECTION', '  2', 'ENTITIES'] + self.out
        # a dimension and a hatch, which the reader must skip
        tail += ['  0', 'DIMENSION', '  8', 'A-DIMS', '100', 'AcDbDimension', '  2', '*D1', ' 10', '0.0', ' 20', '-500.0',
                 ' 70', '32', '  1', '', '  0', 'HATCH', '  8', 'A-HATCH', '100', 'AcDbHatch', '  2', 'SOLID', ' 70', '1',
                 ' 91', '0', '  0', 'ENDSEC', '  0', 'EOF']
        Path(path).write_text('\n'.join(head + body + tail) + '\n')


def write_mm_block_north(path):
    d = Dxf(insunits=4)
    for layer, a, b in wall_lines():
        d.line(layer, a, b)
    d.pline('A-PLOT', [(0, 0), (12000, 0), (12000, 9000), (0, 9000)], True)
    for hinge, rot, tag, _ in DOORS:
        d.insert('A-DOOR', 'D900', hinge, rot)
    for name, size, c, h in LABELS:
        if name in ('LIVING', 'GUEST BED ROOM', 'MASTER BED ROOM') and size:
            d.text('A-TEXT', name, c, h)
            d.text('A-TEXT', f'{size[0]} X {size[1]}', (c[0], c[1] - 350), h)
        elif size:
            d.mtext('A-TEXT', f'{name}\\P{size[0] / 1000:.2f}X{size[1] / 1000:.2f}', c, h)
        else:
            d.text('A-TEXT', name, c, h)
    d.text('A-TEXT', 'UP', (7150, 1600), 150)
    for hinge, rot, tag, box in DOORS:
        bx0, by0, bx1, by1 = box
        d.text('A-TEXT', tag, ((bx0 + bx1) / 2 + (0 if by1 - by0 < bx1 - bx0 else 250),
                               (by0 + by1) / 2 - (300 if by1 - by0 < bx1 - bx0 else 0)), 120)
    for name, kind, geom, r, tpos in SITE:
        if kind == 'circle':
            d.circle('A-SITE', geom, r)
        else:
            x0, y0, x1, y1 = geom
            d.pline('A-SITE', [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], True)
        d.text('A-SITE', name, tpos, 130)
    d.text('A-TEXT', 'GATE', (2800, 8700), 180)
    d.text('A-TEXT', '6.0 M WIDE ROAD', (6000, 9900), 300)
    d.text('A-TEXT', 'GROUND FLOOR PLAN', (6000, -900), 300)
    d.insert('A-FURN', 'BED', (9000, 1000), 0)
    d.insert('A-ANNO-NORTH', 'NORTH', (14500, 7500), 90)
    blocks = {
        'D900': [('line', ('0', (0.0, 0.0), (900.0, 0.0))), ('arc', ('0', (0.0, 0.0), 900.0, 0.0, 90.0))],
        'NORTH': [('line', ('0', (0.0, -600.0), (0.0, 600.0))), ('line', ('0', (-200.0, 300.0), (0.0, 600.0))),
                  ('line', ('0', (200.0, 300.0), (0.0, 600.0))), ('circle', ('0', (0.0, 0.0), 700.0)),
                  ('text', ('0', 'N', (0.0, 800.0), 250.0))],
        'BED': [('pline', ('0', [(0, 0), (1800, 0), (1800, 2000), (0, 2000)], True)),
                ('line', ('0', (0.0, 1600.0), (1800.0, 1600.0)))],
    }
    d.save(path, blocks)


def write_in_compass_north(path):
    """The same house rotated so north is up, in inches, plot as lines with a gate gap and a road beyond it."""
    k = 1 / 25.4

    def P(p):                     # rotate the mm drawing a quarter turn clockwise: its left (north) goes up
        x, y = p
        return (y * k, (12000 - x) * k)
    d = Dxf(insunits=None)
    for layer, a, b in wall_lines():
        d.line(layer, P(a), P(b))
    # plot: 9000 x 12000 mm becomes 354 x 472 in; gate gap on the east (right) side? north is up, road on the E
    W, H = 9000 * k, 12000 * k
    gate = (H * 0.62, H * 0.80)
    d.line('A-PLOT', (0, 0), (W, 0))
    d.line('A-PLOT', (0, H), (W, H))
    d.line('A-PLOT', (0, 0), (0, H))
    d.line('A-PLOT', (W, 0), (W, gate[0]))
    d.line('A-PLOT', (W, gate[1]), (W, H))
    d.text('A-TEXT', 'MAIN GATE', (W - 30, (gate[0] + gate[1]) / 2), 6, 90)
    d.text('A-TEXT', 'R O A D', (W + 60, H / 2), 9, 90)
    for (hx, hy), rot, tag, box in DOORS:
        tip, (a0, a1) = door_geometry((hx, hy), rot)
        d.line('A-DOOR', P((hx, hy)), P(tip))
        c = P((hx, hy))
        d.arc('A-DOOR', c, 900 * k, a0 - 90, a1 - 90)
        bx0, by0, bx1, by1 = box
        q = P(((bx0 + bx1) / 2 + (0 if by1 - by0 < bx1 - bx0 else 250), (by0 + by1) / 2 - (300 if by1 - by0 < bx1 - bx0 else 0)))
        d.text('A-TEXT', 'MD' if tag == 'MD' else tag, q, 4)
    for name, size, c, h in LABELS:
        if size:
            # rotated drawing: the first size runs up the sheet now
            d.mtext('A-TEXT', f'{name}\\P{feet_inches(size[1])}X{feet_inches(size[0])}', P(c), h * k)
        else:
            d.mtext('A-TEXT', name, P(c), h * k)
    d.text('A-TEXT', 'UP', P((7150, 1600)), 5)
    for name, kind, geom, r, tpos in SITE:
        if kind == 'circle':
            d.circle('A-SITE', P(geom), r * k)
        else:
            x0, y0, x1, y1 = geom
            d.pline('A-SITE', [P((x0, y0)), P((x1, y0)), P((x1, y1)), P((x0, y1))], True)
        d.text('A-SITE', name, P(tpos), 5)
    for s, (dx, dy) in (('N', (0, 14)), ('S', (0, -14)), ('E', (14, 0)), ('W', (-14, 0))):
        d.text('A-ANNO', s, (W + 120 + dx, H - 60 + dy), 4)
    d.circle('A-ANNO', (W + 120, H - 60), 10)
    d.text('A-TEXT', 'GROUND FLOOR PLAN', (W / 2, -36), 9)
    d.save(path, {})


def write_mm_letters_dwg(path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'vastu-checker' / 'scripts'))
    from dwg_write import DwgWriter
    w = DwgWriter()
    for layer, a, b in wall_lines():
        w.line(layer, a, b)
    w.pline('A-PLOT', [(0, 0), (12000, 0), (12000, 9000), (0, 9000)], True)
    for (hx, hy), rot, tag, box in DOORS:
        tip, (a0, a1) = door_geometry((hx, hy), rot)
        w.line('A-DOOR', (hx, hy), tip)
        w.arc('A-DOOR', (hx, hy), 900, a0, a1)
        bx0, by0, bx1, by1 = box
        w.text('A-TEXT', (((bx0 + bx1) / 2 + (0 if by1 - by0 < bx1 - bx0 else 250)),
                          (by0 + by1) / 2 - (300 if by1 - by0 < bx1 - bx0 else 0)), 120, tag, 'center', 'middle')
    for name, size, c, h in LABELS:
        s = name if not size else f'{name}\n{size[0]}X{size[1]}'
        w.text('A-TEXT', c, h, s, 'center', 'middle', bold=False)
    w.text('A-TEXT', (7150, 1600), 150, 'UP', 'center', 'middle')
    for name, kind, geom, r, tpos in SITE:
        if kind == 'circle':
            w.circle('A-SITE', geom, r)
        else:
            x0, y0, x1, y1 = geom
            w.pline('A-SITE', [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], True)
        w.text('A-SITE', tpos, 130, name, 'center', 'middle')
    w.text('A-TEXT', (6000, 9900), 300, '6.0 M WIDE ROAD', 'center', 'middle')
    w.text('A-TEXT', (6000, -900), 300, 'GROUND FLOOR PLAN', 'center', 'middle')
    for s, (dx, dy) in (('N', (-450, 0)), ('S', (450, 0)), ('E', (0, 450)), ('W', (0, -450))):
        w.text('A-ANNO', (14500 + dx, 7500 + dy), 150, s, 'center', 'middle')
    w.circle('A-ANNO', (14500, 7500), 300)
    w.save(path)


def write_all(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    write_mm_block_north(out / 'house_mm_block_north.dxf')
    write_in_compass_north(out / 'house_in_compass_north.dxf')
    write_mm_letters_dwg(out / 'house_mm_letters.dwg')
    return sorted(out.iterdir())


if __name__ == '__main__':
    for p in write_all(sys.argv[1] if len(sys.argv) > 1 else 'test-drawings'):
        print(p)
