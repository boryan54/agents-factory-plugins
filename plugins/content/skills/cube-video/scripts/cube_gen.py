#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Генерация контента для 3D-куба через Genosai REST API. Только stdlib
(+ certifi, если стоит - без него на Windows валится проверка сертификатов).

Фазы:
    balance            остаток кредитов
    models             таблица моделей с ЗАМЕРЕННЫМИ ценами - это гейт выбора
    frame              стартовый кадр(ы) 9:16
    video              оживление кадра выбранной моделью
    music              инструментальная подложка
    status             фактические траты по кэшу results.json

Запуск:
    python cube_gen.py models
    python cube_gen.py frame --project project.json --dry-run
    python cube_gen.py frame --project project.json
    python cube_gen.py video --project project.json
    python cube_gen.py status --project project.json

ВСЕГДА сначала --dry-run: он печатает, что уйдёт в API, и смету, но не тратит
ни кредита. Платная фаза запускается только после явного «ок» заказчика.
"""
import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.request
import uuid

HOST = "https://api.genosai.io"

# ── Цены ЗАМЕРЕНЫ на живом API (2026-08-21), а не взяты из каталога ──────────
# кредитов за одну задачу при указанной настройке
PRICES = [
    # модель,            что,      настройка,          цена, заметка
    ("chatgpt-image-2", "кадр", "2K, 9:16", 10,
     "рабочая лошадка стартового кадра, держит каркас промпта"),
    ("nano-banana-2", "кадр", "2K, 9:16", 12,
     "второе мнение по кадру, иногда чище фон"),
    ("grok-imagine-1.5", "видео", "1080p, 6 сек", 48,
     "ДЕФОЛТ. Лучшая картинка за свои деньги; дрейф тени ~1.25"),
    ("veo-3.1-lite", "видео", "720p, 6 сек", 30,
     "самый дешёвый тест движения; дрейф ~1.10"),
    ("veo-3.1-fast", "видео", "старшие разрешения", 180,
     "дорого, берём только под конкретную задачу"),
    ("kling-3.0", "видео", "6 сек", 100,
     "запасной вариант, если grok лежит"),
    ("seedance-2.0-fast", "видео", "720p, 6 сек", 120,
     "НЕ предлагать по умолчанию: дорого при среднем результате"),
    ("seedance-2.0", "видео", "720p, 6 сек", 180,
     "НЕ предлагать по умолчанию; дрейф тени лучший (~0.2)"),
    ("seedance-2.0", "видео", "1080p, 6 сек", 450,
     "почти четырёхкратная переплата против grok. Предупреждать отдельно"),
    ("suno-v5.5", "музыка", "2 трека", 16,
     "instrumental: true обязателен"),
]
BROKEN = {"seedance-2.5": "на 720p отдаёт HTTP 400 - не предлагать"}


# ── Ключ и сеть ──────────────────────────────────────────────────────────────
def find_key():
    """env GENOSAI_API_KEY или .secrets/genosai.env вверх по дереву."""
    if os.environ.get("GENOSAI_API_KEY"):
        return os.environ["GENOSAI_API_KEY"]
    seeds = [os.getcwd(), os.path.dirname(os.path.abspath(__file__))]
    seen = set()
    for seed in seeds:
        d = seed
        while d and d not in seen:
            seen.add(d)
            p = os.path.join(d, ".secrets", "genosai.env")
            if os.path.isfile(p):
                for line in open(p, encoding="utf-8"):
                    m = re.search(r"GENOSAI_API_KEY\s*=\s*(\S+)", line)
                    if m:
                        return m.group(1).strip("\"'")
            nd = os.path.dirname(d)
            if nd == d:
                break
            d = nd
    sys.exit("нет ключа: создай .secrets/genosai.env со строкой "
             "GENOSAI_API_KEY=sdk_live_... . В чате ключ не спрашивать.")


def ssl_ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        # без certifi системный python на Windows падает
        # на CERTIFICATE_VERIFY_FAILED - поставь certifi, если увидишь это
        return ssl.create_default_context()


KEY = None
CTX = ssl_ctx()


def api(path, body=None, timeout=120):
    global KEY
    KEY = KEY or find_key()
    req = urllib.request.Request(
        HOST + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + KEY,
                 "Content-Type": "application/json",
                 "Connection": "close"},
        method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, context=CTX, timeout=timeout) as r:
        return json.loads(r.read().decode())


def upload(path):
    """POST /v1/uploads -> url. Ответ бывает и с обёрткой data, и без неё."""
    global KEY
    KEY = KEY or find_key()
    boundary = uuid.uuid4().hex
    data = open(path, "rb").read()
    ct = "image/png" if path.lower().endswith(".png") else "image/jpeg"
    head = ("--" + boundary + "\r\n"
            "Content-Disposition: form-data; name=\"file\"; filename=\""
            + os.path.basename(path) + "\"\r\n"
            "Content-Type: " + ct + "\r\n\r\n")
    body = head.encode() + data + ("\r\n--" + boundary + "--\r\n").encode()
    req = urllib.request.Request(
        HOST + "/v1/uploads", data=body,
        headers={"Authorization": "Bearer " + KEY, "Connection": "close",
                 "Content-Type": "multipart/form-data; boundary=" + boundary},
        method="POST")
    with urllib.request.urlopen(req, context=CTX, timeout=300) as r:
        resp = json.loads(r.read().decode())
    url = (resp.get("data") or resp).get("url")
    if not url:
        sys.exit("загрузка не вернула url: " + json.dumps(resp)[:400])
    return url


def download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, context=CTX, timeout=600) as r:
        data = r.read()
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    open(dest, "wb").write(data)
    return len(data)


# ── Кэш результатов ─────────────────────────────────────────────────────────
def cache_path(proj_path):
    return os.path.join(os.path.dirname(os.path.abspath(proj_path)),
                        "results.json")


def load_cache(p):
    return json.load(open(p, encoding="utf-8")) if os.path.isfile(p) else {}


def save_cache(p, data):
    json.dump(data, open(p, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)


# ── Задачи ──────────────────────────────────────────────────────────────────
def wait_all(tasks, poll=10, limit=120):
    """tasks: [(имя, taskId)] -> {имя: (status, cost, [urls])}"""
    done = {}
    for _ in range(limit):
        if len(done) == len(tasks):
            break
        for name, tid in tasks:
            if name in done:
                continue
            d = (api("/v1/taskInfo?taskId=" + tid).get("data") or {})
            if d.get("status") in ("succeeded", "failed", "error"):
                urls = (d.get("result") or {}).get("media_urls") or []
                done[name] = (d.get("status"), d.get("cost") or 0, urls)
                print("  {}: {} cost={} файлов={}".format(
                    name, d.get("status"), d.get("cost"), len(urls)))
        if len(done) < len(tasks):
            time.sleep(poll)
    for name, _ in tasks:
        done.setdefault(name, ("timeout", 0, []))
    return done


def as_list(node, default_id):
    """Секция project.json может быть объектом или списком вариантов."""
    if node is None:
        return []
    if isinstance(node, dict):
        node = [node]
    out = []
    for i, item in enumerate(node, 1):
        item = dict(item)
        if len(node) > 1:
            item.setdefault("id", "{}_{:02d}".format(default_id, i))
        else:
            item.setdefault("id", default_id)
        out.append(item)
    return out


def price_of(model, resolution):
    """-> (цена, точное ли совпадение по настройке)."""
    res = (resolution or "").lower()
    for m, _, setting, cost, _ in PRICES:
        if m == model and res and res in setting.lower():
            return cost, True
    for m, _, _, cost, _ in PRICES:
        if m == model:
            return cost, False
    return None, False


def show_estimate(jobs, kind):
    total = 0
    approx = False
    print("")
    print("Смета фазы " + kind + ":")
    for j in jobs:
        c, exact = price_of(j.get("model", ""), j.get("resolution"))
        total += c or 0
        if c and not exact:
            approx = True
        if c:
            cell = "~" + str(c) + ("" if exact else " *")
        else:
            cell = "цена неизвестна"
        print("  {:16} {:18} {:10} {:>16} кред.".format(
            j["id"], j.get("model", "?"), j.get("resolution", "-"), cell))
    print("  ИТОГО ~{} кредитов".format(total))
    if approx:
        print("  * цена замерена на другой настройке этой модели - "
              "факт смотри в status после генерации")
    return total


# ── Фазы ────────────────────────────────────────────────────────────────────
def phase_balance(_args):
    d = api("/v1/balance").get("data") or {}
    print("Баланс: {} кредитов (основной {}, бонус {})".format(
        d.get("total"), d.get("main"), d.get("bonus")))


def phase_models(_args):
    print("Цены ЗАМЕРЕНЫ на живом API 2026-08-21, каталожные отличаются.\n")
    print("{:18}{:8}{:22}{:>7}  заметка".format(
        "модель", "что", "настройка", "кред."))
    print("-" * 100)
    for m, kind, setting, cost, note in PRICES:
        print("{:18}{:8}{:22}{:7}  {}".format(m, kind, setting, cost, note))
    print("\nНе работает:")
    for m, why in BROKEN.items():
        print("  {}: {}".format(m, why))
    print("\nВыбор модели за заказчиком. Дефолт - grok-imagine-1.5.")


def _generate(args, node_key, out_sub, ext, extra_builder):
    proj = json.load(open(args.project, encoding="utf-8"))
    base = os.path.dirname(os.path.abspath(args.project))
    out_dir = os.path.join(base, proj.get("out_dir", "."), out_sub)
    jobs = as_list(proj.get(node_key), node_key)
    if not jobs:
        sys.exit("в project.json нет секции " + node_key)
    aspect = proj.get("aspect_ratio", "9:16")

    for j in jobs:
        if not j.get("prompt"):
            sys.exit("[{}] пустой prompt".format(j["id"]))
        if not j.get("model"):
            sys.exit("[{}] не указана model - выбор модели за заказчиком, "
                     "молча не подставляем".format(j["id"]))
    show_estimate(jobs, node_key)

    if args.dry_run:
        print("\n--- dry-run, ничего не отправлено, кредиты не потрачены ---")
        for j in jobs:
            params = {k: v for k, v in j.items() if k not in ("prompt", "id")}
            print("\n[{}] aspect={} {}".format(j["id"], aspect, params))
            print("prompt:\n" + j["prompt"])
        return

    cache = load_cache(cache_path(args.project))
    tasks = []
    for j in jobs:
        prev = cache.get(j["id"])
        if prev and prev.get("status") == "succeeded" and not args.force:
            print("  {}: уже сделано, пропуск (--force чтобы заново)".format(
                j["id"]))
            continue
        inp = {"prompt": j["prompt"], "aspect_ratio": aspect}
        inp.update(extra_builder(j, base))
        r = api("/v1/createTask", {"model": j["model"], "input": inp})
        tid = (r.get("data") or {}).get("taskId")
        if not tid:
            print("  {}: задача не создалась: {}".format(
                j["id"], json.dumps(r, ensure_ascii=False)[:300]))
            continue
        tasks.append((j["id"], tid))
        print("  создано {} ({})".format(j["id"], j["model"]))
        time.sleep(1)

    if not tasks:
        return
    done = wait_all(tasks)
    spent = 0
    for name, (status, cost, urls) in done.items():
        spent += cost
        model = next(j["model"] for j in jobs if j["id"] == name)
        rec = {"status": status, "cost": cost, "model": model, "files": []}
        for k, u in enumerate(urls):
            suffix = "" if len(urls) == 1 else "_{}".format(k + 1)
            dest = os.path.join(out_dir, name + suffix + ext)
            size = download(u, dest)
            rec["files"].append(os.path.relpath(dest, base).replace("\\", "/"))
            print("  {} {} КБ".format(rec["files"][-1], size // 1024))
        cache[name] = rec
    save_cache(cache_path(args.project), cache)
    print("\nПотрачено в этой фазе: {} кредитов".format(spent))


def phase_frame(args):
    def extra(j, base):
        e = {"resolution": j.get("resolution", "2K")}
        refs = j.get("image_urls") or j.get("refs") or []
        urls = [r if r.startswith("http") else upload(os.path.join(base, r))
                for r in refs]
        if urls:
            e["image_urls"] = urls
        return e
    _generate(args, "frame", "frames", ".png", extra)


def phase_video(args):
    def extra(j, base):
        e = {"resolution": j.get("resolution", "720p"),
             "duration": str(j.get("duration", "6"))}
        sf = j.get("start_frame")
        if not sf:
            sys.exit("[{}] нет start_frame: без стартового кадра композиция "
                     "уплывёт, для куба так не делаем".format(j["id"]))
        e["image_urls"] = [sf if sf.startswith("http")
                           else upload(os.path.join(base, sf))]
        return e
    _generate(args, "video", "clips", ".mp4", extra)


def phase_music(args):
    def extra(j, _base):
        return {"instrumental": j.get("instrumental", True)}
    _generate(args, "music", "audio", ".mp3", extra)


def phase_status(args):
    cache = load_cache(cache_path(args.project))
    if not cache:
        print("results.json пуст - платных фаз ещё не было")
        return
    total = 0
    print("{:20}{:18}{:12}{:>7}  файлы".format(
        "задача", "модель", "статус", "кред."))
    for name, rec in cache.items():
        total += rec.get("cost", 0)
        print("{:20}{:18}{:12}{:7}  {}".format(
            name, rec.get("model", "?"), rec.get("status", "?"),
            rec.get("cost", 0), ", ".join(rec.get("files", []))))
    print("\nВсего потрачено: {} кредитов".format(total))


PHASES = {"balance": phase_balance, "models": phase_models,
          "frame": phase_frame, "video": phase_video,
          "music": phase_music, "status": phase_status}


def main():
    ap = argparse.ArgumentParser(description="Генерация контента для 3D-куба")
    ap.add_argument("phase", choices=sorted(PHASES))
    ap.add_argument("--project", default="project.json")
    ap.add_argument("--dry-run", action="store_true",
                    help="показать промпты и смету, ничего не тратить")
    ap.add_argument("--force", action="store_true",
                    help="перегенерировать даже то, что уже в results.json")
    args = ap.parse_args()
    PHASES[args.phase](args)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    main()
