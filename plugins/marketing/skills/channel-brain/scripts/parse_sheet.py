#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Парсер выгрузки канала из ТАБЛИЦЫ (CSV/Google Sheets), а не из result.json.

Зачем: не всякий канал выгружается через Telegram Desktop — часто посты уже
собраны в таблицу сторонним сервисом. Формат колонок такой выгрузки другой,
а метрика обычно ПРОСМОТРЫ, а не реакции. Скрипт приводит такую таблицу к тому
же виду, с которым работает channel-brain (posts.jsonl + stats.json), и честно
помечает, какая метрика внутри, — чтобы агент писал «по просмотрам», а не
выдумывал реакции, которых в данных нет.

Ожидаемые колонки (регистр и порядок не важны, лишние игнорируются):
    Ссылка · Дата · Текст · Просмотры (или Реакции) · Медиа

Чинит две типовые беды таких выгрузок:
  - ПОЛНОЕ задвоение текста в ячейке (текст склеен сам с собой);
  - строки без даты или без текста.

Запуск:
    py parse_sheet.py <путь>/канал.csv [--channel "Имя канала"]
"""
import argparse, csv, json, os, re, statistics, sys
from collections import Counter
from datetime import datetime

WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
DATE_FORMATS = ["%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"]


def col(row, *names):
    """Достаёт колонку по любому из имён, без учёта регистра и пробелов."""
    low = {(k or "").strip().lower(): v for k, v in row.items()}
    for n in names:
        if n.lower() in low:
            return (low[n.lower()] or "").strip()
    return ""


def undouble(text):
    """Текст, склеенный сам с собой, — частый дефект табличных выгрузок."""
    t = (text or "").strip()
    h = len(t) // 2
    if len(t) > 40 and t[:h] == t[h:]:
        return t[:h].strip()
    return t


def parse_date(s):
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    return None


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    csv.field_size_limit(10_000_000)

    ap = argparse.ArgumentParser(description="Таблица с постами → формат channel-brain")
    ap.add_argument("csv_path")
    ap.add_argument("--channel", default="", help="имя канала для отчётов")
    a = ap.parse_args()

    src = os.path.abspath(a.csv_path)
    if not os.path.isfile(src):
        print(f"Не найден файл: {src}")
        sys.exit(1)

    with open(src, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print("Таблица пуста")
        sys.exit(1)

    metric = "reactions" if any(col(r, "реакции", "reactions") for r in rows) else "views"
    metric_ru = "реакции" if metric == "reactions" else "просмотры"

    posts, doubled, skipped = [], 0, 0
    for r in rows:
        raw = col(r, "текст", "text")
        text = undouble(raw)
        if text != raw.strip():
            doubled += 1
        dt = parse_date(col(r, "дата", "date"))
        if not text or not dt:
            skipped += 1
            continue
        link = col(r, "ссылка", "link", "url")
        m = re.search(r"/(\d+)\s*$", link)
        val = col(r, "просмотры", "views", "реакции", "reactions")
        posts.append({
            "id": int(m.group(1)) if m else len(posts) + 1,
            "date": dt.strftime("%Y-%m-%d"),
            "datetime": dt.isoformat(timespec="minutes"),
            "hour": dt.hour, "weekday": WEEKDAYS[dt.weekday()], "year": dt.year,
            "text": text, "chars": len(text),
            metric: int(val) if val.isdigit() else 0,
            "link": link,
            "links": [u.rstrip(".,;)") for u in re.findall(r"https?://[^\s)\]]+", text)],
            "media": col(r, "медиа", "media") or None,
            "forwarded": False,
        })

    if not posts:
        print("Не удалось разобрать ни одной строки — проверь колонки Дата и Текст")
        sys.exit(1)

    posts.sort(key=lambda p: p["datetime"])
    out_dir = os.path.join(os.path.dirname(src), "channel-brain-data")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "posts.jsonl"), "w", encoding="utf-8") as f:
        for p in posts:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    vals = [p[metric] for p in posts]
    chars = [p["chars"] for p in posts]
    days = max((datetime.fromisoformat(posts[-1]["datetime"])
                - datetime.fromisoformat(posts[0]["datetime"])).days, 1)
    domains = Counter()
    for p in posts:
        for u in p["links"]:
            mm = re.match(r"https?://([^/]+)", u)
            if mm:
                domains[mm.group(1).lower().removeprefix("www.")] += 1

    by_year = {}
    for y in sorted({p["year"] for p in posts}):
        yp = [p for p in posts if p["year"] == y]
        by_year[str(y)] = {"posts": len(yp),
                           f"avg_{metric}": round(statistics.mean(p[metric] for p in yp), 1),
                           "median_chars": int(statistics.median(p["chars"] for p in yp))}

    stats = {
        "channel": a.channel or os.path.splitext(os.path.basename(src))[0],
        "metric": metric,
        "metric_note": f"В этой выгрузке метрика вовлечённости — {metric_ru.upper()}. "
                       f"В отчётах писать «по {metric_ru}», другой метрики в данных нет.",
        "period": {"from": posts[0]["date"], "to": posts[-1]["date"], "days": days},
        "counts": {"posts": len(posts), "with_links": sum(1 for p in posts if p["links"]),
                   "doubled_fixed": doubled, "skipped_rows": skipped},
        "rhythm": {"per_week": round(len(posts) / (days / 7), 2),
                   "by_weekday": Counter(p["weekday"] for p in posts).most_common(),
                   "by_hour": sorted(Counter(p["hour"] for p in posts).items())},
        metric: {"avg": round(statistics.mean(vals), 1), "median": int(statistics.median(vals)),
                 "max": max(vals), "zero_share": round(sum(1 for v in vals if v == 0) / len(vals), 2)},
        "length": {"median_chars": int(statistics.median(chars)),
                   "avg_chars": int(statistics.mean(chars)), "max_chars": max(chars)},
        "by_year": by_year,
        "top_domains": domains.most_common(15),
        "top_posts": [{"id": p["id"], "date": p["date"], metric: p[metric],
                       "preview": p["text"][:120].replace("\n", " ")}
                      for p in sorted(posts, key=lambda x: -x[metric])[:30]],
    }
    with open(os.path.join(out_dir, "stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    print(f"Канал: {stats['channel']}")
    print(f"Постов: {len(posts)}  ({stats['period']['from']} → {stats['period']['to']}, "
          f"{stats['rhythm']['per_week']}/нед)")
    print(f"Метрика: {metric_ru.upper()} — сред. {stats[metric]['avg']}, "
          f"медиана {stats[metric]['median']}, макс {stats[metric]['max']}")
    print(f"Длина поста: медиана {stats['length']['median_chars']} знаков")
    if doubled:
        print(f"Починено задвоенных текстов: {doubled}")
    if skipped:
        print(f"Пропущено строк без даты или текста: {skipped}")
    print(f"Готово: {out_dir}")


if __name__ == "__main__":
    main()
