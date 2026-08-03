#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Парсер выгрузки Telegram-канала (result.json из Telegram Desktop).

Вход:  result.json (формат JSON, экспорт истории чата)
Выход: рядом с выгрузкой папка channel-brain-data/
       - posts.jsonl — по посту на строку: id, дата, текст, реакции, ссылки…
       - stats.json  — агрегаты: ритм, топы, длины, домены, годы

Только стандартная библиотека. Запуск:
    py parse_export.py /путь/к/result.json
"""
import json, os, re, sys, statistics
from collections import Counter
from datetime import datetime

WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def text_of(entity):
    """text в экспорте бывает строкой или списком кусков (ссылки, жирный и т.п.)."""
    t = entity.get("text", "")
    if isinstance(t, str):
        return t
    parts = []
    for p in t:
        if isinstance(p, str):
            parts.append(p)
        elif isinstance(p, dict):
            parts.append(p.get("text", ""))
    return "".join(parts)


def links_of(entity):
    out = []
    t = entity.get("text", "")
    if isinstance(t, list):
        for p in t:
            if isinstance(p, dict) and p.get("type") in ("link", "text_link"):
                out.append(p.get("href") or p.get("text") or "")
    out += re.findall(r"https?://[^\s)\]]+", text_of(entity))
    seen, uniq = set(), []
    for u in out:
        u = u.rstrip(".,;)")
        if u and u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def reactions_of(entity):
    """Сумма всех реакций поста (в JSON-экспорте нет ни просмотров, ни репостов)."""
    total = 0
    for r in entity.get("reactions", []) or []:
        try:
            total += int(r.get("count", 0))
        except (TypeError, ValueError):
            pass
    return total


def main():
    if len(sys.argv) < 2:
        print("Использование: py parse_export.py <путь/к/result.json>")
        sys.exit(1)
    src = os.path.abspath(sys.argv[1])
    if os.path.isdir(src):                       # дали папку ChatExport_… — ищем внутри
        src = os.path.join(src, "result.json")
    if not os.path.isfile(src):
        print(f"Не найден файл: {src}")
        sys.exit(1)

    with open(src, encoding="utf-8") as f:
        data = json.load(f)

    channel = data.get("name") or "канал"
    messages = data.get("messages") or []

    posts = []
    for m in messages:
        if m.get("type") != "message":           # служебные события пропускаем
            continue
        body = text_of(m).strip()
        media = m.get("media_type") or ("photo" if m.get("photo") else None)
        if not body and not media:
            continue
        try:
            dt = datetime.fromisoformat(m.get("date", ""))
        except ValueError:
            continue
        posts.append({
            "id": m.get("id"),
            "date": dt.strftime("%Y-%m-%d"),
            "datetime": dt.isoformat(timespec="minutes"),
            "hour": dt.hour,
            "weekday": WEEKDAYS[dt.weekday()],
            "year": dt.year,
            "text": body,
            "chars": len(body),
            "reactions": reactions_of(m),
            "links": links_of(m),
            "media": media,
            "forwarded": bool(m.get("forwarded_from")),
        })

    if not posts:
        print("В выгрузке нет постов с текстом. Проверь, что это экспорт канала в формате JSON.")
        sys.exit(1)

    posts.sort(key=lambda p: p["datetime"])
    out_dir = os.path.join(os.path.dirname(src), "channel-brain-data")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "posts.jsonl"), "w", encoding="utf-8") as f:
        for p in posts:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    reacts = [p["reactions"] for p in posts]
    chars = [p["chars"] for p in posts]
    first, last = posts[0]["date"], posts[-1]["date"]
    days = max((datetime.fromisoformat(posts[-1]["datetime"])
                - datetime.fromisoformat(posts[0]["datetime"])).days, 1)

    domains = Counter()
    for p in posts:
        for u in p["links"]:
            m = re.match(r"https?://([^/]+)", u)
            if m:
                domains[m.group(1).lower().removeprefix("www.")] += 1

    by_year = {}
    for y in sorted({p["year"] for p in posts}):
        yp = [p for p in posts if p["year"] == y]
        by_year[str(y)] = {
            "posts": len(yp),
            "avg_reactions": round(statistics.mean(p["reactions"] for p in yp), 1),
            "median_chars": int(statistics.median(p["chars"] for p in yp)),
        }

    top = sorted(posts, key=lambda p: -p["reactions"])[:30]
    stats = {
        "channel": channel,
        "period": {"from": first, "to": last, "days": days},
        "counts": {
            "posts": len(posts),
            "with_media": sum(1 for p in posts if p["media"]),
            "forwarded": sum(1 for p in posts if p["forwarded"]),
            "with_links": sum(1 for p in posts if p["links"]),
        },
        "rhythm": {
            "per_week": round(len(posts) / (days / 7), 1),
            "by_weekday": Counter(p["weekday"] for p in posts).most_common(),
            "by_hour": sorted(Counter(p["hour"] for p in posts).items()),
        },
        "reactions": {
            "avg": round(statistics.mean(reacts), 1),
            "median": int(statistics.median(reacts)),
            "max": max(reacts),
            "zero_share": round(sum(1 for r in reacts if r == 0) / len(reacts), 2),
        },
        "length": {
            "median_chars": int(statistics.median(chars)),
            "avg_chars": int(statistics.mean(chars)),
            "max_chars": max(chars),
        },
        "by_year": by_year,
        "top_domains": domains.most_common(15),
        "top_posts": [{"id": p["id"], "date": p["date"], "reactions": p["reactions"],
                       "preview": p["text"][:120].replace("\n", " ")} for p in top],
    }
    with open(os.path.join(out_dir, "stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    print(f"Канал: {channel}")
    print(f"Постов: {len(posts)}  ({first} → {last}, {stats['rhythm']['per_week']}/нед)")
    print(f"Реакции: сред. {stats['reactions']['avg']}, медиана {stats['reactions']['median']}, "
          f"макс {stats['reactions']['max']}, без реакций {int(stats['reactions']['zero_share']*100)}%")
    print(f"Длина поста: медиана {stats['length']['median_chars']} знаков")
    print(f"Готово: {out_dir}")


if __name__ == "__main__":
    main()
