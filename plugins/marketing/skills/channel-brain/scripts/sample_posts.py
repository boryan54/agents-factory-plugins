#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Выборки постов из posts.jsonl — чтобы не тянуть весь канал в контекст.

Примеры:
    py sample_posts.py <путь>/channel-brain-data/posts.jsonl --top 30
    py sample_posts.py ... --recent 20 --full
    py sample_posts.py ... --random 30 --seed 7
    py sample_posts.py ... --id 1234 --full
    py sample_posts.py ... --bottom 15          # худшие по реакциям (мёртвые ветки)

По умолчанию текст обрезается до 400 знаков; --full отдаёт целиком
(нужно для свайп-файла и разбора стиля).
"""
import argparse, json, random, sys


def load(path):
    posts = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                posts.append(json.loads(line))
    return posts


def pick_metric(posts):
    """Какая метрика реально заполнена: reactions (JSON-экспорт) или views (таблица).

    Сортировать всегда по `reactions` нельзя: в HTML-выгрузке их нет вовсе, поле
    стоит в нуле, и «топ по реакциям» молча возвращает случайные посты.
    """
    for name in ("reactions", "views"):
        if any((p.get(name) or 0) for p in posts):
            return name
    return "reactions"


def show(posts, full, limit_chars=400, metric="reactions"):
    label = "реакций" if metric == "reactions" else "просмотров"
    for p in posts:
        text = p["text"] if full else p["text"][:limit_chars]
        cut = "" if full or len(p["text"]) <= limit_chars else " …"
        val = p.get(metric)
        media = f" · фото {len(p['photos'])}" if p.get("photos") else ""
        print(f"\n#{p['id']} ({p['date']}, {p['weekday']}) · "
              f"{label}: {'нет данных' if val is None else val} · "
              f"{p['chars']} знаков{media}")
        if p["links"]:
            print(f"ссылки: {', '.join(p['links'][:5])}")
        print(text + cut)


def main():
    ap = argparse.ArgumentParser(description="Выборки постов канала")
    ap.add_argument("posts", help="путь к posts.jsonl")
    ap.add_argument("--top", type=int, help="N лучших по реакциям")
    ap.add_argument("--bottom", type=int, help="N худших по реакциям")
    ap.add_argument("--recent", type=int, help="N свежих")
    ap.add_argument("--random", type=int, help="N случайных")
    ap.add_argument("--id", type=int, action="append", help="конкретный пост по id (можно несколько)")
    ap.add_argument("--year", type=int, help="ограничить годом")
    ap.add_argument("--full", action="store_true", help="текст целиком, без обрезки")
    ap.add_argument("--seed", type=int, default=42, help="зерно случайной выборки (для повторяемости)")
    a = ap.parse_args()

    posts = load(a.posts)
    metric = pick_metric(posts)
    if a.year:
        posts = [p for p in posts if p["year"] == a.year]
    if not posts:
        print("Постов не найдено")
        sys.exit(1)

    if a.id:
        ids = set(a.id)
        show([p for p in posts if p["id"] in ids], a.full, metric=metric)
        return
    if a.top:
        show(sorted(posts, key=lambda p: -(p.get(metric) or 0))[:a.top], a.full, metric=metric)
        return
    if a.bottom:
        show(sorted((x for x in posts if x.get(metric) is not None),
                    key=lambda p: p.get(metric) or 0)[:a.bottom], a.full, metric=metric)
        return
    if a.recent:
        show(posts[-a.recent:], a.full, metric=metric)
        return
    if a.random:
        rnd = random.Random(a.seed)
        show(rnd.sample(posts, min(a.random, len(posts))), a.full, metric=metric)
        return
    print(f"Всего постов: {len(posts)}. Укажи выборку: --top / --bottom / --recent / --random / --id")


if __name__ == "__main__":
    main()
