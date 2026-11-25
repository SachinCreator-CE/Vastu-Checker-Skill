"""Write AutoCAD 2010 format DWG files (AC1024) from simple drawing items.

The same writer as the house-structural-engineer skill's scripts/dwg.py (keep the two copies in step), working on
the template in ../assets/dwg_template.json.

The file is built on a template made from a house drawing (assets/dwg_template.json): its layers, linetypes,
text and dimension styles, layouts and dictionaries are kept, and the drawing's entities are added to model
space as LINE, LWPOLYLINE, ARC, CIRCLE, SOLID and MTEXT objects. The encoders reproduce every object of these
types in the source drawing byte for byte.

    from dwg_write import DwgWriter
    w = DwgWriter()                      # loads the template next to the skill
    w.layer('Door', 3)                   # optional: new layer with a colour (ACI)
    w.line('Wall', (0, 0), (120, 0))
    w.text('Text', (60, 30), 6, 'BED-ROOM-1\\n12\\'0" X 14\\'6"', 'center', 'middle')
    w.save('plan.dwg')
"""
import base64
import json
import math
import struct
import uuid
import zlib
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / 'assets' / 'dwg_template.json'

# ---------------------------------------------------------------- checks and codes

_T = []
for _i in range(256):
    _c = _i
    for _ in range(8):
        _c = (_c >> 1) ^ 0xA001 if _c & 1 else _c >> 1
    _T.append(_c)


def crc16(data, seed=0xC0C1):
    c = seed
    for b in data:
        c = (c >> 8) ^ _T[(c ^ b) & 0xFF]
    return c


def checksum(seed, buf):
    s1, s2 = seed & 0xFFFF, seed >> 16
    i, n = 0, len(buf)
    while n:
        c = min(0x15B0, n)
        n -= c
        for k in range(c):
            s1 += buf[i]
            s2 += s1
            i += 1
        s1 %= 0xFFF1
        s2 %= 0xFFF1
    return (s2 << 16) | (s1 & 0xFFFF)


def enc_umc(v):
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def enc_smc(v):
    neg, v, out = v < 0, abs(v), bytearray()
    while True:
        if v >= 0x40:
            out.append((v & 0x7F) | 0x80)
            v >>= 7
        else:
            out.append(v | (0x40 if neg else 0))
            return bytes(out)


def enc_ms(v):
    out = bytearray()
    while v >= 0x8000:
        w = (v & 0x7FFF) | 0x8000
        out += bytes([w & 0xFF, w >> 8])
        v >>= 15
    out += bytes([v & 0xFF, v >> 8])
    return bytes(out)


_ZERO = struct.pack('<d', 0.0)
_ONE = struct.pack('<d', 1.0)


class W:
    """Bit writer, most significant bit first."""

    def __init__(self):
        self.b = bytearray()
        self.n = 0

    def bits(self, v, n):
        for i in range(n - 1, -1, -1):
            if self.n % 8 == 0:
                self.b.append(0)
            if (v >> i) & 1:
                self.b[-1] |= 0x80 >> (self.n % 8)
            self.n += 1

    def raw(self, data, nbits, start=0):
        """Copy nbits from a byte buffer starting at bit `start`."""
        for p in range(start, start + nbits):
            self.bits((data[p >> 3] >> (7 - (p & 7))) & 1, 1)

    def extend(self, other):
        self.raw(other.b, other.n)

    def B(self, v): self.bits(1 if v else 0, 1)
    def BB(self, v): self.bits(v, 2)
    def RC(self, v): self.bits(v & 0xFF, 8)
    def RS(self, v): self.RC(v); self.RC(v >> 8)
    def RL(self, v): self.RS(v & 0xFFFF); self.RS((v >> 16) & 0xFFFF)

    def RD(self, v):
        for x in struct.pack('<d', v):
            self.RC(x)

    def BS(self, v):
        v &= 0xFFFF
        if v == 0: self.bits(2, 2)
        elif v == 256: self.bits(3, 2)
        elif v < 256: self.bits(1, 2); self.RC(v)
        else: self.bits(0, 2); self.RS(v)

    def BL(self, v):
        v &= 0xFFFFFFFF
        if v == 0: self.bits(2, 2)
        elif v < 256: self.bits(1, 2); self.RC(v)
        else: self.bits(0, 2); self.RL(v)

    def BD(self, v):
        p = struct.pack('<d', v)
        if p == _ZERO: self.bits(2, 2)
        elif p == _ONE: self.bits(1, 2)
        else: self.bits(0, 2); self.RD(v)

    def DD(self, v, dflt):
        a, d = struct.pack('<d', v), struct.pack('<d', dflt)
        if a == d:
            self.bits(0, 2)
        elif a[4:] == d[4:]:
            self.bits(1, 2)
            for i in range(4): self.RC(a[i])
        elif a[6:] == d[6:]:
            self.bits(2, 2)
            self.RC(a[4]); self.RC(a[5])
            for i in range(4): self.RC(a[i])
        else:
            self.bits(3, 2)
            self.RD(v)

    def BT(self, v):
        if struct.pack('<d', v) == _ZERO: self.B(1)
        else: self.B(0); self.BD(v)

    def BE(self, v):
        if tuple(v) == (0.0, 0.0, 1.0): self.B(1)
        else: self.B(0); self.BD(v[0]); self.BD(v[1]); self.BD(v[2])

    def BOT(self, v):
        if v < 256: self.bits(0, 2); self.RC(v)
        else: self.bits(2, 2); self.RS(v)

    def H(self, code, v):
        n = (v.bit_length() + 7) // 8
        self.bits(code, 4)
        self.bits(n, 4)
        for i in range(n - 1, -1, -1):
            self.RC(v >> (8 * i))

    def TU(self, s):
        self.BS(len(s))
        for ch in s:
            self.RS(ord(ch))

    def CMC(self, idx, rgb, cb):
        self.BS(idx); self.BL(rgb); self.RC(cb)


def pack_object(main, strings, handles):
    body = W()
    body.extend(main)
    if strings is not None:
        body.extend(strings)
        n = strings.n
        if n >= 0x8000:
            body.RS(n >> 15)
            body.RS((n & 0x7FFF) | 0x8000)
        else:
            body.RS(n)
        body.B(1)
    else:
        body.B(0)
    mainbits = body.n
    body.extend(handles)
    while body.n % 8:
        body.bits(0, 1)
    size = body.n // 8
    out = enc_ms(size) + enc_umc(size * 8 - mainbits) + bytes(body.b)
    c = crc16(out)
    return out + bytes([c & 0xFF, c >> 8])


# ---------------------------------------------------------------- objects

LINE, LWPOLYLINE, ARC, CIRCLE, SOLID, MTEXT = 19, 77, 17, 18, 31, 44


def _entity(typ, handle, layer, color=256, ltype=None, ltscale=1.0, lw=29):
    m = W()
    m.BOT(typ)
    m.H(0, handle)
    m.BS(0)                 # no extended data
    m.B(0)                  # no graphic image
    m.BB(2)                 # model space
    m.BL(0)                 # reactors
    m.B(1)                  # no extension dictionary
    m.BS(color)
    m.BD(ltscale)
    m.BB(3 if ltype else 0)  # linetype by layer, or a linetype handle follows
    m.BB(0)                 # plot style by layer
    m.BB(0)                 # material by layer
    m.RC(0)                 # shadows
    m.B(0); m.B(0); m.B(0)  # visual styles
    m.BS(0)                 # visible
    m.RC(lw)                # lineweight: 29 by layer
    h = W()
    h.H(5, layer)
    if ltype:
        h.H(5, ltype)
    return m, h


def obj_line(handle, layer, p1, p2, **kw):
    m, h = _entity(LINE, handle, layer, **kw)
    m.B(1)
    m.RD(p1[0]); m.DD(p2[0], p1[0]); m.RD(p1[1]); m.DD(p2[1], p1[1])
    m.BT(0.0); m.BE((0.0, 0.0, 1.0))
    return pack_object(m, None, h)


def obj_pline(handle, layer, pts, closed=False, width=None, bulges=None, **kw):
    """Lightweight polyline; bulges (one per vertex, tan of a quarter of the arc angle) make arc segments."""
    m, h = _entity(LWPOLYLINE, handle, layer, **kw)
    flag = (512 if closed else 0) | (4 if width else 0) | (16 if bulges else 0)
    m.BS(flag)
    if width:
        m.BD(width)
    m.BL(len(pts))
    if bulges:
        m.BL(len(bulges))
    x, y = pts[0]
    m.RD(x); m.RD(y)
    for nx, ny in pts[1:]:
        m.DD(nx, x); m.DD(ny, y)
        x, y = nx, ny
    for b in bulges or []:
        m.BD(b)
    return pack_object(m, None, h)


def obj_circle(handle, layer, c, r, **kw):
    m, h = _entity(CIRCLE, handle, layer, **kw)
    m.BD(c[0]); m.BD(c[1]); m.BD(0.0); m.BD(r); m.BT(0.0); m.BE((0.0, 0.0, 1.0))
    return pack_object(m, None, h)


def obj_arc(handle, layer, c, r, a0, a1, **kw):
    """Angles in degrees, counter-clockwise from a0 to a1."""
    m, h = _entity(ARC, handle, layer, **kw)
    m.BD(c[0]); m.BD(c[1]); m.BD(0.0); m.BD(r); m.BT(0.0); m.BE((0.0, 0.0, 1.0))
    m.BD(math.radians(a0 % 360)); m.BD(math.radians(a1 % 360))
    return pack_object(m, None, h)


def obj_solid(handle, layer, pts, **kw):
    """Corners in drawing order around the shape (3 or 4 points)."""
    pts = list(pts) + [pts[-1]] * (4 - len(pts))
    m, h = _entity(SOLID, handle, layer, **kw)
    m.BT(0.0); m.BD(0.0)
    for x, y in (pts[0], pts[1], pts[3], pts[2]):
        m.RD(x); m.RD(y)
    m.BE((0.0, 0.0, 1.0))
    return pack_object(m, None, h)


SPECIAL_CODES = (('%%c', '\u00d8'), ('%%C', '\u00d8'), ('%%d', '\u00b0'), ('%%D', '\u00b0'), ('%%p', '\u00b1'),
                 ('%%P', '\u00b1'))


def mtext_escape(s):
    """MTEXT contents: backslashes and braces escaped, newlines as \\P, and the TEXT control codes %%c, %%d, %%p
    written as the Unicode characters (diameter, degree, plus-minus) so every font shows them."""
    for a, b in SPECIAL_CODES:
        s = s.replace(a, b)
    return s.replace('\\', '\\\\').replace('{', '\\{').replace('}', '\\}').replace('\n', '\\P')


ATTACH = {('left', 'top'): 1, ('center', 'top'): 2, ('right', 'top'): 3,
          ('left', 'middle'): 4, ('center', 'middle'): 5, ('right', 'middle'): 6,
          ('left', 'bottom'): 7, ('center', 'bottom'): 8, ('right', 'bottom'): 9}


DEFAULT_FONT = 'Bookman Old Style'     # inline MTEXT font; drawing generators may set their office font


def obj_mtext(handle, layer, style, p, h, text, ha='left', va='baseline', rot=0.0, bold=True,
              font=None, width_est=None, **kw):
    font = font or DEFAULT_FONT
    m, hs = _entity(MTEXT, handle, layer, **kw)
    rows = text.split('\n')
    m.BD(p[0]); m.BD(p[1]); m.BD(0.0)
    m.BD(0.0); m.BD(0.0); m.BD(1.0)
    a = math.radians(rot)
    m.BD(math.cos(a) if abs(math.cos(a)) > 1e-12 else 0.0)
    m.BD(math.sin(a) if abs(math.sin(a)) > 1e-12 else 0.0)
    m.BD(0.0)
    m.BD(0.0)                          # no wrapping width
    m.BD(0.0)
    m.BD(h)
    m.BS(ATTACH[(ha, 'bottom' if va == 'baseline' else va)])
    m.BS(5)                            # drawing direction: by style
    m.BD(h * (1 + 1.6667 * (len(rows) - 1)))
    m.BD(width_est if width_est else 0.9 * h * max(len(r) for r in rows))
    s = W()
    s.TU('{\\f%s|b%d|i0|c0|p18;%s}' % (font, 1 if bold else 0, mtext_escape(text)))
    m.BS(1); m.BD(1.0); m.B(0); m.BL(0)
    hs.H(5, style)
    return pack_object(m, s, hs)


def obj_layer(handle, proto, name, color, flags, ltype):
    c, f, h = proto
    m = W()
    m.BOT(51)
    m.H(0, handle)
    m.BS(0)
    m.BL(0)
    m.B(1)
    s = W()
    s.TU(name)
    m.B(1); m.B(0)
    m.BS(flags)
    m.CMC(0, 0xC3000000 | color, 0)
    hw = W()
    hw.H(*h['owner'])
    hw.H(*h['xref']); hw.H(*h['plotstyle']); hw.H(*h['material'])
    hw.H(5, ltype)
    return pack_object(m, s, hw)


def obj_control(typ, handle, entries, xdic=None, owner=(4, 0)):
    m = W()
    m.BOT(typ)
    m.H(0, handle)
    m.BS(0); m.BL(0); m.B(0 if xdic else 1)
    m.BL(len(entries))
    hw = W()
    hw.H(*owner)
    if xdic:
        hw.H(*xdic)
    for e in entries:
        hw.H(2, e)
    return pack_object(m, None, hw)


def obj_block_header(handle, bh, owned):
    """Model space block record with its entity list (bh: the template's fields for it)."""
    m = W()
    m.BOT(49)
    m.H(0, handle)
    m.BS(0)
    m.BL(0)
    m.B(1)                      # no extension dictionary
    s = W()
    s.TU(bh['name'])
    for key in ('f64', 'xdep', 'anon', 'hasatt', 'isxref', 'overlaid', 'loaded'):
        m.B(bh[key])
    m.BL(len(owned))
    for v in bh['base']:
        m.BD(v)
    s.TU('')
    m.RC(0)
    s.TU('')
    m.BL(0)
    m.BS(bh['units']); m.B(bh['explodable']); m.RC(bh['scaling'])
    hw = W()
    hw.H(*bh['owner'])
    hw.H(*bh['null'])
    hw.H(*bh['block'])
    for e in owned:
        hw.H(3, e)
    hw.H(*bh['endblk'])
    hw.H(*bh['layout'])
    return pack_object(m, s, hw)


def encode_handle_map(items):
    out = bytearray()
    i = 0
    while i < len(items):
        body = bytearray()
        ph = pl = 0
        while i < len(items):
            h, loc = items[i]
            pair = enc_umc(h - ph) + enc_smc(loc - pl)
            if len(body) + len(pair) + 2 > 2034:
                break
            body += pair
            ph, pl = h, loc
            i += 1
        sec = bytes([(len(body) + 2) >> 8, (len(body) + 2) & 0xFF]) + body
        c = crc16(sec)
        out += sec + bytes([c >> 8, c & 0xFF])
    sec = b'\x00\x02'
    c = crc16(sec)
    return bytes(out + sec + bytes([c >> 8, c & 0xFF]))


# ---------------------------------------------------------------- file container

MAGIC = bytes.fromhex('f8466a0496730ed9162f6768d4f74a4ad0576876')


def xor_header(raw):
    out, seed = bytearray(), 1
    for b in raw:
        seed = (seed * 0x343FD + 0x269EC3) & 0xFFFFFFFF
        out.append(b ^ ((seed >> 16) & 0xFF))
    return bytes(out)


def compress_literal(data):
    """A valid DWG LZ77 stream that stores the data as one literal run."""
    n = len(data)
    out = bytearray()
    if n <= 18:
        out.append(n - 3)
    else:
        rest = n - 18
        zeros = (rest - 1) // 0xFF
        out.append(0)
        out += b'\x00' * zeros
        out.append(rest - 0xFF * zeros)
    out += data
    out.append(0x11)
    return bytes(out)


def write_container(fh, sections, order):
    pages, page_of = [], {}
    addr, num = 0x100, 0
    by_name = {s['name']: s for s in sections}
    preview_addr = appinfo_addr = 0
    for name in order:
        s = by_name[name]
        d = s['data']
        page_of[name] = []
        step = s['maxsize']
        for start in range(0, len(d), step):
            chunk = d[start:start + step]
            if not any(chunk):
                continue
            body = compress_literal(chunk) if s['comp'] == 2 else chunk + b'\0' * (step - len(chunk))
            num += 1
            size = (32 + len(body) + 31) // 32 * 32
            dchk = checksum(0, body)
            hdr = [0x4163043B, s['id'], len(body), size, start & 0xFFFFFFFF, start >> 32, 0, dchk]
            hdr[6] = checksum(dchk, struct.pack('<8I', *hdr))
            mask = 0x4164536B ^ addr
            page = struct.pack('<8I', *[v ^ mask for v in hdr]) + body
            page += b'\0' * (size - len(page))
            if name == 'AcDb:Preview' and not preview_addr:
                preview_addr = addr + 0x20
            if name == 'AcDb:AppInfo' and not appinfo_addr:
                appinfo_addr = addr + 0x20
            pages.append((num, addr, page))
            page_of[name].append((num, len(body), start))
            addr += size
    sm = bytearray(struct.pack('<5I', len(sections), 2, 0x7400, 0, len(sections)))
    for s in sections:
        pl = page_of.get(s['name'], [])
        sm += struct.pack('<QIIIIII', len(s['data']), len(pl), s['maxsize'], s['unk'], s['comp'], s['id'], s['enc'])
        sm += s['name'].encode('latin1').ljust(64, b'\0')
        for pnum, csize, start in pl:
            sm += struct.pack('<IIQ', pnum, csize, start)

    def system_page(ptype, payload):
        body = compress_literal(bytes(payload))
        chk = checksum(0, struct.pack('<5I', ptype, len(payload), len(body), 2, 0) + body)
        page = struct.pack('<5I', ptype, len(payload), len(body), 2, chk) + body
        return page + b'\0' * ((len(page) + 31) // 32 * 32 - len(page))

    num += 1
    sm_num = num
    sm_page = system_page(0x4163003B, sm)
    pages.append((sm_num, addr, sm_page))
    addr += len(sm_page)
    num += 1
    pm_num, pm_addr = num, addr
    pm = bytearray()
    for n, a, p in pages:
        pm += struct.pack('<ii', n, len(p))
    pm += struct.pack('<ii', pm_num, 0)
    pm[-4:] = struct.pack('<i', len(system_page(0x41630E3B, pm)))
    pm_page = system_page(0x41630E3B, pm)
    pages.append((pm_num, pm_addr, pm_page))
    end = pm_addr + len(pm_page)
    e = bytearray(0x6C)
    e[0:12] = b'AcFssFcAJMB\0'
    struct.pack_into('<III', e, 0x0C, 0, 0x6C, 4)
    struct.pack_into('<II', e, 0x24, 1, pm_num)
    struct.pack_into('<QQ', e, 0x2C, end - 0x100, end)
    struct.pack_into('<II', e, 0x3C, 0, len(pages))
    struct.pack_into('<IIII', e, 0x44, 0x20, 0x80, 0x40, pm_num)
    struct.pack_into('<Q', e, 0x54, pm_addr - 0x100)
    struct.pack_into('<III', e, 0x5C, sm_num, pm_num, 0)
    struct.pack_into('<I', e, 0x68, zlib.crc32(bytes(e)) & 0xFFFFFFFF)
    enc = xor_header(bytes(e))
    head = bytearray(0x100)
    head[0:6] = b'AC1024'
    head[0x0B] = fh['maint']
    head[0x0C] = fh['b0C']
    struct.pack_into('<I', head, 0x0D, preview_addr)
    head[0x11] = fh['dwgver']
    head[0x12] = fh['appmaint']
    struct.pack_into('<H', head, 0x13, fh['codepage'])
    struct.pack_into('<I', head, 0x28, 0x80)
    struct.pack_into('<I', head, 0x2C, appinfo_addr)
    head[0x80:0x80 + 0x6C] = enc
    head[0xEC:0x100] = MAGIC
    out = bytearray(head)
    for n, a, p in pages:
        out += p
    return bytes(out + enc)


# ---------------------------------------------------------------- the writer

ACI_LINEWEIGHTS = [0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90, 100, 106, 120, 140, 158, 200, 211]


def lineweight_index(mm100):
    """Index of the nearest standard lineweight (hundredths of a mm)."""
    return min(range(len(ACI_LINEWEIGHTS)), key=lambda i: abs(ACI_LINEWEIGHTS[i] - mm100))


class DwgWriter:
    def __init__(self, template=TEMPLATE):
        t = json.loads(Path(template).read_text())
        self.t = t
        self.next = t['handseed']
        self.layers = {k: v['handle'] for k, v in t['layers'].items()}
        self.layer_lookup = {k.lower(): k for k in self.layers}
        self.new_layers = []
        self.entities = []
        self.ext = [math.inf, math.inf, -math.inf, -math.inf]
        self.style = t['styles'][t.get('text_style', 'Standard')]

    def _h(self):
        h = self.next
        self.next += 1
        return h

    def _grow(self, *pts):
        for x, y in pts:
            self.ext[0] = min(self.ext[0], x)
            self.ext[1] = min(self.ext[1], y)
            self.ext[2] = max(self.ext[2], x)
            self.ext[3] = max(self.ext[3], y)

    def linetype(self, name):
        if not name:
            return None
        lt = self.t['linetypes']
        for k, v in lt.items():
            if k.lower() == name.lower():
                return v
        raise KeyError(f'linetype {name!r} is not in the template ({", ".join(lt)})')

    def layer(self, name, color=7, ltype=None, lineweight=None, plot=True):
        """Use a template layer, or create one with an ACI colour, linetype name and lineweight (mm/100)."""
        key = self.layer_lookup.get(name.lower())
        if key:
            return self.layers[key]
        h = self._h()
        lw = 31 if lineweight is None else lineweight_index(lineweight)
        flags = (16 if plot else 0) | (lw << 5)
        self.new_layers.append((h, name, color, flags, self.linetype(ltype) or self.t['linetypes']['Continuous']))
        self.layers[name] = h
        self.layer_lookup[name.lower()] = name
        return h

    def _props(self, kw):
        out = {}
        if kw.get('color') is not None:
            out['color'] = kw['color']
        if kw.get('ltype'):
            out['ltype'] = self.linetype(kw['ltype'])
            out['ltscale'] = float(kw.get('ltscale', 1.0))
        if kw.get('lineweight') is not None:
            out['lw'] = lineweight_index(kw['lineweight'])
        return out

    def line(self, layer, a, b, **kw):
        self._grow(a, b)
        self.entities.append(lambda h, L=self.layer(layer), p=self._props(kw): obj_line(h, L, a, b, **p))

    def pline(self, layer, pts, closed=False, width=None, bulges=None, **kw):
        pts = [tuple(p) for p in pts]
        self._grow(*pts)
        self.entities.append(lambda h, L=self.layer(layer), p=self._props(kw):
                             obj_pline(h, L, pts, closed, width, bulges, **p))

    def dot(self, layer, c, r, **kw):
        """Filled circle of radius r (a closed two-arc polyline with width r, as AutoCAD's DONUT)."""
        self.pline(layer, [(c[0] - r / 2, c[1]), (c[0] + r / 2, c[1])], True, r, [1.0, 1.0], **kw)

    def circle(self, layer, c, r, **kw):
        self._grow((c[0] - r, c[1] - r), (c[0] + r, c[1] + r))
        self.entities.append(lambda h, L=self.layer(layer), p=self._props(kw): obj_circle(h, L, c, r, **p))

    def arc(self, layer, c, r, a0, a1, **kw):
        self._grow((c[0] - r, c[1] - r), (c[0] + r, c[1] + r))
        self.entities.append(lambda h, L=self.layer(layer), p=self._props(kw): obj_arc(h, L, c, r, a0, a1, **p))

    def solid(self, layer, pts, **kw):
        self._grow(*pts)
        self.entities.append(lambda h, L=self.layer(layer), p=self._props(kw): obj_solid(h, L, pts, **p))

    def text(self, layer, p, h, s, ha='left', va='baseline', rot=0.0, bold=True, width_est=None, **kw):
        if not s:
            return
        self._grow(p)
        st = self.style
        self.entities.append(lambda hd, L=self.layer(layer), q=self._props(kw):
                             obj_mtext(hd, L, st, p, h, s, ha, va, rot, bold, width_est=width_est, **q))

    # -- assembly --------------------------------------------------------------
    def _header(self, handseed, ext):
        t = self.t['header']
        raw = base64.b64decode(t['raw'])
        main = W()
        spans = t['spans']
        pos = t['main_start']
        x0, y0, x1, y1 = ext

        def copy_to(p):
            nonlocal pos
            main.raw(raw, p - pos, pos)
            pos = p

        def fields(span, writer):
            nonlocal pos
            copy_to(span[0])
            writer()
            pos = span[1]

        fields(spans['HANDSEED'], lambda: main.H(0, handseed))
        fields(spans['M_EXTMIN'], lambda: (main.BD(x0), main.BD(y0), main.BD(0.0)))
        fields(spans['M_EXTMAX'], lambda: (main.BD(x1), main.BD(y1), main.BD(0.0)))
        fields(spans['M_LIMMIN'], lambda: (main.RD(x0), main.RD(y0)))
        fields(spans['M_LIMMAX'], lambda: (main.RD(x1), main.RD(y1)))
        copy_to(t['main_end'])
        strings = bytearray(raw)
        for gpos in t['guids']:                 # fresh GUIDs, same length
            new = '{%s}' % str(uuid.uuid4()).upper()
            g = W()
            g.TU(new)
            for i in range(g.n):
                p = gpos + i
                bit = (g.b[i >> 3] >> (7 - (i & 7))) & 1
                if bit:
                    strings[p >> 3] |= 0x80 >> (p & 7)
                else:
                    strings[p >> 3] &= ~(0x80 >> (p & 7)) & 0xFF
        data = W()
        sizebits = 32 + main.n + t['str_bits'] + 17
        data.RL(sizebits)
        data.extend(main)
        data.raw(strings, t['str_bits'], t['main_end'])
        data.RS(t['str_bits'])
        data.B(1)
        data.raw(raw, t['hnd_end'] - t['hnd_start'], t['hnd_start'])
        while data.n % 8:
            data.bits(0, 1)
        body = struct.pack('<II', len(data.b), 0) + bytes(data.b)
        c = crc16(body)
        return (raw[:16] + body + bytes([c & 0xFF, c >> 8]) + base64.b64decode(t['end_sentinel'])
                + base64.b64decode(t['tail']))

    def _vport(self, ext):
        v = self.t['vport']
        raw = bytearray(base64.b64decode(v['raw']))
        x0, y0, x1, y1 = ext
        w, h = (x1 - x0) * 1.08, (y1 - y0) * 1.08
        height = max(h, w * 0.62)

        def put_rd(bitpos, value):
            bb = struct.pack('<d', value)
            for i in range(64):
                p = bitpos + i
                bit = (bb[i >> 3] >> (7 - (i & 7))) & 1
                if bit:
                    raw[p >> 3] |= 0x80 >> (p & 7)
                else:
                    raw[p >> 3] &= ~(0x80 >> (p & 7)) & 0xFF
        put_rd(v['height_rd'], height)
        put_rd(v['width_rd'], height * 1.6)
        put_rd(v['center_rd'], (x0 + x1) / 2)
        put_rd(v['center_rd'] + 64, (y0 + y1) / 2)
        size_end = v['data_end']
        body = bytes(raw[:size_end])
        c = crc16(body)
        return body + bytes([c & 0xFF, c >> 8])

    def tobytes(self):
        t = self.t
        objects = {int(k, 16): base64.b64decode(v) for k, v in t['objects'].items()}
        handles = [self._h() for _ in self.entities]
        for h, make in zip(handles, self.entities):
            objects[h] = make(h)
        proto = (None, None, t['layer_proto'])
        for h, name, color, flags, lt in self.new_layers:
            objects[h] = obj_layer(h, proto, name, color, flags, lt)
        lc = t['layer_control']
        objects[lc['handle']] = obj_control(50, lc['handle'], lc['entries'] + [h for h, *_ in self.new_layers],
                                            lc.get('xdic'))
        ms = t['model_space']
        objects[ms['handle']] = obj_block_header(ms['handle'], ms, handles)
        if math.isinf(self.ext[0]):
            self.ext = [0.0, 0.0, 100.0, 100.0]
        ext = tuple(self.ext)
        objects[t['vport']['handle']] = self._vport(ext)
        handseed = self.next
        sec = bytearray(b'\xca\x0d\x00\x00')
        locs = []
        for h in sorted(objects):
            locs.append((h, len(sec)))
            sec += objects[h]
        aux = bytearray(base64.b64decode(t['aux']['raw']))
        struct.pack_into('<I', aux, t['aux']['handseed_at'], handseed)
        ofs = bytearray(base64.b64decode(t['objfreespace']['raw']))
        struct.pack_into('<I', ofs, t['objfreespace']['count_at'], len(objects))
        data = dict((k, base64.b64decode(v)) for k, v in t['blobs'].items())
        data['AcDb:AcDbObjects'] = bytes(sec)
        data['AcDb:Handles'] = encode_handle_map(locs)
        data['AcDb:Header'] = self._header(handseed, ext)
        data['AcDb:AuxHeader'] = bytes(aux)
        data['AcDb:ObjFreeSpace'] = bytes(ofs)
        sections = [dict(s, data=data.get(s['name'], b'')) for s in t['sections']]
        return write_container(t['file_header'], sections, t['order'])

    def save(self, path):
        Path(path).write_bytes(self.tobytes())
        return path


def draw_items(items, path, layer_props=None, template=TEMPLATE):
    """Write the items of the plan generator's Drawing (line, pline, arc, circle, solid, text tuples) as DWG.
    layer_props: {layer name: dict(color=, ltype=, lineweight=)} for layers to create or map;
    a 'map_to' key renames the layer (e.g. 'Str-Column' -> 'Str Column')."""
    w = DwgWriter(template)
    props = layer_props or {}

    def L(name):
        p = props.get(name, {})
        target = p.get('map_to', name)
        w.layer(target, p.get('color', 7), p.get('ltype'), p.get('lineweight'))
        return target

    for it in items:
        kind, layer = it[0], L(it[1])
        kw = it[-1] if isinstance(it[-1], dict) else {}
        if kind == 'line':
            w.line(layer, it[2], it[3], **kw)
        elif kind == 'pline':
            w.pline(layer, it[2], it[3], **kw)
        elif kind == 'arc':
            w.arc(layer, it[2], it[3], it[4], it[5], **kw)
        elif kind == 'circle':
            w.circle(layer, it[2], it[3], **kw)
        elif kind == 'solid':
            w.solid(layer, it[2], **kw)
        elif kind == 'dot':
            w.dot(layer, it[2], it[3], **kw)
        elif kind == 'text':
            (x, y), h, s, ha, va, rot, style = it[2:9]
            w.text(layer, (x, y), h, s, ha, va, rot, bold=(style == 'B'), **kw)
    return w.save(path)
