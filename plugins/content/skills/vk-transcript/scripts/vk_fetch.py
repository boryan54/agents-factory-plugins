#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Уровень 0 каскада: метаданные + готовые субтитры ВК-Видео -> cues.json + meta.json.

Зависимости: yt-dlp, certifi. Больше ничего (остальное — stdlib).

Грабли, которые здесь обойдены (проверено на живой ссылке, см. SKILL.md):
  * нет корневых сертификатов -> SSL_CERT_FILE = certifi.where() до любого запроса;
  * CDN отдаёт HTTP 400 без User-Agent и Referer -> заголовки берём из http_headers
    в JSON yt-dlp и дополняем Referer: https://vkvideo.ru/;
  * ВК присылает ДВА варианта субтитров 'ru', побайтно одинаковых -> дедуп;
  * `--write-subs` падает с "unsafe file extension: 'ru.Ru AUTO'" -> качаем VTT сами;
  * субтитры ВК лежат в info['subtitles'], а НЕ в 'automatic_captions'.

Запуск:
    python vk_fetch.py <url> --out <папка> [--slug имя] [--lang ru]
                       [--cookies-from-browser chrome] [--probe]

    --probe  — только диагностика: метаданные и список доступных субтитров, без скачивания.

Результат в <папка>:
    <slug>.meta.json  — title, uploader, duration, upload_date, url, chapters, subs
    <slug>.cues.json  — [{"start": сек, "end": сек, "text": "..."}, ...]
    <slug>.vtt        — исходник субтитров как пришёл (для разбора спорных мест)
"""
import argparse
import hashlib
import json
import os
import re
import ssl
import subprocess
import sys
import urllib.request

try:
    import certifi
except ImportError:
    sys.exit("нет certifi: pip install certifi (без него SSL падает "
             "с CERTIFICATE_VERIFY_FAILED)")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
REFERER = "https://vkvideo.ru/"


def hms(sec):
    """Секунды -> ЧЧ:ММ:СС. Покрытие сверяется с длительностью видео, а она в ЧЧ:ММ:СС."""
    sec = int(sec or 0)
    return "%02d:%02d:%02d" % (sec // 3600, sec % 3600 // 60, sec % 60)


def plural(n, one, few, many):
    """Согласование числительных: 1 реплика / 2 реплики / 5 реплик."""
    n = abs(int(n))
    if n % 100 // 10 == 1:
        return many
    last = n % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def setup_ssl():
    """Корневые сертификаты для urllib И для дочернего yt-dlp."""
    os.environ["SSL_CERT_FILE"] = certifi.where()
    os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
    return ssl.create_default_context(cafile=certifi.where())


def normalize_url(raw):
    """vk.com / vkvideo.ru / ?z=video... / video_ext.php -> https://vkvideo.ru/video-X_Y"""
    raw = raw.strip().strip('"').strip("'")
    m = re.search(r"(video-?\d+_\d+)", raw)
    if m:
        return "https://vkvideo.ru/" + m.group(1)
    m = re.search(r"[?&]oid=(-?\d+)&id=(\d+)", raw)      # video_ext.php?oid=-12&id=34
    if m:
        return "https://vkvideo.ru/video%s_%s" % (m.group(1), m.group(2))
    return raw                                            # отдадим yt-dlp как есть


def slugify(title, maxlen=48):
    table = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
    out = "".join(table.get(ch, ch) for ch in (title or "").lower())
    out = re.sub(r"[^a-z0-9]+", "-", out).strip("-")
    return (out[:maxlen].rstrip("-") or "video")


def ytdlp_json(url, cookies_browser=None):
    """yt-dlp -J --skip-download. Возвращает dict info."""
    cmd = [sys.executable, "-m", "yt_dlp", "-J", "--skip-download", "--no-warnings"]
    if cookies_browser:
        cmd += ["--cookies-from-browser", cookies_browser]
    cmd.append(url)
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0 or not (p.stdout or "").strip():
        sys.exit("yt-dlp не отдал JSON (код %s).\n%s" % (p.returncode, (p.stderr or "")[-2000:]))
    return json.loads(p.stdout)


def pick_track(subs, lang):
    """Выбрать одну дорожку нужного языка. ВК шлёт дубли — берём первую."""
    if not subs:
        return None, []
    order = [lang] + [k for k in subs if k != lang]
    for code in order:
        variants = subs.get(code) or []
        if variants:
            return code, variants
    return None, []


def http_get(url, headers, ctx):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, context=ctx, timeout=60) as r:
        return r.read()


TS = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})\s*-->\s*"
                r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})")
TAGS = re.compile(r"<[^>]+>")


def _sec(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def parse_vtt(text):
    """WEBVTT -> [{'start','end','text'}]. Снимает теги, схлопывает повторы подряд."""
    cues, cur = [], None
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        m = TS.search(line)
        if m:
            if cur:
                cues.append(cur)
            cur = {"start": _sec(*m.group(1, 2, 3, 4)),
                   "end": _sec(*m.group(5, 6, 7, 8)), "text": ""}
            continue
        if cur is None:
            continue
        s = TAGS.sub("", line).strip()
        if not s or s.startswith(("WEBVTT", "NOTE", "STYLE", "Kind:", "Language:")):
            continue
        cur["text"] = (cur["text"] + " " + s).strip()
    if cur:
        cues.append(cur)

    for c in cues:
        c["text"] = re.sub(r"\s+", " ", c["text"]).strip()
    return dedupe_rolling([c for c in cues if c["text"]])


def dedupe_rolling(cues, window=80, min_overlap=2):
    """ВК шлёт РОЛЛОВЫЕ субтитры: каждая реплика повторяет хвост предыдущей.

    Проверено на живой ссылке: 29 872 слова сырых реплик -> 9 997 уникальных.
    Без этой склейки текст утраивается. Оставляем от реплики только НОВЫЕ слова,
    время берём то, в которое они появились впервые.
    """
    out, acc = [], []
    for c in cues:
        w = c["text"].split()
        tail = acc[-window:]
        k = 0
        for n in range(min(len(w), len(tail)), min_overlap - 1, -1):
            if tail[-n:] == w[:n]:
                k = n
                break
        new = w[k:]
        if not new:                        # реплика целиком уже сказана
            if out:
                out[-1]["end"] = max(out[-1]["end"], c["end"])
            continue
        acc.extend(new)
        out.append({"start": c["start"], "end": c["end"], "text": " ".join(new)})
    return out


def main():
    try:                                   # Windows-консоль в cp866 роняет кириллицу
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--out", default=".")
    ap.add_argument("--slug")
    ap.add_argument("--lang", default="ru")
    ap.add_argument("--cookies-from-browser")
    ap.add_argument("--probe", action="store_true")
    a = ap.parse_args()

    ctx = setup_ssl()
    url = normalize_url(a.url)
    info = ytdlp_json(url, a.cookies_from_browser)

    subs = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    meta = {
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        "duration": info.get("duration"),
        "upload_date": info.get("upload_date"),
        "url": info.get("webpage_url") or url,
        "chapters": info.get("chapters") or [],
        "subtitle_langs": {k: len(v or []) for k, v in subs.items()},
        "auto_caption_langs": sorted(auto.keys()),
    }

    if a.probe:
        print(json.dumps(meta, ensure_ascii=False, indent=2))
        print("ДЛИТЕЛЬНОСТЬ: %s (%s сек)" % (hms(meta["duration"]), meta["duration"]))
        print("СУБТИТРЫ: %s" % ("есть – уровень 0 закрывает задачу"
                                if subs else "НЕТ – нужен уровень 1 (ASR)"))
        return

    os.makedirs(a.out, exist_ok=True)
    slug = a.slug or slugify(meta["title"])
    meta_path = os.path.join(a.out, slug + ".meta.json")

    code, variants = pick_track(subs, a.lang)
    if not code:
        meta["engine"] = None
        json.dump(meta, open(meta_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print("СУБТИТРОВ НЕТ. Метаданные: %s" % meta_path)
        print("Переходи на уровень 1 (ASR) – см. references/levels.md")
        sys.exit(2)

    headers = dict((info.get("http_headers") or {}))
    headers.setdefault("User-Agent", UA)
    headers["Referer"] = REFERER          # без него CDN отдаёт HTTP 400

    seen, raw = set(), None
    for v in variants:                     # дедуп побайтно одинаковых вариантов ВК
        try:
            body = http_get(v["url"], headers, ctx)
        except Exception as e:             # noqa: BLE001 — причина уходит в отчёт
            print("вариант не скачался: %s" % e)
            continue
        h = hashlib.sha256(body).hexdigest()
        if h in seen:
            continue
        seen.add(h)
        if raw is None:
            raw = body
    if raw is None:
        sys.exit("субтитры заявлены, но ни один вариант не скачался")

    text = raw.decode("utf-8", "replace")
    open(os.path.join(a.out, slug + ".vtt"), "w", encoding="utf-8").write(text)
    cues = parse_vtt(text)
    if not cues:
        sys.exit("субтитры скачались, но реплик не разобрано – проверь %s.vtt" % slug)

    words = sum(len(c["text"].split()) for c in cues)
    meta["engine"] = "субтитры ВК (%s)" % code
    meta["cue_count"] = len(cues)
    meta["word_count"] = words
    meta["coverage"] = [round(cues[0]["start"], 1), round(cues[-1]["end"], 1)]
    meta["coverage_hms"] = [hms(cues[0]["start"]), hms(cues[-1]["end"])]
    meta["subtitle_variants"] = {"заявлено": len(variants), "уникальных": len(seen)}

    json.dump(meta, open(meta_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump(cues, open(os.path.join(a.out, slug + ".cues.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    print("OK  %d %s, %d %s, вариантов %d (уникальных %d)"
          % (len(cues), plural(len(cues), "реплика", "реплики", "реплик"),
             words, plural(words, "слово", "слова", "слов"), len(variants), len(seen)))
    print("    покрытие %s … %s (%.0f–%.0f сек), длительность видео %s"
          % (hms(cues[0]["start"]), hms(cues[-1]["end"]),
             cues[0]["start"], cues[-1]["end"], hms(meta.get("duration"))))
    print("    %s / %s.cues.json" % (a.out, slug))


if __name__ == "__main__":
    main()
