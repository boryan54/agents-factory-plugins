#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Конвейер A/B-креативов для РСЯ Яндекс Директа на базе Genosai (chatgpt-image-2).

    python3 rsya_ab.py <config.json> estimate                 # смета в кредитах – показать и получить «да» ДО gen
    python3 rsya_ab.py <config.json> gen                      # генерация всех вариантов во всех пропорциях (resume по taskId)
    python3 rsya_ab.py <config.json> upload [--apply]         # заливка картинок + клоны объявлений (без --apply – план)
    python3 rsya_ab.py <config.json> report [--since ГГГГ-ММ-ДД] [--min-impr 3000]
    python3 rsya_ab.py <config.json> check                    # что реально стоит в группах: картинки/типы/Associated

Формат config.json (см. references/rsya-creatives.md, раздел 5):
{
  "client_login": "client-login",
  "name_prefix": "client-2026-10",
  "workdir": "rsya-2026-10",                  # относительно config.json; сюда падают out/, images.json, uploaded.json
  "model": "chatgpt-image-2", "resolution": "1K",
  "aspects": ["1:1", "16:9", "3:4"],
  "style": "photorealistic, natural daylight, ... (единый стиль-блок клиента)",
  "goals": ["ID_ЦЕЛИ_МЕТРИКИ"],
  "concepts": [
    {"id": "personal", "ad_group_id": 123456789, "control_ad_id": 1234567890123,
     "variants": [
       {"id": "A", "scene": "female trainer correcting a client's squat in a dark neon gym",
        "caption": ["−50 %", "Первая тренировка 1 600 ₽"]},
       {"id": "B", "scene": "...", "caption": ["..."], "replace_ad_id": 1234567890124}
     ]},
    {"id": "retarget", "ad_group_id": 123456789, "control_ad_id": 1234567890123,
     "combine_into": 1234567890123,          # все варианты концепта → в ОДНО объявление (до 5 картинок, ротация бандитом)
     "variants": [{"id": "A", "scene": "...", "caption": ["..."]},
                  {"id": "B", "scene": "...", "caption": ["..."], "aspects": ["1:1", "16:9"]}]}
  ]
}
Схемы A/B: (1) отдельные объявления – variants без combine_into: клон контроля с другими AdImageHashes,
CTR по AdId через Reports API (честный тест, лимит 3 объявления в группе); (2) combine_into – все картинки в одно
объявление, Директ ротирует сам, статистику по картинке смотреть в Мастере отчётов (срез «Изображение»), в API её нет.
Ключи читаются из ~/.secrets/yandex-direct.env и $GENOSAI_API_KEY (или ~/.secrets/genosai.env). В файлы не пишутся.
"""
import base64, csv, io, json, math, os, re, sys, threading, time, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

DIRECT = 'https://api.direct.yandex.com/json/v5/'
# Комбинаторные объявления (ResponsiveAd) в add/update принимает ТОЛЬКО версия v501 (в v5 – ошибка 3500 «используйте v501»)
DIRECT_V501 = 'https://api.direct.yandex.com/json/v501/'
GENOSAI = 'https://api.genosai.io'
COMPOSITION = ('single clear subject centered, keep every important element and all text within the central '
               '75% of the frame, nothing important near the edges, simple uncluttered background, high contrast, '
               'instantly readable as a tiny thumbnail')
NO_TEXT = 'no text, no letters, no logos, no watermarks, no interface elements, no buttons, no play icons'
ASPECT_TAG = {'1:1': '1x1', '16:9': '16x9', '3:4': '3x4', '4:3': '4x3', '4:5': '4x5', '9:16': '9x16'}


# ---------------------------------------------------------------- ключи
def _env(path, key):
    p = os.path.expanduser(path)
    if not os.path.exists(p):
        return None
    for line in open(p):
        m = re.match(r'\s*(?:export\s+)?' + key + r'\s*=\s*["\']?([^"\'\s]+)', line)
        if m:
            return m.group(1)
    return None


def direct_token():
    t = os.environ.get('YANDEX_DIRECT_TOKEN') or _env('~/.secrets/yandex-direct.env', 'YANDEX_DIRECT_TOKEN')
    if not t:
        raise SystemExit('нет YANDEX_DIRECT_TOKEN (~/.secrets/yandex-direct.env)')
    return t


def genosai_key():
    k = os.environ.get('GENOSAI_API_KEY') or _env('~/.secrets/genosai.env', 'GENOSAI_API_KEY')
    if not k:
        raise SystemExit('нет GENOSAI_API_KEY (~/.secrets/genosai.env)')
    return k


# ---------------------------------------------------------------- конфиг
class Cfg:
    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.c = json.load(open(self.path))
        self.login = self.c['client_login']
        self.prefix = self.c.get('name_prefix', 'rsya')
        self.workdir = os.path.join(os.path.dirname(self.path), self.c.get('workdir', 'rsya-creatives'))
        self.out = os.path.join(self.workdir, 'out')
        os.makedirs(self.out, exist_ok=True)
        self.model = self.c.get('model', 'chatgpt-image-2')
        self.resolution = self.c.get('resolution', '1K')
        self.aspects = self.c.get('aspects', ['1:1', '16:9', '3:4'])
        self.style = self.c.get('style', '')
        self.goals = [str(g) for g in self.c.get('goals', [])]
        self.concepts = self.c['concepts']
        if '1:1' not in self.aspects:
            print('!! ВНИМАНИЕ: без 1:1 лента режет 16:9 – квадрат обязателен', file=sys.stderr)

    def jobs(self):
        """(key, concept, variant, aspect, prompt) для каждой картинки."""
        for con in self.concepts:
            for v in con['variants']:
                cap = v.get('caption') or []
                if cap:
                    quoted = ' and '.join('«' + c + '»' for c in cap)
                    text_block = (f'Overlay large, clean, perfectly legible Russian text placed inside the central safe area: '
                                  f'{quoted}. Render every Russian word EXACTLY as written, spelled perfectly, no other text, '
                                  f'text and logo together occupy less than 20% of the image, no logos, no watermarks')
                else:
                    text_block = NO_TEXT
                prompt = ', '.join(x for x in [v['scene'], COMPOSITION, self.style, text_block] if x)
                for a in v.get('aspects') or self.aspects:
                    key = f"{con['id']}-{v['id']}-{ASPECT_TAG.get(a, a.replace(':', 'x'))}"
                    yield key, con, v, a, prompt

    def state_path(self, name):
        return os.path.join(self.workdir, name)

    def load(self, name, default):
        p = self.state_path(name)
        return json.load(open(p)) if os.path.exists(p) else default

    def save(self, name, data):
        json.dump(data, open(self.state_path(name), 'w'), ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- Директ
class Direct:
    def __init__(self, login):
        self.h = {'Authorization': 'Bearer ' + direct_token(), 'Client-Login': login, 'Accept-Language': 'ru',
                  'Content-Type': 'application/json; charset=utf-8'}
        self.login = login

    def call(self, svc, method, params):
        base = DIRECT_V501 if svc == 'ads' else DIRECT
        req = urllib.request.Request(base + svc, data=json.dumps({'method': method, 'params': params},
                                                                   ensure_ascii=False).encode(), headers=self.h)
        r = json.loads(urllib.request.urlopen(req, timeout=120).read().decode())
        if 'error' in r:
            raise SystemExit(f"API {svc}.{method}: {r['error']}")
        return r['result']

    def ads_in_groups(self, gids):
        return self.call('ads', 'get', {
            'SelectionCriteria': {'AdGroupIds': list(gids)},
            'FieldNames': ['Id', 'CampaignId', 'AdGroupId', 'Type', 'State', 'Status', 'StatusClarification'],
            'ResponsiveAdFieldNames': ['Titles', 'Texts', 'Href', 'BusinessId', 'SitelinkSetId', 'DisplayUrlPath',
                                       'AdImages', 'VideoExtensions', 'AdExtensions', 'Carousel'],
            'TextAdFieldNames': ['Title', 'Text', 'Href', 'AdImageHash']}).get('Ads', [])

    def images(self):
        return {i['AdImageHash']: i for i in self.call('adimages', 'get', {
            'SelectionCriteria': {}, 'FieldNames': ['AdImageHash', 'Name', 'Type', 'Subtype', 'Associated',
                                                    'PreviewUrl']})['AdImages']}

    def report(self, campaign_ids, date_from, date_to, goals):
        h = dict(self.h, processingMode='auto', returnMoneyInMicros='false', skipReportHeader='true',
                 skipReportSummary='true')
        params = {'SelectionCriteria': {'DateFrom': date_from, 'DateTo': date_to, 'Filter': [
            {'Field': 'AdNetworkType', 'Operator': 'EQUALS', 'Values': ['AD_NETWORK']},
            {'Field': 'CampaignId', 'Operator': 'IN', 'Values': [str(c) for c in campaign_ids]}]},
            'FieldNames': ['AdGroupId', 'AdId', 'Impressions', 'Clicks', 'Cost'] + (['Conversions'] if goals else []),
            'ReportName': f'rsya_ab_{int(time.time())}', 'ReportType': 'CUSTOM_REPORT',
            'DateRangeType': 'CUSTOM_DATE', 'Format': 'TSV', 'IncludeVAT': 'YES'}
        if goals:
            params['Goals'] = goals
            params['AttributionModels'] = ['LYDC']
        req = urllib.request.Request(DIRECT + 'reports', data=json.dumps({'params': params}).encode(), headers=h)
        for _ in range(30):
            try:
                r = urllib.request.urlopen(req, timeout=120)
                if r.status in (201, 202):
                    time.sleep(5); continue
                return list(csv.DictReader(io.StringIO(r.read().decode()), delimiter='\t'))
            except urllib.error.HTTPError as e:
                if e.code in (201, 202):
                    time.sleep(5); continue
                raise SystemExit(f'reports {e.code}: {e.read().decode()[:400]}')
        raise SystemExit('отчёт не готов за 150 с')


# ---------------------------------------------------------------- Genosai
class Genosai:
    def __init__(self):
        self.h = {'Authorization': 'Bearer ' + genosai_key(), 'Content-Type': 'application/json'}
        self.lock = threading.Lock()

    def req(self, method, path, body=None, params=None):
        url = GENOSAI + path + ('?' + urllib.parse.urlencode(params) if params else '')
        data = json.dumps(body, ensure_ascii=False).encode() if body else None
        for attempt in range(12):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, data=data, method=method, headers=self.h),
                                            timeout=90) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                body_txt = e.read().decode(errors='ignore')
                if e.code == 429:
                    wait = e.headers.get('Retry-After')
                    m = re.search(r'(\d+)\s*sec', body_txt)
                    wait = int(wait) if wait and wait.isdigit() else (int(m.group(1)) if m else 20)
                    print(f'   429 → жду {wait} с', flush=True); time.sleep(wait + 1); continue
                if (e.code >= 500 or (e.code == 400 and 'generation failed' in body_txt.lower())) and attempt < 11:
                    print(f'   {e.code} на {path}, повтор через {5 * (attempt + 1)} с', flush=True)
                    time.sleep(5 * (attempt + 1)); continue
                raise RuntimeError(f'Genosai {e.code} {path}: {body_txt[:400]}')
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < 11:
                    time.sleep(5); continue
                raise
        raise RuntimeError('Genosai: исчерпаны попытки')

    def create(self, prompt, aspect, model, resolution):
        inp = {'prompt': prompt, 'aspect_ratio': aspect}
        if resolution:
            inp['resolution'] = resolution
        d = self.req('POST', '/v1/createTask', {'model': model, 'input': inp})['data']
        return d['taskId']

    def info(self, tid):
        return self.req('GET', '/v1/taskInfo', params={'taskId': tid})['data']


# ---------------------------------------------------------------- команды
RATIO = {'1:1': 1.0, '16:9': 16 / 9, '3:4': 3 / 4, '4:3': 4 / 3, '4:5': 4 / 5, '9:16': 9 / 16}


def fix_aspect(path, aspect):
    """Модель иногда отдаёт не точную пропорцию (1344×768 вместо 16:9) – Директ такое отвергает (5004).
    Центрированный кроп до точной пропорции; правим только если отклонение > 0,5 %."""
    r = RATIO.get(aspect)
    if not r:
        return
    try:
        from PIL import Image
    except ImportError:
        return
    im = Image.open(path); w, h = im.size
    if abs(w / h - r) / r <= 0.005:
        return
    if w / h > r:
        nw = int(round(h * r)); box = ((w - nw) // 2, 0, (w - nw) // 2 + nw, h)
    else:
        nh = int(round(w / r)); box = (0, (h - nh) // 2, w, (h - nh) // 2 + nh)
    im.crop(box).save(path)
    print(f'   кроп {os.path.basename(path)}: {w}×{h} → {box[2]-box[0]}×{box[3]-box[1]} (точная {aspect})', flush=True)


def cmd_estimate(cfg):
    jobs = list(cfg.jobs())
    price = {'chatgpt-image-2': {'1K': 6, '2K': 10, '4K': 16}, 'chatgpt-image-2-5': {'1K': 6, '2K': 10, '4K': 16}}
    per = price.get(cfg.model, {}).get(cfg.resolution)
    print(f'Модель {cfg.model} {cfg.resolution}; пропорции {cfg.aspects}; картинок: {len(jobs)}')
    if cfg.model not in ('chatgpt-image-2', 'chatgpt-image-2-5'):
        print('!! ПРАВИЛО: креативы РСЯ – только chatgpt-image-2. Смени model в конфиге.')
    done = sum(os.path.exists(os.path.join(cfg.out, k + '.png')) for k, *_ in jobs)
    print(f'уже сгенерировано: {done}, к генерации: {len(jobs) - done}')
    if per:
        print(f'СМЕТА: {(len(jobs) - done)} × {per} кр = {(len(jobs) - done) * per} кредитов Genosai')
    else:
        print('цена модели неизвестна скрипту – сверь по GET /v1/models (cost_credits_default)')
    print('\nПромпты (проверь подписи побуквенно – они уйдут в модель как есть):')
    seen = set()
    for key, con, v, a, prompt in jobs:
        vid = f"{con['id']}-{v['id']}"
        if vid in seen:
            continue
        seen.add(vid)
        print(f"\n[{vid}] группа {con['ad_group_id']}, контроль {con['control_ad_id']}"
              + (f", ЗАМЕНА в объявлении {v['replace_ad_id']}" if v.get('replace_ad_id') else ''))
        print('  подпись:', v.get('caption') or '– (без текста, вариант Ч)')
        print('  промпт:', prompt)
    print('\n⛔ Генерацию запускать только после явного «да» на эту смету.')


def cmd_gen(cfg):
    if cfg.model not in ('chatgpt-image-2', 'chatgpt-image-2-5'):
        raise SystemExit('ПРАВИЛО: креативы РСЯ генерируются только chatgpt-image-2')
    g = Genosai()
    st = cfg.load('gen_state.json', {})
    todo = [(k, a, p) for k, con, v, a, p in cfg.jobs() if not os.path.exists(os.path.join(cfg.out, k + '.png'))]
    if not todo:
        print('всё уже сгенерировано'); return
    lock = threading.Lock()

    def worker(key, aspect, prompt):
        try:
            _worker(key, aspect, prompt)
        except Exception as e:  # одна упавшая задача не должна убивать остальные
            print(f'!! {key}: {e}', flush=True)

    def _worker(key, aspect, prompt):
        tid = (st.get(key) or {}).get('taskId')
        if not tid:
            tid = g.create(prompt, aspect, cfg.model, cfg.resolution)
            with lock:
                st[key] = {'taskId': tid, 'started': time.strftime('%Y-%m-%d %H:%M:%S'), 'aspect': aspect}
                cfg.save('gen_state.json', st)
            print(f'   → {key} taskId {tid}', flush=True)
        t0 = time.time()
        while True:
            time.sleep(8)
            d = g.info(tid)
            s = d.get('status')
            if s == 'succeeded':
                urls = ((d.get('result') or {}).get('media_urls')) or []
                if not urls:
                    print(f'!! {key}: succeeded без media_urls', flush=True); return
                dest = os.path.join(cfg.out, key + '.png')
                urllib.request.urlretrieve(urls[0], dest)
                fix_aspect(dest, aspect)
                print(f'   ✓ {key} ({int(time.time() - t0)} с) cost={d.get("cost")}', flush=True); return
            if s in ('failed', 'error', 'cancelled'):
                print(f'!! {key}: {s} – {d.get("message") or str(d)[:200]} – taskId сброшен; поправь сюжет и перезапусти gen', flush=True)
                with lock:
                    st.pop(key, None); cfg.save('gen_state.json', st)
                return
            if time.time() - t0 > 900:
                print(f'!! {key}: 15 мин без результата, оставляю taskId для resume', flush=True); return

    print(f'к генерации {len(todo)} картинок; диспетчеризация по одной в секунду, пул = {len(todo)}')
    with ThreadPoolExecutor(max_workers=len(todo)) as ex:
        futs = []
        for i, (k, a, p) in enumerate(todo):
            if i:
                time.sleep(1)
            futs.append(ex.submit(worker, k, a, p))
        for f in futs:
            f.result()
    missing = [k for k, a, p in todo if not os.path.exists(os.path.join(cfg.out, k + '.png'))]
    print('готово' if not missing else f'НЕ сгенерированы: {missing} – запусти gen ещё раз')


def _control(direct, con):
    ads = {a['Id']: a for a in direct.ads_in_groups([con['ad_group_id']])}
    ctl = ads.get(con['control_ad_id'])
    if not ctl:
        raise SystemExit(f"контрольное объявление {con['control_ad_id']} не найдено в группе {con['ad_group_id']}")
    if ctl['Type'] != 'RESPONSIVE_AD':
        raise SystemExit(f"контроль {ctl['Id']} типа {ctl['Type']} – скрипт клонирует только RESPONSIVE_AD "
                         f"(с 14.07.2026 все ТГО переведены в комбинаторные; проверь объявление)")
    live = [a for a in ads.values() if a['State'] != 'ARCHIVED']
    return ctl, live


def _clone_body(ctl, hashes):
    ra = ctl['ResponsiveAd']
    body = {'Titles': [t['Title'] for t in ra['Titles']], 'Texts': [t['Text'] for t in ra['Texts']],
            'AdImageHashes': hashes}
    for k in ('Href', 'BusinessId', 'SitelinkSetId', 'DisplayUrlPath'):
        if ra.get(k) not in (None, ''):
            body[k] = ra[k]
    ext = [e['AdExtensionId'] for e in (ra.get('AdExtensions') or [])]
    if ext:
        body['AdExtensionIds'] = ext
    vids = [v['Id'] for v in ((ra.get('VideoExtensions') or {}).get('Items') or []) if v.get('Id')]
    if vids:
        body['VideoExtensionIds'] = vids
    return body


def cmd_upload(cfg, apply):
    d = Direct(cfg.login)
    hashes = cfg.load('images.json', {})
    jobs = list(cfg.jobs())
    # 1. картинки
    to_up = []
    for key, con, v, a, p in jobs:
        f = os.path.join(cfg.out, key + '.png')
        if not os.path.exists(f):
            if apply:
                raise SystemExit(f'нет файла {f} – сначала gen')
            print(f'   (нет файла {key}.png – сначала gen; план ниже условный)')
            continue
        if key not in hashes:
            fix_aspect(f, a)
            to_up.append((key, f))
    print(f'картинок всего {len(jobs)}, уже залито {len(hashes)}, к заливке {len(to_up)}')
    for key, f in to_up:
        print(f'   {key}: {os.path.getsize(f) // 1024} КБ  Name={cfg.prefix}-{key}')
    if apply and to_up:
        for i in range(0, len(to_up), 10):
            chunk = to_up[i:i + 10]
            res = d.call('adimages', 'add', {'AdImages': [
                {'Name': f'{cfg.prefix}-{k}', 'ImageData': base64.b64encode(open(f, 'rb').read()).decode()}
                for k, f in chunk]})
            for (k, f), item in zip(chunk, res['AddResults']):
                if 'AdImageHash' not in item:
                    print(f'!! {k}: {item}'); continue
                hashes[k] = item['AdImageHash']
                print(f'   img {k} → {item["AdImageHash"]}')
            cfg.save('images.json', hashes)
    # 2. объявления
    up = cfg.load('uploaded.json', {'ads': {}, 'controls': {}})
    plan = []
    for con in cfg.concepts:
        ctl, live = _control(d, con)
        up['controls'][con['id']] = {'ad_id': ctl['Id'], 'campaign_id': ctl['CampaignId'], 'ad_group_id': ctl['AdGroupId']}
        free = 3 - len(live)
        if con.get('combine_into'):
            hs = []
            for v in con['variants']:
                for a in v.get('aspects') or cfg.aspects:
                    k = f"{con['id']}-{v['id']}-{ASPECT_TAG.get(a, a.replace(':', 'x'))}"
                    hs.append(hashes.get(k) or ('<hash>' if not apply else None))
            if any(h is None for h in hs):
                raise SystemExit(f"{con['id']}: не все картинки залиты")
            if len(hs) > 5:
                raise SystemExit(f"{con['id']}: {len(hs)} картинок, а в одно объявление входит максимум 5 – сократи aspects у вариантов")
            plan.append((con['id'], con, {'replace_ad_id': con['combine_into']}, ctl, hs, 'update'))
            print(f"   {con['id']}: {len(hs)} картинок → в объявление {con['combine_into']} (ротация внутри объявления) {hs}")
            continue
        for v in con['variants']:
            vid = f"{con['id']}-{v['id']}"
            if vid in up['ads']:
                print(f'   {vid}: уже залит как {up["ads"][vid]}'); continue
            hs = [hashes.get(f"{vid}-{ASPECT_TAG.get(a, a.replace(':', 'x'))}") for a in cfg.aspects]
            if not apply:
                hs = [h or '<hash>' for h in hs]
            if any(h is None for h in hs):
                raise SystemExit(f'{vid}: не все картинки залиты')
            mode = 'update' if v.get('replace_ad_id') else 'add'
            if mode == 'add':
                free -= 1
            plan.append((vid, con, v, ctl, hs, mode))
            print(f"   {vid}: {mode} в группе {con['ad_group_id']} (клон {ctl['Id']}, «{ctl['ResponsiveAd']['Titles'][0]['Title'][:40]}»), "
                  f"картинки {hs}" + (f", замена в {v['replace_ad_id']}" if mode == 'update' else ''))
        if free < 0:
            print(f"!! группа {con['ad_group_id']}: неархивных объявлений {len(live)}, лимит 3 – не хватает {-free} слот(ов). "
                  f"Укажи replace_ad_id у варианта (ads.update картинок в проигравшем) или заархивируй лишнее.")
    if not apply:
        print('\nDRY-RUN. Залить: upload --apply'); return
    adds = [(vid, con, v, ctl, hs) for vid, con, v, ctl, hs, m in plan if m == 'add']
    upds = [(vid, con, v, ctl, hs) for vid, con, v, ctl, hs, m in plan if m == 'update']
    new_ids = []
    if adds:
        res = d.call('ads', 'add', {'Ads': [{'AdGroupId': con['ad_group_id'], 'ResponsiveAd': _clone_body(ctl, hs)}
                                            for vid, con, v, ctl, hs in adds]})
        for (vid, *_), item in zip(adds, res['AddResults']):
            if 'Id' not in item:
                print(f'!! {vid}: {item}'); continue
            up['ads'][vid] = item['Id']; new_ids.append(item['Id'])
            print(f'   ad {vid} → {item["Id"]}')
    if upds:
        # в ads.update nillable-массив передаётся обёрткой {"Items": [...]} (в ads.add – плоским списком)
        res = d.call('ads', 'update', {'Ads': [{'Id': v['replace_ad_id'], 'ResponsiveAd': {'AdImageHashes': {'Items': hs}}}
                                               for vid, con, v, ctl, hs in upds]})
        for (vid, con, v, *_), item in zip(upds, res['UpdateResults']):
            if item.get('Errors'):
                print(f'!! {vid}: {item["Errors"]}'); continue
            up['ads'][vid] = v['replace_ad_id']
            print(f'   ad {vid} → картинки заменены в {v["replace_ad_id"]}')
            d.call('ads', 'resume', {'SelectionCriteria': {'Ids': [v['replace_ad_id']]}})
    if new_ids:
        d.call('ads', 'moderate', {'SelectionCriteria': {'Ids': new_ids}})
        print(f'на модерацию: {len(new_ids)}')
    up.setdefault('ts', time.strftime('%Y-%m-%d %H:%M'))
    cfg.save('uploaded.json', up)
    print('карта сохранена в uploaded.json. Через 1–2 дня проверь: check')


def _ztest(c1, n1, c2, n2):
    if min(n1, n2) == 0:
        return 1.0
    p = (c1 + c2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    return 1.0 if se == 0 else math.erfc(abs(c1 / n1 - c2 / n2) / se / math.sqrt(2))


def cmd_report(cfg, since, min_impr):
    d = Direct(cfg.login)
    up = cfg.load('uploaded.json', {'ads': {}, 'controls': {}})
    since = since or (up.get('ts') or '')[:10]
    if not since:
        raise SystemExit('не знаю дату старта: --since ГГГГ-ММ-ДД')
    gids = [c['ad_group_id'] for c in cfg.concepts]
    ads = {a['Id']: a for a in d.ads_in_groups(gids)}
    camps = sorted({a['CampaignId'] for a in ads.values()})
    rows = d.report(camps, since, time.strftime('%Y-%m-%d'), cfg.goals)
    stat = {}
    for r in rows:
        aid = int(r['AdId'])
        s = stat.setdefault(aid, {'imp': 0, 'clk': 0, 'cost': 0.0, 'conv': 0})
        s['imp'] += int(r['Impressions']); s['clk'] += int(r['Clicks']); s['cost'] += float(r['Cost'])
        for k, v in r.items():
            if k.startswith('Conversions') and v not in ('--', ''):
                s['conv'] += int(v)
    name_of = {aid: vid for vid, aid in up['ads'].items()}
    print(f'РСЯ с {since}, порог {min_impr} показов на вариант, цели {cfg.goals or "–"}\n')
    for con in cfg.concepts:
        ctl_id = con['control_ad_id']
        c = stat.get(ctl_id, {'imp': 0, 'clk': 0, 'cost': 0, 'conv': 0})
        print(f"== {con['id']} (группа {con['ad_group_id']})")
        print(f"   {'вариант':<14}{'ad':<21}{'показы':>8}{'клики':>7}{'CTR':>8}{'расход':>9}{'конв':>6}{'p':>8}  вердикт")
        for aid, a in sorted(ads.items()):
            if a['AdGroupId'] != con['ad_group_id']:
                continue
            s = stat.get(aid, {'imp': 0, 'clk': 0, 'cost': 0, 'conv': 0})
            label = 'КОНТРОЛЬ' if aid == ctl_id else name_of.get(aid, f"({a['State'].lower()})")
            ctr = s['clk'] / s['imp'] * 100 if s['imp'] else 0
            p = _ztest(s['clk'], s['imp'], c['clk'], c['imp']) if aid != ctl_id else 1
            if aid == ctl_id:
                verdict = ''
            elif s['imp'] < min_impr or c['imp'] < min_impr:
                verdict = 'мало данных'
            elif p >= 0.05:
                verdict = 'без разницы'
            else:
                verdict = 'ЛУЧШЕ контроля' if ctr > (c['clk'] / c['imp'] * 100 if c['imp'] else 0) else 'ХУЖЕ контроля'
            print(f"   {label:<14}{aid:<21}{s['imp']:>8}{s['clk']:>7}{ctr:>7.2f}%{s['cost']:>9.0f}{s['conv']:>6}{p:>8.3f}  {verdict}")
        print('   решение по CTR при ≥ порога и p < 0,05; финальный судья – конверсии/CPA (≥ 30 конверсий или 14 дней)\n')


def cmd_check(cfg):
    d = Direct(cfg.login)
    imgs = d.images()
    for con in cfg.concepts:
        print(f"== {con['id']} (группа {con['ad_group_id']})")
        for a in d.ads_in_groups([con['ad_group_id']]):
            items = ((a.get('ResponsiveAd') or {}).get('AdImages') or {}).get('Items') or []
            types = [f"{imgs.get(i['ImageHash'], {}).get('Type', '?')}:{imgs.get(i['ImageHash'], {}).get('Name', '?')}" for i in items]
            flag = '' if items else '  !! БЕЗ КАРТИНКИ'
            if items and not any(t.startswith('REGULAR') for t in types):
                flag = '  !! нет квадрата/вертикали (только WIDE)'
            if items and not any(t.startswith('WIDE') for t in types):
                flag += '  !! нет 16:9'
            title = ((a.get('ResponsiveAd') or {}).get('Titles') or [{}])[0].get('Title', '')
            print(f"   {a['Id']} {a['State']:9}{a['Status']:11}{a['Type']:14}{types}{flag} | {title[:45]}")
    orphans = [i['Name'] for i in imgs.values() if i.get('Associated') == 'NO' and i.get('Name', '').startswith(cfg.prefix)]
    if orphans:
        print(f'\n!! залитые, но не привязанные картинки ({cfg.prefix}-*): {orphans}')


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    cfg = Cfg(sys.argv[1]); cmd = sys.argv[2]; args = sys.argv[3:]
    if cmd == 'estimate':
        cmd_estimate(cfg)
    elif cmd == 'gen':
        cmd_gen(cfg)
    elif cmd == 'upload':
        cmd_upload(cfg, '--apply' in args)
    elif cmd == 'report':
        since = args[args.index('--since') + 1] if '--since' in args else None
        mi = int(args[args.index('--min-impr') + 1]) if '--min-impr' in args else 3000
        cmd_report(cfg, since, mi)
    elif cmd == 'check':
        cmd_check(cfg)
    else:
        raise SystemExit(__doc__)


if __name__ == '__main__':
    main()
