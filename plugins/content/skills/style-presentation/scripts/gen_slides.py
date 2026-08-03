# -*- coding: utf-8 -*-
"""
gen_slides.py — генерация слайдов презентации в едином стиле через Genosai REST API.

Читает project.json (см. project.example.json), один раз заливает фотореференсы в S3,
ПАРАЛЛЕЛЬНО запускает генерацию всех слайдов (формат из aspect_ratio), опрашивает задачи
одновременно, переотправляет упавшие/просроченные (таймаут 5 мин на попытку), устойчив к
transient-ошибкам API (503/таймаут) и к сбою загрузки референса (перезалив + новая ссылка).

Запуск:
  python gen_slides.py path/to/project.json            # догенерит недостающие слайды
  python gen_slides.py path/to/project.json --force     # пересоздать все
  python gen_slides.py path/to/project.json 5 9          # только слайды 5 и 9
  python gen_slides.py path/to/project.json 5 9 --force  # пересоздать 5 и 9

Ключ API (sdk_...) ищется в порядке: переменная GENOSAI_API_KEY → поле "api_key_file" в
project.json → E:\\Claude\\genosai-cli\\api_key.txt.
"""
import os
import sys
import json
import time
import uuid
import mimetypes
import urllib.request
import urllib.error
import urllib.parse

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE_URL = "https://api.genosai.io"
POLL_TIMEOUT = 300          # 5 минут на попытку
MAX_ATTEMPTS = 8
# --- Троттлинг под rate limit Kie.ai (HTTP 429) ---
# Стратегия: НЕ отправлять залпом. Держим (1) равномерный интервал между createTask,
# (2) страховочный потолок 14 новых задач за скользящее окно 10 с, и (3) общий ограничитель
# темпа на ВСЕ HTTP-запросы (включая опрос статуса), чтобы суммарно не выходить за лимит.
SUBMIT_MAX = 14             # потолок: не более 14 НОВЫХ задач генерации
SUBMIT_WINDOW = 10.0        # за скользящее окно 10 секунд
SUBMIT_INTERVAL = 2.0       # равномерная пауза между отправками createTask (сек) → ~5 задач/10с
GLOBAL_MIN_GAP = 0.7        # минимальный интервал между любыми запросами к API (сек) → ~14/10с
_submit_times = []          # метки времени последних отправок createTask
_last_submit = [0.0]        # время последней отправки createTask
_last_api = [0.0]           # время последнего ЛЮБОГО запроса к API (для общего темпа)


def pace_api():
    """Общий ограничитель темпа: не чаще одного запроса раз в GLOBAL_MIN_GAP секунд."""
    gap = GLOBAL_MIN_GAP - (time.time() - _last_api[0])
    if gap > 0:
        time.sleep(gap)
    _last_api[0] = time.time()


def throttle_submit():
    """Равномерно распределяет отправку createTask: фиксированный интервал между запросами
    + страховочный потолок SUBMIT_MAX за SUBMIT_WINDOW. Так мы не ловим 429 из-за залпа."""
    # 1) равномерный интервал между отправками
    gap = SUBMIT_INTERVAL - (time.time() - _last_submit[0])
    if gap > 0:
        time.sleep(gap)
    # 2) страховочный потолок за скользящее окно
    while True:
        now = time.time()
        while _submit_times and now - _submit_times[0] >= SUBMIT_WINDOW:
            _submit_times.pop(0)
        if len(_submit_times) < SUBMIT_MAX:
            break
        wait = SUBMIT_WINDOW - (now - _submit_times[0]) + 0.05
        if wait > 0:
            time.sleep(wait)
    _submit_times.append(time.time())
    _last_submit[0] = time.time()
IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp")
# --- Авто-сжатие референсов ---
# ЛЮБОЙ референс (PNG/большой JPEG/любой размер) перед загрузкой автоматически пережимается
# один раз в компактный JPEG и кэшируется. Тяжёлые референсы модель подгружает ненадёжно
# (ошибка «не удалось загрузить референсное изображение») и грузятся медленно — лёгкие работают.
REF_MAX_PX = 1024           # максимальная сторона сжатого референса, px
REF_JPEG_QUALITY = 85       # качество JPEG
# Корень скилла (на уровень выше scripts/) — там лежит reference-sets/
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REFSETS_DIR = os.path.join(SKILL_DIR, "reference-sets")


def _find_secrets_env(start):
    """Ищет .secrets/genosai.env вверх по дереву от start (стандарт фабрики)."""
    d = os.path.abspath(start)
    for _ in range(6):
        p = os.path.join(d, ".secrets", "genosai.env")
        if os.path.exists(p):
            return p
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    return ""


SECRETS_ENV = _find_secrets_env(SKILL_DIR) or _find_secrets_env(os.getcwd())


def resolve_reference_set(name, cfg_dir):
    """Возвращает список путей к картинкам набора `name`.
    name может быть: именем папки внутри reference-sets/, либо абсолютным/относительным
    путём к папке с картинками. Регистр имени папки игнорируется."""
    candidates = []
    if os.path.isabs(name):
        candidates.append(name)
    else:
        candidates.append(os.path.join(REFSETS_DIR, name))
        candidates.append(os.path.join(cfg_dir, name))
    folder = next((c for c in candidates if os.path.isdir(c)), None)
    if folder is None and not os.path.isabs(name) and os.path.isdir(REFSETS_DIR):
        # регистронезависимый поиск папки набора
        low = name.lower()
        for d in os.listdir(REFSETS_DIR):
            if d.lower() == low and os.path.isdir(os.path.join(REFSETS_DIR, d)):
                folder = os.path.join(REFSETS_DIR, d)
                break
    if folder is None:
        sys.exit("Набор референсов не найден: '%s' (искал в %s)" % (name, REFSETS_DIR))
    imgs = sorted(
        os.path.join(folder, f) for f in os.listdir(folder)
        if f.lower().endswith(IMG_EXTS)
    )
    if not imgs:
        sys.exit("В наборе '%s' нет картинок (%s)" % (folder, ", ".join(IMG_EXTS)))
    return imgs


# ---------- ключ ----------

def _read_env_file(path):
    """Читает .secrets/genosai.env в формате `export GENOSAI_API_KEY=sdk_live_...`
    (кавычки и префикс export необязательны). Возвращает ключ или ''."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].strip()
                if "=" in line:
                    name, _, val = line.partition("=")
                    if name.strip() == "GENOSAI_API_KEY":
                        return val.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def get_key(cfg):
    # 1) переменная окружения (после `source .secrets/genosai.env`)
    k = os.environ.get("GENOSAI_API_KEY", "").strip()
    if k:
        return k
    # 2) явный api_key_file из project.json (сырой ключ одной строкой)
    kf = cfg.get("api_key_file")
    if kf and os.path.exists(kf):
        with open(kf, "r", encoding="utf-8") as f:
            v = f.read().strip()
        if v and not v.startswith("#"):
            return v
    # 3) стандарт фабрики: <корень фабрики>/.secrets/genosai.env
    for env_path in (SECRETS_ENV, os.path.join(os.getcwd(), ".secrets", "genosai.env")):
        v = _read_env_file(env_path)
        if v:
            return v
    sys.exit(
        "Нет API-ключа Genosai. Положи ключ в .secrets/genosai.env "
        "(export GENOSAI_API_KEY=sdk_live_...) в корне фабрики "
        "или задай переменную окружения GENOSAI_API_KEY."
    )


# ---------- REST ----------

def api_request(method, path, key, body=None, raw_body=None, content_type=None, timeout=120):
    pace_api()   # общий ограничитель темпа на все запросы к API
    url = BASE_URL + path
    headers = {"Authorization": "Bearer " + key}
    data = None
    if raw_body is not None:
        data = raw_body
        if content_type:
            headers["Content-Type"] = content_type
    elif body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def safe_request(method, path, key, body=None, retries=6):
    """Устойчивый вызов: ловит 429/503/таймаут/сетевые ошибки и ретраит. Возвращает dict или None.
    HTTP 429 (rate limit) не считается против обычного счётчика ретраев — ждём дольше и повторяем."""
    attempt = 0
    r429 = 0
    while attempt < retries and r429 < 10:
        try:
            return api_request(method, path, key, body=body)
        except urllib.error.HTTPError as e:
            if getattr(e, "code", None) == 429:
                r429 += 1
                print("   [429 rate limit] пауза 12с и повтор (429-попытка %d/10)" % r429)
                time.sleep(12)
                continue
            attempt += 1
            print("   [API ретрай %d/%d] HTTP %s" % (attempt, retries, getattr(e, "code", "?")))
            time.sleep(5)
        except Exception as e:
            attempt += 1
            print("   [API ретрай %d/%d] %s" % (attempt, retries, str(e)[:90]))
            time.sleep(5)
    return None


def _extract_url(data):
    for key in ("url", "media_url", "fileUrl", "image_url"):
        if isinstance(data, dict) and data.get(key):
            return data[key]
    d = data.get("data") if isinstance(data, dict) else None
    if isinstance(d, dict):
        for key in ("url", "media_url", "fileUrl", "image_url"):
            if d.get(key):
                return d[key]
    return None


def upload(path_to_file, key):
    """multipart-загрузка референса, возвращает URL (или None)."""
    for _ in range(4):
        try:
            with open(path_to_file, "rb") as f:
                file_data = f.read()
            filename = os.path.basename(path_to_file)
            mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            boundary = "----GenosaiBoundary" + uuid.uuid4().hex
            parts = [
                ("--" + boundary).encode(),
                ('Content-Disposition: form-data; name="file"; filename="%s"' % filename).encode(),
                ("Content-Type: %s" % mime).encode(),
                b"", file_data,
                ("--" + boundary + "--").encode(), b"",
            ]
            raw = b"\r\n".join(parts)
            ct = "multipart/form-data; boundary=" + boundary
            data = api_request("POST", "/v1/uploads", key, raw_body=raw, content_type=ct)
            url = _extract_url(data)
            if url:
                return url
        except Exception as e:
            print("   загрузка референса не удалась (%s), ретрай" % str(e)[:80])
        time.sleep(5)
    return None


# ---------- авто-сжатие референсов ----------

def compress_ref(path, cache_dir):
    """Сжимает референс в лёгкий JPEG (<=REF_MAX_PX по большей стороне, quality REF_JPEG_QUALITY)
    ОДИН РАЗ и кэширует в cache_dir. Возвращает путь к сжатой версии. Если Pillow не установлен
    или что-то пошло не так — возвращает исходный путь (генерация не ломается)."""
    try:
        from PIL import Image
    except Exception:
        print("   [Pillow не найден — референсы не сжимаются, использую как есть]")
        return path
    try:
        os.makedirs(cache_dir, exist_ok=True)
        base = os.path.splitext(os.path.basename(path))[0]
        out = os.path.join(cache_dir, "ref_" + base + ".jpg")
        # кэш: пересжимаем только если исходник новее кэша или кэша ещё нет
        if os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(path):
            return out
        im = Image.open(path).convert("RGB")
        im.thumbnail((REF_MAX_PX, REF_MAX_PX))
        im.save(out, "JPEG", quality=REF_JPEG_QUALITY)
        kb = os.path.getsize(out) // 1024
        print("   референс сжат: %s → %s (%d KB)" % (os.path.basename(path), os.path.basename(out), kb))
        return out
    except Exception as e:
        print("   [сжатие референса не удалось: %s — использую исходник]" % str(e)[:80])
        return path


# ---------- генерация ----------

def slide_path(out_dir, num):
    return os.path.join(out_dir, "slide_%02d.png" % num)


def download_result(out_dir, num, url):
    out = slide_path(out_dir, num)
    for _ in range(4):
        try:
            with urllib.request.urlopen(url, timeout=120) as r, open(out, "wb") as f:
                f.write(r.read())
            print("[слайд %d] сохранено: %s" % (num, out))
            return True
        except Exception as e:
            print("[слайд %d] не скачалось (%s), ретрай" % (num, str(e)[:80]))
            time.sleep(4)
    print("[слайд %d] !! не удалось скачать. URL: %s" % (num, url))
    return False


class Generator:
    def __init__(self, cfg, key):
        self.key = key
        self.model = cfg.get("model", "chatgpt-image-2")
        self.ar = cfg.get("aspect_ratio", "16:9")
        self.style = cfg.get("style", "")
        self.out_dir = cfg["out_dir"]
        self.cfg_dir = cfg["_cfg_dir"]
        self.ref_files = []
        # 1) набор референсов по имени (reference_set), если задан
        rs = cfg.get("reference_set")
        if rs:
            self.ref_files.extend(resolve_reference_set(rs, self.cfg_dir))
            print("Набор референсов '%s': %d картинок" % (rs, len(self.ref_files)))
        # 2) дополнительные одиночные файлы из references (если есть)
        for r in cfg.get("references", []):
            p = r if os.path.isabs(r) else os.path.join(self.cfg_dir, r)
            self.ref_files.append(p)
        # 3) авто-сжатие: любой референс один раз пережимаем в лёгкий JPEG (кэш рядом с out_dir)
        if self.ref_files:
            cache = os.path.join(self.out_dir, "_ref_cache")
            self.ref_files = [compress_ref(p, cache) if os.path.exists(p) else p
                              for p in self.ref_files]
        self.ref_urls = []
        self._last_ref_refresh = 0.0

    def upload_refs(self):
        urls = []
        for p in self.ref_files:
            if not os.path.exists(p):
                sys.exit("Референс не найден: " + p)
            u = upload(p, self.key)
            if not u:
                sys.exit("Не удалось залить референс: " + p)
            print("   референс залит: %s" % u)
            urls.append(u)
        self.ref_urls = urls

    def maybe_refresh_refs(self):
        if time.time() - self._last_ref_refresh < 25:
            return
        new = []
        for p in self.ref_files:
            u = upload(p, self.key)
            if u:
                new.append(u)
        self._last_ref_refresh = time.time()
        if new:
            self.ref_urls = new
            print("   референсы перезалиты (%d шт.)" % len(new))

    def submit(self, slide):
        inp = {"prompt": self.style + slide["image_prompt"], "aspect_ratio": self.ar}
        if self.ref_urls:
            inp["image_urls"] = list(self.ref_urls)
        throttle_submit()   # соблюдаем лимит: не больше SUBMIT_MAX задач за SUBMIT_WINDOW сек
        resp = safe_request("POST", "/v1/createTask", self.key,
                            body={"model": self.model, "input": inp})
        if not resp:
            return None
        return (resp.get("data") or {}).get("taskId") or resp.get("taskId")

    def run(self, slides):
        # 1) разом отправляем все
        state = {}
        for s in slides:
            tid = self.submit(s)
            state[s["num"]] = {"slide": s, "task": tid, "start": time.time(), "att": 1}
            print("[слайд %d] отправлено (task=%s)" % (s["num"], tid))
        # 2) одновременный опрос
        while state:
            for num in list(state.keys()):
                st = state[num]
                if not st["task"]:
                    tid = self.submit(st["slide"])
                    if tid:
                        st["task"] = tid
                        st["start"] = time.time()
                        print("[слайд %d] переотправлено (попытка %d)" % (num, st["att"]))
                    continue
                info = safe_request("GET", "/v1/taskInfo?taskId=" + urllib.parse.quote(st["task"]), self.key)
                if not info:
                    continue
                d = info.get("data", info)
                status = d.get("status")
                if status == "succeeded":
                    res = d.get("result") or {}
                    url = res.get("media_url") or (res.get("media_urls") or [None])[0]
                    if url:
                        download_result(self.out_dir, num, url)
                    else:
                        print("[слайд %d] succeeded, но URL не найден: %s" % (num, str(res)[:200]))
                    del state[num]
                    print("   >>> осталось слайдов: %d" % len(state))
                elif status in ("failed", "error", "canceled"):
                    msg = (d.get("message", "") or "")
                    print("[слайд %d] ПРОВАЛ: %s (%s)" % (num, status, msg))
                    if "референс" in msg.lower() or "reference" in msg.lower():
                        self.maybe_refresh_refs()
                    self._retry_or_drop(state, num)
                else:
                    if time.time() - st["start"] > POLL_TIMEOUT:
                        print("[слайд %d] 5 мин вышло — повторная отправка" % num)
                        self._retry_or_drop(state, num)
            if state:
                time.sleep(4)

    def _retry_or_drop(self, state, num):
        st = state[num]
        if st["att"] < MAX_ATTEMPTS:
            st["att"] += 1
            st["task"] = None
        else:
            print("[слайд %d] !! отказ после %d попыток" % (num, MAX_ATTEMPTS))
            del state[num]


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit("Использование: python gen_slides.py project.json [номера...] [--force]")
    cfg_path = args[0]
    force = "--force" in args
    only = [int(a) for a in args[1:] if a.isdigit()]

    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["_cfg_dir"] = os.path.dirname(os.path.abspath(cfg_path))
    out_dir = cfg["out_dir"]
    if not os.path.exists(out_dir):
        os.makedirs(out_dir)

    key = get_key(cfg)
    gen = Generator(cfg, key)

    targets = []
    for s in cfg["slides"]:
        if "image_prompt" not in s:
            continue
        if only and s["num"] not in only:
            continue
        if (not force) and os.path.exists(slide_path(out_dir, s["num"])):
            print("== Слайд %d уже есть, пропускаю ==" % s["num"])
            continue
        targets.append(s)

    if not targets:
        print("=== Нечего генерировать ===")
        return

    print("Заливаю референсы один раз (%d шт.)..." % len(gen.ref_files))
    gen.upload_refs()
    gen._last_ref_refresh = time.time()
    print("Параллельная генерация слайдов: %s" % ", ".join(str(s["num"]) for s in targets))
    gen.run(targets)
    print("=== Готово ===")


if __name__ == "__main__":
    main()
