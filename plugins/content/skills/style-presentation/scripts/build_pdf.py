# -*- coding: utf-8 -*-
"""
build_pdf.py — собирает презентацию в PDF из готовых картинок слайдов (slide_NN.png).
Каждый слайд = страница PDF (изображение на всю страницу, без полей). LibreOffice не нужен.

Запуск:  python build_pdf.py path/to/project.json
Требует: Pillow  (pip install Pillow) — обычно уже стоит.
"""
import os
import sys
import json

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    from PIL import Image
except ImportError:
    sys.exit("Нужен Pillow: pip install Pillow")


def main():
    if len(sys.argv) < 2:
        sys.exit("Использование: python build_pdf.py project.json")
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        cfg = json.load(f)
    out_dir = cfg["out_dir"]
    slides = sorted(cfg.get("slides", []), key=lambda s: s["num"])

    pages = []
    for s in slides:
        p = os.path.join(out_dir, "slide_%02d.png" % s["num"])
        if not os.path.exists(p):
            print("ПРОПУСК (нет файла): %s" % p)
            continue
        img = Image.open(p)
        if img.mode in ("RGBA", "P", "LA"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            img = img.convert("RGBA")
            bg.paste(img, mask=img.split()[-1])
            img = bg
        else:
            img = img.convert("RGB")
        pages.append(img)

    if not pages:
        sys.exit("Нет картинок слайдов для сборки PDF.")

    safe = "".join(c for c in (cfg.get("project_name") or "presentation")
                   if c not in '\\/:*?"<>|')
    out = os.path.join(out_dir, safe + ".pdf")
    pages[0].save(out, "PDF", resolution=150.0, save_all=True, append_images=pages[1:])
    print("OK: %s (страниц: %d)" % (out, len(pages)))


if __name__ == "__main__":
    main()
