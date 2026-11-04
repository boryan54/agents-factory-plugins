# -*- coding: utf-8 -*-
"""
fetch_slides.py — скачать готовые слайды по ссылкам BobGate в out_dir проекта (режим веб-чата claude.ai).

В веб-чате нет ключа в .secrets: слайды генерирует коннектор BobGate (инструмент generate_image),
он возвращает ссылки https://bobgate.kupryakov.ru/files/... Этот скрипт кладёт их как slide_NN.png,
дальше сборка как обычно: build_pptx.py / build_pdf.py.

Запуск:
  python fetch_slides.py project.json 1=<ссылка> 2=<ссылка> ...
  python fetch_slides.py project.json urls.json        # {"1": "<ссылка>", "2": "<ссылка>"}
"""
import json
import os
import sys
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        cfg = json.load(f)
    out_dir = cfg["out_dir"]
    if not os.path.isabs(out_dir):
        out_dir = os.path.join(os.path.dirname(os.path.abspath(sys.argv[1])), out_dir)
    os.makedirs(out_dir, exist_ok=True)
    pairs = {}
    if len(sys.argv) == 3 and sys.argv[2].endswith(".json"):
        with open(sys.argv[2], "r", encoding="utf-8") as f:
            pairs = {int(k): v for k, v in json.load(f).items()}
    else:
        for a in sys.argv[2:]:
            num, _, url = a.partition("=")
            pairs[int(num)] = url
    failed = []
    for num, url in sorted(pairs.items()):
        out = os.path.join(out_dir, "slide_%02d.png" % num)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "style-presentation/fetch"})
            with urllib.request.urlopen(req, timeout=120) as r, open(out, "wb") as f:
                f.write(r.read())
            print("[слайд %d] сохранено: %s" % (num, out))
        except Exception as e:
            failed.append(num)
            print("[слайд %d] !! не скачано (%s)" % (num, str(e)[:120]))
    if failed:
        print("Не скачаны: %s. Если песочница не пускает в интернет – в claude.ai: Настройки → Возможности → "
              "разрешённые домены, добавить bobgate.kupryakov.ru" % ", ".join(map(str, failed)))
        sys.exit(1)


if __name__ == "__main__":
    main()
