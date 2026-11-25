"""Read a CAD drawing (DWG or DXF) and flatten it into plain geometry and text in world coordinates.

    from cad_read import load, flatten
    dwg = load('plan.dwg')            # or .dxf
    geo = flatten(dwg)                # segments, arcs, texts, inserts, with layer names

DWG files go through dwg_read.py (AutoCAD 2000/2004/2010/2013/2018 formats). DXF files (ASCII, any
version from R12 on) are read here. Blocks are expanded with their insertion transforms, so door,
window and furniture blocks come out as ordinary lines and arcs on their own layers.
"""
import math
import re
from pathlib import Path


class CadError(Exception):
    pass


# ---------------------------------------------------------------- DXF

def _pairs(path):
    raw = Path(path).read_bytes()
    if raw.startswith(b'AutoCAD Binary DXF'):
        raise CadError('binary DXF is not supported: save it as ASCII DXF (the default in AutoCAD).')
    text = None
    for enc in ('utf-8', 'cp1252'):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    lines = text.splitlines()
    out = []
    for i in range(0, len(lines) - 1, 2):
        try:
            code = int(lines[i].strip())
        except ValueError:
            raise CadError(f'not a readable DXF (line {i + 1}).')
        out.append((code, lines[i + 1].strip() if code != 1 and code != 3 else lines[i + 1].rstrip('\r\n')))
    return out


def _records(pairs, start, end_names):
    """Split pairs into records that each start with a group 0, until a 0/end_names pair."""
    recs, cur = [], None
    i = start
    while i < len(pairs):
        c, v = pairs[i]
        if c == 0:
            if v in end_names:
                if cur:
                    recs.append(cur)
                return recs, i
            if cur:
                recs.append(cur)
            cur = [(c, v)]
        elif cur is not None:
            cur.append((c, v))
        i += 1
    if cur:
        recs.append(cur)
    return recs, i


def _f(rec, code, dflt=0.0):
    for c, v in rec:
        if c == code:
            try:
                return float(v)
            except ValueError:
                return dflt
    return dflt


def _s(rec, code, dflt=''):
    for c, v in rec:
        if c == code:
            return v
    return dflt


def _ocs_flag(rec):
    return _f(rec, 230, 1.0) < -0.5


def _dxf_entity(rec):
    t = rec[0][1]
    layer = _s(rec, 8, '0')
    if any(c == 67 and v.strip() == '1' for c, v in rec):
        return None  # paper space
    e = {'layer': layer}
    if t == 'LINE':
        e.update(type='LINE', p1=(_f(rec, 10), _f(rec, 20)), p2=(_f(rec, 11), _f(rec, 21)))
    elif t in ('ARC', 'CIRCLE'):
        e.update(type=t, c=(_f(rec, 10), _f(rec, 20)), r=_f(rec, 40), ocs=_ocs_flag(rec))
        if t == 'ARC':
            e.update(a0=math.radians(_f(rec, 50)), a1=math.radians(_f(rec, 51)))
    elif t == 'ELLIPSE':
        e.update(type='ELLIPSE', c=(_f(rec, 10), _f(rec, 20)), major=(_f(rec, 11), _f(rec, 21)), ratio=_f(rec, 40, 1),
                 a0=_f(rec, 41), a1=_f(rec, 42, 2 * math.pi))
    elif t == 'LWPOLYLINE':
        pts, bul = [], []
        for c, v in rec:
            if c == 10:
                pts.append([float(v), 0.0])
                bul.append(0.0)
            elif c == 20 and pts:
                pts[-1][1] = float(v)
            elif c == 42 and bul:
                bul[-1] = float(v)
        flags = int(_f(rec, 70))
        e.update(type='LWPOLYLINE', pts=[tuple(p) for p in pts], bulges=bul, closed=bool(flags & 1), ocs=_ocs_flag(rec))
    elif t in ('SOLID', 'TRACE', '3DFACE'):
        pts = [(_f(rec, 10), _f(rec, 20)), (_f(rec, 11), _f(rec, 21)), (_f(rec, 13), _f(rec, 23)),
               (_f(rec, 12), _f(rec, 22))]
        e.update(type='SOLID', pts=pts, ocs=_ocs_flag(rec))
    elif t in ('TEXT', 'ATTRIB'):
        e.update(type=t, text=_s(rec, 1), p=(_f(rec, 10), _f(rec, 20)), ap=(_f(rec, 11), _f(rec, 21)),
                 h=_f(rec, 40, 1.0), rot=math.radians(_f(rec, 50)), ha=int(_f(rec, 72)),
                 va=int(_f(rec, 74 if t == 'ATTRIB' else 73)), ocs=_ocs_flag(rec))
    elif t == 'MTEXT':
        parts = [v for c, v in rec if c == 3] + [v for c, v in rec if c == 1]
        rot = math.radians(_f(rec, 50))
        if any(c == 11 for c, _ in rec):
            rot = math.atan2(_f(rec, 21), _f(rec, 11))
        e.update(type='MTEXT', text=''.join(parts), p=(_f(rec, 10), _f(rec, 20)), h=_f(rec, 40, 1.0), rot=rot,
                 attach=int(_f(rec, 71, 1)), width=_f(rec, 41), ocs=_ocs_flag(rec))
    elif t == 'INSERT':
        e.update(type='INSERT', name=_s(rec, 2), p=(_f(rec, 10), _f(rec, 20)), sx=_f(rec, 41, 1.0), sy=_f(rec, 42, 1.0),
                 rot=math.radians(_f(rec, 50)), ocs=_ocs_flag(rec))
    else:
        return None
    return e


def read_dxf(path):
    pairs = _pairs(path)
    units, layers, blocks, model = None, {}, {}, []
    i = 0
    while i < len(pairs):
        c, v = pairs[i]
        if c == 0 and v == 'SECTION' and i + 1 < len(pairs):
            name = pairs[i + 1][1]
            if name == 'HEADER':
                j = i + 2
                while j < len(pairs) and pairs[j] != (0, 'ENDSEC'):
                    if pairs[j] == (9, '$INSUNITS') and j + 1 < len(pairs):
                        units = int(float(pairs[j + 1][1]))
                    j += 1
                i = j
            elif name == 'TABLES':
                recs, i = _records(pairs, i + 2, {'ENDSEC'})
                for r in recs:
                    if r[0][1] == 'LAYER':
                        nm = _s(r, 2)
                        if nm:
                            col = int(_f(r, 62, 7))
                            fl = int(_f(r, 70))
                            layers[nm] = {'frozen': bool(fl & 1), 'off': col < 0, 'color': abs(col)}
            elif name == 'BLOCKS':
                recs, i = _records(pairs, i + 2, {'ENDSEC'})
                cur = None
                pending = None
                for r in recs:
                    t = r[0][1]
                    if t == 'BLOCK':
                        cur = {'base': (_f(r, 10), _f(r, 20)), 'ents': []}
                        blocks[_s(r, 2)] = cur
                    elif t == 'ENDBLK':
                        cur = None
                    elif cur is not None:
                        pending = _collect(r, cur['ents'], pending)
            elif name == 'ENTITIES':
                recs, i = _records(pairs, i + 2, {'ENDSEC'})
                pending = None
                for r in recs:
                    pending = _collect(r, model, pending)
        i += 1
    return {'version': 'DXF', 'units': units, 'layers': layers, 'blocks': blocks, 'model': model, 'problems': 0}


def _collect(rec, out, pending):
    """Handle POLYLINE/VERTEX/SEQEND and INSERT/ATTRIB sequences."""
    t = rec[0][1]
    if t == 'POLYLINE':
        e = {'layer': _s(rec, 8, '0'), 'type': 'LWPOLYLINE', 'pts': [], 'bulges': [],
             'closed': bool(int(_f(rec, 70)) & 1), 'ocs': _ocs_flag(rec), '_poly3d': bool(int(_f(rec, 70)) & (8 | 16 | 64))}
        if not any(c == 67 and v.strip() == '1' for c, v in rec):
            out.append(e)
        return e
    if t == 'VERTEX':
        if pending is not None and pending.get('type') == 'LWPOLYLINE':
            if not int(_f(rec, 70)) & 16:
                pending['pts'].append((_f(rec, 10), _f(rec, 20)))
                pending['bulges'].append(_f(rec, 42))
        return pending
    if t == 'SEQEND':
        return None
    if t == 'ATTRIB':
        if pending is not None and pending.get('type') == 'INSERT':
            a = _dxf_entity(rec)
            if a:
                pending.setdefault('attribs', []).append(a)
        return pending
    e = _dxf_entity(rec)
    if e is not None:
        out.append(e)
        if e['type'] == 'INSERT':
            return e
    return None


# ---------------------------------------------------------------- loading

def load(path):
    p = Path(path)
    head = p.read_bytes()[:6]
    if head.startswith(b'AC10'):
        from dwg_read import read_dwg, DwgError
        try:
            return read_dwg(p)
        except DwgError as ex:
            raise CadError(str(ex))
    if p.suffix.lower() == '.dxf' or head[:1] in (b' ', b'0', b'9'):
        return read_dxf(p)
    raise CadError(f'{p.name}: not a DWG or DXF file.')


# ---------------------------------------------------------------- text

SPECIAL = {'%%c': '\u00d8', '%%d': '\u00b0', '%%p': '\u00b1', '%%u': '', '%%o': ''}


def mtext_plain(s):
    """MTEXT or TEXT contents as plain text, with \\P as newlines."""
    for k, v in SPECIAL.items():
        s = s.replace(k, v).replace(k.upper(), v)
    s = re.sub(r'\\S([^;]*)[#^/]([^;]*);', r'\1/\2', s)          # stacked fractions
    s = re.sub(r'\\[ACcFfHhQTWp][^;\\{}]*;', '', s)               # formatting with arguments
    s = re.sub(r'\\[LlOoKkNX]', '', s)
    s = s.replace('\\P', '\n').replace('\\~', ' ').replace('\\\\', '\\')
    s = s.replace('\\{', '\x01').replace('\\}', '\x02').replace('{', '').replace('}', '')
    s = s.replace('\x01', '{').replace('\x02', '}')
    return s


# ---------------------------------------------------------------- flattening

def _mat_mul(a, b):
    return (a[0] * b[0] + a[1] * b[3], a[0] * b[1] + a[1] * b[4], a[0] * b[2] + a[1] * b[5] + a[2],
            a[3] * b[0] + a[4] * b[3], a[3] * b[1] + a[4] * b[4], a[3] * b[2] + a[4] * b[5] + a[5])


IDENT = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
MIRROR_X = (-1.0, 0.0, 0.0, 0.0, 1.0, 0.0)


def _apply(m, p):
    return (m[0] * p[0] + m[1] * p[1] + m[2], m[3] * p[0] + m[4] * p[1] + m[5])


def _insert_matrix(e, base):
    c, s = math.cos(e['rot']), math.sin(e['rot'])
    sx, sy = e.get('sx', 1.0) or 1.0, e.get('sy', 1.0) or 1.0
    m = (c * sx, -s * sy, e['p'][0], s * sx, c * sy, e['p'][1])
    m = _mat_mul(m, (1.0, 0.0, -base[0], 0.0, 1.0, -base[1]))
    if e.get('ocs'):
        m = _mat_mul(MIRROR_X, m)
    return m


def _bulge_geom(p0, p1, b):
    """Centre, radius, start angle and signed sweep of a polyline arc segment with bulge b."""
    ang = 4 * math.atan(b)
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    chord = math.hypot(dx, dy)
    d = chord / (2 * math.tan(ang / 2))
    cx = (p0[0] + p1[0]) / 2 - dy / chord * d
    cy = (p0[1] + p1[1]) / 2 + dx / chord * d
    r = chord / (2 * abs(math.sin(ang / 2)))
    return (cx, cy), r, math.atan2(p0[1] - cy, p0[0] - cx), ang


def bulge_points(p0, p1, b, step_deg=10.0):
    """Points along a polyline arc segment from p0 to p1 with bulge b (p0 excluded, p1 included)."""
    if abs(b) < 1e-9 or math.dist(p0, p1) < 1e-12:
        return [p1]
    (cx, cy), r, a0, ang = _bulge_geom(p0, p1, b)
    n = max(2, int(abs(math.degrees(ang)) / step_deg))
    return [(cx + r * math.cos(a0 + ang * k / n), cy + r * math.sin(a0 + ang * k / n)) for k in range(1, n)] + [p1]


def _arc_world(m, c, r, a0, a1, mirror_ocs):
    """Arc (centre, radius, CCW start/end angles) through an affine map; None if the map distorts it."""
    if mirror_ocs:
        c = (-c[0], c[1])
        a0, a1 = math.pi - a1, math.pi - a0
    sx = math.hypot(m[0], m[3])
    sy = math.hypot(m[1], m[4])
    if abs(sx - sy) > 1e-6 * max(sx, sy, 1e-12):
        return None
    det = m[0] * m[4] - m[1] * m[3]
    wc = _apply(m, c)
    p0 = _apply(m, (c[0] + r * math.cos(a0), c[1] + r * math.sin(a0)))
    p1 = _apply(m, (c[0] + r * math.cos(a1), c[1] + r * math.sin(a1)))
    if det < 0:
        p0, p1 = p1, p0
    b0 = math.atan2(p0[1] - wc[1], p0[0] - wc[0])
    b1 = math.atan2(p1[1] - wc[1], p1[0] - wc[0])
    return wc, r * sx, b0, b1


def _arc_pts(c, r, a0, a1, step_deg=10.0):
    while a1 <= a0:
        a1 += 2 * math.pi
    n = max(2, int(math.degrees(a1 - a0) / step_deg))
    return [(c[0] + r * math.cos(a0 + (a1 - a0) * k / n), c[1] + r * math.sin(a0 + (a1 - a0) * k / n))
            for k in range(n + 1)]


def text_centre(e, plain, m):
    """Approximate visual centre of a TEXT/MTEXT in world coordinates, and its world height and rotation."""
    rows = [r for r in plain.split('\n')] or ['']
    h = e.get('h', 1.0) or 1.0
    w = max(len(r) for r in rows) * h * 0.75
    if e.get('type') == 'MTEXT':
        if e.get('width') and e['width'] > 0:
            w = min(w, e['width'])
        tot = h * (1 + 1.667 * (len(rows) - 1))
        att = e.get('attach', 1) or 1
        col, row = (att - 1) % 3, (att - 1) // 3
        dx = {0: w / 2, 1: 0.0, 2: -w / 2}[col]
        dy = {0: -tot / 2, 1: 0.0, 2: tot / 2}[row]
        base = e['p']
    else:
        ha, va = e.get('ha', 0), e.get('va', 0)
        base = e['ap'] if (ha or va) and 'ap' in e else e['p']
        if ha in (1, 4):
            dx = 0.0
        elif ha == 2:
            dx = -w / 2
        elif ha in (3, 5):
            base, dx = e['p'], w / 2
        else:
            dx = w / 2
        dy = {0: h / 2, 1: h / 2, 2: 0.0, 3: -h / 2}.get(va, h / 2)
        if ha == 4:
            dy = 0.0
    rot = e.get('rot', 0.0)
    if e.get('ocs'):
        base = (-base[0], base[1])
        rot = math.pi - rot
    cx = base[0] + dx * math.cos(rot) - dy * math.sin(rot)
    cy = base[1] + dx * math.sin(rot) + dy * math.cos(rot)
    wc = _apply(m, (cx, cy))
    sc = math.hypot(m[1], m[4])
    wrot = math.atan2(m[3] * math.cos(rot) + m[4] * math.sin(rot), m[0] * math.cos(rot) + m[1] * math.sin(rot))
    return wc, h * sc, wrot


def flatten(dwg, max_depth=8):
    """World geometry from model space.

    Returns a dict with
      segs:    [(x1, y1, x2, y2, layer)]             lines, polyline edges, arcs and circles as short chords
      arcs:    [(cx, cy, r, a0, a1, layer)]           true arcs (radians, counter-clockwise), for doors
      texts:   [{'text', 'x', 'y', 'h', 'rot', 'layer', 'block'}]   visual centres
      inserts: [{'name', 'x', 'y', 'rot', 'sx', 'sy', 'layer', 'mirror'}]
      closed:  [([(x, y), ...], layer)]               closed polylines (for plot outlines)
    """
    blocks = dwg['blocks']
    layers = dwg['layers']
    segs, arcs, texts, inserts, closed = [], [], [], [], []

    def hidden(layer):
        L = layers.get(layer)
        return bool(L and (L['frozen'] or L['off']))

    def walk(ents, m, depth, inherit_layer, block_name):
        for e in ents:
            layer = e.get('layer', '0')
            if layer == '0' and inherit_layer:
                layer = inherit_layer
            if hidden(layer):
                continue
            t = e['type']
            mo = _mat_mul(m, MIRROR_X) if e.get('ocs') and t not in ('INSERT', 'TEXT', 'ATTRIB', 'MTEXT') else m
            if t == 'LINE':
                a, b = _apply(m, e['p1']), _apply(m, e['p2'])
                segs.append((a[0], a[1], b[0], b[1], layer))
            elif t == 'LWPOLYLINE':
                pts = e['pts']
                if len(pts) < 2:
                    continue
                bul = list(e.get('bulges') or []) + [0.0] * len(pts)
                seq = list(range(len(pts))) + ([0] if e.get('closed') else [])
                path = [pts[seq[0]]]
                for k in range(len(seq) - 1):
                    path += bulge_points(pts[seq[k]], pts[seq[k + 1]], bul[seq[k]])
                wp = [_apply(mo, p) for p in path]
                for k in range(len(wp) - 1):
                    segs.append((wp[k][0], wp[k][1], wp[k + 1][0], wp[k + 1][1], layer))
                if e.get('closed') or (len(pts) > 3 and math.dist(pts[0], pts[-1]) < 1e-6):
                    closed.append((wp, layer))
                for k in range(len(seq) - 1):
                    b = bul[seq[k]]
                    if abs(b) > 1e-9:
                        p0, p1 = _apply(mo, pts[seq[k]]), _apply(mo, pts[seq[k + 1]])
                        ang = 4 * math.atan(b)
                        if 1.2 < abs(ang) < 1.9:
                            arcs.append(_bulge_arc(p0, p1, b if (mo[0] * mo[4] - mo[1] * mo[3]) > 0 else -b, layer))
            elif t in ('ARC', 'CIRCLE'):
                a0, a1 = e.get('a0', 0.0), e.get('a1', 2 * math.pi)
                if t == 'CIRCLE':
                    a0, a1 = 0.0, 2 * math.pi - 1e-9
                w = _arc_world(m, e['c'], e['r'], a0, a1, e.get('ocs'))
                if w:
                    c, r, b0, b1 = w
                    pts = _arc_pts(c, r, b0, b1) if t == 'ARC' else _arc_pts(c, r, 0, 2 * math.pi)
                    if t == 'ARC':
                        arcs.append((c[0], c[1], r, b0, b1, layer))
                else:
                    pts = [_apply(mo, p) for p in _arc_pts((e['c'][0], e['c'][1]) if not e.get('ocs') else e['c'],
                                                            e['r'], a0, a1)]
                for k in range(len(pts) - 1):
                    segs.append((pts[k][0], pts[k][1], pts[k + 1][0], pts[k + 1][1], layer))
            elif t == 'ELLIPSE':
                (cx, cy), (mx, my) = e['c'], e['major']
                R = math.hypot(mx, my)
                ang = math.atan2(my, mx)
                a0, a1 = e['a0'], e['a1']
                while a1 <= a0:
                    a1 += 2 * math.pi
                n = max(4, int(math.degrees(a1 - a0) / 15))
                pts = []
                for k in range(n + 1):
                    tt = a0 + (a1 - a0) * k / n
                    u, v = R * math.cos(tt), R * e['ratio'] * math.sin(tt)
                    pts.append(_apply(m, (cx + u * math.cos(ang) - v * math.sin(ang), cy + u * math.sin(ang) + v * math.cos(ang))))
                for k in range(n):
                    segs.append((pts[k][0], pts[k][1], pts[k + 1][0], pts[k + 1][1], layer))
            elif t == 'SOLID':
                p = [_apply(mo, q) for q in e['pts']]
                for k in range(4):
                    a, b = p[k], p[(k + 1) % 4]
                    if math.dist(a, b) > 1e-9:
                        segs.append((a[0], a[1], b[0], b[1], layer))
            elif t in ('TEXT', 'MTEXT', 'ATTRIB'):
                plain = mtext_plain(e.get('text', ''))
                if not plain.strip():
                    continue
                c, h, rot = text_centre(e, plain, m)
                texts.append({'text': plain, 'x': c[0], 'y': c[1], 'h': h, 'rot': rot, 'layer': layer,
                              'block': block_name})
            elif t == 'INSERT':
                b = blocks.get(e.get('name'))
                mi = _mat_mul(m, _insert_matrix(e, b['base'] if b else (0.0, 0.0)))
                ip = _apply(m, e['p'] if not e.get('ocs') else (-e['p'][0], e['p'][1]))
                inserts.append({'name': e.get('name') or '', 'x': ip[0], 'y': ip[1],
                                'rot': math.atan2(mi[3], mi[0]), 'sx': math.hypot(mi[0], mi[3]),
                                'sy': math.hypot(mi[1], mi[4]), 'layer': layer,
                                'mirror': (mi[0] * mi[4] - mi[1] * mi[3]) < 0, 'depth': depth})
                if b and depth < max_depth:
                    walk(b['ents'], mi, depth + 1, layer, e.get('name'))
                for a in e.get('attribs', []):
                    plain = mtext_plain(a.get('text', ''))
                    if plain.strip():
                        c, h, rot = text_centre(a, plain, m)
                        texts.append({'text': plain, 'x': c[0], 'y': c[1], 'h': h, 'rot': rot,
                                      'layer': a.get('layer', layer), 'block': e.get('name')})

    walk(dwg['model'], IDENT, 0, None, None)
    return {'segs': segs, 'arcs': arcs, 'texts': texts, 'inserts': inserts, 'closed': closed,
            'layers': layers, 'units': dwg.get('units'), 'version': dwg.get('version')}


def _bulge_arc(p0, p1, b, layer):
    """A polyline arc segment as (cx, cy, r, a0, a1, layer), counter-clockwise from a0 to a1."""
    (cx, cy), r, a0, ang = _bulge_geom(p0, p1, b)
    a1 = a0 + ang
    if ang < 0:
        a0, a1 = a1, a0
    return (cx, cy, r, a0, a1, layer)
