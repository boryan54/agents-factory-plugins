#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сшивает метрики вовлечённости из таблицы с постами из HTML-выгрузки.

Зачем: у двух источников разная полнота, и ни один не самодостаточен.
  - HTML-экспорт Telegram даёт ТЕКСТ и КАРТИНКИ, но не содержит ни просмотров,
    ни реакций;
  - табличная выгрузка даёт МЕТРИКИ, но без медиа.
Скрипт берёт лучшее из обоих: к разобранному HTML добавляет метрику по номеру
поста (а если номера не сошлись — по дате и времени).

Запуск:
    py merge_metrics.py <папка выгрузки>/channel-brain-data/posts.jsonl <таблица.csv>
"""
import argparse, csv, json, os, re, statistics, sys
from datetime import datetime

DATE_FORMATS = ["%d.%m.%Y %H:%M", "%d.%m.%Y %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"]


def parse_date(s):
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(s.strip(), f)
        except ValueError:
            continue
    return None


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    csv.field_size_limit(10_000_000)

    ap = argparse.ArgumentParser(description="Добавить метрики из таблицы в posts.jsonl")
    ap.add_argument("posts", help="путь к posts.jsonl (от parse_html.py)")
    ap.add_argument("table", help="путь к таблице CSV с колонками Ссылка/Дата/Просмотры")
    a = ap.parse_args()

    posts = [json.loads(l) for l in open(a.posts, encoding="utf-8") if l.strip()]
    with open(a.table, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    def col(r, *names):
        low = {(k or "").strip().lower(): v for k, v in r.items()}
        for n in names:
            if n.lower() in low:
                return (low[n.lower()] or "").strip()
        return ""

    metric = "reactions" if any(col(r, "реакции", "reactions") for r in rows) else "views"
    by_id, by_min = {}, {}
    for r in rows:
        val = col(r, "просмотры", "views", "реакции", "reactions")
        val = int(val) if val.isdigit() else 0
        m = re.search(r"/(\d+)\s*$", col(r, "ссылка", "link", "url"))
        dt = parse_date(col(r, "дата", "date"))
        if m:
            by_id[int(m.group(1))] = val
        if dt:
            by_min[dt.strftime("%Y-%m-%dT%H:%M")] = val

    hit_id = hit_dt = miss = 0
    for p in posts:
        if p["id"] in by_id:
            p[metric] = by_id[p["id"]]; hit_id += 1
        elif p["datetime"] in by_min:
            p[metric] = by_min[p["datetime"]]; hit_dt += 1
        else:
            p[metric] = None; miss += 1          # None, а не 0: «данных нет» ≠ «ноль просмотров»

    with open(a.posts, "w", encoding="utf-8") as f:
        for p in posts:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    # обновляем stats.json: метрика, её сводка и разрез «с картинкой / без»
    sp = os.path.join(os.path.dirname(a.posts), "stats.json")
    if os.path.isfile(sp):
        stats = json.load(open(sp, encoding="utf-8"))
        vals = [p[metric] for p in posts if p[metric] is not None]
        metric_ru = "реакции" if metric == "reactions" else "просмотры"
        stats["metric"] = metric
        stats["metric_note"] = (f"Метрика вовлечённости — {metric_ru.upper()}, подшита из "
                                f"табличной выгрузки. Писать «по {metric_ru}». "
                                f"У постов без данных значение null — это НЕ ноль.")
        if vals:
            s = sorted(vals)
            stats[metric] = {"avg": round(statistics.mean(vals), 1),
                             "median": int(statistics.median(vals)),
                             "max": max(vals), "min": min(vals), "no_data": miss}
            with_m = [p[metric] for p in posts if p.get("has_media") and p[metric] is not None]
            without_m = [p[metric] for p in posts if not p.get("has_media") and p[metric] is not None]
            stats["media_effect"] = {
                "with_media": {"posts": len(with_m),
                               "median": int(statistics.median(with_m)) if with_m else None},
                "without_media": {"posts": len(without_m),
                                  "median": int(statistics.median(without_m)) if without_m else None},
                "note": "Сравнивать осторожно: на просмотры влияет и возраст поста, и тема.",
            }
            stats["top_posts"] = [{"id": p["id"], "date": p["date"], metric: p[metric],
                                   "photos": len(p.get("photos", [])),
                                   "preview": p["text"][:120].replace("\n", " ")}
                                  for p in sorted((x for x in posts if x[metric] is not None),
                                                  key=lambda x: -x[metric])[:30]]
        json.dump(stats, open(sp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print(f"Метрика: {metric}")
    print(f"Сшито по номеру поста: {hit_id}, по дате и времени: {hit_dt}, без данных: {miss}")
    if miss:
        print("  У постов без данных стоит null — это отсутствие данных, а не ноль.")
    print(f"Обновлено: {a.posts}" + (f" и {sp}" if os.path.isfile(sp) else ""))


if __name__ == "__main__":
    main()
