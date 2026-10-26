"""Заводит папку ролика из шаблона motion-kit: python init_project.py <папка ролика>
Кладёт engine.js, scene.js (пример), timeline.json, audio.py и kit.json (путь к scripts/),
затем собирает index.html и data.js. Существующие файлы не перезаписывает."""
import json, pathlib, shutil, sys

KIT = pathlib.Path(__file__).resolve().parent
TPL = KIT.parent / "template"

def main():
    if len(sys.argv) < 2: sys.exit("использование: python init_project.py <папка ролика>")
    dst = pathlib.Path(sys.argv[1]).resolve(); dst.mkdir(parents=True, exist_ok=True)
    for name in ("engine.js", "scene.js", "timeline.json", "audio.py"):
        if (dst / name).exists(): print("  есть, не трогаю:", name); continue
        shutil.copyfile(TPL / name, dst / name); print("  +", name)
    (dst / "kit.json").write_text(json.dumps({"scripts": str(KIT)}, ensure_ascii=False), encoding="utf-8")
    for d in ("audio", "out", "qa"): (dst / d).mkdir(exist_ok=True)
    sys.path.insert(0, str(KIT)); import build_page; build_page.build(dst)
    print("готово:", dst)

if __name__ == "__main__":
    main()
