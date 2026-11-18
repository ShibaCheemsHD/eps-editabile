import base64, io, json, math, os, re, shutil, struct, subprocess, tempfile, uuid, datetime
import tedoc
from tedoc import D, N, S, ustr
MM = 72 / 25.4
KAPPA = 0.5522847498
HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, '..', 'assets', 'modello_illustrator.eps')

class Spot:

    def __init__(self, name, cmyk_alt):
        self.name, self.alt = (name, tuple(cmyk_alt))

def _cmyk(color, tint=100):
    c = color.alt if isinstance(color, Spot) else color
    t = tint / 100
    return tuple((v / 100 * t for v in c))

def _f(v):
    s = f'{v:.4f}'.rstrip('0').rstrip('.')
    return '0' if s in ('-0', '') else s

class FontMetrics:

    def __init__(self, path):
        from fontTools.ttLib import TTFont
        f = TTFont(path, lazy=True)
        self.upm = f['head'].unitsPerEm
        self.cmap = f.getBestCmap()
        self.hmtx = f['hmtx']
        self.asc, self.desc = (f['hhea'].ascent, f['hhea'].descent)
        self.font = f

    def width(self, text, size):
        w = 0
        for ch in text:
            g = self.cmap.get(ord(ch)) or self.cmap.get(32)
            w += self.hmtx[g][0] if g else self.upm * 0.5
        return w * size / self.upm

    def gids(self, text):
        out = []
        for ch in text:
            g = self.cmap.get(ord(ch))
            out.append(self.font.getGlyphID(g) if g else 0)
        return out

class EstimatedMetrics:
    upm, asc, desc = (1000, 950, -250)

    def width(self, text, size):
        return len(text) * 0.58 * size

    def gids(self, text):
        return [max(1, ord(c) - 29) if ord(c) < 8192 else 1 for c in text]

def _a85_lines(data, prefix=b'%'):
    enc = base64.a85encode(data, adobe=False) + b'~>'
    return [prefix + enc[i:i + 100] for i in range(0, len(enc), 100)]

def _a85_decode_block(lines):
    s = b''.join((l[1:].strip() if l.startswith(b'%') else l.strip() for l in lines))
    return base64.a85decode(s[:s.find(b'~>')], adobe=False)

class Document:

    def __init__(self, width_mm, height_mm, fonts_json=None, bleed_mm=5.0, safe_mm='auto', title='Progetto', template=TEMPLATE, tac_max=300, min_text_pt=8.0, min_line_pt=0.25):
        self.W, self.H = (float(width_mm), float(height_mm))
        if safe_mm == 'auto':
            short, long_ = (min(width_mm, height_mm), max(width_mm, height_mm))
            safe_mm = 3.0 if short < 100 else 10.0 if long_ > 500 else 5.0
        self.bleed, self.safe, self.title = (float(bleed_mm), float(safe_mm), title)
        self.tac_max, self.min_text_pt, self.min_line_pt = (tac_max, min_text_pt, min_line_pt)
        self.layer_props = {}
        self.notes = []
        self.typo_fixes = []
        self.template = template
        self.layers = {}
        self.items, self.warnings = ([], [])
        self.used_fonts = []
        self.fonts, self.fonts_dir = ({}, None)
        if fonts_json:
            self.fonts_dir = os.path.dirname(os.path.abspath(fonts_json))
            self.fonts = json.load(open(fonts_json))
        self._metrics = {}

    def metrics(self, font):
        if font not in self._metrics:
            info = self.fonts.get(font)
            if info and info.get('file'):
                self._metrics[font] = FontMetrics(os.path.join(self.fonts_dir, info['file']))
            else:
                if font not in self.fonts:
                    self.warnings.append(f"Font '{font}' non in fonts.json: si assume installato (a licenza)")
                self._metrics[font] = EstimatedMetrics()
        return self._metrics[font]

    def text_width(self, text, font, size_pt, tracking=0):
        w = self.metrics(font).width(text, size_pt) + tracking * size_pt / 1000 * len(text)
        return w / MM

    def fit_size(self, text, font, max_width_mm, max_size_pt, min_size_pt=4, tracking=0):
        w = self.text_width(text, font, max_size_pt, tracking)
        size = max_size_pt if w <= max_width_mm else max_size_pt * max_width_mm / w
        return max(min_size_pt, math.floor(size * 4) / 4)

    def layer(self, name, printable=True):
        self.layers.setdefault(name, [])
        self.layer_props[name] = {'print': printable}
        return name

    def _add(self, layer, item):
        if layer not in self.layers:
            self.layer(layer, printable=layer != 'Note')
        self.layers[layer].append(item)

    def _register(self, kind, x0, y0, x1, y1, bleed, label):
        self.items.append(dict(kind=kind, x0=min(x0, x1), y0=min(y0, y1), x1=max(x0, x1), y1=max(y0, y1), bleed=bleed, label=label))

    def _bleedbox(self):
        b = self.bleed
        return (-b, -b, self.W + b, self.H + b)

    @staticmethod
    def _bounds(cmds):
        xs = [v for c in cmds for v in c[1::2]]
        ys = [v for c in cmds for v in c[2::2]]
        return (min(xs), min(ys), max(xs), max(ys))

    def _shape(self, cmds, fill, stroke, stroke_w, bleed, tint, label, layer):
        clip = False
        op_fill = op_stroke = False
        bx = self._bounds(cmds)
        area = (bx[2] - bx[0]) * (bx[3] - bx[1])
        if _is_black(fill):
            if area > 2000:
                fill = (60, 40, 40, 100)
                self.notes.append(f'{label}: nero pieno su grande area → nero ricco C60 M40 Y40 K100')
            else:
                fill, op_fill = ((0, 0, 0, 100), True)
        if _is_black(stroke):
            stroke, op_stroke = ((0, 0, 0, 100), True)
        if bleed:
            bx0, by0, bx1, by1 = self._bleedbox()
            x0, y0, x1, y1 = self._bounds(cmds)
            clip = x0 < bx0 or y0 < by0 or x1 > bx1 or y1 > by1
        self._add(layer, dict(kind='path', cmds=cmds, fill=fill, stroke=stroke, stroke_w=stroke_w, tint=tint, clip=clip, label=label, op_fill=op_fill, op_stroke=op_stroke))
        x0, y0, x1, y1 = self._bounds(cmds)
        sw = stroke_w / MM / 2 if stroke is not None and (not bleed) else 0
        if bleed:
            bx0, by0, bx1, by1 = self._bleedbox()
            x0, y0, x1, y1 = (max(x0, bx0), max(y0, by0), min(x1, bx1), min(y1, by1))
        self._register('forma', x0 - sw, y0 - sw, x1 + sw, y1 + sw, bleed, label)

    def background(self, color, tint=100, layer='Fondo'):
        b = self.bleed
        self.rect(-b, -b, self.W + 2 * b, self.H + 2 * b, fill=color, tint=tint, bleed=True, label='fondo', layer=layer)

    def rect(self, x, y, w, h, fill=None, stroke=None, stroke_w=1, radius=0, bleed=False, tint=100, label='rettangolo', layer='Grafica'):
        if radius:
            r, k = (radius, radius * KAPPA)
            x1, y1 = (x + w, y + h)
            cmds = [('M', x + r, y), ('L', x1 - r, y), ('C', x1 - r + k, y, x1, y + r - k, x1, y + r), ('L', x1, y1 - r), ('C', x1, y1 - r + k, x1 - r + k, y1, x1 - r, y1), ('L', x + r, y1), ('C', x + r - k, y1, x, y1 - r + k, x, y1 - r), ('L', x, y + r), ('C', x, y + r - k, x + r - k, y, x + r, y), ('Z',)]
        else:
            cmds = [('M', x, y), ('L', x + w, y), ('L', x + w, y + h), ('L', x, y + h), ('Z',)]
        self._shape(cmds, fill, stroke, stroke_w, bleed, tint, label, layer)

    def ellipse(self, cx, cy, rx, ry=None, fill=None, stroke=None, stroke_w=1, bleed=False, tint=100, label='ellisse', layer='Grafica'):
        ry = rx if ry is None else ry
        kx, ky = (rx * KAPPA, ry * KAPPA)
        cmds = [('M', cx + rx, cy), ('C', cx + rx, cy + ky, cx + kx, cy + ry, cx, cy + ry), ('C', cx - kx, cy + ry, cx - rx, cy + ky, cx - rx, cy), ('C', cx - rx, cy - ky, cx - kx, cy - ry, cx, cy - ry), ('C', cx + kx, cy - ry, cx + rx, cy - ky, cx + rx, cy), ('Z',)]
        self._shape(cmds, fill, stroke, stroke_w, bleed, tint, label, layer)

    def path(self, cmds, fill=None, stroke=None, stroke_w=1, bleed=False, tint=100, label='tracciato', layer='Grafica'):
        self._shape(list(cmds), fill, stroke, stroke_w, bleed, tint, label, layer)

    def polygon(self, points, **kw):
        cmds = [('M',) + tuple(points[0])] + [('L',) + tuple(p) for p in points[1:]] + [('Z',)]
        self.path(cmds, **kw)

    def line(self, x1, y1, x2, y2, stroke=(0, 0, 0, 100), stroke_w=1, bleed=False, tint=100, label='linea', layer='Grafica'):
        self._shape([('M', x1, y1), ('L', x2, y2)], None, stroke, stroke_w, bleed, tint, label, layer)

    def text(self, x, y, text, font, size, color=(0, 0, 0, 100), align='left', leading=None, tracking=0, tint=100, label=None, layer='Testi', typo=True):
        if typo:
            text, fixes = tipografia_it(text)
            if fixes:
                self.typo_fixes += [f'«{text.split(chr(10))[0][:30]}»: {f}' for f in fixes]
        if _is_black(color):
            color = (0, 0, 0, 100)
        paras = text.split('\n')
        lead = leading or round(size * 1.2, 2)
        m = self.metrics(font)
        if font not in self.used_fonts:
            self.used_fonts.append(font)
        if isinstance(color, Spot):
            self.warnings.append(f'testo «{paras[0][:20]}»: tinta piatta resa in quadricromia')
        self._add(layer, dict(kind='text', x=x, y=y, paras=paras, font=font, size=size, color=color, tint=tint, align=align, lead=lead, tracking=tracking, label=label or f'testo «{paras[0][:30]}»'))
        widths = [self.text_width(p, font, size, tracking) for p in paras]
        asc, desc = (m.asc * size / m.upm / MM, m.desc * size / m.upm / MM)
        lefts = [{'left': x, 'center': x - w / 2, 'right': x - w}[align] for w in widths]
        self._register('testo', min(lefts), y - asc, max((l + w for l, w in zip(lefts, widths))), y + (len(paras) - 1) * lead / MM - desc, False, label or f'testo «{paras[0][:30]}»')
        return max(widths)

    def check(self, tol=0.05):
        errs, b, s = ([], self.bleed, self.safe)
        errs += self._check_print()[0]
        for it in self.items:
            lab = it['label']
            if not it['bleed']:
                if it['x0'] < s - tol or it['y0'] < s - tol or it['x1'] > self.W - s + tol or (it['y1'] > self.H - s + tol):
                    errs.append(f"{lab}: fuori dall'area di sicurezza ({s:g} mm dal taglio)")
                continue
            out = {'sinistra': it['x0'] < -tol, 'destra': it['x1'] > self.W + tol, 'alto': it['y0'] < -tol, 'basso': it['y1'] > self.H + tol}
            short = {'sinistra': it['x0'] > -b + tol, 'destra': it['x1'] < self.W + b - tol, 'alto': it['y0'] > -b + tol, 'basso': it['y1'] < self.H + b - tol}
            touch = {'sinistra': abs(it['x0']) <= tol, 'destra': abs(it['x1'] - self.W) <= tol, 'alto': abs(it['y0']) <= tol, 'basso': abs(it['y1'] - self.H) <= tol}
            for side in out:
                if out[side] and short[side]:
                    errs.append(f'{lab}: esce dal taglio a {side} ma non copre tutti i {b:g} mm di abbondanza')
                if touch[side]:
                    errs.append(f'{lab}: finisce esattamente sul taglio a {side}, senza abbondanza')
        return errs

    def _check_print(self):
        errs, warns = ([], [])
        for lname, items in self.layers.items():
            if not self.layer_props.get(lname, {}).get('print', True):
                continue
            for it in items:
                lab = it.get('label', '')
                cols = [(it.get('fill'), 'riempimento'), (it.get('stroke'), 'traccia'), (it.get('color'), 'testo')]
                for c, what in cols:
                    if c is None or isinstance(c, Spot):
                        continue
                    tac = sum(c) * it['tint'] / 100
                    if tac > self.tac_max + 0.01:
                        errs.append(f'{lab}: copertura {what} {tac:.0f}% oltre il limite {self.tac_max}%')
                if it['kind'] == 'path' and it['stroke'] is not None and (it['stroke_w'] < self.min_line_pt):
                    errs.append(f"{lab}: filetto {it['stroke_w']:g} pt sotto il minimo {self.min_line_pt:g} pt")
                if it['kind'] == 'text':
                    if it['size'] < self.min_text_pt:
                        errs.append(f"{lab}: corpo {it['size']:g} pt sotto il minimo {self.min_text_pt:g} pt")
                    c = it['color']
                    if it['size'] < 12 and c is not None and (not isinstance(c, Spot)):
                        inks = sum((1 for v in c if v * it['tint'] / 100 > 0.5))
                        if inks >= 3:
                            warns.append(f"{lab}: testo piccolo ({it['size']:g} pt) composto da {inks} inchiostri (rischio fuori registro: meglio 1–2 inchiostri)")
                    if len(it['paras']) > 1:
                        last = [p for p in it['paras'] if p.strip()][-1]
                        if len(last.split()) == 1:
                            warns.append(f"{lab}: parola isolata nell'ultima riga («{last.strip()}»)")
        return (errs, warns)

    def tac_max_found(self):
        m = 0
        for items in self.layers.values():
            for it in items:
                for c in (it.get('fill'), it.get('stroke'), it.get('color')):
                    if c is not None and (not isinstance(c, Spot)):
                        m = max(m, sum(c) * it['tint'] / 100)
        return m

    def report(self):
        L = [f'Formato rifilato: {self.W:g} x {self.H:g} mm (= tavola da disegno)', f'Abbondanza: {self.bleed:g} mm su tutti i lati (impostata anche nel documento)', f'Area di sicurezza: {self.safe:g} mm dal taglio', 'Segni di taglio: nessuno', 'Maschere: al massimo una per gruppo di forme in abbondanza, mai annidate', 'Livelli: ' + ', '.join((f"{n}{('' if self.layer_props.get(n, {}).get('print', True) else ' (non stampabile)')}" for n in self.layers)), 'Testo: vivo ed editabile, scala orizzontale/verticale 100%', f'Copertura inchiostro massima: {self.tac_max_found():.0f}% (limite {self.tac_max}%)']
        spots = self._spots()
        if spots:
            L.append('Tinte piatte: ' + ', '.join(spots))
        errs, warns = self._check_print()
        cerr = [e for e in self.check() if e not in errs]
        if not errs and (not cerr):
            L.append('Controlli: superati')
        for e in errs + cerr:
            L.append('ERRORE: ' + e)
        for w in warns + self.warnings:
            L.append('Avviso: ' + w)
        for n in self.notes:
            L.append('Correzione automatica: ' + n)
        for t in self.typo_fixes:
            L.append('Tipografia: ' + t)
        return L

    def _ps_string(self, s):
        data = s.replace('\xa0', ' ').encode('cp1252', 'replace')
        out = []
        for b in data:
            if b in (40, 41, 92):
                out.append('\\' + chr(b))
            elif 32 <= b < 127:
                out.append(chr(b))
            else:
                out.append(f'\\{b:03o}')
        return '(' + ''.join(out) + ')'

    def _print_part(self):
        from fontTools.agl import UV2AGL
        b = self.bleed * MM
        Wb, Hb = (self.W * MM + 2 * b, self.H * MM + 2 * b)
        names = []
        for c in range(256):
            try:
                ch = bytes([c]).decode('cp1252')
                names.append(UV2AGL.get(ord(ch), f'uni{ord(ch):04X}') if c >= 32 else '.notdef')
            except UnicodeDecodeError:
                names.append('.notdef')
        X = lambda x: x * MM
        Y = lambda y: (self.H - y) * MM
        body = []
        bx0, by0, bx1, by1 = self._bleedbox()
        seq = [dict(it, _layer=ln) for ln, items in self.layers.items() if self.layer_props.get(ln, {}).get('print', True) for it in items]

        def setcol(c, tint):
            if isinstance(c, Spot):
                cc = [v / 100 for v in c.alt]
                return f'[/Separation ({c.name}) /DeviceCMYK {{dup {_f(cc[0])} mul exch dup {_f(cc[1])} mul exch dup {_f(cc[2])} mul exch {_f(cc[3])} mul}}] setcolorspace {_f(tint / 100)} setcolor'
            return ' '.join((_f(v) for v in _cmyk(c, tint))) + ' setcmykcolor'
        open_clip = False
        for idx, it in enumerate(seq):
            if it.get('clip') and (not open_clip):
                body.append(f'gsave newpath {X(bx0):.3f} {Y(by0):.3f} moveto {X(bx1):.3f} {Y(by0):.3f} lineto {X(bx1):.3f} {Y(by1):.3f} lineto {X(bx0):.3f} {Y(by1):.3f} lineto closepath clip newpath')
                open_clip = True
            if it['kind'] == 'path':
                p = ['newpath']
                for c in it['cmds']:
                    v = c[1:]
                    xy = [f'{X(v[i]):.3f} {Y(v[i + 1]):.3f}' for i in range(0, len(v), 2)]
                    p.append({'M': '{} moveto', 'L': '{} lineto', 'C': '{} {} {} curveto', 'Z': 'closepath'}[c[0]].format(*xy))
                body.append('gsave ' + ' '.join(p))
                if it['fill'] is not None:
                    op = 'true setoverprint ' if it.get('op_fill') else ''
                    body.append(f"gsave {op}{setcol(it['fill'], it['tint'])} fill grestore")
                if it['stroke'] is not None:
                    op = 'true setoverprint ' if it.get('op_stroke') else ''
                    body.append(f"{op}{setcol(it['stroke'], it['tint'])} {it['stroke_w']:g} setlinewidth stroke")
                body.append('grestore')
            else:
                body.append(f"gsave {setcol(it['color'] or (0, 0, 0, 100), it['tint'])} /{it['font']} findfont {it['size']:g} scalefont setfont")
                for i, para in enumerate(it['paras']):
                    if not para:
                        continue
                    w = self.text_width(para, it['font'], it['size'], it['tracking'])
                    x0 = {'left': it['x'], 'center': it['x'] - w / 2, 'right': it['x'] - w}[it['align']]
                    yy = Y(it['y']) - i * it['lead']
                    ax = it['tracking'] * it['size'] / 1000
                    show = f'{_f(ax)} 0 {self._ps_string(para)} ashow' if ax else f'{self._ps_string(para)} show'
                    body.append(f'{X(x0):.3f} {yy:.3f} moveto {show}')
                body.append('grestore')
            nxt = seq[idx + 1] if idx + 1 < len(seq) else None
            if open_clip and (not (nxt is not None and nxt.get('clip') and (nxt['_layer'] == it['_layer']))):
                body.append('grestore')
                open_clip = False
        hdr = ['%!PS-Adobe-3.1 EPSF-3.0', f'%%Title: ({self.title})', '%%Creator: (Adobe Illustrator\\(R\\) 27.0)', f'%%CreationDate: {datetime.date.today():%d/%m/%y}', f'%%BoundingBox: 0 0 {math.ceil(Wb)} {math.ceil(Hb)}', f'%%HiResBoundingBox: 0 0 {Wb:.4f} {Hb:.4f}', f'%%CropBox: 0 0 {Wb:.4f} {Hb:.4f}', '%%LanguageLevel: 2', '%%DocumentData: Clean7Bit', '%ADOBeginClientInjection: DocumentHeader "AI11EPS"', '%%AI8_CreatorVersion: 27.0.0', '%AI9_PrintingDataBegin', '%ADOEndClientInjection: DocumentHeader "AI11EPS"', '%%Pages: 1']
        if self.used_fonts:
            hdr.append('%%DocumentNeededFonts: ' + self.used_fonts[0])
            hdr += ['%%+ ' + f for f in self.used_fonts[1:]]
        hdr += ['%%DocumentProcessColors: Cyan Magenta Yellow Black', '%%EndComments', '%%BeginProlog', '/EB_Enc [ ' + ' '.join(('/' + n for n in names)) + ' ] def', '/EB_ReEnc { dup findfont dup length dict begin', '  { 1 index /FID ne { def } { pop pop } ifelse } forall', '  /Encoding EB_Enc def currentdict end definefont pop } bind def', '%%EndProlog', '%%BeginSetup']
        hdr += [f'/{f} EB_ReEnc' for f in self.used_fonts]
        hdr += ['%%EndSetup', '%%Page: 1 1', 'save', f'{b:.4f} {b:.4f} translate']
        tail = ['restore', 'showpage', '%%Trailer', '%%EOF', '%AI9_PrintingDataEnd', '', 'userdict /AI9_read_buffer 256 string put', 'userdict begin', '/ai9_skip_data', '{', '\tmark', '\t{', '\t\tcurrentfile AI9_read_buffer { readline } stopped', '\t\t{', '\t\t}', '\t\t{', '\t\t\tnot', '\t\t\t{', '\t\t\t\texit', '\t\t\t} if', '\t\t\t(%AI9_PrivateDataEnd) eq', '\t\t\t{', '\t\t\t\texit', '\t\t\t} if', '\t\t} ifelse', '\t} loop', '\tcleartomark', '} def', 'end', 'userdict /ai9_skip_data get exec']
        return '\r'.join(hdr + body + tail).encode('latin-1') + b'\r'

    def _load_template(self):
        d = open(self.template, 'rb').read()
        if d[:4] == b'\xc5\xd0\xd3\xc6':
            po, pl = struct.unpack('<2I', d[4:12])
            d = d[po:po + pl]
        a = d.find(b'%AI9_PrivateDataBegin')
        i = d.find(b'%AI24_DataStream')
        j = d.find(b'%AI9_PrivateDataEnd', i)
        if a < 0 or i < 0:
            raise SystemExit('Il modello non contiene dati nativi Illustrator (%AI24_DataStream)')
        head = d[a:i + len(b'%AI24_DataStream')]
        lines = [l for l in d[i + len(b'%AI24_DataStream'):j].replace(b'\n', b'\r').split(b'\r') if l.strip()]
        import zstandard
        raw = _a85_decode_block(lines)
        native = zstandard.ZstdDecompressor().decompressobj().decompress(raw)
        return (head, native)

    def _geometry(self):
        Wp, Hp, bp = (self.W * MM, self.H * MM, self.bleed * MM)
        tbx = math.ceil(Wp / 2) + 0.5
        tby_top = math.ceil(Hp / 2) + 0.5
        ro = (8191.5 - tbx, 8191.5 - tby_top)
        return (Wp, Hp, bp, tbx, Hp - tby_top, ro)

    def _page(self, x, y):
        return (x * MM, (self.H - y) * MM)

    @staticmethod
    def _closed_points(sp):
        if sp[-1][0] != 'Z':
            return (sp, False)
        body = [c for c in sp if c[0] != 'Z']
        first, last = (body[0][1:3], body[-1][-2:])
        if abs(first[0] - last[0]) > 1e-06 or abs(first[1] - last[1]) > 1e-06:
            body.append(('L',) + tuple(first))
        return (body, True)

    def _paint_ops(self, color, tint, stroke):
        if isinstance(color, Spot):
            c = ' '.join((_f(v / 100) for v in color.alt))
            return f"{c} {tedoc_ps(color.name)} {_f(1 - tint / 100)} {('X' if stroke else 'x')}"
        return ' '.join((_f(v) for v in _cmyk(color, tint))) + (' K' if stroke else ' k')

    def _objects_native(self):
        per_layer, texts = ({}, [])
        for lname, items in self.layers.items():
            out = []
            open_clip = False
            for idx, it in enumerate(items):
                if it.get('clip') and (not open_clip):
                    out.append('q')
                    open_clip = True
                if it['kind'] == 'path':
                    fill, stroke = (it['fill'], it['stroke'])
                    if fill is not None:
                        out += ['4 As', '1 O' if it.get('op_fill') else '0 O', self._paint_ops(fill, it['tint'], False)]
                        if stroke is not None:
                            out += ['1 R' if it.get('op_stroke') else '0 R', self._paint_ops(stroke, it['tint'], True), f"{_f(it['stroke_w'])} w"]
                    else:
                        out += ['2 As', '0 D', '1 R' if it.get('op_stroke') else '0 R', self._paint_ops(stroke, it['tint'], True), f"{_f(it['stroke_w'])} w"]
                    if fill is not None and stroke is not None:
                        pc, po = ('b', 'B')
                    elif fill is not None:
                        pc, po = ('f', 'F')
                    else:
                        pc, po = ('s', 'S')
                    subs, cur = ([], [])
                    for c in it['cmds']:
                        if c[0] == 'M' and cur:
                            subs.append(cur)
                            cur = []
                        cur.append(c)
                    subs.append(cur)
                    if len(subs) > 1:
                        out.append('*u')
                    for sp in subs:
                        pts_cmds, closed = self._closed_points(sp)
                        for c in pts_cmds:
                            v = c[1:]
                            pts = [self._page(v[i], v[i + 1]) for i in range(0, len(v), 2)]
                            s_ = ' '.join((f'{_f(px)} {_f(py)}' for px, py in pts))
                            out.append(s_ + {'M': ' m', 'L': ' L', 'C': ' C'}[c[0]])
                        out.append(pc if closed else po)
                    if len(subs) > 1:
                        out.append('*U')
                else:
                    ti = len(texts)
                    if out:
                        out += ['0 0 Xd', '6 () XW']
                    out += ['/AI11Text :', '0 /FreeUndo ,', '0 /FrameIndex ,', f'{ti} /StoryIndex ,', '2 /TextAntialiasing ,', ';', '']
                    texts.append(it)
                nxt = items[idx + 1] if idx + 1 < len(items) else None
                if open_clip and (not (nxt is not None and nxt.get('clip'))):
                    out += self._clip_path_native()
                    open_clip = False
            per_layer[lname] = out
        return (per_layer, texts)

    def _clip_path_native(self):
        bx0, by0, bx1, by1 = self._bleedbox()
        pts = [(bx0, by0), (bx1, by0), (bx1, by1), (bx0, by1), (bx0, by0)]
        out = ['4 As', '0 1 0 0 0 Xy']
        for i, (x, y) in enumerate(pts):
            px, py = self._page(x, y)
            out.append(f'{_f(px)} {_f(py)} ' + ('m' if i == 0 else 'L'))
        return out + ['h', 'W', 'n', 'Q']

    def _build_textdoc(self, tpl_bytes, texts, ro):
        import copy
        v = tedoc.parse(tpl_bytes)
        top = {v[i]: v[i + 1] for i in range(0, len(v), 2)}
        res, doc = (top['0'], top['1'])
        tpl_frames, tpl_stories = (res['8']['0'], doc['1'])
        single, multi = ({}, None)
        for i, st in enumerate(tpl_stories):
            pr = st['0']['5']['0']
            j = pr[0]['0']['0']['5'].get('0', 0)
            if len(pr) == 1:
                single.setdefault(j, i)
            elif multi is None:
                multi = i
        if 0 not in single:
            raise SystemExit('Il modello non contiene un testo a punto allineato a sinistra')
        fontlist = res['1']['0']
        fidx = {}
        for i, fe in enumerate(fontlist):
            fidx.setdefault(tedoc.utext(fe['0']['0']['0']), i)
        for f in self.used_fonts:
            if f not in fidx:
                fe = copy.deepcopy(fontlist[0])
                fe['0'].set('97', S(str(uuid.uuid4()).encode()))
                fe['0']['0'].set('0', ustr(f))
                fontlist.append(fe)
                fidx[f] = len(fontlist) - 1
        frames, stories = ([], [])
        for k, it in enumerate(texts):
            just = {'left': 0, 'right': 1, 'center': 2}[it['align']]
            if len(it['paras']) > 1 and multi is not None:
                base = multi
            else:
                base = single.get(just, single[0])
            P = [float(ro[0] + it['x'] * MM), float(ro[1] + it['y'] * MM)]
            fr = copy.deepcopy(tpl_frames[base])
            fr['0'].set('97', S(str(uuid.uuid4()).encode()))
            fr['0'].set('0', P)
            frames.append(fr)
            nxt = _lines_of(tpl_stories[multi])[1] if multi is not None else None
            par = tpl_stories[multi]['1']['1']['0'][0] if multi is not None else None
            stories.append(self._story_clone(it, k, P, fidx[it['font']], tpl_stories[base], nxt, par))
        res['8'].set('0', frames)
        doc.set('1', stories)
        return b''.join((b' /' + k.encode() + b' ' + tedoc.dump(top[k]) for k in top))

    def _story_clone(self, it, frame_idx, P, font_index, T, T_next, T_par):
        import copy
        st = copy.deepcopy(T)
        m = self.metrics(it['font'])
        size, lead = (float(it['size']), float(it['lead']))
        just = {'left': 0, 'right': 1, 'center': 2}[it['align']]
        paras = it['paras']
        full = '\r'.join(paras) + '\r'
        s0 = st['0']
        s0.set('0', ustr(full))
        prun0 = s0['5']['0'][0]
        pruns = []
        for p in paras:
            r = copy.deepcopy(prun0)
            r['0']['0'].set('97', S(str(uuid.uuid4()).encode()))
            ps = r['0']['0']['5']
            if just:
                ps.set('0', just)
            else:
                ps.remove_key('0')
            r.set('1', len(p) + 1)
            pruns.append(r)
        s0['5'].set('0', pruns)
        crun = copy.deepcopy(s0['6']['0'][0])
        crun['0']['0'].set('97', S(str(uuid.uuid4()).encode()))
        cs = D([(N('0'), font_index), (N('1'), size), (N('4'), False), (N('5'), lead), (N('6'), 1.0), (N('7'), 1.0), (N('8'), int(it['tracking']))])
        if it['color'] is not None:
            c, mg, y, k = _cmyk(it['color'], it['tint'])
            cs.append((N('53'), D([(N('99'), N('SimplePaint')), (N('0'), D([(N('0'), 2), (N('1'), [1.0, float(c), float(mg), float(y), float(k)])]))])))
        cs.append((N('89'), size))
        crun['0']['0'].set('6', cs)
        crun.set('1', len(full))
        s0['6'].set('0', [crun])
        rend = st['1']
        rend['0'][0].set('0', frame_idx)
        F = rend['2'][0]['6'][0]
        F.set('10', frame_idx)
        F['0'].set('0', list(P))
        R2 = F['6'][0]['6'][0]
        L0 = R2['6'][0]
        asc = -float(m.asc * size / m.upm)
        desc = float(-m.desc * size / m.upm)
        lines, start = ([], 0)
        for i, p in enumerate(paras):
            n = len(p) + 1
            w = float(m.width(p, size) + it['tracking'] * size / 1000 * len(p))
            xo = {0: 0.0, 1: -w, 2: -w / 2}[just]
            yo = float(i * lead)
            L = copy.deepcopy(L0 if i == 0 or T_next is None else T_next)
            if i:
                L.set('10', yo)
                L.set('0', D([(N('0'), [0.0, yo])]))
            L.set('14', asc)
            L.set('15', desc)
            L.set('1', [0.0, asc, 0.0, desc])
            Sg = L['6'][0]
            Sg['15'].set('0', n)
            Sg.set('20', start)
            G = Sg['6'][0]
            G.set('1', [0.0, asc, w, desc])
            G.set('5', m.gids(p) + [3])
            bb = [P[0] + xo, P[1] + yo + asc, P[0] + xo + w, P[1] + yo + desc]
            G.set('8', bb)
            G.set('9', list(bb))
            G['10'].set('1', [n])
            G.set('12', asc)
            G.set('13', desc)
            lines.append(L)
            start += n
        R2.set('6', lines)
        if len(paras) > 1 and T_par is not None:
            items = []
            for p in paras:
                x = copy.deepcopy(T_par)
                x.set('1', len(p) + 1)
                items.append(x)
            rend.set('1', D([(N('0'), items)]))
            rend.sort(key=lambda kv: int(kv[0]))
        elif rend.has('1'):
            rend.remove_key('1')
        return st

    def _native(self, native):
        Wp, Hp, bp, tbx, tby, ro = self._geometry()
        layer_objs, texts = self._objects_native()
        nat = native

        def sub(pat, rep, count=1, required=True):
            nonlocal nat
            new, n = re.subn(pat, rep, nat, count=count)
            if required and n == 0:
                raise SystemExit(f'Modello: sezione non trovata ({pat[:40]!r})')
            nat = new
        bb = (-bp, -bp, Wp + bp, Hp + bp)
        sub(b'%%BoundingBox:[^\\r]*', f'%%BoundingBox: {math.floor(bb[0])} {math.floor(bb[1])} {math.ceil(bb[2])} {math.ceil(bb[3])}'.encode())
        sub(b'%%HiResBoundingBox:[^\\r]*', f'%%HiResBoundingBox: {bb[0]:.4f} {bb[1]:.4f} {bb[2]:.4f} {bb[3]:.4f}'.encode())
        fonts = self.used_fonts or ['MyriadPro-Regular']
        fl = (fonts[0] + '\r' + ''.join((f'%%+ {f}\r' for f in fonts[1:]))).encode()
        sub(b'%%DocumentFonts:[^\\r]*\\r(?:%%\\+[^\\r]*\\r)*', b'%%DocumentFonts: ' + fl)
        sub(b'%%DocumentNeededFonts:[^\\r]*\\r(?:%%\\+[^\\r]*\\r)*', b'%%DocumentNeededFonts: ' + fl, required=False)
        sub(b'%%DocumentSuppliedFonts:[^\\r]*\\r(?:%%\\+[^\\r]*\\r)*', b'', required=False)
        spots = self._spots()
        m = re.search(b'%%DocumentCustomColors:[^\\r]*\\r(?:%%\\+[^\\r]*\\r)*%%CMYKCustomColor:[^\\r]*\\r(?:%%\\+[^\\r]*\\r)*', nat)
        if m:
            old = m.group(0).decode('latin-1')
            cmyks = re.findall('((?:[-\\d.e]+ ){4})\\(([^)]*)\\)', old.split('%%CMYKCustomColor:')[1])
            allc = {n: c.strip() for c, n in cmyks}
            for n, alt in spots.items():
                allc.setdefault(n, ' '.join((_f(v / 100) for v in alt)))
            ks = list(allc)
            new = '%%DocumentCustomColors: ' + tedoc_ps(ks[0]) + '\r' + ''.join((f'%%+ {tedoc_ps(n)}\r' for n in ks[1:]))
            new += '%%CMYKCustomColor: ' + f'{allc[ks[0]]} {tedoc_ps(ks[0])}\r' + ''.join((f'%%+ {allc[n]} {tedoc_ps(n)}\r' for n in ks[1:]))
            nat = nat[:m.start()] + new.encode('latin-1') + nat[m.end():]
            pal = nat.find(b'%AI5_BeginPalette')
            pb = nat.find(b'\rPB\r', pal)
            add = b''
            for n, alt in spots.items():
                if f'({n}) 0 x'.encode() not in nat[pal:pb]:
                    add += f"\r{' '.join((_f(v / 100) for v in alt))} {tedoc_ps(n)} 0 x\r{tedoc_ps(n)}\rPc".encode('latin-1')
            nat = nat[:pb] + add + nat[pb:]
        elif spots:
            raise SystemExit('Il modello non contiene tinte piatte: impossibile aggiungere Pantone')
        nl = max(1, len(self.layers))
        sub(b'%AI5_NumLayers:[^\\r]*', f'%AI5_NumLayers: {nl}'.encode())
        sub(b'%AI5_OpenViewLayers:[^\\r]*', ('%AI5_OpenViewLayers: ' + '7' * nl).encode(), required=False)
        sub(b'%AI3_Cropmarks:[^\\r]*', f'%AI3_Cropmarks: 0 0 {Wp:.4f} {Hp:.4f}'.encode())
        sub(b'%AI3_TemplateBox:[^\\r]*', f'%AI3_TemplateBox: {tbx} {tby:.4f} {tbx} {tby:.4f}'.encode())
        sub(b'%AI3_TileBox:[^\\r]*', f'%AI3_TileBox: 0 0 {Wp:.4f} {Hp:.4f}'.encode(), required=False)
        sub(b'%%PageOrigin:[^\\r]*', b'%%PageOrigin:0 0', required=False)
        for tag in (b'%AI10_OpenToVie: ', b'%AI9_OpenToView: '):
            sub(re.escape(tag) + b'[-\\d.e]+ [-\\d.e]+ ', tag + f'-40 {Hp + 40:.4f} '.encode(), required=False)
        sub(b'%_[-\\d.e]+ [-\\d.e]+ /RealPoint\\r%_ \\(RulerOrigin\\)', f'%_{ro[0]:g} {ro[1]:g} /RealPoint\r%_ (RulerOrigin)'.encode())
        sub(b'%_[-\\d.e]+ [-\\d.e]+ /RealPointRelToROrigin\\r%_ \\(PositionPoint2\\)', f'%_{Wp:.4f} 0 /RealPointRelToROrigin\r%_ (PositionPoint2)'.encode())
        sub(b'%_[-\\d.e]+ [-\\d.e]+ /RealPointRelToROrigin\\r%_ \\(PositionPoint1\\)', f'%_0 {Hp:.4f} /RealPointRelToROrigin\r%_ (PositionPoint1)'.encode())
        for side in (b'Left', b'Right', b'Top', b'Bottom'):
            sub(b'%_[-\\d.e]+ /Real \\(Bleed' + side + b'Value\\)', f'%_{bp:.4f} /Real (Bleed'.encode() + side + b'Value)', required=False)
        sub(b'\\r[-\\d.e]+ [-\\d.e]+ /RulerOrigin ,', f'\r{ro[0]:g} {ro[1] + Hp:.4f} /RulerOrigin ,'.encode())
        a = nat.find(b'/AI11TextDocument : /ASCII85Decode ,\r')
        if a < 0:
            raise SystemExit('Modello: AI11TextDocument non trovato')
        s0 = a + len(b'/AI11TextDocument : /ASCII85Decode ,\r')
        e0 = nat.find(b'~>', s0) + 2
        tpl_td = _a85_decode_block(nat[s0:e0].split(b'\r'))
        td = self._build_textdoc(tpl_td, texts, ro)
        nat = nat[:s0] + b'\r'.join(_a85_lines(td)) + nat[e0:]
        l0 = nat.find(b'%AI5_BeginLayer')
        h_end = nat.find(b'\r0 Xw\r', l0) + len(b'\r0 Xw\r')
        l1 = nat.rfind(b'%AI5_EndLayer--\r') + len(b'%AI5_EndLayer--\r')
        if l0 < 0 or h_end < len(b'\r0 Xw\r') or nat.count(b'%AI5_BeginLayer') != 1:
            raise SystemExit('Il modello deve avere un solo livello')
        tpl_hdr = nat[l0:h_end].decode('latin-1')
        tpl_name = re.search('\\r\\(([^\\r]*)\\) Ln\\r', tpl_hdr).group(1)
        colors = [(79, 128, 255), (255, 79, 79), (79, 255, 79), (255, 255, 79), (255, 79, 255), (79, 255, 255), (128, 128, 128)]
        blocks = []
        names = list(self.layers) or ['Livello 1']
        for i, name in enumerate(names):
            h = tpl_hdr.replace(f'({tpl_name}) Ln', f'{tedoc_ps(name)} Ln')
            uid = re.sub('[^A-Za-z0-9_]', '_', name) or f'Livello_{i + 1}'
            h = re.sub('/XMLUID : \\([^)]*\\)', f'/XMLUID : ({uid})', h)
            pr = 1 if self.layer_props.get(name, {}).get('print', True) else 0
            r, g, b = colors[i % len(colors)]
            h = re.sub('\\r1 1 1 1 0 0 1 0 \\d+ \\d+ \\d+ ', f'\r1 1 1 {pr} 0 0 1 0 {r} {g} {b} ', h)
            body = '\r'.join(layer_objs.get(name, []))
            blocks.append(h + (body + '\r' if body else '') + 'LB\r%AI5_EndLayer--\r')
        nat = nat[:l0] + ''.join(blocks).encode('latin-1') + nat[l1:]
        return nat

    def _spots(self):
        sp = {}
        for items in self.layers.values():
            for it in items:
                for k in ('fill', 'stroke', 'color'):
                    c = it.get(k)
                    if isinstance(c, Spot):
                        sp[c.name] = c.alt
        return sp

    def save(self, path, strict=True):
        errs = self.check()
        if errs and strict:
            raise SystemExit('EPS NON salvato, correggere:\n  - ' + '\n  - '.join(errs))
        import zstandard
        head, native = self._load_template()
        nat = self._native(native)
        params = zstandard.ZstdCompressionParameters.from_level(19, window_log=21, write_content_size=False, write_checksum=False)
        comp = zstandard.ZstdCompressor(compression_params=params).compress(nat)
        head = re.sub(b'%%Title: \\([^\\r]*\\)', f'%%Title: ({os.path.basename(path)})'.encode(), head)
        priv = head + b'\r' + b'\r'.join(_a85_lines(comp)) + b'\r%AI9_PrivateDataEnd\r'
        with open(path, 'wb') as fh:
            fh.write(self._print_part())
            fh.write(priv)
        return errs + self.warnings

    def _fontmap(self, tmp):
        fm = []
        for f in self.used_fonts:
            info = self.fonts.get(f) or {}
            if info.get('file'):
                fm.append(f"/{f} ({os.path.join(self.fonts_dir, info['file'])}) ;")
            else:
                fb = 'NimbusSans-Bold' if info.get('weight', 400) >= 600 else 'NimbusSans-Regular'
                fm.append(f'/{f} /{fb} ;')
        p = os.path.join(tmp, 'Fontmap.GS')
        open(p, 'w').write('\n'.join(fm) + '\n')
        return p

    def preview(self, eps_path, png_path, dpi=150, guides=True):
        from PIL import Image, ImageDraw
        tmp = tempfile.mkdtemp()
        try:
            fm = self._fontmap(tmp)
            subprocess.run(['gs', '-q', '-dNOPAUSE', '-dBATCH', '-dNOSAFER', '-dEPSCrop', '-sDEVICE=png16m', f'-r{dpi}', '-dTextAlphaBits=4', '-dGraphicsAlphaBits=4', f'-sFONTMAP={fm}', '-dNOPLATFONTS', f'-sOutputFile={png_path}', eps_path], check=True)
            im = Image.open(png_path).convert('RGB')
            px = dpi / 25.4
            bb = self.bleed * px
            if guides:
                d = ImageDraw.Draw(im)
                d.rectangle([bb, bb, bb + self.W * px, bb + self.H * px], outline=(255, 0, 0), width=2)
                sv = (self.bleed + self.safe) * px
                d.rectangle([sv, sv, bb + (self.W - self.safe) * px, bb + (self.H - self.safe) * px], outline=(0, 170, 255), width=1)
            else:
                im = im.crop((round(bb), round(bb), round(bb + self.W * px), round(bb + self.H * px)))
            im.save(png_path)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return png_path

def tedoc_ps(s):
    return '(' + s.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)') + ')'

def _lines_of(story):
    return story['1']['2'][0]['6'][0]['6'][0]['6'][0]['6']

def _is_black(c):
    if c is None or isinstance(c, Spot):
        return False
    cc, mm, yy, kk = c
    return kk >= 90 and max(cc, mm, yy) <= 10
_ACCENTI = [('\\bperchè\\b', 'perché'), ('\\bPerchè\\b', 'Perché'), ('\\bpoichè\\b', 'poiché'), ('\\baffinchè\\b', 'affinché'), ('\\bbenchè\\b', 'benché'), ('\\bfinchè\\b', 'finché'), ('\\bsicchè\\b', 'sicché'), ('\\bnè\\b', 'né'), ('\\bNè\\b', 'Né'), ('\\bsè\\b', 'sé'), ('\\bpò\\b', 'po’'), ("\\bpo'", 'po’'), ("\\bqual'è\\b", 'qual è'), ("\\bQual'è\\b", 'Qual è'), ('(^|[\\s“\\"(])E\' ', '\\1È '), ('\\bventitrè\\b', 'ventitré'), ('\\btrentatrè\\b', 'trentatré')]

def tipografia_it(text):
    import re as _re
    fixes, t = ([], text)
    for pat, rep in _ACCENTI:
        m = _re.search(pat, t)
        if m:
            fixes.append(f'«{m.group(0).strip()}» → «{m.expand(rep).strip()}»')
            t = _re.sub(pat, rep, t)
    if '...' in t:
        t = t.replace('...', '…')
        fixes.append('tre punti → …')
    new = _re.sub('(?<=\\S) - (?=\\S)', ' – ', t)
    if new != t:
        t = new
        fixes.append('trattino tra spazi → lineetta –')
    new = _re.sub("(?<=\\w)'(?=\\w)", '’', t)
    if new != t:
        t = new
        fixes.append('apostrofo dritto → ’')
    if '"' in t:
        out, open_q = ([], True)
        for ch in t:
            if ch == '"':
                out.append('“' if open_q else '”')
                open_q = not open_q
            else:
                out.append(ch)
        t = ''.join(out)
        fixes.append('virgolette dritte → “ ”')
    new = _re.sub("(?<=\\s)'(?=\\w)", '‘', t)
    new = _re.sub("(?<=\\w)'(?=[\\s.,;:!?)]|$)", '’', new)
    if new != t:
        t = new
        fixes.append('virgolette semplici → ‘ ’')
    new = _re.sub('[ \\t]{2,}', ' ', t)
    if new != t:
        t = new
        fixes.append('spazi doppi eliminati')
    new = _re.sub(' +([,.;:!?])', '\\1', t)
    if new != t:
        t = new
        fixes.append('spazio prima della punteggiatura eliminato')
    return (t, fixes)

def nome_file(progetto, w_mm, h_mm, versione=1, data=None):
    import re as _re, unicodedata
    p = unicodedata.normalize('NFKD', progetto).encode('ascii', 'ignore').decode()
    p = _re.sub('[^A-Za-z0-9]+', '_', p).strip('_') or 'Progetto'
    data = data or datetime.date.today().isoformat()
    return f'{p}_{w_mm:g}x{h_mm:g}mm_v{versione}_{data}'
