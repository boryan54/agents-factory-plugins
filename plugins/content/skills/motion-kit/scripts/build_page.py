"""Собирает index.html и data.js из timeline.json: python build_page.py <папка ролика>
Запускать после каждой правки timeline.json (тексты, субтитры, QR, длительность).
QR строится библиотекой qrcode (pip install qrcode), если в timeline задан qr_url."""
import json, pathlib, sys

PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
{webfonts}<style>html,body{{margin:0;background:#E9EEF6;display:flex;justify-content:center}}canvas{{height:100vh;width:auto;max-width:100vw}}</style>
</head><body><canvas id="c" width="{w}" height="{h}"></canvas>
<script src="data.js"></script><script src="engine.js"></script><script src="scene.js"></script></body></html>
"""
GF = '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=block">\n'

def build(root):
    root = pathlib.Path(root)
    tl = json.loads((root / "timeline.json").read_text(encoding="utf-8"))
    qr = None
    if tl.get("qr_url"):
        try:
            import qrcode
        except ImportError:
            sys.exit("нужен qrcode: pip install qrcode")
        q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=2)
        q.add_data(tl["qr_url"]); q.make(fit=True)
        qr = [[1 if v else 0 for v in row] for row in q.get_matrix()]
    (root / "data.js").write_text("window.TL=" + json.dumps(tl, ensure_ascii=False) + ";\nwindow.QR=" + json.dumps(qr) + ";\n", encoding="utf-8")
    (root / "index.html").write_text(PAGE.format(title=tl.get("title", "motion"), w=tl.get("w", 1080), h=tl.get("h", 1920),
                                                 webfonts=GF if tl.get("webfonts", True) else ""), encoding="utf-8")
    print(f"  index.html + data.js ({tl.get('w', 1080)}×{tl.get('h', 1920)}, {tl.get('dur')} с" + (f", QR {len(qr)}×{len(qr)}" if qr else "") + ")")

if __name__ == "__main__":
    if len(sys.argv) < 2: sys.exit("использование: python build_page.py <папка ролика>")
    build(sys.argv[1])
