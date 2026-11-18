import json, os, re, sys, urllib.request, urllib.parse
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer
RAW = 'https://raw.githubusercontent.com/google/fonts/main/'
WEIGHT_NAMES = {100: 'Thin', 200: 'ExtraLight', 300: 'Light', 400: 'Regular', 500: 'Medium', 600: 'SemiBold', 700: 'Bold', 800: 'ExtraBold', 900: 'Black'}

def _get(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()

def find_family(family):
    slug = re.sub('[^a-z0-9]', '', family.lower())
    for lic in ('ofl', 'apache', 'ufl'):
        try:
            meta = _get(f'{RAW}{lic}/{slug}/METADATA.pb').decode('utf-8')
            return (lic, slug, meta)
        except Exception:
            continue
    return (None, slug, None)

def parse_meta(meta):
    fonts = []
    for block in re.findall('fonts \\{(.*?)\\n\\}', meta, re.S):
        d = dict(re.findall('(\\w+): "?([^"\\n]*)"?', block))
        fonts.append(d)
    lic = re.search('license: "(\\w+)"', meta)
    return (fonts, lic.group(1) if lic else '?')

def style_name(weight, italic):
    w = WEIGHT_NAMES.get(weight, str(weight))
    if italic:
        return 'Italic' if w == 'Regular' else w + 'Italic'
    return w

def set_names(font, family, weight, italic):
    sub = style_name(weight, italic)
    ps = f"{family.replace(' ', '')}-{sub}"
    full = f'{family} {sub}' if sub != 'Regular' else f'{family} Regular'
    name = font['name']
    legacy_fam = family if weight in (400, 700) else f'{family} {WEIGHT_NAMES.get(weight, weight)}'
    legacy_sub = ('Bold ' if weight == 700 else '') + ('Italic' if italic else '')
    legacy_sub = legacy_sub.strip() or 'Regular'
    for rec in list(name.names):
        if rec.nameID in (1, 2, 3, 4, 6, 16, 17, 25):
            name.removeNames(nameID=rec.nameID)
    for pid, eid, lid in ((3, 1, 1033), (1, 0, 0)):
        name.setName(legacy_fam, 1, pid, eid, lid)
        name.setName(legacy_sub, 2, pid, eid, lid)
        name.setName(f'{ps};static', 3, pid, eid, lid)
        name.setName(full, 4, pid, eid, lid)
        name.setName(ps, 6, pid, eid, lid)
        name.setName(family, 16, pid, eid, lid)
        name.setName(sub, 17, pid, eid, lid)
    if 'OS/2' in font:
        font['OS/2'].usWeightClass = weight
        sel = font['OS/2'].fsSelection & ~97
        if italic:
            sel |= 1
        if weight == 700:
            sel |= 32
        if not italic and weight != 700:
            sel |= 64
        font['OS/2'].fsSelection = sel
    if 'head' in font:
        mac = 0
        if weight == 700:
            mac |= 1
        if italic:
            mac |= 2
        font['head'].macStyle = mac
    return ps

def get_font(family, weight, italic, out_dir, cache):
    lic_dir, slug, meta = cache.get(family) or find_family(family)
    cache[family] = (lic_dir, slug, meta)
    if not meta:
        return None
    fonts, lic = parse_meta(meta)
    want_style = 'italic' if italic else 'normal'
    cands = [f for f in fonts if f.get('style') == want_style]
    if not cands:
        raise SystemExit(f'{family}: stile {want_style} non disponibile')
    exact = [f for f in cands if '[' not in f['filename'] and int(f.get('weight', 0)) == weight]
    if exact:
        fn = exact[0]['filename']
    else:
        var = [f for f in cands if '[' in f['filename']]
        if not var:
            have = sorted({int(f['weight']) for f in cands})
            raise SystemExit(f'{family}: peso {weight} non disponibile (pesi: {have})')
        fn = var[0]['filename']
    local_src = os.path.join(out_dir, '.src', fn)
    os.makedirs(os.path.dirname(local_src), exist_ok=True)
    if not os.path.exists(local_src):
        with open(local_src, 'wb') as fh:
            fh.write(_get(RAW + f'{lic_dir}/{slug}/' + urllib.parse.quote(fn)))
    font = TTFont(local_src)
    if 'fvar' in font:
        loc = {}
        for ax in font['fvar'].axes:
            if ax.axisTag == 'wght':
                loc['wght'] = max(ax.minValue, min(ax.maxValue, weight))
            elif ax.axisTag == 'ital':
                loc['ital'] = 1 if italic else 0
            else:
                loc[ax.axisTag] = ax.defaultValue
        font = instancer.instantiateVariableFont(font, loc)
    ps = set_names(font, family, weight, italic)
    out = os.path.join(out_dir, ps + '.ttf')
    font.save(out)
    return (ps, {'file': os.path.basename(out), 'family': family, 'weight': weight, 'italic': italic, 'license': lic})

def main():
    out_dir = sys.argv[1]
    os.makedirs(out_dir, exist_ok=True)
    manifest_path = os.path.join(out_dir, 'fonts.json')
    manifest = json.load(open(manifest_path)) if os.path.exists(manifest_path) else {}
    cache = {}
    for spec in sys.argv[2:]:
        family, _, ws = spec.partition(':')
        for w in (ws or '400').split(','):
            italic = w.endswith('i')
            weight = int(w.rstrip('i'))
            res = get_font(family.strip(), weight, italic, out_dir, cache)
            if res is None:
                ps = f"{family.strip().replace(' ', '')}-{style_name(weight, italic)}"
                manifest[ps] = {'file': None, 'family': family.strip(), 'weight': weight, 'italic': italic, 'license': 'licensed'}
                print(f"[a licenza] {family} {w}: non su Google Fonts, si assume installato → uso il nome '{ps}' (verificare il nome PostScript esatto)")
            else:
                ps, info = res
                manifest[ps] = info
                print(f"[ok] {ps}  ({info['license']})")
    json.dump(manifest, open(manifest_path, 'w'), indent=2, ensure_ascii=False)
if __name__ == '__main__':
    main()
