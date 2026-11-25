#!/usr/bin/env python3
"""Draw the Vastu check on the user's own CAD plan.

Usage:
  python3 vastu_overlay.py plan.json --drawing drawing.dwg --out DIR [--name NAME] [--manaiyadi]

plan.json is the file cad_plan.py wrote (it carries a "cad" block that places each floor back on the
drawing). The check is run again from plan.json, so edits made to it are drawn. Writes, into DIR:
  NAME_vastu.dwg  the overlay layers only, in the drawing's own coordinates (AutoCAD 2010 format)
  NAME_vastu.dxf  the same as R12 DXF, a fallback for any CAD program
  NAME_vastu.pdf  and NAME_vastu.png: the plan with the overlay, to view and share

Overlay layers: VASTU-GRID (3x3 zones), VASTU-ZONE (zone names), VASTU-STATUS (each room outlined in its
status colour, with a tag), VASTU-DOOR (main door and its facing), VASTU-NORTH, VASTU-NOTES (title, legend).
Because the coordinates are the drawing's own, the DWG lines up when attached at 0,0 (XATTACH) or copied in
with PASTEORIG.
"""
import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vastu_check  # noqa: E402

STATUS_ACI = {'Follows': 3, 'Acceptable': 2, 'Contested': 30, 'Against': 1}
STATUS_RGB = {'Follows': '#2e9e57', 'Acceptable': '#c0a000', 'Contested': '#f08a00', 'Against': '#e0302b'}
GREY_ACI, GREY_RGB = 8, '#8a8a8a'
LAYERS = {'VASTU-GRID': 5, 'VASTU-ZONE': 5, 'VASTU-STATUS': 7, 'VASTU-DOOR': 6, 'VASTU-NORTH': 1, 'VASTU-NOTES': 7,
          'VASTU-PLOT': 94}
SHORT = {'NE': 'puja, water, open', 'N': 'living, cash, door', 'E': 'living, bath, door', 'SE': 'kitchen, fire',
         'S': 'bedroom, store', 'SW': 'master bed, stairs, heavy', 'W': 'dining, study, kids', 'NW': 'guest, store, parking',
         'C': 'keep open'}


def status_of(st):
    for k in STATUS_ACI:
        if st and st.startswith(k):
            return k
    return None


class Items:
    """Drawing items in drawing coordinates, written to DXF/DWG and drawn to PDF/PNG alike."""

    def __init__(self):
        self.items = []

    def line(self, layer, a, b, aci=256, dashed=False):
        self.items.append(('line', layer, a, b, aci, dashed))

    def pline(self, layer, pts, closed=False, width=0.0, aci=256, dashed=False):
        self.items.append(('pline', layer, [tuple(p) for p in pts], closed, width, aci, dashed))

    def text(self, layer, p, h, s, aci=256, ha='left', va='bottom', rot=0.0):
        self.items.append(('text', layer, p, h, s, aci, ha, va, rot))

    def solid(self, layer, pts, aci=256):
        self.items.append(('solid', layer, pts, aci))


def build_items(plan, results):
    cad = plan.get('cad')
    if not cad:
        sys.exit('This plan file has no "cad" block; make it with cad_plan.py from the drawing.')
    unit_key = next(k for k in cad if k.startswith('units_per_'))
    k = cad[unit_key]
    up = plan.get('up', 'N')
    floors = {f['level']: f for f in plan.get('floors', [])}
    base_level = min(p['level'] for p in cad['plans'])
    by_level = {}
    for it in results['items']:
        by_level.setdefault((it['kind'], it['name'], it['level']), it)
    it_out = Items()
    north = cad.get('north_deg')
    for cp in cad['plans']:
        lv = cp['level']
        ox, oy = cp['origin']

        def X(x, ox=ox):
            return ox + x * k

        def Y(y, oy=oy):
            return oy - y * k
        ref = floors.get(lv) or plan.get('building') or {'x': 0, 'y': 0, 'width': plan['plot']['width'],
                                                         'depth': plan['plot']['depth']}
        x0, y1, x1, y0 = X(ref['x']), Y(ref['y']), X(ref['x'] + ref['width']), Y(ref['y'] + ref['depth'])
        w, h = x1 - x0, y1 - y0
        th = max(min(w, h) / 30, 4 * k / 12)
        # plot outline on the lowest level
        if lv == base_level:
            pw, pd = plan['plot']['width'], plan['plot']['depth']
            it_out.pline('VASTU-PLOT', [(X(0), Y(0)), (X(pw), Y(0)), (X(pw), Y(pd)), (X(0), Y(pd))], True, aci=94,
                         dashed=True)
        # 3x3 grid and zone names
        it_out.pline('VASTU-GRID', [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], True, width=th * 0.12, aci=5)
        for i in (1, 2):
            it_out.line('VASTU-GRID', (x0 + w * i / 3, y0), (x0 + w * i / 3, y1), aci=5, dashed=True)
            it_out.line('VASTU-GRID', (x0, y0 + h * i / 3), (x1, y0 + h * i / 3), aci=5, dashed=True)
        top, right, bottom, left = vastu_check.drawing_dirs(up)
        for r, v in enumerate((top, None, bottom)):
            for c, hz in enumerate((left, None, right)):
                z = vastu_check.compose(v, hz)
                cx0 = x0 + w * c / 3
                cy1 = y1 - h * r / 3
                it_out.text('VASTU-ZONE', (cx0 + th * 0.4, cy1 - th * 1.5), th * 1.25, z, aci=5)
        # rooms and site items of this level
        groups = {}
        for room in plan.get('rooms', []):
            if room.get('level', 0) == lv:
                groups.setdefault(('room', room['name'], lv), []).append(room)
        if lv == base_level:
            for s in plan.get('site', []):
                groups.setdefault(('site', s['name'], s.get('level', 0)), []).append(s)
        for key, parts in groups.items():
            res = by_level.get(key) or by_level.get((key[0], key[1], 0))
            st = status_of(res['status']) if res else None
            aci = STATUS_ACI.get(st, GREY_ACI)
            for p in parts:
                if p.get('width') and p.get('depth'):
                    a, b = (X(p['x']), Y(p['y'])), (X(p['x'] + p['width']), Y(p['y'] + p['depth']))
                    it_out.pline('VASTU-STATUS', [(a[0], a[1]), (b[0], a[1]), (b[0], b[1]), (a[0], b[1])], True,
                                 width=th * 0.18, aci=aci)
                    tx, ty = a[0] + th * 0.35, b[1] + th * 0.35
                else:
                    cx, cy = X(p['x']), Y(p['y'])
                    r_ = th * 0.6
                    it_out.pline('VASTU-STATUS', [(cx - r_, cy), (cx, cy + r_), (cx + r_, cy), (cx, cy - r_)], True,
                                 width=th * 0.15, aci=aci)
                    tx, ty = cx + r_ * 1.4, cy
            if res:
                word = {'No rule in the source': 'no rule', 'Not listed': 'not listed'}.get(res['status'], res['status'])
                tag = f"{key[1]}: {res['zone'] or '-'} {word}"
                it_out.text('VASTU-STATUS', (tx, ty), th * 0.55, tag, aci=aci)
        # main door
        md = plan.get('main_door')
        if md and md.get('level', 0) == lv:
            px_, py_ = X(md['x']), Y(md['y'])
            facing = md.get('facing')
            st = status_of((results.get('door') or {}).get('status'))
            aci = STATUS_ACI.get(st, 6)
            if facing and north is not None:
                b = {'N': 0, 'E': 90, 'S': 180, 'W': 270}[facing]
                ang = math.radians(north - b)
                L = th * 3.2
                tip = (px_ + math.cos(ang) * L, py_ + math.sin(ang) * L)
                it_out.pline('VASTU-DOOR', [(px_, py_), tip], width=th * 0.2, aci=aci)
                hl = th * 0.9
                for s_ in (-1, 1):
                    a2 = ang + math.pi + s_ * 0.45
                    it_out.pline('VASTU-DOOR', [tip, (tip[0] + math.cos(a2) * hl, tip[1] + math.sin(a2) * hl)],
                                 width=th * 0.2, aci=aci)
                it_out.text('VASTU-DOOR', (tip[0] + th * 0.4, tip[1]), th * 0.7, f'MAIN DOOR faces {facing}', aci=aci)
            else:
                it_out.text('VASTU-DOOR', (px_, py_), th * 0.7, 'MAIN DOOR', aci=aci)
        # north arrow and title
        bx0, by0, bx1, by1 = cp['box']
        if north is not None:
            L = th * 4
            cxn, cyn = x1 + th * 6, y1 - th * 3
            a = math.radians(north)
            tip = (cxn + math.cos(a) * L / 2, cyn + math.sin(a) * L / 2)
            tail = (cxn - math.cos(a) * L / 2, cyn - math.sin(a) * L / 2)
            it_out.pline('VASTU-NORTH', [tail, tip], width=th * 0.2, aci=1)
            for s_ in (-1, 1):
                a2 = a + math.pi + s_ * 0.4
                it_out.pline('VASTU-NORTH', [tip, (tip[0] + math.cos(a2) * th, tip[1] + math.sin(a2) * th)],
                             width=th * 0.2, aci=1)
            it_out.text('VASTU-NORTH', (tip[0] + math.cos(a) * th * 0.8 - th * 0.4, tip[1] + math.sin(a) * th * 0.8 - th * 0.5),
                        th * 1.1, 'N', aci=1)
        name = (plan.get('levels') or {}).get(str(lv), f'Level {lv}')
        ytop = max(y1, Y(0)) if lv == base_level else y1
        md_ = plan.get('main_door') or {}
        if md_.get('level', 0) == lv:
            ytop = max(ytop, Y(md_['y']) + th * 4.5)
        it_out.text('VASTU-NOTES', (x0, ytop + th * 2.2), th * 1.1, f'VASTU CHECK - {name}', aci=7)
        it_out.text('VASTU-NOTES', (x0, ytop + th * 0.9), th * 0.55,
                    'Green follows, yellow acceptable, orange contested, red against, grey no rule. Zones over the '
                    'floor outline.', aci=7)
    return it_out


# ---------------------------------------------------------------- writers

def write_dxf(items, path):
    """R12 ASCII DXF: opens in every CAD program."""
    out = ['0', 'SECTION', '2', 'HEADER', '9', '$ACADVER', '1', 'AC1009', '0', 'ENDSEC',
           '0', 'SECTION', '2', 'TABLES',
           '0', 'TABLE', '2', 'LTYPE', '70', '2',
           '0', 'LTYPE', '2', 'CONTINUOUS', '70', '0', '3', 'Solid line', '72', '65', '73', '0', '40', '0.0',
           '0', 'LTYPE', '2', 'DASHED', '70', '0', '3', '__ __ __', '72', '65', '73', '2', '40', '18.0', '49', '12.0',
           '49', '-6.0', '0', 'ENDTAB',
           '0', 'TABLE', '2', 'LAYER', '70', str(len(LAYERS))]
    for name, col in LAYERS.items():
        out += ['0', 'LAYER', '2', name, '70', '0', '62', str(col), '6', 'CONTINUOUS']
    out += ['0', 'ENDTAB', '0', 'ENDSEC', '0', 'SECTION', '2', 'ENTITIES']

    def common(layer, aci, dashed):
        c = ['8', layer]
        if dashed:
            c += ['6', 'DASHED']
        if aci != 256:
            c += ['62', str(aci)]
        return c
    for it in items.items:
        kind = it[0]
        if kind == 'line':
            _, layer, a, b, aci, dashed = it
            out += ['0', 'LINE'] + common(layer, aci, dashed) + ['10', f'{a[0]:.4f}', '20', f'{a[1]:.4f}', '30', '0.0',
                                                                 '11', f'{b[0]:.4f}', '21', f'{b[1]:.4f}', '31', '0.0']
        elif kind == 'pline':
            _, layer, pts, closed, width, aci, dashed = it
            out += ['0', 'POLYLINE'] + common(layer, aci, dashed) + ['66', '1', '10', '0.0', '20', '0.0', '30', '0.0',
                                                                     '70', '1' if closed else '0',
                                                                     '40', f'{width:.4f}', '41', f'{width:.4f}']
            for x, y in pts:
                out += ['0', 'VERTEX', '8', layer, '10', f'{x:.4f}', '20', f'{y:.4f}', '30', '0.0']
            out += ['0', 'SEQEND', '8', layer]
        elif kind == 'text':
            _, layer, p, h, s, aci, ha, va, rot = it
            out += ['0', 'TEXT'] + common(layer, aci, False) + ['10', f'{p[0]:.4f}', '20', f'{p[1]:.4f}', '30', '0.0',
                                                                '40', f'{h:.4f}', '1', s.replace('\n', ' '),
                                                                '50', f'{rot:.3f}']
        elif kind == 'solid':
            _, layer, pts, aci = it
            q = list(pts) + [pts[-1]] * (4 - len(pts))
            out += ['0', 'SOLID'] + common(layer, aci, False)
            for i, (x, y) in enumerate((q[0], q[1], q[3], q[2])):
                out += [str(10 + i), f'{x:.4f}', str(20 + i), f'{y:.4f}', str(30 + i), '0.0']
    out += ['0', 'ENDSEC', '0', 'EOF']
    Path(path).write_text('\n'.join(out) + '\n', encoding='cp1252', errors='replace')


def write_dwg(items, path):
    from dwg_write import DwgWriter
    w = DwgWriter()
    for name, col in LAYERS.items():
        w.layer(name, col)
    for it in items.items:
        kind = it[0]
        if kind == 'line':
            _, layer, a, b, aci, dashed = it
            w.line(layer, a, b, color=None if aci == 256 else aci, ltype='HIDDEN' if dashed else None,
                   ltscale=1.0)
        elif kind == 'pline':
            _, layer, pts, closed, width, aci, dashed = it
            w.pline(layer, pts, closed, width or None, color=None if aci == 256 else aci,
                    ltype='HIDDEN' if dashed else None)
        elif kind == 'text':
            _, layer, p, h, s, aci, ha, va, rot = it
            w.text(layer, p, h, s, 'left', 'bottom', rot, bold=False, color=None if aci == 256 else aci)
        elif kind == 'solid':
            _, layer, pts, aci = it
            w.solid(layer, pts, color=None if aci == 256 else aci)
    w.save(path)


def render(plan, items, drawing, path_png, path_pdf):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from matplotlib.patches import Polygon
    aci_rgb = {1: '#e0302b', 2: '#c0a000', 3: '#2e9e57', 5: '#1f5fbf', 6: '#b0309e', 7: '#222222', 8: GREY_RGB,
               30: '#f08a00', 94: '#2e7d32'}
    cad = plan['cad']
    segs = []
    if drawing is not None:
        from cad_read import load, flatten
        geo = flatten(load(drawing))
        segs = geo['segs']
    plans = cad['plans']
    n = len(plans)
    fig, axes = plt.subplots(1, n, figsize=(8.5 * n, 11.5), dpi=120, squeeze=False)
    for ax, cp in zip(axes[0], plans):
        bx0, by0, bx1, by1 = cp['box']
        sel = [((s[0], s[1]), (s[2], s[3])) for s in segs
               if (bx0 <= s[0] <= bx1 and by0 <= s[1] <= by1) or (bx0 <= s[2] <= bx1 and by0 <= s[3] <= by1)]
        ax.add_collection(LineCollection(sel, linewidths=0.3, colors='#b5b5b5', zorder=1))
        for it in items.items:
            kind = it[0]
            pts = []
            if kind == 'line':
                pts = [it[2], it[3]]
            elif kind == 'pline':
                pts = it[2]
            elif kind in ('text', 'solid'):
                pts = [it[2]] if kind == 'text' else it[2]
            if not pts or not any(bx0 <= p[0] <= bx1 and by0 - (by1 - by0) * 0.1 <= p[1] <= by1 + (by1 - by0) * 0.1
                                  for p in pts):
                continue
            if kind == 'line':
                _, layer, a, b, aci, dashed = it
                ax.plot([a[0], b[0]], [a[1], b[1]], color=aci_rgb.get(aci, '#1f5fbf'), lw=0.8,
                        ls=(0, (5, 4)) if dashed else '-', zorder=3)
            elif kind == 'pline':
                _, layer, pts, closed, width, aci, dashed = it
                col = aci_rgb.get(aci, '#555555')
                if layer == 'VASTU-STATUS' and closed and len(pts) == 4:
                    ax.add_patch(Polygon(pts, closed=True, facecolor=col, alpha=0.22, edgecolor='none', zorder=2))
                q = pts + ([pts[0]] if closed else [])
                lw = 2.2 if layer in ('VASTU-STATUS', 'VASTU-DOOR', 'VASTU-NORTH') else 1.0
                ax.plot([p[0] for p in q], [p[1] for p in q], color=col, lw=lw,
                        ls=(0, (6, 4)) if dashed else '-', zorder=4)
            elif kind == 'text':
                _, layer, p, h, s, aci, ha, va, rot = it
                size = 6.5 if h < 1e9 else 6.5
                if layer == 'VASTU-ZONE':
                    size = 11 if len(s) <= 2 else 5.5
                elif layer == 'VASTU-NOTES':
                    size = 10 if 'VASTU CHECK' in s else 6
                elif layer == 'VASTU-NORTH':
                    size = 12
                elif layer == 'VASTU-DOOR':
                    size = 8
                ax.text(p[0], p[1], s, fontsize=size, color=aci_rgb.get(aci, '#222222'), zorder=6,
                        fontweight='bold' if layer in ('VASTU-ZONE', 'VASTU-NORTH', 'VASTU-DOOR') and len(s) < 30 else 'normal',
                        bbox=dict(boxstyle='round,pad=0.12', fc='white', ec='none', alpha=0.65)
                        if layer == 'VASTU-STATUS' else None)
        ys_ = [it[2][1] for it in items.items if it[0] == 'text' and bx0 <= it[2][0] <= bx1]
        ax.set_xlim(bx0, bx1)
        ax.set_ylim(by0, max([by1] + [y + (by1 - by0) * 0.03 for y in ys_ if y < by1 + (by1 - by0) * 0.5]))
        ax.set_aspect('equal')
        ax.set_xticks([])
        ax.set_yticks([])
    fig.tight_layout()
    fig.savefig(path_png)
    fig.savefig(path_pdf)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('plan', help='plan JSON written by cad_plan.py (edited or not)')
    ap.add_argument('--drawing', help='the DWG or DXF it came from, drawn underneath in the PDF and PNG')
    ap.add_argument('--out', default='.')
    ap.add_argument('--name')
    ap.add_argument('--manaiyadi', action='store_true')
    args = ap.parse_args()
    plan = json.loads(Path(args.plan).read_text())
    results = {}
    report = vastu_check.check(plan, args.manaiyadi, results)
    items = build_items(plan, results)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    name = args.name or Path(args.plan).stem.replace('_plan', '')
    done = []
    dxf = out / f'{name}_vastu.dxf'
    write_dxf(items, dxf)
    done.append(dxf)
    try:
        dwg = out / f'{name}_vastu.dwg'
        write_dwg(items, dwg)
        done.append(dwg)
    except Exception as ex:  # the DXF still stands
        print(f'(DWG not written: {ex}; use the DXF)')
    png, pdf = out / f'{name}_vastu.png', out / f'{name}_vastu.pdf'
    render(plan, items, args.drawing, png, pdf)
    done += [pdf, png]
    rep = out / f'{name}_vastu_report.md'
    rep.write_text(report)
    done.append(rep)
    print('\n'.join(str(p) for p in done))


if __name__ == '__main__':
    main()
