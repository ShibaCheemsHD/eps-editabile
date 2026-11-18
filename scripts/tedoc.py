import re

class N(str):
    pass

class D(list):

    def get(self, k, default=None):
        for kk, v in self:
            if kk == k:
                return v
        return default

    def __getitem__(self, k):
        if isinstance(k, (int, slice)):
            return list.__getitem__(self, k)
        for kk, v in self:
            if kk == k:
                return v
        raise KeyError(k)

    def set(self, k, v):
        for i, (kk, _) in enumerate(self):
            if kk == k:
                list.__setitem__(self, i, (N(k), v))
                return
        self.append((N(k), v))

    def has(self, k):
        return any((kk == k for kk, _ in self))

    def remove_key(self, k):
        for i, (kk, _) in enumerate(self):
            if kk == k:
                del self[i]
                return

class S(bytes):
    pass

def ustr(text):
    return S(b'\xfe\xff' + text.encode('utf-16-be'))

def utext(s):
    if s.startswith(b'\xfe\xff'):
        return s[2:].decode('utf-16-be', 'replace')
    return s.decode('latin-1')
_DELIM = b' \t\r\n\x0c\x00'

def parse(data):
    pos = [0]
    n = len(data)

    def skip():
        while pos[0] < n and data[pos[0]] in _DELIM:
            pos[0] += 1

    def value():
        skip()
        c = data[pos[0]:pos[0] + 2]
        if c == b'<<':
            pos[0] += 2
            d = D()
            while True:
                skip()
                if data[pos[0]:pos[0] + 2] == b'>>':
                    pos[0] += 2
                    return d
                k = value()
                v = value()
                d.append((k, v))
        if c[:1] == b'[':
            pos[0] += 1
            arr = []
            while True:
                skip()
                if data[pos[0]:pos[0] + 1] == b']':
                    pos[0] += 1
                    return arr
                arr.append(value())
        if c[:1] == b'(':
            pos[0] += 1
            out = bytearray()
            depth = 0
            while True:
                ch = data[pos[0]]
                if ch == 92:
                    nx = data[pos[0] + 1]
                    out.append(nx)
                    pos[0] += 2
                    continue
                if ch == 40:
                    depth += 1
                elif ch == 41:
                    if depth == 0:
                        pos[0] += 1
                        return S(bytes(out))
                    depth -= 1
                out.append(ch)
                pos[0] += 1
        if c[:1] == b'/':
            pos[0] += 1
            st = pos[0]
            while pos[0] < n and data[pos[0]] not in _DELIM and (data[pos[0]:pos[0] + 1] not in b'/[]()<>'):
                pos[0] += 1
            return N(data[st:pos[0]].decode('latin-1'))
        st = pos[0]
        while pos[0] < n and data[pos[0]] not in _DELIM and (data[pos[0]:pos[0] + 1] not in b'/[]()<>'):
            pos[0] += 1
        tok = data[st:pos[0]].decode('latin-1')
        if tok == 'true':
            return True
        if tok == 'false':
            return False
        if re.fullmatch('-?\\d+', tok):
            return int(tok)
        return float(tok)
    vals = []
    while True:
        skip()
        if pos[0] >= n:
            break
        vals.append(value())
    return vals

def _num(v):
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, int):
        return str(v)
    s = f'{v:.5f}'.rstrip('0')
    if s.endswith('.'):
        s += '0'
    if s in ('-0.0',):
        s = '0.0'
    s = s if '.' in s else s + '.0'
    if s.startswith('0.') and s != '0.0':
        s = s[1:]
    elif s.startswith('-0.') and s != '-0.0':
        s = '-' + s[2:]
    return s

def dump(v, out=None):
    top = out is None
    if top:
        out = bytearray()
    if isinstance(v, D):
        out += b'<< '
        for k, x in v:
            out += b'/' + k.encode('latin-1') + b' '
            dump(x, out)
            out += b' '
        out += b'>>'
    elif isinstance(v, list):
        out += b'[ '
        for x in v:
            dump(x, out)
            out += b' '
        out += b']'
    elif isinstance(v, N):
        out += b'/' + v.encode('latin-1')
    elif isinstance(v, S):
        esc = bytearray()
        for b in v:
            if b in (40, 41, 92):
                esc.append(92)
            esc.append(b)
        out += b'(' + bytes(esc) + b')'
    elif isinstance(v, (bool, int, float)):
        out += _num(v).encode()
    else:
        raise TypeError(type(v))
    return bytes(out) if top else None
