import os, zipfile

def package(zip_path, doc, eps_path, previews=(), extra_notes=()):
    fonts = doc.fonts
    free = [f for f in doc.used_fonts if (fonts.get(f) or {}).get('file')]
    lic = [f for f in doc.used_fonts if f not in free]
    base = os.path.splitext(os.path.basename(eps_path))[0]
    L = [base, '=' * len(base), '', f'{os.path.basename(eps_path)}  →  file da aprire e modificare in Illustrator']
    L += ['', 'CONTROLLI', '---------'] + doc.report() + ['']
    if free:
        L += ["FONT INCLUSI (cartella font/) — installali PRIMA di aprire l'EPS se non li hai già", '(doppio clic sul file › Installa):']
        for f in free:
            info = fonts[f]
            L.append(f"  - {f}  ({info['family']}, licenza {info['license']}, uso libero anche commerciale)")
        L.append('')
    if lic:
        L += ['FONT A LICENZA (non inclusi, si presume siano già installati):'] + [f'  - {f}' for f in lic] + ['']
    L += list(extra_notes)
    L += ['Le anteprime PNG sono solo di controllo: in quella con le guide il rosso è il taglio,', "l'azzurro l'area di sicurezza."]
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.write(eps_path, os.path.basename(eps_path))
        for p in previews:
            z.write(p, os.path.basename(p))
        for f in free:
            z.write(os.path.join(doc.fonts_dir, fonts[f]['file']), 'font/' + fonts[f]['file'])
        z.writestr('LEGGIMI.txt', '\n'.join(L) + '\n')
    return zip_path
