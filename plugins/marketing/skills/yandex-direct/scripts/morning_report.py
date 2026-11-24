#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Утренний отчёт Дирика в PDF – только ЧТЕНИЕ (в кабинете и Метрике ничего не меняет).

    python morning_report.py CLIENT_DIR [--date ГГГГ-ММ-ДД] [--demo] [--no-open]

  CLIENT_DIR  папка клиента с dirik.json (логин кабинета и цели берутся оттуда)
  --date      за какой день отчёт (по умолчанию вчера; если вчера выходной – последний рабочий день)
  --demo      собрать отчёт на выдуманных цифрах – только чтобы посмотреть вёрстку (помечен «ДЕМО»)
  --no-open   не открывать PDF после сборки

Результат: CLIENT_DIR/reports/ГГГГ-ММ-ДД-utro.pdf (дата – день сборки) и журнал CLIENT_DIR/reports/morning.log.
PDF печатает Microsoft Edge (или Chrome) в headless-режиме – ставить ничего не нужно.

Что внутри: вывод одной фразой, показатели дня и недели, график 14 дней, «что сделать сегодня» (кто делает –
ты или Дирик), карточки кампаний (модерация, лимиты, ключи / автотаргетинг), поисковые запросы с пометками,
ключи без показов, Метрика по UTM (визиты, отказы, вовлечение), устройства, урок дня.
Суммы – без НДС (как в ставках Директа). Конверсии – модель атрибуции AUTO, как у стратегий.
"""
import argparse, datetime as dt, html, json, os, shutil, subprocess, sys, tempfile, traceback, urllib.parse, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

M = 1_000_000
STOP_WORDS = ['бесплатно', 'скачать', 'вакансия', 'вакансии', 'работа', 'своими руками', 'реферат', 'картинки', 'фото',
              'что такое', 'это', 'видео', 'википедия', 'б/у', 'бу', 'авито', 'онлайн бесплатно', 'резюме', 'курсовая']
ENGAGEMENT = [('hero_interact', 'покрутил витрину'), ('game_play', 'начал игру'), ('game_finish', 'доиграл'),
              ('kiosk_open', 'открыл киоск'), ('stats_view', 'смотрел статистику'), ('package_click', 'выбрал набор'),
              ('form_start', 'начал форму'), ('scroll_75', 'дочитал до 75 %')]
WD = ['понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье']
MON = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']
LESSONS = [
 ('CTR – кликабельность', 'CTR = клики ÷ показы. На Поиске в услугах норма 2–8 %. Низкий CTR – объявление не отвечает на запрос: '
  'человек искал одно, а заголовок про другое. Лечится заголовком со словами из запроса, а не повышением ставки.'),
 ('Цена клика и цена заявки', 'Дешёвый клик ничего не значит сам по себе. Считаем цену заявки: расход ÷ заявки. '
  'Клик за 400 ₽ с конверсией 5 % даёт заявку за 8 000 ₽ – дешевле, чем клик за 100 ₽ с конверсией 0,5 % (20 000 ₽).'),
 ('Поисковые запросы', 'Ключ – это то, что мы вписали в кампанию. Запрос – то, что человек набрал на самом деле. '
  'Каждое утро смотрим запросы: чужие (вакансии, «бесплатно», другие товары) уходят в минус-слова, удачные – в ключи.'),
 ('Минус-слова', 'Минус-слово отсекает все свои формы: «-вакансия» убьёт и «вакансии». Поэтому мусор режем точечно и '
  'проверяем, не задели ли свои ключи. Минус-фраза, которая целиком входит в ключ, Директом молча игнорируется.'),
 ('Автотаргетинг', 'Директ сам подбирает запросы по тексту объявления и странице. На Поиске выключить его нельзя – только '
  'сузить. Смотрим в отчёте, сколько денег уходит на автотаргетинг и сколько заявок он приносит в сравнении с ключами.'),
 ('Почему 100 кликов', 'Пока кликов меньше 100, выводы – гадание: одна заявка меняет цену заявки вдвое. Поэтому первую '
  'неделю ничего не отключаем, только чистим явный мусор в запросах.'),
 ('Обучение стратегии', 'Автостратегии нужно около 10 конверсий в неделю, чтобы учиться. Каждая смена стратегии, цели или '
  'бюджета больше чем на 20 % сбрасывает обучение. Правка – раз в неделю и одна за раз.'),
 ('Отказы и вовлечение', 'Отказ – визит меньше 15 секунд с одной страницей. Много отказов при нормальном CTR – страница не '
  'совпадает с обещанием объявления. Цели вовлечения (игра, киоск, набор) показывают, кто заинтересовался, ещё до заявки.'),
]


# ------------------------------------------------------------------ утилиты
def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def rub(x, dash='–'):
    if x is None:
        return dash
    return f'{x:,.0f}'.replace(',', '\u202f') + '\u00a0₽'


def num(x):
    return f'{x:,.0f}'.replace(',', '\u202f') if x is not None else '–'


def pct(x, d=1):
    return (f'{x:.{d}f}'.replace('.', ',') + '\u00a0%') if x is not None else '–'


def ddate(d):
    return f'{d.day} {MON[d.month - 1]}'


def esc(s):
    return html.escape(str(s if s is not None else ''))


def blank():
    return {'impr': 0, 'clicks': 0, 'cost': 0.0, 'leads': 0, 'contacts': 0}


def add(a, b):
    for k in blank():            # только цифры: в строке запроса рядом лежат текстовые поля
        a[k] += b.get(k, 0)
    return a


def ratios(a):
    i, c, cost, l = a['impr'], a['clicks'], a['cost'], a['leads']
    return {'ctr': c / i * 100 if i else None, 'cpc': cost / c if c else None, 'cpa': cost / l if l else None,
            'cr': l / c * 100 if c else None}


def report_day(today, forced=None):
    if forced:
        return forced
    d = today - dt.timedelta(days=1)
    while d.weekday() >= 5:      # показы по расписанию только в будни
        d -= dt.timedelta(days=1)
    return d


# ------------------------------------------------------------------ сбор данных
def collect(client_dir, day):
    from direct_api import Direct, token
    errors = []
    dj = json.load(open(os.path.join(client_dir, 'dirik.json'), encoding='utf-8-sig'))
    s = dj.get('settings', {})
    login = s.get('login')
    lead_goals = {str(g['id']): g for g in s.get('goals', []) if g.get('role') == 'lead' and g.get('id')}
    contact_goals = [str(g['id']) for g in s.get('goals', []) if g.get('role') == 'contact' and g.get('id')]
    d = Direct(login)
    data = {'company': dj.get('company', ''), 'login': login, 'day': day, 'errors': errors, 'campaigns': [],
            'daily': [], 'queries': [], 'keywords': [], 'devices': {}, 'metrika': {}, 'balance': None, 'demo': False}

    def safe(label, fn, default=None):
        try:
            return fn()
        except Exception as e:  # отчёт собирается даже при частичном сбое
            errors.append(f'{label}: {e}')
            traceback.print_exc()
            return default

    camps = safe('кампании', lambda: d.call('campaigns', 'get', {
        'SelectionCriteria': {'States': ['ON', 'OFF', 'SUSPENDED', 'ENDED']},
        'FieldNames': ['Id', 'Name', 'State', 'Status', 'StatusClarification', 'StartDate'],
        'UnifiedCampaignFieldNames': ['BiddingStrategy', 'PriorityGoals', 'TrackingParams']}).get('Campaigns', []), [])
    if not camps:
        return data
    ids = [c['Id'] for c in camps]
    goal_ids = sorted(set(lead_goals))
    for c in camps:
        u = c.get('UnifiedCampaign') or {}
        srch = (u.get('BiddingStrategy') or {}).get('Search') or {}
        inner = next((v for v in srch.values() if isinstance(v, dict) and ('WeeklySpendLimit' in v or 'AverageCpc' in v)), {})
        pg = ((u.get('PriorityGoals') or {}).get('Items') or [])
        g = str(pg[0]['GoalId']) if pg else None
        if g and g not in goal_ids:
            goal_ids.append(g)
        tp = u.get('TrackingParams') or ''
        utm = dict(urllib.parse.parse_qsl(tp)).get('utm_campaign', '')
        data['campaigns'].append({
            'id': c['Id'], 'name': c['Name'], 'state': c['State'], 'status': c['Status'],
            'clar': c.get('StatusClarification', ''), 'start': c.get('StartDate'),
            'strategy': srch.get('BiddingStrategyType', ''), 'weekly': (inner.get('WeeklySpendLimit') or 0) / M or None,
            'avg_cpc': (inner.get('AverageCpc') or 0) / M or None, 'goal': g,
            'goal_name': (lead_goals.get(g) or {}).get('name') or (lead_goals.get(g) or {}).get('key') or g,
            'target_cpa': pg[0]['Value'] / M if pg else None, 'utm': utm,
            'day': blank(), 'd7': blank(), 'total': blank(), 'ads': [], 'crit': {}})
    by_id = {c['id']: c for c in data['campaigns']}
    all_goals = goal_ids + [g for g in contact_goals if g not in goal_ids]

    def conv_split(r, cid):
        camp = by_id.get(int(r.get('CampaignId') or 0))
        lg = camp['goal'] if camp else None
        leads = contacts = 0
        for k, v in r.items():
            if not k.startswith('Conversions_'):
                continue
            gid = k.split('_')[1]
            n = int(v) if v not in ('--', '', None) else 0
            if gid == lg:
                leads += n
            elif gid in contact_goals:
                contacts += n
        return leads, contacts

    # объявления и модерация
    ads = safe('объявления', lambda: d.call('ads', 'get', {'SelectionCriteria': {'CampaignIds': ids},
               'FieldNames': ['Id', 'CampaignId', 'AdGroupId', 'State', 'Status', 'StatusClarification']}).get('Ads', []), [])
    for a in ads:
        if a['CampaignId'] in by_id:
            by_id[a['CampaignId']]['ads'].append({'id': a['Id'], 'state': a['State'], 'status': a['Status'],
                                                  'clar': a.get('StatusClarification', '')})
    # статистика по дням с начала (не больше 90 дней)
    starts = [dt.date.fromisoformat(c['start']) for c in data['campaigns'] if c.get('start')]
    dfrom = max(min(starts) if starts else day - dt.timedelta(days=13), day - dt.timedelta(days=89))
    dfrom = min(dfrom, day - dt.timedelta(days=13))
    rows = safe('статистика по дням', lambda: d.report(['Date', 'CampaignId', 'Impressions', 'Clicks', 'Cost'],
                dfrom.isoformat(), day.isoformat(), goals=all_goals, include_vat=False), [])
    daily = {}
    d7_from = day - dt.timedelta(days=6)
    for r in rows:
        cid = int(r['CampaignId'])
        if cid not in by_id:
            continue
        leads, contacts = conv_split(r, cid)
        m = {'impr': int(fnum(r['Impressions'])), 'clicks': int(fnum(r['Clicks'])), 'cost': fnum(r['Cost']),
             'leads': leads, 'contacts': contacts}
        rd = dt.date.fromisoformat(r['Date'])
        c = by_id[cid]
        add(c['total'], m)
        if rd >= d7_from:
            add(c['d7'], m)
        if rd == day:
            add(c['day'], m)
        add(daily.setdefault(rd, blank()), m)
    for i in range(13, -1, -1):
        dd = day - dt.timedelta(days=i)
        data['daily'].append(dict(daily.get(dd, blank()), date=dd))
    # ключи / автотаргетинг за 7 дней
    crit = safe('типы условий показа', lambda: d.report(['CampaignId', 'CriterionType', 'Impressions', 'Clicks', 'Cost'],
                d7_from.isoformat(), day.isoformat(), goals=all_goals, include_vat=False), [])
    for r in crit:
        cid = int(r['CampaignId'])
        if cid in by_id:
            leads, contacts = conv_split(r, cid)
            add(by_id[cid]['crit'].setdefault(r['CriterionType'], blank()),
                {'impr': int(fnum(r['Impressions'])), 'clicks': int(fnum(r['Clicks'])), 'cost': fnum(r['Cost']),
                 'leads': leads, 'contacts': contacts})
    # поисковые запросы за 7 дней

    def sq():
        try:
            return d.report(['Date', 'CampaignId', 'Query', 'Criterion', 'TargetingCategory', 'Impressions', 'Clicks', 'Cost'],
                            d7_from.isoformat(), day.isoformat(), report_type='SEARCH_QUERY_PERFORMANCE_REPORT',
                            goals=all_goals, include_vat=False)
        except Exception:
            return d.report(['Date', 'CampaignId', 'Query', 'Criterion', 'Impressions', 'Clicks', 'Cost'],
                            d7_from.isoformat(), day.isoformat(), report_type='SEARCH_QUERY_PERFORMANCE_REPORT',
                            goals=all_goals, include_vat=False)
    qrows = safe('поисковые запросы', sq, [])
    qa = {}
    for r in qrows:
        cid = int(r['CampaignId'])
        if cid not in by_id:
            continue
        key = (cid, r['Query'])
        leads, contacts = conv_split(r, cid)
        q = qa.setdefault(key, {'query': r['Query'], 'campaign': by_id[cid]['name'], 'cid': cid,
                                'criterion': r.get('Criterion', ''), 'cat': r.get('TargetingCategory', ''),
                                'yesterday': False, **blank()})
        add(q, {'impr': int(fnum(r['Impressions'])), 'clicks': int(fnum(r['Clicks'])), 'cost': fnum(r['Cost']),
                'leads': leads, 'contacts': contacts})
        if r['Date'] == day.isoformat():
            q['yesterday'] = True
    data['queries'] = sorted(qa.values(), key=lambda q: (-q['cost'], -q['impr']))
    # ключи: показы за 7 дней, включая ключи без показов
    kw = safe('ключи', lambda: d.call('keywords', 'get', {'SelectionCriteria': {'CampaignIds': ids},
              'FieldNames': ['Id', 'CampaignId', 'Keyword', 'State', 'Status']}).get('Keywords', []), [])
    kstat = safe('статистика ключей', lambda: d.report(['CampaignId', 'CriterionId', 'Criterion', 'Impressions', 'Clicks', 'Cost'],
                 d7_from.isoformat(), day.isoformat(), include_vat=False), [])
    ks = {str(r['CriterionId']): r for r in kstat}
    for k in kw:
        if k['Keyword'] == '---autotargeting':
            continue
        r = ks.get(str(k['Id']), {})
        data['keywords'].append({'campaign': by_id[k['CampaignId']]['name'], 'cid': k['CampaignId'], 'keyword': k['Keyword'],
                                 'status': k.get('Status'), 'impr': int(fnum(r.get('Impressions'))),
                                 'clicks': int(fnum(r.get('Clicks'))), 'cost': fnum(r.get('Cost'))})
    # устройства за 7 дней
    dev = safe('устройства', lambda: d.report(['Device', 'Impressions', 'Clicks', 'Cost'], d7_from.isoformat(),
               day.isoformat(), goals=all_goals, include_vat=False), [])
    for r in dev:
        add(data['devices'].setdefault(r['Device'], blank()), {'impr': int(fnum(r['Impressions'])),
            'clicks': int(fnum(r['Clicks'])), 'cost': fnum(r['Cost']), 'leads': r.get('conv', 0)})
    # баланс счёта (API v4 Live)

    def balance():
        body = {'method': 'AccountManagement', 'param': {'Action': 'Get', 'SelectionCriteria': {'Logins': [login]}},
                'token': token(), 'locale': 'ru'}
        req = urllib.request.Request('https://api.direct.yandex.ru/live/v4/json/', data=json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json'})
        r = json.loads(urllib.request.urlopen(req, timeout=60).read().decode())
        acc = (r.get('data') or {}).get('Accounts') or []
        return fnum(acc[0]['Amount']) if acc else None
    data['balance'] = safe('баланс', balance)
    # Метрика: визиты из Директа по utm_campaign
    counter = s.get('counter_id') or 113667200

    def metrika(d1, d2):
        mg = json.loads(urllib.request.urlopen(urllib.request.Request(
            f'https://api-metrika.yandex.net/management/v1/counter/{counter}/goals',
            headers={'Authorization': 'OAuth ' + token()}), timeout=60).read().decode())['goals']
        ident = {((g.get('conditions') or [{}])[0].get('url')): g['id'] for g in mg if g.get('type') == 'action'}
        eng = [(k, ident[k]) for k, _ in ENGAGEMENT if k in ident]
        mets = ['ym:s:visits', 'ym:s:bounceRate', 'ym:s:avgVisitDurationSeconds', 'ym:s:pageDepth'] + \
               [f'ym:s:goal{gid}reaches' for _, gid in eng]
        q = {'ids': counter, 'date1': d1.isoformat(), 'date2': d2.isoformat(), 'dimensions': 'ym:s:UTMCampaign',
             'metrics': ','.join(mets[:20]), 'filters': "ym:s:UTMSource=='yandex'", 'limit': 50, 'accuracy': 'full'}
        r = json.loads(urllib.request.urlopen(urllib.request.Request(
            'https://api-metrika.yandex.net/stat/v1/data?' + urllib.parse.urlencode(q),
            headers={'Authorization': 'OAuth ' + token()}), timeout=60).read().decode())
        out = {}
        for row in r.get('data', []):
            name = row['dimensions'][0].get('name') or '(без метки)'
            m = row['metrics']
            out[name] = {'visits': m[0], 'bounce': m[1], 'dur': m[2], 'depth': m[3],
                         'goals': {k: m[4 + i] for i, (k, _) in enumerate(eng)}}
        return out
    data['metrika'] = {'d7': safe('Метрика, 7 дней', lambda: metrika(d7_from, day), {}),
                       'day': safe('Метрика, день', lambda: metrika(day, day), {})}
    return data


# ------------------------------------------------------------------ демо-данные (только вёрстка)
def demo(day):
    import random
    random.seed(7)
    camps = [('UV-Land – Выставки – Поиск', 'vystavki', 15000, 250, 12000, 'lead_expo'),
             ('UV-Land – Промо в ТЦ – Поиск', 'promo', 10000, 350, 6000, 'lead_promo'),
             ('UV-Land – Цифровой продавец – Поиск', 'prodavec', 7000, 200, 10000, 'lead_seller')]
    data = {'company': 'UV-Land', 'login': 'demo-login', 'day': day, 'errors': [], 'campaigns': [], 'daily': [],
            'queries': [], 'keywords': [], 'devices': {}, 'metrika': {'d7': {}, 'day': {}}, 'balance': 14250.0, 'demo': True}
    for i in range(13, -1, -1):
        dd = day - dt.timedelta(days=i)
        on = i < 6 and dd.weekday() < 5
        c = random.randint(4, 14) if on else 0
        data['daily'].append({'date': dd, 'impr': c * 18, 'clicks': c, 'cost': c * random.randint(220, 360) * 1.0,
                              'leads': 1 if on and random.random() < 0.3 else 0, 'contacts': 0})
    for n, (name, utm, wk, cpc, cpa, gk) in enumerate(camps):
        day_m = {'impr': [64, 120, 22][n], 'clicks': [3, 4, 1][n], 'cost': [790.0, 1420.0, 210.0][n], 'leads': [0, 1, 0][n], 'contacts': [1, 0, 0][n]}
        d7 = {'impr': [310, 655, 98][n], 'clicks': [14, 19, 3][n], 'cost': [3610.0, 6840.0, 640.0][n], 'leads': [1, 1, 0][n], 'contacts': [2, 1, 0][n]}
        data['campaigns'].append({'id': 716450150 + n * 14, 'name': name, 'state': 'ON', 'status': 'ACCEPTED',
            'clar': 'Идут показы', 'start': (day - dt.timedelta(days=5)).isoformat(), 'strategy': 'AVERAGE_CPC',
            'weekly': wk, 'avg_cpc': cpc, 'goal': gk, 'goal_name': gk, 'target_cpa': cpa, 'utm': utm,
            'day': day_m, 'd7': d7, 'total': dict(d7), 'ads': [{'id': 1, 'state': 'ON', 'status': 'ACCEPTED', 'clar': ''},
            {'id': 2, 'state': 'OFF', 'status': 'REJECTED' if n == 2 else 'ACCEPTED', 'clar': 'Отклонено: обещание не подтверждено на сайте' if n == 2 else ''}],
            'crit': {'KEYWORD': {'impr': d7['impr'] // 2, 'clicks': d7['clicks'] // 2, 'cost': d7['cost'] / 2, 'leads': d7['leads'], 'contacts': 0},
                     'AUTOTARGETING': {'impr': d7['impr'] // 2, 'clicks': d7['clicks'] - d7['clicks'] // 2, 'cost': d7['cost'] / 2, 'leads': 0, 'contacts': 0}}})
        data['metrika']['d7'][utm] = {'visits': d7['clicks'] + 1, 'bounce': [21.4, 33.0, 50.0][n], 'dur': [96, 71, 40][n], 'depth': 1.4,
                                     'goals': {'hero_interact': 6, 'game_play': 4, 'game_finish': 2, 'kiosk_open': [0, 0, 1][n],
                                               'stats_view': 0, 'package_click': 3, 'form_start': 2, 'scroll_75': 5}}
    for q, cname, cr, im, cl, co, ld, y in [
        ('интерактивный стенд на выставку москва', 0, 'интерактивный стенд', 41, 3, 820, 1, True),
        ('реклама в тц цена', 1, 'реклама в тц', 88, 4, 1510, 1, True),
        ('промо акция в тц вакансии', 1, 'промо акция в тц', 30, 2, 690, 0, True),
        ('btl агентство москва', 1, 'btl агентство', 61, 3, 1180, 0, False),
        ('голографическая витрина купить', 0, 'автотаргетинг', 19, 2, 470, 0, True),
        ('интерактивный киоск для магазина цена', 2, 'интерактивный киоск', 12, 1, 210, 0, False),
        ('игра на выставке для детей', 0, 'игра на выставке', 25, 2, 515, 0, False)]:
        data['queries'].append({'query': q, 'campaign': camps[cname][0], 'cid': 0, 'criterion': cr, 'cat': '',
                                'yesterday': y, 'impr': im, 'clicks': cl, 'cost': float(co), 'leads': ld, 'contacts': 0})
    for kwd, cn, im in [('интерактив для стенда', 0, 0), ('сбор контактов на выставке', 0, 3), ('интерактивная промо акция', 1, 0),
                        ('визуализатор плитки для магазина', 2, 0), ('интерактивный стенд', 0, 140)]:
        data['keywords'].append({'campaign': camps[cn][0], 'cid': 0, 'keyword': kwd, 'status': 'ACCEPTED', 'impr': im,
                                 'clicks': im // 30, 'cost': im * 8.0})
    data['devices'] = {'DESKTOP': {'impr': 700, 'clicks': 27, 'cost': 8100.0, 'leads': 2, 'contacts': 0},
                       'MOBILE': {'impr': 340, 'clicks': 9, 'cost': 2850.0, 'leads': 0, 'contacts': 0},
                       'TABLET': {'impr': 23, 'clicks': 0, 'cost': 0.0, 'leads': 0, 'contacts': 0}}
    return data


# ------------------------------------------------------------------ советы
def advise(data, today):
    tips = []   # (уровень: alarm/today/watch, кто: ты/Дирик, текст)
    camps = data['campaigns']
    tot7 = blank()
    for c in camps:
        add(tot7, c['d7'])
    for c in camps:
        rej = [a for a in c['ads'] if a['status'] == 'REJECTED']
        mod = [a for a in c['ads'] if a['status'] in ('MODERATION', 'DRAFT')]
        if rej:
            tips.append(('alarm', 'Дирик', f'«{c["name"]}»: {len(rej)} объявл. отклонено модерацией – «{rej[0]["clar"] or "причина в интерфейсе"}». '
                         'Разберу причину и пришлю правку текста на «да».'))
        elif mod and c['status'] != 'ACCEPTED':
            tips.append(('watch', 'Дирик', f'«{c["name"]}»: объявления на модерации ({len(mod)}). Обычно до 1–2 дней, слежу.'))
        started = c.get('start') and (today - dt.date.fromisoformat(c['start'])).days >= 2
        if c['state'] == 'ON' and started and c['day']['impr'] == 0:
            tips.append(('alarm', 'Дирик', f'«{c["name"]}»: в рабочий день 0 показов. Проверю статус «мало показов», ставку и баланс.'))
        if c['target_cpa'] and c['d7']['leads'] == 0 and c['d7']['cost'] >= 2 * c['target_cpa']:
            tips.append(('today', 'Дирик', f'«{c["name"]}»: за неделю {rub(c["d7"]["cost"])} без заявок – это 2 цели CPA. '
                         'Разберу запросы и посадочную, предложу правку.'))
        r7 = ratios(c['d7'])
        if c['d7']['impr'] >= 200 and r7['ctr'] is not None and r7['ctr'] < 2:
            tips.append(('today', 'Дирик', f'«{c["name"]}»: CTR {pct(r7["ctr"])} при {num(c["d7"]["impr"])} показах – объявления '
                         'не цепляют. Подготовлю новые заголовки со словами из запросов.'))
        if c['weekly'] and c['d7']['cost'] >= 0.9 * c['weekly']:
            tips.append(('watch', 'ты', f'«{c["name"]}»: недельный лимит {rub(c["weekly"])} выбран на '
                         f'{c["d7"]["cost"] / c["weekly"] * 100:.0f} %. Если заявки идут по цели – можно обсудить рост на 10–20 %.'))
    bad = [q for q in data['queries'] if any(w in q['query'].split() or (' ' in w and w in q['query']) for w in STOP_WORDS)]
    if bad:
        tips.append(('today', 'ты', 'Запросы в минус: ' + ', '.join(f'«{q["query"]}»' for q in bad[:5]) +
                     '. Отметь «да» в панели – добавлю минус-слова.'))
    dry = [q for q in data['queries'] if q['clicks'] >= 3 and q['leads'] == 0 and q not in bad]
    if dry:
        tips.append(('watch', 'Дирик', f'{len(dry)} запр. с 3+ кликами без заявок – пока наблюдаю, решение по окну 30 дней.'))
    zero = [k for k in data['keywords'] if k['impr'] == 0]
    running3 = any(c['state'] == 'ON' and c.get('start') and (today - dt.date.fromisoformat(c['start'])).days >= 3 for c in camps)
    if zero and running3:
        tips.append(('watch', 'Дирик', f'{len(zero)} ключ(ей) без показов за 7 дней – у них нет спроса. Не мешают, но и не работают.'))
    if data['balance'] is not None:
        spend_day = sum(x['cost'] for x in data['daily'][-7:]) / 5 * 1.22   # 5 рабочих дней, баланс с НДС
        if spend_day > 0:
            left = data['balance'] / spend_day
            if left < 5:
                tips.append(('alarm', 'ты', f'Баланс {rub(data["balance"])} – хватит примерно на {left:.0f} раб. дн. Пополни счёт в Директе.'))
        elif data['balance'] < 1000 and any(c['state'] == 'ON' for c in camps):
            tips.append(('alarm', 'ты', f'Баланс {rub(data["balance"])} – реклама скоро остановится. Пополни счёт.'))
    if tot7['clicks'] < 100 and any(c['d7']['clicks'] for c in camps):
        tips.append(('watch', 'Дирик', f'За неделю {num(tot7["clicks"])} кликов – меньше 100, выводы рано. Ничего не отключаю, только чищу мусор.'))
    if data['errors']:
        tips.append(('watch', 'Дирик', 'Часть данных не загрузилась: ' + '; '.join(data['errors'][:3]) + '.'))
    if not tips:
        tips.append(('watch', 'Дирик', 'Всё идёт по плану, вмешательство не нужно.'))
    order = {'alarm': 0, 'today': 1, 'watch': 2}
    return sorted(tips, key=lambda t: order[t[0]])


def verdict(data, tips):
    c_day = blank()
    for c in data['campaigns']:
        add(c_day, c['day'])
    alarms = [t for t in tips if t[0] == 'alarm']
    mods = [c for c in data['campaigns'] if c['status'] == 'MODERATION']
    if not data['campaigns']:
        return 'Активных кампаний нет.'
    w_impr = sum(c['d7']['impr'] for c in data['campaigns'])
    if w_impr == 0:
        if mods:
            return f'Показов ещё не было: {len(mods)} из {len(data["campaigns"])} кампаний на модерации.'
        ok = [c for c in data['campaigns'] if c['status'] == 'ACCEPTED']
        return f'Показов ещё не было: {len(ok)} из {len(data["campaigns"])} кампаний прошли модерацию и ждут первых показов.'
    head = f'{ddate(data["day"]).capitalize()}: {rub(c_day["cost"])}, {num(c_day["clicks"])} кликов, ' \
           f'{num(c_day["leads"])} заяв{"ка" if c_day["leads"] == 1 else "ок"}'
    if alarms:
        return head + ' – есть что исправить срочно.'
    return head + ('.' if c_day['leads'] else ' – пока без заявок.')


# ------------------------------------------------------------------ вёрстка
CSS = """
@page { size: A4; margin: 14mm 14mm 16mm; @bottom-right { content: counter(page) ' / ' counter(pages); font: 8pt Bahnschrift; color: #5B6475; } }
:root { --ink:#14213D; --paper:#FFFFFF; --graphite:#5B6475; --rule:#E2E5EB; --marker:#D9F45A; --alarm:#C2410C; }
* { box-sizing: border-box; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
html, body { background: var(--paper); color: var(--ink); margin: 0; }
body { font: 9.5pt/1.45 'Segoe UI', sans-serif; }
h1, h2, h3, .fig, .kicker { font-family: Bahnschrift, 'Segoe UI', sans-serif; font-weight: 600; }
h1 { font-size: 22pt; line-height: 1.1; margin: 0; font-stretch: semi-condensed; letter-spacing: -0.2pt; }
h2 { font-size: 12.5pt; margin: 18pt 0 6pt; padding-bottom: 3pt; border-bottom: 1.2pt solid var(--ink); }
h3 { font-size: 11pt; margin: 0 0 4pt; }
.kicker { font-size: 8pt; text-transform: uppercase; letter-spacing: 1pt; color: var(--graphite); font-weight: 400; }
.top { display: flex; justify-content: space-between; align-items: flex-end; border-bottom: 1.2pt solid var(--ink); padding-bottom: 6pt; }
.top .meta { text-align: right; font-size: 8.5pt; color: var(--graphite); }
.verdict { font: 600 15pt/1.35 Bahnschrift, sans-serif; margin: 12pt 0 4pt; }
.hl { background: linear-gradient(176deg, transparent 8%, var(--marker) 9%, var(--marker) 90%, transparent 92%);
      padding: 0 3pt; margin: 0 -1pt; border-radius: 2pt 6pt 3pt 7pt; box-decoration-break: clone; -webkit-box-decoration-break: clone; }
.sub { color: var(--graphite); font-size: 8.5pt; }
.ledger { display: grid; grid-template-columns: repeat(6, 1fr); border-top: .6pt solid var(--rule); border-bottom: .6pt solid var(--rule); margin: 8pt 0; }
.ledger > div { padding: 6pt 8pt 7pt; border-left: .6pt solid var(--rule); }
.ledger > div:first-child { border-left: 0; padding-left: 0; }
.fig { font-size: 17pt; line-height: 1.15; font-variant-numeric: tabular-nums; }
.fig small { font-size: 9pt; color: var(--graphite); font-weight: 400; }
.lbl { font-size: 7.8pt; color: var(--graphite); text-transform: uppercase; letter-spacing: .5pt; }
.delta { font-size: 7.8pt; color: var(--graphite); }
.cols { display: grid; grid-template-columns: 1.15fr 1fr; gap: 14pt; }
.chart svg { width: 100%; height: auto; display: block; }
.todo { list-style: none; margin: 0; padding: 0; }
.todo li { display: grid; grid-template-columns: 46pt 1fr; gap: 6pt; padding: 5pt 0; border-bottom: .6pt solid var(--rule); break-inside: avoid; }
.who { font: 600 7.5pt Bahnschrift; text-transform: uppercase; letter-spacing: .6pt; padding-top: 1.5pt; }
.who.you { color: var(--ink); } .who.dirik { color: var(--graphite); }
.lvl-alarm .txt { color: var(--alarm); font-weight: 600; }
.lvl-today .txt .hlx { background: var(--marker); }
table { width: 100%; border-collapse: collapse; font-size: 8.5pt; margin: 4pt 0 2pt; }
th { font: 400 7.5pt Bahnschrift; text-transform: uppercase; letter-spacing: .5pt; color: var(--graphite); text-align: right; padding: 3pt 4pt; border-bottom: .8pt solid var(--ink); }
th:first-child, td:first-child { text-align: left; padding-left: 0; }
td { padding: 3pt 4pt; border-bottom: .5pt solid var(--rule); text-align: right; font-variant-numeric: tabular-nums; vertical-align: top; }
td.l, th.l { text-align: left; }
tr { break-inside: avoid; }
.q { font-family: Consolas, monospace; font-size: 8.3pt; }
.tag { font: 600 7pt Bahnschrift; text-transform: uppercase; letter-spacing: .4pt; padding: 1pt 4pt; border: .6pt solid var(--ink); border-radius: 2pt; white-space: nowrap; }
.tag.alarm { color: var(--alarm); border-color: var(--alarm); }
.tag.mute { color: var(--graphite); border-color: var(--rule); }
.camp { border-top: .6pt solid var(--rule); padding-top: 8pt; margin-top: 10pt; break-inside: avoid; }
.camp .head { display: flex; justify-content: space-between; align-items: baseline; gap: 8pt; }
.mini { display: grid; grid-template-columns: repeat(7, 1fr); gap: 0; margin: 6pt 0; }
.mini > div { border-left: .6pt solid var(--rule); padding: 2pt 6pt; }
.mini > div:first-child { border-left: 0; padding-left: 0; }
.mini .fig { font-size: 12pt; }
.bar { height: 5pt; background: var(--rule); border-radius: 3pt; overflow: hidden; margin-top: 3pt; }
.bar > i { display: block; height: 100%; background: var(--ink); }
.lesson { margin-top: 16pt; padding: 10pt 12pt; border-left: 3pt solid var(--marker); background: #F6F8EC; break-inside: avoid; }
.lesson h3 { margin-bottom: 3pt; }
.demo { position: fixed; top: 40%; left: 0; right: 0; text-align: center; font: 700 110pt Bahnschrift; color: rgba(194,65,12,.10); transform: rotate(-24deg); z-index: 5; pointer-events: none; }
.pb { break-before: page; }
.empty { color: var(--graphite); font-style: italic; padding: 4pt 0; }
"""

STATUS_RU = {'ACCEPTED': 'принята', 'MODERATION': 'на модерации', 'DRAFT': 'черновик', 'REJECTED': 'отклонена', 'UNKNOWN': '?'}
STATE_RU = {'ON': 'идут показы', 'OFF': 'остановлена', 'SUSPENDED': 'на паузе', 'ENDED': 'завершена', 'ARCHIVED': 'архив'}
CRIT_RU = {'KEYWORD': 'ключи', 'AUTOTARGETING': 'автотаргетинг', 'RETARGETING': 'ретаргетинг'}
DEV_RU = {'DESKTOP': 'компьютеры', 'MOBILE': 'телефоны', 'TABLET': 'планшеты'}


def chart(daily):
    W, H, pad_l, pad_b, top = 360, 150, 30, 18, 10
    n = len(daily)
    bw = (W - pad_l) / n
    mx = max([x['cost'] for x in daily] + [1])
    step = 10 ** max(len(str(int(mx))) - 1, 0)
    mx = ((mx // step) + 1) * step
    out = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" font-family="Bahnschrift" font-size="7">']
    for frac in (0, .5, 1):
        y = top + (H - pad_b - top) * (1 - frac)
        out.append(f'<line x1="{pad_l}" x2="{W}" y1="{y:.1f}" y2="{y:.1f}" stroke="#E2E5EB" stroke-width=".6"/>'
                   f'<text x="{pad_l - 4}" y="{y + 2.5:.1f}" text-anchor="end" fill="#5B6475">{num(mx * frac)}</text>')
    for i, x in enumerate(daily):
        h = (H - pad_b - top) * x['cost'] / mx
        cx = pad_l + i * bw + bw / 2
        if h > 0:
            out.append(f'<rect x="{pad_l + i * bw + bw * .18:.1f}" y="{H - pad_b - h:.1f}" width="{bw * .64:.1f}" height="{h:.1f}" fill="#14213D" rx="1"/>')
        if x['leads']:
            out.append(f'<circle cx="{cx:.1f}" cy="{H - pad_b - h - 7:.1f}" r="4.2" fill="#D9F45A" stroke="#14213D" stroke-width=".8"/>'
                       f'<text x="{cx:.1f}" y="{H - pad_b - h - 4.6:.1f}" text-anchor="middle" font-size="6.5" fill="#14213D">{x["leads"]}</text>')
        wk = x['date'].weekday() >= 5
        out.append(f'<text x="{cx:.1f}" y="{H - 6}" text-anchor="middle" fill="{"#B8BEC9" if wk else "#5B6475"}">{x["date"].day}</text>')
    out.append('</svg>')
    return ''.join(out)


def fig(label, value, sub=''):
    return f'<div><div class="lbl">{esc(label)}</div><div class="fig">{value}</div><div class="delta">{sub}</div></div>'


def render(data, tips, today):
    day = data['day']
    T = blank(); W = blank()
    for c in data['campaigns']:
        add(T, c['day']); add(W, c['d7'])
    rT, rW = ratios(T), ratios(W)
    weekly = sum(c['weekly'] or 0 for c in data['campaigns'])
    v = verdict(data, tips)
    p = [f'<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Утренний отчёт {esc(data["company"])}</title><style>{CSS}</style></head><body>']
    if data.get('demo'):
        p.append('<div class="demo">ДЕМО</div>')
    p.append(f'<div class="top"><div><div class="kicker">Утренний отчёт Дирика · {esc(data["company"])} · Яндекс Директ{" · ДЕМО, цифры выдуманы" if data.get("demo") else ""}</div>'
             f'<h1>{WD[today.weekday()].capitalize()}, {ddate(today)}</h1></div>'
             f'<div class="meta">кабинет {esc(data["login"])}<br>данные за {ddate(day)} ({WD[day.weekday()]}) и 7 дней<br>'
             f'суммы без НДС · собран {dt.datetime.now():%H:%M}</div></div>')
    p.append(f'<div class="verdict"><span class="hl">{esc(v)}</span></div>')
    bal = rub(data['balance']) if data['balance'] is not None else '–'
    p.append('<div class="ledger">' +
             fig('расход за день', rub(T['cost']), f'за 7 дней {rub(W["cost"])}' + (f' из {rub(weekly)}' if weekly else '')) +
             fig('клики', num(T['clicks']), f'7 дней: {num(W["clicks"])}') +
             fig('CTR', pct(rT['ctr']), f'7 дней: {pct(rW["ctr"])}') +
             fig('цена клика', rub(rT['cpc']), f'7 дней: {rub(rW["cpc"])}') +
             fig('заявки', num(T['leads']), f'7 дней: {num(W["leads"])} · цена {rub(rW["cpa"])}') +
             fig('баланс', bal, 'с НДС, на счёте') + '</div>')
    # график + задачи
    p.append('<div class="cols"><div class="chart"><h2>Расход и заявки, 14 дней</h2>' + chart(data['daily']) +
             '<div class="sub">столбик – расход за день; лаймовый кружок – заявки; серые даты – выходные (показов нет по расписанию)</div></div>')
    p.append('<div><h2>Что сделать сегодня</h2><ul class="todo">')
    for lvl, who, txt in tips:
        cls = 'you' if who == 'ты' else 'dirik'
        t = esc(txt)
        if lvl == 'today':
            t = f'<span class="hlx">{t}</span>'
        p.append(f'<li class="lvl-{lvl}"><div class="who {cls}">{"ты" if who == "ты" else "Дирик"}</div><div class="txt">{t}</div></li>')
    p.append('</ul></div></div>')
    # кампании
    p.append('<h2>Кампании</h2>')
    for c in data['campaigns']:
        r1, r7 = ratios(c['day']), ratios(c['d7'])
        st = STATUS_RU.get(c['status'], c['status'])
        tagc = 'alarm' if c['status'] == 'REJECTED' else ('mute' if c['state'] != 'ON' else '')
        ads_info = ', '.join(f'{STATUS_RU.get(s, s)} – {sum(1 for a in c["ads"] if a["status"] == s)}'
                             for s in sorted({a['status'] for a in c['ads']}))
        use = (c['d7']['cost'] / c['weekly'] * 100) if c['weekly'] else 0
        p.append(f'<div class="camp"><div class="head"><h3>{esc(c["name"])}</h3>'
                 f'<span class="tag {tagc}">{esc(STATE_RU.get(c["state"], c["state"]))} · {esc(st)}</span></div>'
                 f'<div class="sub">ID {c["id"]} · utm_campaign={esc(c["utm"])} · средняя цена клика {rub(c["avg_cpc"])} · '
                 f'цель: {esc(c["goal_name"])}, цель CPA {rub(c["target_cpa"])} · объявления: {esc(ads_info or "нет")}'
                 + (f' · {esc(c["clar"])}' if c['clar'] else '') + '</div>')
        p.append('<div class="mini">' +
                 fig('показы', num(c['day']['impr']), f'7 дн: {num(c["d7"]["impr"])}') +
                 fig('клики', num(c['day']['clicks']), f'7 дн: {num(c["d7"]["clicks"])}') +
                 fig('CTR', pct(r1['ctr']), f'7 дн: {pct(r7["ctr"])}') +
                 fig('цена клика', rub(r1['cpc']), f'7 дн: {rub(r7["cpc"])}') +
                 fig('расход', rub(c['day']['cost']), f'7 дн: {rub(c["d7"]["cost"])}') +
                 fig('заявки', num(c['day']['leads']), f'7 дн: {num(c["d7"]["leads"])}') +
                 fig('цена заявки', rub(r7['cpa']), f'контакты 7 дн: {num(c["d7"]["contacts"])}') + '</div>')
        if c['weekly']:
            p.append(f'<div class="sub">недельный лимит {rub(c["weekly"])}: потрачено за 7 дней {use:.0f} %</div>'
                     f'<div class="bar"><i style="width:{min(use, 100):.0f}%"></i></div>')
        if c['crit']:
            p.append('<table><tr><th class="l">куда ушли деньги, 7 дней</th><th>показы</th><th>клики</th><th>CTR</th><th>расход</th><th>заявки</th></tr>')
            for k, m in sorted(c['crit'].items(), key=lambda x: -x[1]['cost']):
                rr = ratios(m)
                p.append(f'<tr><td>{esc(CRIT_RU.get(k, k))}</td><td>{num(m["impr"])}</td><td>{num(m["clicks"])}</td>'
                         f'<td>{pct(rr["ctr"])}</td><td>{rub(m["cost"])}</td><td>{num(m["leads"])}</td></tr>')
            p.append('</table>')
        p.append('</div>')
    # запросы
    p.append('<h2>Поисковые запросы за 7 дней</h2><div class="sub">что люди набрали на самом деле. '
             '<span class="hl">выделено</span> – кандидат в минус-слова; «вчера» – запрос был и вчера</div>')
    if data['queries']:
        p.append('<table><tr><th class="l">запрос</th><th class="l">кампания · условие</th><th>показы</th><th>клики</th>'
                 '<th>расход</th><th>заявки</th><th></th></tr>')
        for q in data['queries'][:40]:
            badq = any(w in q['query'].split() or (' ' in w and w in q['query']) for w in STOP_WORDS)
            qt = f'<span class="hl">{esc(q["query"])}</span>' if badq else esc(q['query'])
            short = q['campaign'].replace('UV-Land – ', '').replace(' – Поиск', '')
            crit = q['criterion'] if q['criterion'] and q['criterion'] != '---autotargeting' else 'автотаргетинг'
            mark = '<span class="tag alarm">в минус</span>' if badq else ('<span class="tag mute">вчера</span>' if q['yesterday'] else '')
            p.append(f'<tr><td class="q">{qt}</td><td class="l">{esc(short)} · {esc(crit)}</td><td>{num(q["impr"])}</td>'
                     f'<td>{num(q["clicks"])}</td><td>{rub(q["cost"])}</td><td>{num(q["leads"])}</td><td>{mark}</td></tr>')
        p.append('</table>')
        if len(data['queries']) > 40:
            p.append(f'<div class="sub">и ещё {len(data["queries"]) - 40} запросов с меньшим расходом</div>')
    else:
        p.append('<div class="empty">Запросов пока нет: Директ отдаёт их после первых показов.</div>')
    # ключи
    p.append('<h2>Ключи за 7 дней</h2>')
    live = [k for k in data['keywords'] if k['impr'] > 0]
    zero = [k for k in data['keywords'] if k['impr'] == 0]
    if not live and zero:
        p.append(f'<div class="empty">Показов по ключам ещё не было ({len(zero)} ключей в кампаниях).</div>')
    if live:
        p.append('<table><tr><th class="l">ключ</th><th class="l">кампания</th><th>показы</th><th>клики</th><th>CTR</th><th>расход</th></tr>')
        for k in sorted(live, key=lambda k: (-k['impr'], k['keyword'])):
            short = k['campaign'].replace('UV-Land – ', '').replace(' – Поиск', '')
            ctr = k['clicks'] / k['impr'] * 100 if k['impr'] else None
            p.append(f'<tr><td class="q">{esc(k["keyword"])}</td><td class="l">{esc(short)}</td><td>{num(k["impr"]) if k["impr"] else "0"}</td>'
                     f'<td>{num(k["clicks"])}</td><td>{pct(ctr)}</td><td>{rub(k["cost"])}</td></tr>')
        p.append('</table>')
        if zero:
            p.append(f'<div class="sub" style="margin-top:4pt"><b>Без показов за 7 дней ({len(zero)}):</b> ' +
                     esc(', '.join(k['keyword'] for k in sorted(zero, key=lambda k: k['keyword']))) +
                     '. Людей с такими запросами почти нет: ключ не тратит деньги, но и не работает.</div>')
    # Метрика
    p.append('<h2>Что делали на сайте – Метрика, переходы из Директа, 7 дней</h2>')
    m7 = data['metrika'].get('d7') or {}
    if m7:
        heads = ''.join(f'<th>{esc(n)}</th>' for _, n in ENGAGEMENT)
        p.append(f'<table><tr><th class="l">utm_campaign</th><th>визиты</th><th>отказы</th><th>время</th>{heads}</tr>')
        for name, m in sorted(m7.items(), key=lambda x: -x[1]['visits']):
            cells = ''.join(f'<td>{num(m["goals"].get(k))}</td>' for k, _ in ENGAGEMENT)
            p.append(f'<tr><td>{esc(name)}</td><td>{num(m["visits"])}</td><td>{pct(m["bounce"], 0)}</td>'
                     f'<td>{int(m["dur"]) // 60}:{int(m["dur"]) % 60:02d}</td>{cells}</tr>')
        p.append('</table><div class="sub">цели вовлечения показывают интерес до заявки: кто играл, крутил витрину, выбирал набор</div>')
    else:
        p.append('<div class="empty">Переходов из Директа в Метрике пока нет.</div>')
    # устройства
    if data['devices']:
        p.append('<h2>Устройства, 7 дней</h2><table><tr><th class="l">устройство</th><th>показы</th><th>клики</th><th>CTR</th><th>цена клика</th><th>расход</th><th>заявки</th></tr>')
        for k, m in sorted(data['devices'].items(), key=lambda x: -x[1]['cost']):
            rr = ratios(m)
            p.append(f'<tr><td>{esc(DEV_RU.get(k, k))}</td><td>{num(m["impr"])}</td><td>{num(m["clicks"])}</td><td>{pct(rr["ctr"])}</td>'
                     f'<td>{rub(rr["cpc"])}</td><td>{rub(m["cost"])}</td><td>{num(m["leads"])}</td></tr>')
        p.append('</table>')
    # урок дня
    t, txt = LESSONS[today.toordinal() % len(LESSONS)]
    p.append(f'<div class="lesson"><div class="kicker">урок дня</div><h3>{esc(t)}</h3>{esc(txt)}</div>')
    if data['errors']:
        p.append('<div class="sub" style="margin-top:10pt">Не загрузилось: ' + esc('; '.join(data['errors'])) + '</div>')
    p.append('</body></html>')
    return ''.join(p)


# ------------------------------------------------------------------ PDF
BROWSERS = [r'C:\Program Files\Google\Chrome\Application\chrome.exe',          # Chrome печатает надёжно
            r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
            r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',     # Edge – запасной (на части ПК
            r'C:\Program Files\Microsoft\Edge\Application\msedge.exe']           # headless-печать закрыта политикой)


def to_pdf(html_path, pdf_path):
    # печатаем во временную папку (латиница в пути), потом переносим – кириллица в пути ломает часть сборок браузера
    tmp_pdf = os.path.join(tempfile.mkdtemp(prefix='dirik-out-'), 'report.pdf')
    tried = []
    for b in [x for x in BROWSERS if os.path.exists(x)]:
        prof = tempfile.mkdtemp(prefix='dirik-pdf-')
        cmd = [b, '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
               f'--user-data-dir={prof}', '--no-pdf-header-footer', '--virtual-time-budget=3000',
               f'--print-to-pdf={tmp_pdf}', 'file:///' + html_path.replace('\\', '/')]
        try:
            subprocess.run(cmd, check=False, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            pass
        if os.path.exists(tmp_pdf) and os.path.getsize(tmp_pdf) > 1000:
            shutil.move(tmp_pdf, pdf_path)
            return
        tried.append(os.path.basename(b))
    raise SystemExit('PDF не собрался, пробовал: ' + ', '.join(tried or ['браузер не найден']))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('client_dir')
    ap.add_argument('--date')
    ap.add_argument('--as-of', help='собрать пропущенное утро: «сегодня» = эта дата (ГГГГ-ММ-ДД)')
    ap.add_argument('--demo', action='store_true')
    ap.add_argument('--no-open', action='store_true')
    a = ap.parse_args()
    out_dir = os.path.join(a.client_dir, 'reports')
    os.makedirs(out_dir, exist_ok=True)
    log = open(os.path.join(out_dir, 'morning.log'), 'a', encoding='utf-8')
    sys.stdout = sys.stderr = log if sys.stdout is None or not sys.stdout.isatty() else sys.stdout
    today = dt.date.fromisoformat(a.as_of) if a.as_of else dt.date.today()
    day = report_day(today, dt.date.fromisoformat(a.date) if a.date else None)
    print(f'--- {dt.datetime.now():%Y-%m-%d %H:%M:%S} отчёт за {day} {"(демо)" if a.demo else ""}', flush=True)
    data = demo(day) if a.demo else collect(a.client_dir, day)
    tips = advise(data, today)
    name = f'{today.isoformat()}-utro{"-demo" if a.demo else ""}'
    html_path = os.path.abspath(os.path.join(tempfile.gettempdir(), name + '.html'))
    open(html_path, 'w', encoding='utf-8').write(render(data, tips, today))
    pdf_path = os.path.abspath(os.path.join(out_dir, name + '.pdf'))
    to_pdf(html_path, pdf_path)
    print('готово:', pdf_path, '| ошибки:', data['errors'] or 'нет', flush=True)
    if not a.no_open:
        os.startfile(pdf_path)


if __name__ == '__main__':
    main()
