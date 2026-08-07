#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Парсер HTML-выгрузки Telegram (messages.html) — с картинками.

Зачем: JSON-экспорт машиночитаем, но НЕ содержит медиа. HTML-экспорт с
включёнными фото кладёт рядом папку `photos/` и ссылается на файлы из
разметки — только так можно связать пост с его картинкой и разобрать
визуальный стиль канала, а не только текст.

Вход: папка выгрузки (`messages.html` + `photos/`), в том числе разбитая
на `messages2.html`, `messages3.html` … — все части читаются подряд.

Выход: рядом с выгрузкой `channel-brain-data/`
  - posts.jsonl — посты + локальные пути к фото каждого
  - stats.json  — агрегаты, включая долю постов с картинками

Только стандартная библиотека. Запуск:
    py parse_html.py <папка выгрузки> [--channel "Имя"]
"""
import argparse, glob, html, json, os, re, statistics, sys
from collections import Counter
from datetime import datetime

WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

# Альбом из нескольких фото экспортируется как ОДИН пост с текстом плюс несколько
# продолжений с классом `joined`. Для читателя это единый пост, поэтому продолжения
# подклеиваются к предыдущему, а не считаются отдельными постами.
RE_MSG = re.compile(r'<div class="(message default clearfix(?: joined)?)"[^>]*id="message(\d+)"(.*?)(?=<div class="message |\Z)', re.S)
RE_DATE = re.compile(r'class="pull_right date details" title="([^"]+)"')
RE_TEXT = re.compile(r'<div class="text">(.*?)</div>', re.S)
RE_PHOTO = re.compile(r'<a class="photo_wrap[^"]*" href="([^"]+)"')
RE_VIDEO = re.compile(r'href="(video_files/[^"]+)"')
RE_FWD = re.compile(r'class="forwarded body"')


def to_text(frag):
    """HTML абзаца → чистый текст с сохранением переносов."""
    t = re.sub(r"<br\s*/?>", "\n", frag)
    t = re.sub(r"</?blockquote>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    return html.unescape(t).strip()


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    ap = argparse.ArgumentParser(description="HTML-выгрузка Telegram → формат channel-brain")
    ap.add_argument("folder", help="папка выгрузки (где лежит messages.html)")
    ap.add_argument("--channel", default="", help="имя канала для отчётов")
    a = ap.parse_args()

    root = os.path.abspath(a.folder)
    parts = sorted(glob.glob(os.path.join(root, "messages*.html")),
                   key=lambda p: int(re.sub(r"\D", "", os.path.basename(p)) or 0))
    if not parts:
        print(f"В папке нет messages.html: {root}")
        sys.exit(1)

    raw = "".join(open(p, encoding="utf-8").read() for p in parts)
    channel = a.channel
    if not channel:
        m = re.search(r'<div class="text bold">\s*(.*?)\s*</div>', raw, re.S)
        channel = to_text(m.group(1)) if m else os.path.basename(root)

    posts, no_text, albums = [], 0, 0
    for cls, mid, body in RE_MSG.findall(raw):
        tm = RE_TEXT.search(body)
        text = to_text(tm.group(1)) if tm else ""
        photos = [html.unescape(p) for p in RE_PHOTO.findall(body) if "_thumb" not in p]
        videos = [html.unescape(v) for v in RE_VIDEO.findall(body) if "_thumb" not in v]

        # продолжение альбома — подклеиваем медиа и текст к предыдущему посту
        if "joined" in cls and posts:
            prev = posts[-1]
            prev["photos"] += photos
            prev["videos"] += videos
            if text:
                prev["text"] = (prev["text"] + "\n" + text).strip()
                prev["chars"] = len(prev["text"])
            prev["has_media"] = bool(prev["photos"] or prev["videos"])
            if photos or videos:
                albums += 1
            continue

        dm = RE_DATE.search(body)
        if not dm:
            continue
        try:
            dt = datetime.strptime(dm.group(1)[:19], "%d.%m.%Y %H:%M:%S")
        except ValueError:
            continue
        if not text and not photos and not videos:
            continue
        if not text:
            no_text += 1
        posts.append({
            "id": int(mid),
            "date": dt.strftime("%Y-%m-%d"),
            "datetime": dt.isoformat(timespec="minutes"),
            "hour": dt.hour, "weekday": WEEKDAYS[dt.weekday()], "year": dt.year,
            "text": text, "chars": len(text),
            "reactions": 0,                       # HTML-экспорт реакций не содержит
            "photos": photos, "videos": videos,
            "has_media": bool(photos or videos),
            "links": [u.rstrip(".,;)") for u in re.findall(r"https?://[^\s)\]\"<]+", text)],
            "forwarded": bool(RE_FWD.search(body)),
        })

    if not posts:
        print("Не разобрано ни одного сообщения — проверь, что это экспорт канала")
        sys.exit(1)

    posts.sort(key=lambda p: p["datetime"])
    out_dir = os.path.join(root, "channel-brain-data")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "posts.jsonl"), "w", encoding="utf-8") as f:
        for p in posts:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    chars = [p["chars"] for p in posts if p["chars"]]
    days = max((datetime.fromisoformat(posts[-1]["datetime"])
                - datetime.fromisoformat(posts[0]["datetime"])).days, 1)
    domains = Counter()
    for p in posts:
        for u in p["links"]:
            mm = re.match(r"https?://([^/]+)", u)
            if mm:
                domains[mm.group(1).lower().removeprefix("www.")] += 1
    with_media = sum(1 for p in posts if p["has_media"])

    stats = {
        "channel": channel,
        "metric": "none",
        "metric_note": "HTML-экспорт Telegram НЕ содержит ни реакций, ни просмотров. "
                       "Метрики вовлечённости брать из табличной выгрузки (parse_sheet.py) "
                       "и сопоставлять по id поста. Не выдумывать цифры.",
        "period": {"from": posts[0]["date"], "to": posts[-1]["date"], "days": days},
        "counts": {"posts": len(posts), "with_media": with_media,
                   "media_share": round(with_media / len(posts), 2),
                   "photos_total": sum(len(p["photos"]) for p in posts),
                   "videos_total": sum(len(p["videos"]) for p in posts),
                   "album_parts_merged": albums,
                   "without_text": no_text,
                   "forwarded": sum(1 for p in posts if p["forwarded"]),
                   "with_links": sum(1 for p in posts if p["links"])},
        "rhythm": {"per_week": round(len(posts) / (days / 7), 2),
                   "by_weekday": Counter(p["weekday"] for p in posts).most_common(),
                   "by_hour": sorted(Counter(p["hour"] for p in posts).items())},
        "length": {"median_chars": int(statistics.median(chars)) if chars else 0,
                   "avg_chars": int(statistics.mean(chars)) if chars else 0,
                   "max_chars": max(chars) if chars else 0},
        "by_year": {str(y): {"posts": sum(1 for p in posts if p["year"] == y),
                             "with_media": sum(1 for p in posts if p["year"] == y and p["has_media"])}
                    for y in sorted({p["year"] for p in posts})},
        "top_domains": domains.most_common(15),
    }
    with open(os.path.join(out_dir, "stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    print(f"Канал: {channel}")
    print(f"Постов: {len(posts)}  ({stats['period']['from']} → {stats['period']['to']}, "
          f"{stats['rhythm']['per_week']}/нед)")
    print(f"С медиа: {with_media} ({int(stats['counts']['media_share']*100)}%) — "
          f"фото {stats['counts']['photos_total']}, видео {stats['counts']['videos_total']}")
    print(f"Длина поста: медиана {stats['length']['median_chars']} знаков")
    print("Метрик вовлечённости в HTML-экспорте нет — брать из табличной выгрузки.")
    print(f"Готово: {out_dir}")


if __name__ == "__main__":
    main()
