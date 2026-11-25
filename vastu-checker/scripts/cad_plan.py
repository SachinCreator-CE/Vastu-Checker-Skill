#!/usr/bin/env python3
"""Read a house plan from a CAD drawing (DWG or DXF) into the Vastu checker's plan format.

Usage:
  python3 cad_plan.py drawing.dwg --out DIR [--plans 2,5] [--up N|E|S|W] [--north-deg A] [--units in|ft|mm|cm|m]

Writes DIR/<name>_plan.json (the plan format in ../references/plan-format.md, plus a "cad" block that
maps it back onto the drawing), DIR/<name>_read.png (what was read, to check by eye) and
DIR/<name>_plans.png (every floor plan found in the drawing, numbered), and prints a summary with
warnings. How it works and how to correct it: ../references/cad-input.md.
"""
import argparse
import json
import math
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cad_read import load, flatten, CadError  # noqa: E402
from cad_labels import classify, COMPASS  # noqa: E402

try:
    from scipy import ndimage as ndi
except Exception:  # pragma: no cover - scipy is optional
    ndi = None

INSUNITS = {1: 'in', 2: 'ft', 4: 'mm', 5: 'cm', 6: 'm'}
PER_FT = {'in': 12.0, 'ft': 1.0, 'mm': 304.8, 'cm': 30.48, 'm': 0.3048}
SKIP_LAYERS = re.compile(
    r'(furn|^fur$|fur[-_ ]|fixture|interior|^car$|^cars?[-_ ]|vehicle|a-fur|dim|hatch|^grid|grid ?no|centre|center|'
    r'defpoints|title|frame|viewport|vport|chart|beam|footing|steel|col ?no|col ?nom|column|^col$|slab|section|'
    r'electric|^elec|plumb|light|landscape|^tree|plant|^text|anno|^s-|^str|logo|border|sheet)', re.I)
INDOOR_KINDS = {'room'}
OUTSIDE = 1
POINT_TYPES = {'gate', 'sump', 'borewell', 'well', 'septic_tank', 'tulsi', 'generator', 'water_feature', 'tree_tall',
               'tree_small', 'overhead_tank'}
PROJECTIONS = {'balcony', 'portico', 'verandah', 'terrace'}
PLOT_LAYER = re.compile(r'plot|site|boundary|property|compound|prop[-_ ]?line', re.I)


# ---------------------------------------------------------------- small helpers

def fmt_ftin(v):
    total = round(v * 12)
    return f"{total // 12}'{total % 12}\""


def dilate(b, r=1):
    out = b.copy()
    for _ in range(r):
        o = out.copy()
        o[1:, :] |= out[:-1, :]
        o[:-1, :] |= out[1:, :]
        o[:, 1:] |= out[:, :-1]
        o[:, :-1] |= out[:, 1:]
        out = o
    return out


def dilate_sq(b):
    out = b.copy()
    out[1:, :] |= b[:-1, :]
    out[:-1, :] |= b[1:, :]
    o2 = out.copy()
    o2[:, 1:] |= out[:, :-1]
    o2[:, :-1] |= out[:, 1:]
    return o2


def label_cc(mask):
    """4-connected components of a boolean mask."""
    if ndi is not None:
        lab, n = ndi.label(mask)
        return lab, n
    h, w = mask.shape
    lab = np.zeros((h, w), np.int32)
    n = 0
    flat = mask.ravel()
    labf = lab.ravel()
    for start in np.flatnonzero(flat):
        if labf[start]:
            continue
        n += 1
        stack = [start]
        labf[start] = n
        while stack:
            p = stack.pop()
            r, c = divmod(p, w)
            for q, ok in ((p - w, r > 0), (p + w, r < h - 1), (p - 1, c > 0), (p + 1, c < w - 1)):
                if ok and flat[q] and not labf[q]:
                    labf[q] = n
                    stack.append(q)
    return lab, n


def distance(free):
    if ndi is not None:
        return ndi.distance_transform_edt(free)
    d = np.zeros(free.shape, np.float32)
    cur = free.copy()
    k = 0
    while cur.any() and k < 400:
        k += 1
        d[cur] = k
        e = cur.copy()
        e[1:, :] &= cur[:-1, :]
        e[:-1, :] &= cur[1:, :]
        e[:, 1:] &= cur[:, :-1]
        e[:, :-1] &= cur[:, 1:]
        e[0, :] = e[-1, :] = False
        e[:, 0] = e[:, -1] = False
        cur = e
    return d


def watershed(free, dist, seeds):
    """Flood free space from seeds {label: [(r, c), ...]}, widest places first, so regions meet at narrow
    openings such as doorways. Returns an int32 label image (0 where nothing reached). A plain priority flood:
    scipy's watershed_ift was tried and mislabels rooms on real plans."""
    import heapq
    h, w = free.shape
    lab = np.zeros((h, w), np.int32)
    labf = lab.ravel()
    fr = free.ravel()
    dv = (-dist).ravel().tolist()
    heap = []
    for L, pts in seeds.items():
        for r, c in pts:
            if 0 <= r < h and 0 <= c < w:
                p = r * w + c
                if fr[p] and not labf[p]:
                    labf[p] = L
                    heap.append((dv[p], p, L))
    heapq.heapify(heap)
    push, pop = heapq.heappush, heapq.heappop
    n = h * w
    while heap:
        _, p, L = pop(heap)
        c = p % w
        for q in (p - w, p + w, p - 1 if c > 0 else -1, p + 1 if c < w - 1 else -1):
            if 0 <= q < n and fr[q] and not labf[q]:
                labf[q] = L
                push(heap, (dv[q], q, L))
    return lab


def fill_holes(mask):
    if ndi is not None:
        return ndi.binary_fill_holes(mask)
    outside = ~mask
    lab, n = label_cc(outside)
    border = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])))
    border.discard(0)
    return mask | (outside & ~np.isin(lab, list(border)))


def trim_box(mask, box, cov=0.6):
    """Shrink a mask's bounding box from each side past rows/columns that are mostly empty, so bumps such as
    window boxes and sunshades along a face don't widen it."""
    r0, c0, r1, c1 = box
    while r1 - r0 > 2 and mask[r0, c0:c1].mean() < cov:
        r0 += 1
    while r1 - r0 > 2 and mask[r1 - 1, c0:c1].mean() < cov:
        r1 -= 1
    while c1 - c0 > 2 and mask[r0:r1, c0].mean() < cov:
        c0 += 1
    while c1 - c0 > 2 and mask[r0:r1, c1 - 1].mean() < cov:
        c1 -= 1
    return r0, c0, r1, c1


def max_rect(mask):
    """Largest axis-aligned rectangle of True cells: (r0, c0, r1, c1) inclusive-exclusive, area."""
    h, w = mask.shape
    heights = np.zeros(w, np.int32)
    best = (0, 0, 0, 0, 0)
    for r in range(h):
        heights = np.where(mask[r], heights + 1, 0)
        stack = []
        for c in range(w + 1):
            hc = heights[c] if c < w else 0
            start = c
            while stack and stack[-1][1] >= hc:
                s, hs = stack.pop()
                area = hs * (c - s)
                if area > best[4]:
                    best = (r - hs + 1, s, r + 1, c, area)
                start = s
            stack.append((start, hc))
    return best[:4], best[4]


# ---------------------------------------------------------------- the drawing as a whole

class Drawing:
    def __init__(self, path):
        self.path = Path(path)
        self.dwg = load(path)
        self.geo = flatten(self.dwg)
        self.warnings = []
        self.texts = []
        for t in self.geo['texts']:
            c = classify(t['text'])
            c.update(x=t['x'], y=t['y'], h=t['h'], rot=t['rot'], layer=t['layer'], block=t['block'],
                     raw=t['text'])
            self.texts.append(c)
        segs = [s for s in self.geo['segs'] if not SKIP_LAYERS.search(s[4] or '')]
        self.skipped_layers = sorted({s[4] for s in self.geo['segs'] if SKIP_LAYERS.search(s[4] or '')})
        self.seg_arr = np.array([s[:4] for s in segs], float) if segs else np.zeros((0, 4))
        self.seg_layer = [s[4] for s in segs]
        self.arcs = [a for a in self.geo['arcs'] if not SKIP_LAYERS.search(a[5] or '')]
        self.compasses = self.find_compasses()

    # -------------------------------------------------- compass

    def find_compasses(self):
        letters = [t for t in self.texts if t['kind'] == 'compass']
        used, out = set(), []
        for i, a in enumerate(letters):
            if i in used:
                continue
            grp = [j for j, b in enumerate(letters)
                   if math.hypot(a['x'] - b['x'], a['y'] - b['y']) < 25 * max(a['h'], b['h'], 1e-9)]
            used.update(grp)
            g = {letters[j]['type']: letters[j] for j in grp}
            vecs = []
            for k1, k2, rot in (('N', 'S', 0), ('E', 'W', 90), ('NE', 'SW', 45), ('NW', 'SE', -45)):
                if k1 in g and k2 in g:
                    vx, vy = g[k1]['x'] - g[k2]['x'], g[k1]['y'] - g[k2]['y']
                    a_ = math.atan2(vy, vx) + math.radians(rot)
                    vecs.append((math.cos(a_), math.sin(a_)))
            cx = sum(letters[j]['x'] for j in grp) / len(grp)
            cy = sum(letters[j]['y'] for j in grp) / len(grp)
            if vecs:
                vx, vy = sum(v[0] for v in vecs), sum(v[1] for v in vecs)
                out.append({'x': cx, 'y': cy, 'north': math.degrees(math.atan2(vy, vx)) % 360,
                            'how': 'compass letters ' + ' '.join(sorted(g)), 'conf': 'high' if len(vecs) > 1 else 'medium'})
            elif 'N' in g and len(grp) == 1:
                n = g['N']
                v = self.arrow_from_letter(n)
                if v is not None:
                    out.append({'x': n['x'], 'y': n['y'], 'north': v, 'how': 'N with its arrow', 'conf': 'medium'})
        for ins in self.geo['inserts']:
            if re.search(r'north|n[-_ ]?arrow|narrow|compass|orient', ins['name'], re.I):
                if any(math.hypot(ins['x'] - c['x'], ins['y'] - c['y']) < 200 for c in out):
                    continue
                ang = math.degrees(ins['rot']) + 90
                if ins.get('mirror'):
                    ang = 180 - ang
                out.append({'x': ins['x'], 'y': ins['y'], 'north': ang % 360,
                            'how': f"north arrow block {ins['name']}", 'conf': 'medium'})
        return out

    def arrow_from_letter(self, n):
        r = 6 * n['h']
        a = self.seg_arr
        if not len(a):
            return None
        mx, my = (a[:, 0] + a[:, 2]) / 2, (a[:, 1] + a[:, 3]) / 2
        near = (np.hypot(mx - n['x'], my - n['y']) < r)
        if near.sum() < 2:
            return None
        L = np.hypot(a[near, 2] - a[near, 0], a[near, 3] - a[near, 1]) + 1e-9
        cx, cy = (mx[near] * L).sum() / L.sum(), (my[near] * L).sum() / L.sum()
        if math.hypot(n['x'] - cx, n['y'] - cy) < 0.3 * n['h']:
            return None
        return math.degrees(math.atan2(n['y'] - cy, n['x'] - cx)) % 360

    # -------------------------------------------------- floor plans

    def attach_sizes(self):
        """A size written as its own text (12'0"X14'0" under BED ROOM) belongs to the nearest name above it."""
        names = [t for t in self.texts if t['kind'] in ('room', 'site') and not t.get('dims')]
        for d in [t for t in self.texts if t['kind'] == 'dims' and t.get('dims')]:
            best = None
            for n in names:
                dx, dy = d['x'] - n['x'], n['y'] - d['y']
                lim = 4 * max(n['h'], d['h'])
                if abs(dx) < 2.5 * lim and -0.5 * lim < dy < lim:
                    score = abs(dx) + 2 * abs(dy)
                    if best is None or score < best[0]:
                        best = (score, n)
            if best:
                best[1]['dims'], best[1]['dims_text'] = d['dims'], d['dims_text']
                names.remove(best[1])

    def find_plans(self):
        self.attach_sizes()
        labels = [t for t in self.texts if t['kind'] in ('room', 'site', 'stair_hint', 'main_door', 'road')]
        if not labels:
            return []
        rooms = [t for t in labels if t['kind'] in ('room', 'site')]
        h = float(np.median([t['h'] for t in rooms])) if rooms else float(np.median([t['h'] for t in labels]))
        thr = 45 * h
        n = len(labels)
        parent = list(range(n))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        xs = np.array([t['x'] for t in labels])
        ys = np.array([t['y'] for t in labels])
        for i in range(n):
            d = np.hypot(xs - xs[i], ys - ys[i])
            for j in np.flatnonzero(d < thr):
                if j > i:
                    parent[find(i)] = find(j)
        groups = {}
        for i in range(n):
            groups.setdefault(find(i), []).append(labels[i])
        plans = []
        for g in groups.values():
            rm = [t for t in g if t['kind'] in ('room', 'site')]
            if len(rm) < 3:
                continue
            x0 = min(t['x'] for t in g)
            x1 = max(t['x'] for t in g)
            y0 = min(t['y'] for t in g)
            y1 = max(t['y'] for t in g)
            plans.append({'labels': g, 'bbox': (x0, y0, x1, y1), 'h': h})
        # reading order: rows from the top, then left to right
        if plans:
            rows = []
            for p in sorted(plans, key=lambda p: -p['bbox'][3]):
                for row in rows:
                    lo, hi = row['lo'], row['hi']
                    ov = min(hi, p['bbox'][3]) - max(lo, p['bbox'][1])
                    if ov > 0.5 * min(hi - lo, p['bbox'][3] - p['bbox'][1]):
                        row['plans'].append(p)
                        row['lo'], row['hi'] = min(lo, p['bbox'][1]), max(hi, p['bbox'][3])
                        break
                else:
                    rows.append({'lo': p['bbox'][1], 'hi': p['bbox'][3], 'plans': [p]})
            plans = [p for row in rows for p in sorted(row['plans'], key=lambda q: q['bbox'][0])]
        titles = [t for t in self.texts if t['kind'] == 'title']
        for i, p in enumerate(plans, 1):
            p['id'] = i
            x0, y0, x1, y1 = p['bbox']
            w, hh = x1 - x0, y1 - y0
            best = None
            for t in titles:
                dx = max(x0 - t['x'], 0, t['x'] - x1)
                dy = max(y0 - t['y'], 0, t['y'] - y1)
                if dx > 0.6 * w + 20 * h or dy > 0.45 * hh + 30 * h:
                    continue
                score = dx + dy + (0 if 'PLAN' in ' '.join(t['lines']) else 15 * h) + (0 if t['y'] < y1 else 10 * h)
                if best is None or score < best[0]:
                    best = (score, t)
            p['title'] = ' '.join(best[1]['lines']) if best else None
            p['level'], p['level_name'] = (best[1].get('level'), best[1].get('level_name')) if best else (None, None)
            p['signature'] = sorted((t['name'], t['dims_text'] or '') for t in p['labels'] if t['kind'] in ('room', 'site'))
        return plans

    def units(self, plans, forced=None):
        if forced:
            return forced, PER_FT[forced], 'given'
        u = self.geo.get('units')
        if u in INSUNITS:
            return INSUNITS[u], PER_FT[INSUNITS[u]], 'drawing setting INSUNITS'
        best = None
        for p in plans[:4]:
            span = max(p['bbox'][2] - p['bbox'][0], p['bbox'][3] - p['bbox'][1])
            for name, k in PER_FT.items():
                ft_span = span / k
                text_in = p['h'] / k * 12
                if not (8 <= ft_span <= 300 and 1.5 <= text_in <= 30):
                    continue
                score = abs(math.log(ft_span / 30)) + abs(math.log(text_in / 6))
                if best is None or score < best[0]:
                    best = (score, name, k)
        if best is None:
            return 'in', 12.0, 'guessed'
        return best[1], best[2], 'guessed from sizes'


# ---------------------------------------------------------------- one floor plan

class PlanReader:
    def __init__(self, dr, plan, unit, k):
        self.dr, self.plan, self.unit, self.k = dr, plan, unit, k
        self.warn = []
        x0, y0, x1, y1 = plan['bbox']
        span = max(x1 - x0, y1 - y0)
        m = max(10 * k, 0.25 * span)
        self.box = (x0 - m, y0 - m, x1 + m, y1 + m)
        self.px = max(k / 12.0, span / 1300.0)          # about 1 inch per pixel
        bx0, by0, bx1, by1 = self.box
        self.W = int(math.ceil((bx1 - bx0) / self.px)) + 1
        self.H = int(math.ceil((by1 - by0) / self.px)) + 1
        a = dr.seg_arr
        sel = ((np.maximum(a[:, 0], a[:, 2]) >= bx0) & (np.minimum(a[:, 0], a[:, 2]) <= bx1) &
               (np.maximum(a[:, 1], a[:, 3]) >= by0) & (np.minimum(a[:, 1], a[:, 3]) <= by1)) if len(a) else np.zeros(0, bool)
        self.segs = a[sel]
        self.seg_layers = [dr.seg_layer[i] for i in np.flatnonzero(sel)]
        self.arcs = [c for c in dr.arcs if bx0 <= c[0] <= bx1 and by0 <= c[1] <= by1]
        self.texts = [t for t in dr.texts if bx0 <= t['x'] <= bx1 and by0 <= t['y'] <= by1]
        self.raw = self.rasterize(self.segs)
        self.B = dilate_sq(self.raw)
        self.free = ~self.B
        self.dist = distance(self.free)
        self.Hc = np.zeros((self.H, self.W + 1), np.int32)
        self.Hc[:, 1:] = np.cumsum(self.B, axis=1)
        self.Vc = np.zeros((self.W, self.H + 1), np.int32)
        self.Vc[:, 1:] = np.cumsum(self.B.T, axis=1)

    # -------------------------------------------------- raster helpers

    def to_px(self, x, y):
        return (y_ := (self.box[3] - y) / self.px), (x - self.box[0]) / self.px

    def to_xy(self, r, c):
        return self.box[0] + c * self.px, self.box[3] - r * self.px

    def rasterize(self, segs):
        B = np.zeros((self.H, self.W), bool)
        if not len(segs):
            return B
        c1 = (segs[:, 0] - self.box[0]) / self.px
        r1 = (self.box[3] - segs[:, 1]) / self.px
        c2 = (segs[:, 2] - self.box[0]) / self.px
        r2 = (self.box[3] - segs[:, 3]) / self.px
        L = np.hypot(c2 - c1, r2 - r1)
        n = np.minimum(np.ceil(L * 1.5).astype(np.int64) + 1, 200000)
        idx = np.repeat(np.arange(len(n)), n)
        start = np.repeat(np.cumsum(n) - n, n)
        t = (np.arange(n.sum()) - start) / np.repeat(np.maximum(n - 1, 1), n)
        rr = np.rint(r1[idx] + (r2 - r1)[idx] * t).astype(np.int64)
        cc = np.rint(c1[idx] + (c2 - c1)[idx] * t).astype(np.int64)
        ok = (rr >= 0) & (rr < self.H) & (cc >= 0) & (cc < self.W)
        B[rr[ok], cc[ok]] = True
        return B

    def rowcov(self, r, c0, c1):
        r = np.clip(r, 0, self.H - 1)
        c0 = np.clip(c0, 0, self.W)
        c1 = np.clip(c1, 0, self.W)
        n = np.maximum(c1 - c0, 1)
        return (self.Hc[r, c1] - self.Hc[r, c0]) / n

    def colcov(self, c, r0, r1):
        c = np.clip(c, 0, self.W - 1)
        r0 = np.clip(r0, 0, self.H)
        r1 = np.clip(r1, 0, self.H)
        n = np.maximum(r1 - r0, 1)
        return (self.Vc[c, r1] - self.Vc[c, r0]) / n

    def seed_for(self, t, radius_ft=2.5):
        """A free pixel in the space the label sits in: the label's own centre if it is free, else the nearest
        free pixel that is not a wall cavity."""
        r, c = self.to_px(t['x'], t['y'])
        r, c = int(round(r)), int(round(c))
        if 0 <= r < self.H and 0 <= c < self.W and self.free[r, c] and self.dist[r, c] >= 1.5:
            return r, c
        rad = int(max(radius_ft * self.k / self.px, 3 * t['h'] / self.px))
        r0, r1 = max(0, r - rad), min(self.H, r + rad + 1)
        c0, c1 = max(0, c - rad), min(self.W, c + rad + 1)
        win = self.dist[r0:r1, c0:c1]
        if not win.size:
            return None
        yy, xx = np.mgrid[r0:r1, c0:c1]
        d2 = (yy - r) ** 2 + (xx - c) ** 2
        ok = (win >= max(2.5, 0.5 * float(win.max()) if win.max() < 5 else 2.5)) & (d2 <= rad * rad)
        if not ok.any():
            return None
        score = np.where(ok, -np.sqrt(d2) + 0.05 * win, -1e9)
        i = int(np.argmax(score))
        return int(yy.ravel()[i]), int(xx.ravel()[i])

    # -------------------------------------------------- reading

    def read(self):
        self.labels = [t for t in self.texts if t['kind'] in ('room', 'site', 'road', 'main_door', 'stair_hint')]
        self.labels = [t for t in self.labels if t in self.plan['labels'] or t['kind'] in ('road', 'stair_hint', 'main_door')]
        seeds, self.items = {}, []
        L = 2
        for t in self.labels:
            if t['kind'] in ('room', 'site', 'road'):
                s = self.seed_for(t)
                item = {'label': t, 'name': t['name'], 'type': t['type'], 'kind': t['kind'], 'id': L, 'seed': s}
                self.items.append(item)
                if s is not None and not (t['type'] in POINT_TYPES and not t.get('dims')):
                    seeds[L] = [s]
                L += 1
        # stairs: UP / DN arrows are seeds of the nearest staircase
        stairs = [it for it in self.items if it['type'] == 'staircase']
        for t in self.labels:
            if t['kind'] != 'stair_hint':
                continue
            s = self.seed_for(t, 1.0)
            if s is None:
                continue
            if stairs:
                st = min(stairs, key=lambda it: math.hypot(it['label']['x'] - t['x'], it['label']['y'] - t['y']))
                if math.hypot(st['label']['x'] - t['x'], st['label']['y'] - t['y']) < 15 * self.k:
                    seeds.setdefault(st['id'], []).append(s)
                    continue
            item = {'label': t, 'name': 'STAIRCASE', 'type': 'staircase', 'kind': 'room', 'id': L, 'seed': s}
            self.items.append(item)
            stairs.append(item)
            seeds[L] = [s]
            L += 1
        border = [(0, c) for c in range(0, self.W, 4)] + [(self.H - 1, c) for c in range(0, self.W, 4)] + \
                 [(r, 0) for r in range(0, self.H, 4)] + [(r, self.W - 1) for r in range(0, self.H, 4)]
        seeds[OUTSIDE] = [p for p in border if self.free[p]]
        for it in self.items:            # a road is outside the plot
            if it['kind'] == 'road' and it['seed'] is not None:
                seeds.pop(it['id'], None)
                seeds[OUTSIDE].append(it['seed'])
        self.seed_pts = seeds
        self.lab = watershed(self.free, self.dist, seeds)
        self.absorb_enclosed()
        for it in self.items:
            it['region'] = fill_holes(self.lab == it['id']) if it['seed'] is not None else None
            self.fit(it)
        self.outline()
        self.doors()
        return self

    def absorb_enclosed(self):
        """Small enclosed spaces inside one room (behind a counter, inside a wardrobe or lift drawing) join it."""
        lab = self.lab
        un = self.free & (lab == 0)
        cc, n = label_cc(un)
        if not n:
            return
        objs = ndi.find_objects(cc) if ndi is not None else None
        for i in range(1, n + 1):
            if objs is not None:
                sl = objs[i - 1]
                if sl is None:
                    continue
                r0, r1 = max(sl[0].start - 3, 0), min(sl[0].stop + 3, self.H)
                c0, c1 = max(sl[1].start - 3, 0), min(sl[1].stop + 3, self.W)
            else:
                ys, xs = np.nonzero(cc == i)
                r0, r1, c0, c1 = max(ys.min() - 3, 0), min(ys.max() + 4, self.H), max(xs.min() - 3, 0), min(xs.max() + 4, self.W)
            comp = cc[r0:r1, c0:c1] == i
            if comp.sum() < 4 or self.dist[r0:r1, c0:c1][comp].max() < 2.5:
                continue
            ring = dilate(comp, 3) & ~comp
            near = np.unique(lab[r0:r1, c0:c1][ring])
            near = near[near > 0]
            if len(near) == 1 and near[0] != OUTSIDE:
                sub = lab[r0:r1, c0:c1]
                sub[comp] = near[0]

    # -------------------------------------------------- rooms

    def fit(self, it):
        """Place a labelled room: its stated size snapped to the walls around the label, else its region."""
        t = it['label']
        region = it['region']
        it['rect'], it['how'] = None, None
        dims = t.get('dims')
        if dims and it['seed'] is not None:
            best = None
            r, c = self.to_px(t['x'], t['y'])
            for (a, b) in ((dims[0], dims[1]), (dims[1], dims[0])):
                Wp, Hp = int(round(a * self.k / self.px)), int(round(b * self.k / self.px))
                if Wp < 4 or Hp < 4:
                    continue
                mx, my = max(1, int(0.08 * Wp)), max(1, int(0.08 * Hp))
                c0s = np.arange(int(c) - Wp + mx, int(c) - mx + 1)
                r0s = np.arange(int(r) - Hp + my, int(r) - my + 1)
                c0s = c0s[(c0s >= 0) & (c0s + Wp < self.W)]
                r0s = r0s[(r0s >= 0) & (r0s + Hp < self.H)]
                if not len(c0s) or not len(r0s):
                    continue
                R0, C0 = np.meshgrid(r0s, c0s, indexing='ij')
                top = self.rowcov(R0, C0, C0 + Wp)
                bot = self.rowcov(R0 + Hp, C0, C0 + Wp)
                lef = self.colcov(C0, R0, R0 + Hp)
                rig = self.colcov(C0 + Wp, R0, R0 + Hp)
                score = (top + bot + lef + rig) / 4 - 0.15 * np.std([top, bot, lef, rig], axis=0)
                i = np.unravel_index(int(np.argmax(score)), score.shape)
                s = float(score[i])
                if best is None or s > best[0]:
                    best = (s, int(R0[i]), int(C0[i]), Hp, Wp)
            if best and best[0] >= 0.5:
                s, r0, c0, Hp, Wp = best
                r0, c0, r1, c1 = self.refine(r0, c0, r0 + Hp, c0 + Wp)
                it['rect'] = (r0, c0, r1, c1)
                it['how'] = f'stated size fitted to walls ({round(s * 100)}% of its edges on lines)'
                it['fit'] = s
        if it['rect'] is None and it['type'] == 'staircase':
            fl = self.flights(it)
            if fl:
                it['rect'] = fl
                it['how'] = 'stair flights found from the tread lines'
                return
        if it['rect'] is None and (it['type'] in POINT_TYPES or it['kind'] == 'road'):
            it['how'] = 'label position (a point)'
            return
        if it['rect'] is None and region is not None and region.any():
            ys, xs = np.nonzero(region)
            span_ft = max(ys.max() - ys.min(), xs.max() - xs.min()) * self.px / self.k
            limit = 18 if it['type'] in ('staircase', 'toilet', 'bath', 'puja', 'store', 'lift', 'duct', 'dress') else 40
            if span_ft > limit:
                it['how'] = 'label position only (the space around it is open to a larger area)'
                self.warn.append(f"{it['name']}: the space around its label is open to a larger area "
                                 f"({round(span_ft)} ft across), so only its label position is used.")
                region = None
        if it['rect'] is None and region is not None and region.any():
            ys, xs = np.nonzero(region)
            r0, r1, c0, c1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
            fill = region.sum() / ((r1 - r0) * (c1 - c0))
            it['rect'] = (int(r0) - 2, int(c0) - 2, int(r1) + 1, int(c1) + 1)
            it['how'] = 'space around the label' + ('' if fill > 0.8 else f' (irregular, fills {round(fill * 100)}% of its box)')
            it['fill'] = fill
            if fill < 0.8:
                it['parts'] = self.decompose(region[r0:r1, c0:c1], r0, c0)
            if dims:
                self.warn.append(f"{it['name']}: its stated size {t['dims_text']} didn't match the walls around the label; "
                                 'used the space around the label instead.')
        if it['rect'] is None:
            it['how'] = it['how'] or 'label position only'
            if it['kind'] == 'room' and 'open to a larger area' not in it['how']:
                self.warn.append(f"{it['name']}: couldn't find the room's walls; only the label position is known.")
        if it['rect'] is not None:
            r0, c0, r1, c1 = it['rect']
            big = (r1 - r0) * self.px / self.k > 60 or (c1 - c0) * self.px / self.k > 60
            if it['kind'] == 'room' and big:
                self.warn.append(f"{it['name']}: its space runs over 60 ft; it may have leaked through an opening.")

    def refine(self, r0, c0, r1, c1, reach=None):
        reach = reach or max(2, int(round(3 * (self.k / 12) / self.px)))
        def best_row(rr, a, b):
            cand = [(float(self.rowcov(np.array([x]), np.array([a]), np.array([b]))[0]), -abs(x - rr), x)
                    for x in range(rr - reach, rr + reach + 1)]
            return max(cand)[2]
        def best_col(cc, a, b):
            cand = [(float(self.colcov(np.array([x]), np.array([a]), np.array([b]))[0]), -abs(x - cc), x)
                    for x in range(cc - reach, cc + reach + 1)]
            return max(cand)[2]
        nr0, nr1 = best_row(r0, c0, c1), best_row(r1, c0, c1)
        nc0, nc1 = best_col(c0, r0, r1), best_col(c1, r0, r1)
        return nr0, nc0, nr1, nc1

    def decompose(self, sub, r0, c0):
        parts, rem = [], sub.copy()
        total = sub.sum()
        while rem.sum() > 0.08 * total and len(parts) < 3:
            (a, b, c, d), area = max_rect(rem)
            if area < 0.08 * total:
                break
            parts.append((r0 + a, c0 + b, r0 + c, c0 + d))
            rem[a:c, b:d] = False
        return parts

    # -------------------------------------------------- footprint and plot

    def band_out(self, edge, side, span, reach_px, cov=0.45):
        """For each offset moving outward from one side of a box: is there a line covering `cov` of the span?"""
        a, b = span
        offs = []
        for d in range(0, reach_px + 1):
            if side == 'top':
                v = self.rowcov(np.array([edge - d]), np.array([a]), np.array([b]))[0]
            elif side == 'bottom':
                v = self.rowcov(np.array([edge + d]), np.array([a]), np.array([b]))[0]
            elif side == 'left':
                v = self.colcov(np.array([edge - d]), np.array([a]), np.array([b]))[0]
            else:
                v = self.colcov(np.array([edge + d]), np.array([a]), np.array([b]))[0]
            offs.append(v >= cov)
        return offs

    def flights(self, it):
        """A staircase's extent from its tread lines: runs of parallel lines of equal length, evenly spaced."""
        pts = [(it['label']['x'], it['label']['y'])] + [self.to_xy(r, c) for r, c in self.seed_pts.get(it['id'], [])]
        k = self.k
        reach = 14 * k
        groups = {}
        for x1, y1, x2, y2 in self.segs:
            L = math.hypot(x2 - x1, y2 - y1)
            if not (2.3 * k <= L <= 8 * k):
                continue
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            if min(math.hypot(mx - px_, my - py_) for px_, py_ in pts) > reach:
                continue
            tol = 2 * k / 12
            if abs(x2 - x1) < 0.03 * L:
                key = ('v', round(min(y1, y2) / tol), round(max(y1, y2) / tol))
                groups.setdefault(key, []).append((mx, min(y1, y2), max(y1, y2)))
            elif abs(y2 - y1) < 0.03 * L:
                key = ('h', round(min(x1, x2) / tol), round(max(x1, x2) / tol))
                groups.setdefault(key, []).append((my, min(x1, x2), max(x1, x2)))
        rects = []
        for key, g in groups.items():
            g = sorted(set((round(v[0], 3), v[1], v[2]) for v in g))
            run = [g[0]]
            for v in g[1:] + [None]:
                if v is not None and 6 * k / 12 <= v[0] - run[-1][0] <= 15 * k / 12:
                    run.append(v)
                    continue
                if len(run) >= 4:
                    lo, hi = run[0][0], run[-1][0]
                    a, b = min(r[1] for r in run), max(r[2] for r in run)
                    rects.append((lo, a, hi, b) if key[0] == 'v' else (a, lo, b, hi))
                run = [v] if v is not None else []
        if not rects:
            return None
        x0 = min(r[0] for r in rects)
        y0 = min(r[1] for r in rects)
        x1 = max(r[2] for r in rects)
        y1 = max(r[3] for r in rects)
        r0, c0 = self.to_px(x0, y1)
        r1, c1 = self.to_px(x1, y0)
        r0, c0, r1, c1 = int(round(r0)), int(round(c0)), int(round(r1)), int(round(c1))
        # stretch along the climb to a wall within 4 ft (the landing)
        vertical_treads = any(k_[0] == 'v' for k_ in groups if len(groups[k_]) >= 4)
        reach_px = int(4 * k / self.px)
        sides = (('left', c0, (r0, r1)), ('right', c1, (r0, r1))) if vertical_treads else \
                (('top', r0, (c0, c1)), ('bottom', r1, (c0, c1)))
        ext = {}
        for side, edge, span in sides:
            hits = self.band_out(edge, side, span, reach_px, cov=0.6)
            ext[side] = next((d for d in range(2, len(hits)) if hits[d]), 0)
        r0 -= ext.get('top', 0)
        r1 += ext.get('bottom', 0)
        c0 -= ext.get('left', 0)
        c1 += ext.get('right', 0)
        return (r0, c0, r1, c1)

    def outline(self):
        """Plot and building outline. The plot is what the outside can't reach past (a closed plot line);
        the building is the plot minus the open setback strips that run along its edge."""
        self.plot_rect, self.plot_poly, self.plot_how, self.fp = None, None, None, None
        self.no_plot = False
        rooms = [it for it in self.items if it['kind'] == 'room' and it['rect'] is not None]
        if not rooms:
            self.warn.append('No rooms placed, so the building outline is unknown.')
            return
        inside = self.lab != OUTSIDE
        cc, n = label_cc(inside)
        votes = {}
        for it in rooms:
            r0, c0, r1, c1 = it['rect']
            v = int(cc[min(max((r0 + r1) // 2, 0), self.H - 1), min(max((c0 + c1) // 2, 0), self.W - 1)])
            if v:
                votes[v] = votes.get(v, 0) + 1
        plot_mask = None
        out_near = dilate(self.lab == OUTSIDE, max(2, int(round(8 * self.k / 12 / self.px))))
        reached = [it['name'] for it in self.items if it['rect'] is not None and it['kind'] in ('room', 'site')
                   and out_near[it['rect'][0]:it['rect'][2], it['rect'][1]:it['rect'][3]].any()]
        if votes and not reached:
            pid = max(votes, key=votes.get)
            plot_mask = fill_holes(cc == pid) & (self.lab != OUTSIDE)
            if plot_mask[0, :].any() or plot_mask[-1, :].any() or plot_mask[:, 0].any() or plot_mask[:, -1].any():
                plot_mask = None
        claimed = np.zeros_like(self.free)
        for it in self.items:
            if it['rect'] is not None and it['kind'] in ('room', 'site'):
                r0, c0, r1, c1 = it['rect']
                claimed[max(r0, 0):r1, max(c0, 0):c1] = True
        # building outline A: plot minus the free strips touching the plot's edge
        A, flood_plot = None, None
        if plot_mask is not None:
            free_in = self.free & plot_mask & ~claimed
            core = plot_mask.copy()
            for _ in range(4):
                e = core.copy()
                e[1:, :] &= core[:-1, :]
                e[:-1, :] &= core[1:, :]
                e[:, 1:] &= core[:, :-1]
                e[:, :-1] &= core[:, 1:]
                core = e
            ring = plot_mask & ~core
            cc2, n2 = label_cc(free_in)
            edge_ids = np.unique(cc2[ring & free_in])
            edge_ids = edge_ids[edge_ids > 0]
            bmask = plot_mask & ~np.isin(cc2, edge_ids)
            cc3, _ = label_cc(bmask)
            v3 = {}
            for it in rooms:
                r0, c0, r1, c1 = it['rect']
                q = int(cc3[min(max((r0 + r1) // 2, 0), self.H - 1), min(max((c0 + c1) // 2, 0), self.W - 1)])
                if q:
                    v3[q] = v3.get(q, 0) + 1
            if v3:
                m3 = cc3 == max(v3, key=v3.get)
                # cut thin links (a post or a line touching the plot line) and keep the building's core
                rr = max(2, int(round(4 * self.k / 12 / self.px)))
                core3 = ~dilate(~m3, rr)
                c4, _ = label_cc(core3)
                v4 = {}
                for it in rooms:
                    r0, c0, r1, c1 = it['rect']
                    q = int(c4[min(max((r0 + r1) // 2, 0), self.H - 1), min(max((c0 + c1) // 2, 0), self.W - 1)])
                    if q:
                        v4[q] = v4.get(q, 0) + 1
                if v4:
                    m3 = dilate(c4 == max(v4, key=v4.get), rr + 1) & m3
                ys, xs = np.nonzero(m3)
                t = trim_box(m3, (int(ys.min()), int(xs.min()), int(ys.max()) + 1, int(xs.max()) + 1))
                A = (t[0] + 1, t[1] + 1, t[2] - 2, t[3] - 2)            # to the centre of the outer face line
                # the plot reaches as far as the open strips that touch the building
                touch = np.unique(cc2[dilate(m3, 3) & free_in])
                touch = touch[touch > 0]
                if len(touch):
                    pm = m3 | np.isin(cc2, touch)
                    ys, xs = np.nonzero(pm)
                    flood_plot = (int(ys.min()) - 2, int(xs.min()) - 2, int(ys.max()) + 2, int(xs.max()) + 2)
        # building outline B: the rooms (and parking on a stilt floor) out to their walls
        use = [it for it in rooms if it['type'] not in PROJECTIONS] or rooms
        if len(use) < 4 or self.plan.get('level_name') == 'Stilt':
            use = use + [it for it in self.items if it['type'] == 'parking' and it['rect'] is not None]
        r0 = min(it['rect'][0] for it in use)
        c0 = min(it['rect'][1] for it in use)
        r1 = max(it['rect'][2] for it in use)
        c1 = max(it['rect'][3] for it in use)
        wall = int(round(10 * self.k / 12 / self.px))
        grow = {}
        for side, edge, span in (('top', r0, (c0, c1)), ('bottom', r1, (c0, c1)), ('left', c0, (r0, r1)),
                                 ('right', c1, (r0, r1))):
            hits = self.band_out(edge, side, span, wall)
            # the band of lines at the rooms' edge, then at most one more line (the wall's outer face)
            d, bands = 0, 0
            last = 0
            while d < len(hits):
                if hits[d]:
                    bands += 1
                    while d < len(hits) and hits[d]:
                        last = d
                        d += 1
                    if bands == 2 or (bands == 1 and last > 2):
                        break
                else:
                    d += 1
            grow[side] = last if bands else 0
        grow = {k_: max(v - 1, 0) for k_, v in grow.items()}       # to the centre of the outer face line
        B = (r0 - grow['top'], c0 - grow['left'], r1 + grow['bottom'], c1 + grow['right'])
        fp = B if A is None else (min(A[0], B[0]), min(A[1], B[1]), max(A[2], B[2]), max(A[3], B[3]))
        if flood_plot is not None:
            p = flood_plot
            fp = (max(fp[0], p[0]), max(fp[1], p[1]), min(fp[2], p[2]), min(fp[3], p[3]))
            self.plot_rect, self.plot_how = flood_plot, 'closed boundary line around the building'
        self.fp = fp
        # plot: a closed polyline on a plot layer, else the sealed plot line, else the first line round the
        # building on each side, else any closed polyline round it (often a sheet frame, so last)
        fx0, fy1 = self.to_xy(fp[0], fp[1])
        fx1, fy0 = self.to_xy(fp[2], fp[3])
        tol = 3 * self.px
        cands = []
        for pts, layer in self.dr.geo['closed']:
            xs_ = [q[0] for q in pts]
            ys_ = [q[1] for q in pts]
            if min(xs_) <= fx0 + tol and min(ys_) <= fy0 + tol and max(xs_) >= fx1 - tol and max(ys_) >= fy1 - tol:
                area = (max(xs_) - min(xs_)) * (max(ys_) - min(ys_))
                fa = (fx1 - fx0) * (fy1 - fy0)
                if fa > 0 and area <= 5 * fa:
                    cands.append((area, pts, layer))
        cands.sort(key=lambda c: c[0])
        plot_layer = [c for c in cands if PLOT_LAYER.search(c[2] or '')]

        def use_poly(c, how):
            pts = c[1]
            xs_ = [q[0] for q in pts]
            ys_ = [q[1] for q in pts]
            pr0, pc0 = self.to_px(min(xs_), max(ys_))
            pr1, pc1 = self.to_px(max(xs_), min(ys_))
            self.plot_rect = (int(round(pr0)), int(round(pc0)), int(round(pr1)), int(round(pc1)))
            self.plot_poly = pts
            self.plot_how = how
        if plot_layer:
            use_poly(plot_layer[0], f'closed polyline on layer {plot_layer[0][2]}')
            return
        if self.plot_rect is not None:
            p = self.plot_rect
            if min(fp[0] - p[0], fp[1] - p[1], p[2] - fp[2], p[3] - fp[3]) >= 0 and \
                    max(fp[0] - p[0], fp[1] - p[1], p[2] - fp[2], p[3] - fp[3]) > 2:
                return
        reach = int(30 * self.k / self.px)
        res = {}
        r0, c0, r1, c1 = fp
        for side, edge, span in (('top', r0, (c0, c1)), ('bottom', r1, (c0, c1)), ('left', c0, (r0, r1)),
                                 ('right', c1, (r0, r1))):
            hits = self.band_out(edge, side, span, reach, cov=0.5)
            first = next((d for d in range(3, len(hits)) if hits[d]), None)
            if first is None:
                res[side] = 0
                continue
            d = first
            while d + 1 < len(hits) and hits[d + 1]:
                d += 1
            res[side] = max(d - 1, 0)
        none = [k_ for k_ in res if res[k_] == 0]
        if none and cands:
            use_poly(cands[0], f'closed polyline on layer {cands[0][2]} (check it is the plot, not a frame)')
            return
        self.plot_rect = (r0 - res['top'], c0 - res['left'], r1 + res['bottom'], c1 + res['right'])
        self.plot_how = 'first boundary line around the building on each side'
        if len(none) == 4:
            self.plot_how = 'no plot line found, so the plot is taken as the building outline'
            self.no_plot = True
        elif none:
            self.plot_how += f" (none found on the {', '.join(none)} side, taken as the building's face)"

    # -------------------------------------------------- doors

    def doors(self):
        self.door_list = []
        lines_only = self.rasterize(self.segs)  # arcs are drawn as short chords too; tests below sample radii only
        rmin, rmax = 18 * self.k / 12, 60 * self.k / 12
        tags = [t for t in self.texts if t['kind'] in ('door_tag', 'main_door')]
        seen = []
        for cx, cy, r, a0, a1, layer in self.arcs:
            sweep = math.degrees((a1 - a0) % (2 * math.pi))
            if not (rmin <= r <= rmax and 60 <= sweep <= 110):
                continue
            if any(math.hypot(cx - x, cy - y) < 2 * self.px and abs(r - rr) < 2 * self.px for x, y, rr in seen):
                continue
            seen.append((cx, cy, r))
            ends = [(cx + r * math.cos(a0), cy + r * math.sin(a0)), (cx + r * math.cos(a1), cy + r * math.sin(a1))]
            cover = []
            for ex, ey in ends:
                n = 24
                hits = 0
                for i in range(3, n - 2):
                    x = cx + (ex - cx) * i / n
                    y = cy + (ey - cy) * i / n
                    rr, cc = self.to_px(x, y)
                    rr, cc = int(round(rr)), int(round(cc))
                    if 0 <= rr < self.H and 0 <= cc < self.W and self.B[rr, cc]:
                        hits += 1
                cover.append(hits / (n - 5))
            leaf = 0 if cover[0] >= cover[1] else 1
            wall_end = ends[1 - leaf]
            ux, uy = (ends[leaf][0] - cx) / r, (ends[leaf][1] - cy) / r      # swing direction (into the room)
            ox, oy = (cx + wall_end[0]) / 2, (cy + wall_end[1]) / 2          # middle of the opening
            d = {'x': ox, 'y': oy, 'r': r, 'swing': (ux, uy), 'hinge': (cx, cy), 'sure': abs(cover[0] - cover[1]) > 0.3}
            ins = self.space_at(ox + ux * 1.1 * r, oy + uy * 1.1 * r)
            outs = self.space_at(ox - ux * 0.6 * r, oy - uy * 0.6 * r)
            d['into'], d['from'] = ins, outs
            tag = None
            for t in tags:
                dist_ = math.hypot(t['x'] - ox, t['y'] - oy)
                if dist_ < 1.6 * r and (tag is None or dist_ < tag[0]):
                    tag = (dist_, t)
            d['tag'] = tag[1]['name'] if tag else None
            d['main_tag'] = bool(tag and tag[1]['kind'] == 'main_door')
            self.door_list.append(d)
        self.main = None
        tagged = [d for d in self.door_list if d['main_tag']]
        if tagged:
            self.main = tagged[0]
            self.main_how = f"door tagged {self.main['tag']}"
            return
        named = [t for t in self.texts if t['kind'] == 'main_door' and t['name'] not in ('DM', 'MD')]
        if named and self.door_list:
            t = named[0]
            self.main = min(self.door_list, key=lambda d: math.hypot(d['x'] - t['x'], d['y'] - t['y']))
            self.main_how = f"door nearest the label {t['name']}"
            return
        outdoor = {None, 'outside'}
        cands = [d for d in self.door_list if (d['from'] in outdoor or self.is_outdoorish(d['from']))
                 and d['into'] not in outdoor and not self.is_outdoorish(d['into'])]
        if cands:
            self.main = max(cands, key=lambda d: d['r'])
            self.main_how = 'guessed: widest door leading in from outside (not tagged)'
        else:
            self.main_how = None

    def is_outdoorish(self, name):
        if not name:
            return True
        it = next((i for i in self.items if i['name'] == name), None)
        return bool(it and it['type'] in ('verandah', 'portico', 'parking', 'foyer', 'balcony', 'terrace', 'lawn'))

    def space_at(self, x, y):
        r, c = self.to_px(x, y)
        r, c = int(round(r)), int(round(c))
        if not (0 <= r < self.H and 0 <= c < self.W):
            return 'outside'
        hits = [it for it in self.items if it['rect'] is not None and it['kind'] in ('room', 'site')
                and it['rect'][0] <= r < it['rect'][2] and it['rect'][1] <= c < it['rect'][3]]
        if hits:
            it = min(hits, key=lambda i: (i['rect'][2] - i['rect'][0]) * (i['rect'][3] - i['rect'][1]))
            return it.get('json_name') or it['name']
        if self.fp is not None and not (self.fp[0] <= r < self.fp[2] and self.fp[1] <= c < self.fp[3]):
            return 'outside'
        L = self.lab[r, c]
        if L == 0:
            win = self.lab[max(r - 3, 0):r + 4, max(c - 3, 0):c + 4]
            vals = win[win > 0]
            if not len(vals):
                return None
            L = int(np.bincount(vals).argmax())
        if L == OUTSIDE:
            return 'outside'
        it = next((i for i in self.items if i['id'] == L), None)
        return it['name'] if it else None


# ---------------------------------------------------------------- assembling the plan file

def bearing_of(north_deg, vx, vy):
    """Compass bearing (degrees clockwise from north) of a drawing direction."""
    return (north_deg - math.degrees(math.atan2(vy, vx))) % 360


def cardinal(b):
    return 'NESW'[int(((b + 45) % 360) // 90)]


def build(path, out_dir, name=None, plans_sel=None, up=None, north_deg=None, unit=None, show_all=True):
    dr = Drawing(path)
    plans = dr.find_plans()
    if not plans:
        raise CadError('No floor plan found: no room labels (KITCHEN, BED ROOM, TOILET, ...) in model space. '
                       'If the rooms are labelled in another language or in blocks, see references/cad-input.md.')
    unit, k, unit_how = dr.units(plans, unit)
    readers = {}

    def reader(p):
        if p['id'] not in readers:
            readers[p['id']] = PlanReader(dr, p, unit, k).read()
        return readers[p['id']]

    # which plans: one per level, first in reading order, unless chosen
    warnings = []
    levels = {}
    stilt = any(p['level_name'] == 'Stilt' for p in plans)
    for p in plans:
        lv = p['level']
        if lv is not None and lv != 90 and stilt and p['level_name'] != 'Stilt' and lv >= 0:
            lv += 1
        p['lv'] = lv
    known = [p['lv'] for p in plans if p['lv'] is not None and p['lv'] != 90]
    top = max(known) if known else -1
    for p in plans:
        if p['lv'] == 90:
            p['lv'] = top + 1
    if plans_sel:
        chosen = [p for p in plans if p['id'] in plans_sel]
        missing = set(plans_sel) - {p['id'] for p in chosen}
        if missing:
            raise CadError(f'no plan numbered {sorted(missing)}; the drawing has plans 1 to {len(plans)}.')
        nxt = 0
        for p in chosen:
            if p['lv'] is None:
                while nxt in {q['lv'] for q in chosen}:
                    nxt += 1
                p['lv'] = nxt
    else:
        chosen, seen_lv = [], set()
        untitled = [p for p in plans if p['lv'] is None]
        for p in plans:
            if p['lv'] is not None and p['lv'] not in seen_lv:
                seen_lv.add(p['lv'])
                chosen.append(p)
        if not chosen and untitled:
            untitled[0]['lv'] = 0
            chosen = [untitled[0]]
            warnings.append('No plan titles found; took plan 1 as the ground floor. Name the plans to check with --plans.')
        for lv in sorted(seen_lv):
            same = [p for p in plans if p['lv'] == lv]
            if len(same) > 1:
                differ = [q['id'] for q in same[1:] if q['signature'] != same[0]['signature']]
                if differ:
                    warnings.append(f"Level {lv} ({same[0]['level_name']}) has {len(same)} plans in the drawing "
                                    f"(numbers {', '.join(str(q['id']) for q in same)}); used plan {same[0]['id']}. "
                                    f"Plans {', '.join(map(str, differ))} differ from it: ask which one is final.")
    chosen.sort(key=lambda p: p['lv'])
    rds = [reader(p) for p in chosen]

    # north
    comp_note = None
    if north_deg is None and up:
        north_deg = {'N': 90, 'E': 180, 'S': 270, 'W': 0}[up.upper()]
        comp_note = f'given: top of the drawing faces {up.upper()}'
    if north_deg is None:
        cs = dr.compasses
        if cs:
            p0 = chosen[0]
            cx, cy = (p0['bbox'][0] + p0['bbox'][2]) / 2, (p0['bbox'][1] + p0['bbox'][3]) / 2
            best = min(cs, key=lambda c: math.hypot(c['x'] - cx, c['y'] - cy))
            north_deg = best['north']
            spread = max(abs(((c['north'] - north_deg + 180) % 360) - 180) for c in cs)
            comp_note = f"{best['how']} (confidence {best['conf']})"
            if spread > 10:
                warnings.append('Compass marks in the drawing disagree by up to %d degrees; used the one nearest '
                                'the plan. Confirm north.' % round(spread))
        else:
            warnings.append('No north arrow or compass found. Ask which side of the drawing faces north, then '
                            'run again with --up.')
    if north_deg is not None:
        top_b = bearing_of(north_deg, 0, 1)
        up_dir = cardinal(top_b)
        tilt = ((top_b - {'N': 0, 'E': 90, 'S': 180, 'W': 270}[up_dir] + 180) % 360) - 180
    else:
        up_dir, tilt = 'N', 0.0

    # frame: the first chosen plan's plot (or footprint), top-left corner, y down, in feet or metres
    metric = unit in ('mm', 'cm', 'm')
    out_unit = 'm' if metric else 'ft'
    k_out = {'mm': 1000.0, 'cm': 100.0, 'm': 1.0}[unit] if metric else k   # drawing units per output unit

    def frame(rd):
        ref = rd.plot_rect or rd.fp
        x0, y1 = rd.to_xy(ref[0], ref[1])
        return x0, y1

    base = rds[0]
    if base.fp is None:
        raise CadError('Could not place any room on the first chosen plan; see the warnings and the picture.')
    plan = {'units': out_unit, 'up': up_dir, 'tilt_deg': round(tilt, 1)}
    pr = base.plot_rect or base.fp
    plan['plot'] = {'width': round((pr[3] - pr[1]) * base.px / k_out, 2),
                    'depth': round((pr[2] - pr[0]) * base.px / k_out, 2)}
    if base.no_plot or base.plot_rect is None:
        plan['plot']['drawn'] = False
    rooms, site, floors, cad_plans, level_names = [], [], [], [], {}
    main_door = None
    roads = set()
    for p, rd in zip(chosen, rds):
        fx0, fy1 = frame(rd)
        if rd is not base and base.plot_rect is not None and rd.plot_rect is None and rd.fp is not None:
            # no plot on this plan: line up the building outlines instead
            bx0, by1 = rd.to_xy(rd.fp[0], rd.fp[1])
            gx0, gy1 = base.to_xy(base.fp[0], base.fp[1])
            px0, py1 = frame(base)
            fx0, fy1 = bx0 - (gx0 - px0), by1 + (py1 - gy1)

        def conv(rect, rd=rd, fx0=fx0, fy1=fy1):
            r0, c0, r1, c1 = rect
            x0, y1 = rd.to_xy(r0, c0)
            x1, y0 = rd.to_xy(r1, c1)
            return {'x': round((x0 - fx0) / k_out, 2), 'y': round((fy1 - y1) / k_out, 2),
                    'width': round((x1 - x0) / k_out, 2), 'depth': round((y1 - y0) / k_out, 2)}

        def conv_pt(x, y, fx0=fx0, fy1=fy1):
            return {'x': round((x - fx0) / k_out, 2), 'y': round((fy1 - y) / k_out, 2)}
        lv = p['lv']
        level_names[str(lv)] = p['level_name'] or p['title'] or f"Plan {p['id']}"
        if rd.fp is not None:
            fp = conv(rd.fp)
            if rd is base:
                plan['building'] = fp
            else:
                floors.append(dict({'level': lv}, **fp))
        names = {}
        for it in rd.items:
            if it['kind'] == 'road':
                if rd.plot_rect or rd.fp:
                    ref = rd.plot_rect or rd.fp
                    r, c = rd.to_px(it['label']['x'], it['label']['y'])
                    side = ('top' if r < ref[0] else 'bottom' if r > ref[2] else None) or \
                           ('left' if c < ref[1] else 'right' if c > ref[3] else None)
                    if side and north_deg is not None:
                        v = {'top': (0, 1), 'bottom': (0, -1), 'left': (-1, 0), 'right': (1, 0)}[side]
                        roads.add(cardinal(bearing_of(north_deg, *v)))
                continue
            nm = it['name'] or it['type']
            names[nm] = names.get(nm, 0) + 1
            if names[nm] > 1:
                nm = f'{nm} ({names[nm]})'
            it['json_name'] = nm
            entry = {'name': nm, 'type': it['type'] or 'other'}
            if it['kind'] == 'room':
                entry['level'] = lv
            if it.get('parts'):
                for part in it['parts']:
                    e2 = dict(entry, **conv(part))
                    (rooms if it['kind'] == 'room' else site).append(e2)
            elif it['rect'] is not None:
                e2 = dict(entry, **conv(it['rect']))
                d = it['label'].get('dims')
                if d and it.get('fit'):
                    w_ft, d_ft = (e2['width'], e2['depth']) if not metric else (e2['width'] / 0.3048, e2['depth'] / 0.3048)
                    a, b = (d if abs(d[0] - w_ft) + abs(d[1] - d_ft) <= abs(d[1] - w_ft) + abs(d[0] - d_ft) else (d[1], d[0]))
                    e2['clear'] = [round(a * (0.3048 if metric else 1), 3), round(b * (0.3048 if metric else 1), 3)]
                    e2['label_size'] = it['label']['dims_text']
                (rooms if it['kind'] == 'room' else site).append(e2)
            else:
                e2 = dict(entry, **conv_pt(it['label']['x'], it['label']['y']))
                (rooms if it['kind'] == 'room' else site).append(e2)
            it['entry_how'] = it['how']
        if rd.main is not None and (main_door is None or rd.main_how.startswith('door tagged')):
            d = rd.main
            md = dict({'level': lv}, **conv_pt(d['x'], d['y']))
            if north_deg is not None:
                md['facing'] = cardinal(bearing_of(north_deg, -d['swing'][0], -d['swing'][1]))
            md['width'] = round(d['r'] / k_out, 2)
            md['from'], md['into'] = d['from'], d['into']
            if main_door is None or rd.main_how.startswith('door tagged'):
                main_door = md
                main_door_how = rd.main_how
        cad_plans.append({'id': p['id'], 'title': p['title'], 'level': lv, 'origin': [fx0, fy1],
                          'box': list(rd.box), 'plot_how': rd.plot_how})
    plan['floors'] = floors
    plan['levels'] = level_names
    plan['rooms'] = rooms
    if main_door:
        plan['main_door'] = main_door
    plan['site'] = site
    if roads:
        plan['roads'] = sorted(roads, key='NESW'.index)
    plan['cad'] = {'file': Path(path).name, 'units': unit, 'units_per_' + out_unit: k_out,
                   'north_deg': None if north_deg is None else round(north_deg, 2), 'plans': cad_plans}
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = name or Path(path).stem
    jpath = out_dir / f'{name}_plan.json'
    jpath.write_text(json.dumps(plan, indent=1))

    # summary
    lines = [f'# Read from {Path(path).name}', '']
    lines.append(f"Format: {dr.dwg.get('version')}. Units: {unit} ({unit_how}).")
    if north_deg is not None:
        lines.append(f'North: {comp_note}; the top of the drawing faces {up_dir}'
                     + (f', {abs(round(tilt, 1))} degrees off' if abs(tilt) >= 1 else '') + '.')
    lines.append(f'Floor plans found: {len(plans)}. ' + '; '.join(
        f"{p['id']}: {p['title'] or 'untitled'}" for p in plans))
    lines.append('Used: ' + ', '.join(f"plan {p['id']} as level {p['lv']} ({level_names[str(p['lv'])]})" for p in chosen) + '.')
    if dr.skipped_layers:
        lines.append('Layers not read as walls: ' + ', '.join(dr.skipped_layers) + '.')
    for p, rd in zip(chosen, rds):
        lines += ['', f"## Plan {p['id']}: {p['title'] or 'untitled'} (level {p['lv']})", '']
        if rd.fp is not None:
            w = (rd.fp[3] - rd.fp[1]) * rd.px / k
            d = (rd.fp[2] - rd.fp[0]) * rd.px / k
            lines.append(f'Building outline: {fmt_ftin(w)} x {fmt_ftin(d)} (left-right x top-bottom).')
        if rd.plot_rect is not None:
            w = (rd.plot_rect[3] - rd.plot_rect[1]) * rd.px / k
            d = (rd.plot_rect[2] - rd.plot_rect[0]) * rd.px / k
            if rd.no_plot:
                lines.append('Plot: no plot line in this plan; taken as the building outline (open-space checks need it).')
            else:
                lines.append(f'Plot: {fmt_ftin(w)} x {fmt_ftin(d)}, from the {rd.plot_how}.')
        lines += ['', '| Item | Type | Size read | Label says | How placed |', '|---|---|---|---|---|']
        for it in rd.items:
            if it['kind'] == 'road':
                continue
            size = '-'
            if it['rect'] is not None:
                r0, c0, r1, c1 = it['rect']
                size = f"{fmt_ftin((c1 - c0) * rd.px / k)} x {fmt_ftin((r1 - r0) * rd.px / k)}"
            lines.append(f"| {it.get('json_name', it['name'])} | {it['type']} | {size} | "
                         f"{it['label'].get('dims_text') or '-'} | {it['how']} |")
        if rd.door_list:
            md = rd.main if (rd.main is not None and rd.main_how and rd.main_how.startswith('door tagged')) else None
            lines.append('')
            lines.append(f'Doors found: {len(rd.door_list)}.' + (
                f" Main door: {md['tag'] or 'untagged'}, {fmt_ftin(md['r'] / k)} wide, from {md['from'] or 'unknown'} "
                f"into {md['into'] or 'unknown'} ({rd.main_how})." if md else ' No door tagged as the main door here.'))
        for w_ in rd.warn:
            lines.append(f'- Warning: {w_}')
    if main_door:
        lines.append('')
        lines.append(f"Main door used for the check: level {main_door['level']}, faces {main_door.get('facing', '?')}, "
                     f"from {main_door.get('from') or 'unknown'} into {main_door.get('into') or 'unknown'} "
                     f"({main_door_how}).")
        if main_door_how.startswith('guessed'):
            warnings.append('The main door is not tagged in the drawing (MD, DM or MAIN DOOR); the widest door '
                            'leading in from outside was taken. Confirm it.')
    else:
        warnings.append('Main door not found: no tagged door (MD, DM, MAIN DOOR) and no door leading in from outside. '
                        'Ask where it is and add main_door to the plan file.')
    if roads:
        lines.append(f"Road on the {', '.join(sorted(roads, key='NESW'.index))} side.")
    if warnings:
        lines += [''] + [f'- Warning: {w}' for w in warnings]
    lines += ['', f'Plan file: {jpath}']
    try:
        from cad_draw import draw_read, draw_index
        img = out_dir / f'{name}_read.png'
        draw_read(dr, list(zip(chosen, rds)), plan, img)
        lines.append(f'Picture of what was read: {img}')
        if show_all and len(plans) > 1:
            idx = out_dir / f'{name}_plans.png'
            draw_index(dr, plans, idx)
            lines.append(f'All plans in the drawing, numbered: {idx}')
    except ImportError:
        pass
    return plan, '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('drawing', help='DWG or DXF file')
    ap.add_argument('--out', default='.', help='folder for the plan file and pictures')
    ap.add_argument('--name', help='base name for the outputs (default: the drawing name)')
    ap.add_argument('--plans', help='plan numbers to use, comma separated, lowest floor first (see _plans.png)')
    ap.add_argument('--up', help='compass direction the top of the drawing faces (N, E, S or W), if not read')
    ap.add_argument('--north-deg', type=float, help='direction north points in the drawing, degrees anticlockwise '
                    'from the +X axis (90 = straight up)')
    ap.add_argument('--units', choices=sorted(PER_FT), help='drawing units, if not read correctly')
    args = ap.parse_args()
    sel = [int(x) for x in args.plans.split(',')] if args.plans else None
    try:
        _, summary = build(args.drawing, args.out, args.name, sel, args.up, args.north_deg, args.units)
    except CadError as ex:
        sys.exit(f'Could not read the drawing: {ex}')
    print(summary)


if __name__ == '__main__':
    main()
