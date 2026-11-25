"""Pictures for checking what was read from a CAD drawing, and the Vastu overlay on the plan.

draw_read(): each plan used, with the rooms, building outline, plot, doors, main door, north and the 3x3 grid.
draw_index(): every floor plan found in the drawing, numbered, to choose from.
draw_overlay(): the Vastu result on the user's own plan (used by vastu_overlay.py).
"""
import math

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.collections import LineCollection  # noqa: E402
from matplotlib.patches import Rectangle, FancyArrow  # noqa: E402

TYPE_COLOURS = {
    'puja': '#e8a33d', 'kitchen': '#d9534f', 'master_bedroom': '#5b7fd6', 'bedroom': '#7f9be6',
    'children_bedroom': '#8fb0f0', 'guest_bedroom': '#9fbcef', 'living': '#4caf7a', 'dining': '#8bc34a',
    'toilet': '#26a5b4', 'bath': '#4fc3d0', 'staircase': '#8d6e63', 'store': '#a1887f', 'study': '#9575cd',
    'laundry': '#f06292', 'verandah': '#c0ca33', 'portico': '#c0ca33', 'balcony': '#d4e157',
    'foyer': '#bdbdbd', 'lift': '#9e9e9e', 'parking': '#90a4ae', 'gate': '#000000', 'sump': '#1e88e5',
    'borewell': '#1e88e5', 'septic_tank': '#6d4c41', 'overhead_tank': '#1565c0',
}
STATUS_COLOURS = {'Follows': '#2e9e57', 'Acceptable': '#9ccc65', 'Contested': '#ffb300', 'Against': '#e53935',
                  'Need information': '#9e9e9e', 'No rule in the source': '#bdbdbd', 'Not listed': '#bdbdbd',
                  'Outside its reference area': '#9e9e9e'}
ZONE_ORDER = ['NW', 'N', 'NE', 'W', 'C', 'E', 'SW', 'S', 'SE']


def _segs_in(dr, box, layers_all=False):
    x0, y0, x1, y1 = box
    out = []
    for s in dr.geo['segs']:
        if (x0 <= s[0] <= x1 and y0 <= s[1] <= y1) or (x0 <= s[2] <= x1 and y0 <= s[3] <= y1):
            out.append(((s[0], s[1]), (s[2], s[3])))
    return out


def drawing_dirs(up):
    cw = {'N': 'E', 'E': 'S', 'S': 'W', 'W': 'N'}
    opp = {'N': 'S', 'S': 'N', 'E': 'W', 'W': 'E'}
    return up, cw[up], opp[up], opp[cw[up]]


def zone_name(v, h):
    parts = [d for d in (v, h) if d]
    if not parts:
        return 'C'
    if len(parts) == 1:
        return parts[0]
    return next(d for d in parts if d in 'NS') + next(d for d in parts if d in 'EW')


def grid_cells(x0, y0, x1, y1, up):
    """3x3 zones of a drawing-space box (y up): {zone: (cx0, cy0, cx1, cy1)}."""
    top, right, bottom, left = drawing_dirs(up)
    w, h = (x1 - x0) / 3, (y1 - y0) / 3
    cells = {}
    for r, v in enumerate((top, None, bottom)):
        for c, hz in enumerate((left, None, right)):
            cells[zone_name(v, hz)] = (x0 + c * w, y1 - (r + 1) * h, x0 + (c + 1) * w, y1 - r * h)
    return cells


def north_arrow(ax, x, y, size, north_deg, colour='#c62828'):
    a = math.radians(north_deg)
    dx, dy = math.cos(a) * size, math.sin(a) * size
    ax.add_patch(FancyArrow(x - dx / 2, y - dy / 2, dx, dy, width=size * 0.06, head_width=size * 0.28,
                            head_length=size * 0.3, length_includes_head=True, color=colour, zorder=6))
    ax.text(x + dx * 0.75, y + dy * 0.75, 'N', color=colour, fontsize=11, fontweight='bold', ha='center',
            va='center', zorder=6)


def _rect_xy(rd, rect):
    r0, c0, r1, c1 = rect
    x0, y1 = rd.to_xy(r0, c0)
    x1, y0 = rd.to_xy(r1, c1)
    return x0, y0, x1, y1


def draw_read(dr, pairs, plan, path):
    n = len(pairs)
    cols = min(n, 2)
    rows = math.ceil(n / cols)
    fig, axes = plt.subplots(rows, cols, figsize=(9 * cols, 11 * rows), dpi=110, squeeze=False)
    north = plan.get('cad', {}).get('north_deg')
    up = plan.get('up', 'N')
    for ax, (p, rd) in zip(axes.ravel(), pairs):
        ax.add_collection(LineCollection(_segs_in(dr, rd.box), linewidths=0.35, colors='#9e9e9e', zorder=1))
        for it in rd.items:
            if it['kind'] == 'road':
                ax.text(it['label']['x'], it['label']['y'], 'ROAD', fontsize=8, color='#555', ha='center', zorder=5)
                continue
            col = TYPE_COLOURS.get(it['type'], '#7e57c2')
            rects = it.get('parts') or ([it['rect']] if it['rect'] is not None else [])
            for rect in rects:
                x0, y0, x1, y1 = _rect_xy(rd, rect)
                ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, facecolor=col, alpha=0.28, edgecolor=col,
                                       linewidth=1.4, zorder=2))
            lx, ly = it['label']['x'], it['label']['y']
            if rects:
                x0, y0, x1, y1 = _rect_xy(rd, rects[0])
                lx, ly = (x0 + x1) / 2, (y0 + y1) / 2
            else:
                ax.plot([lx], [ly], marker='x', color=col, markersize=8, zorder=5)
            ax.text(lx, ly, it.get('json_name', it['name']), fontsize=7, ha='center', va='center', zorder=6,
                    color='#212121', bbox=dict(boxstyle='round,pad=0.15', fc='white', ec='none', alpha=0.7))
        if rd.fp is not None:
            x0, y0, x1, y1 = _rect_xy(rd, rd.fp)
            ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False, edgecolor='#212121', linewidth=2.2, zorder=4))
            for z, (a, b, c, d) in grid_cells(x0, y0, x1, y1, up).items():
                ax.add_patch(Rectangle((a, b), c - a, d - b, fill=False, edgecolor='#1565c0', linewidth=0.6,
                                       linestyle=(0, (4, 4)), zorder=3))
                ax.text(a + (c - a) * 0.08, d - (d - b) * 0.08, z, fontsize=9, color='#1565c0', ha='left', va='top',
                        fontweight='bold', zorder=6)
        if rd.plot_rect is not None:
            x0, y0, x1, y1 = _rect_xy(rd, rd.plot_rect)
            ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False, edgecolor='#2e7d32', linewidth=1.8,
                                   linestyle='--', zorder=4))
        md = plan.get('main_door') or {}
        for d in rd.door_list:
            main = d is rd.main and md.get('level') == p['lv'] and rd.main_how and not (
                rd.main_how.startswith('guessed') and any(q is not rd and getattr(q, 'main_how', '') and
                                                          q.main_how.startswith('door tagged') for _, q in pairs))
            s = d['r'] * (0.9 if main else 0.5)
            ax.add_patch(FancyArrow(d['x'], d['y'], -d['swing'][0] * s, -d['swing'][1] * s, width=d['r'] * 0.03,
                                    head_width=d['r'] * (0.3 if main else 0.18), head_length=d['r'] * 0.22,
                                    color='#d32f2f' if main else '#ff8f00', zorder=7, length_includes_head=True))
            if main:
                ax.text(d['x'], d['y'], ' MAIN DOOR', color='#d32f2f', fontsize=8, fontweight='bold', zorder=7)
        bx0, by0, bx1, by1 = rd.box
        if north is not None:
            sz = (bx1 - bx0) * 0.07
            north_arrow(ax, bx1 - sz, by1 - sz, sz, north)
        ax.set_xlim(bx0, bx1)
        ax.set_ylim(by0, by1)
        ax.set_aspect('equal')
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_title(f"Plan {p['id']}: {p['title'] or 'untitled'} - level {p['lv']}\n"
                     'black: building outline, green dashed: plot, blue: 3x3 zones, arrows: doors facing out '
                     '(red = main door)', fontsize=9)
    for ax in axes.ravel()[n:]:
        ax.axis('off')
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def draw_index(dr, plans, path):
    xs0 = min(p['bbox'][0] for p in plans)
    ys0 = min(p['bbox'][1] for p in plans)
    xs1 = max(p['bbox'][2] for p in plans)
    ys1 = max(p['bbox'][3] for p in plans)
    m = 0.08 * max(xs1 - xs0, ys1 - ys0)
    box = (xs0 - m, ys0 - m, xs1 + m, ys1 + m)
    fig, ax = plt.subplots(figsize=(12, 12 * (box[3] - box[1]) / max(box[2] - box[0], 1e-9) + 0.5), dpi=90)
    ax.add_collection(LineCollection(_segs_in(dr, box), linewidths=0.25, colors='#757575'))
    for p in plans:
        x0, y0, x1, y1 = p['bbox']
        ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False, edgecolor='#d32f2f', linewidth=1.5))
        ax.text(x0, y1, f" {p['id']}", color='#d32f2f', fontsize=16, fontweight='bold', va='bottom')
        ax.text(x0, y0, (p['title'] or 'untitled')[:40], color='#d32f2f', fontsize=7, va='top')
    ax.set_xlim(box[0], box[2])
    ax.set_ylim(box[1], box[3])
    ax.set_aspect('equal')
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title('Floor plans found in the drawing, by number', fontsize=11)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
