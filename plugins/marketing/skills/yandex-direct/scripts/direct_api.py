#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Обёртка над API Яндекс Директа для агента «Дирик». Только стандартная библиотека Python 3.8+.

Ключ берётся из переменной YANDEX_DIRECT_TOKEN или из файла ~/.secrets/yandex-direct.env
(строка YANDEX_DIRECT_TOKEN=...). В код и в чат ключ не пишется никогда.

Командная строка (всё только ЧТЕНИЕ, кабинет не меняется):
    python3 direct_api.py check                      # токен работает? сколько баллов API осталось
    python3 direct_api.py campaigns [LOGIN]          # кампании: статус, тип, стратегия, недельный бюджет
    python3 direct_api.py autotargeting LOGIN GROUP_ID [GROUP_ID ...]   # категории автотаргетинга групп

LOGIN – логин кабинета клиента (заголовок Client-Login). Для своего кабинета можно не указывать.

Как библиотека:
    from direct_api import Direct
    d = Direct('client-login')
    d.call('campaigns', 'get', {...})           # сам выберет v5 или v501
    rows = d.report(['CampaignName', 'Clicks', 'Cost'], '2026-09-01', '2026-09-24', goals=['123'])

Ловушки, которые обёртка закрывает сама:
  * ЕПК-кампании и комбинаторные объявления (ResponsiveAd) в campaigns/ads – только /json/v501; при ошибке 3500
    («используйте v501») запрос автоматически повторяется в v501.
  * Reports API: 201/202 = отчёт готовится, ждём; ReportName уникален на каждый запрос (иначе кэш);
    модель атрибуции задаётся ЯВНО (по умолчанию AUTO – как в стратегиях; сам Reports API без неё считает LC,
    и CPA в отчёте разойдётся с кабинетом). Действующие модели: AUTO, LSCCD, FCCD, LC.
    колонки конверсий приходят как Conversions_<goal>_<модель>, значение «--» = 0.
  * Ответ с Warnings печатается: формальный успех с предупреждением (например 10161 «Приоритет не будет применён»)
    – это НЕ успех.
"""
import csv, io, json, os, re, sys, time, urllib.error, urllib.request

API = 'https://api.direct.yandex.com/json/'
V501_FIRST = {'campaigns', 'ads'}


def _read_env(path, key):
    p = os.path.expanduser(path)
    if not os.path.exists(p):
        return None
    for line in open(p, encoding='utf-8'):
        m = re.match(r'\s*(?:export\s+)?' + key + r'\s*=\s*["\']?([^"\'\s]+)', line)
        if m:
            return m.group(1)
    return None


def token():
    t = os.environ.get('YANDEX_DIRECT_TOKEN') or _read_env('~/.secrets/yandex-direct.env', 'YANDEX_DIRECT_TOKEN')
    if t and ('вставьте' in t or not t.isascii()):
        t = None
    if not t:
        raise SystemExit('Нет ключа API Директа. Создайте файл ~/.secrets/yandex-direct.env со строкой '
                         'YANDEX_DIRECT_TOKEN=<токен> (как получить – README.md пакета).')
    return t


class DirectError(Exception):
    def __init__(self, code, detail, raw):
        super().__init__(f'{code}: {detail}')
        self.code, self.detail, self.raw = code, detail, raw


class Direct:
    def __init__(self, login=None, verbose=False):
        self.login = login
        self.verbose = verbose
        self.units = None
        self.h = {'Authorization': 'Bearer ' + token(), 'Accept-Language': 'ru',
                  'Content-Type': 'application/json; charset=utf-8'}
        if login:
            self.h['Client-Login'] = login

    # ------------------------------------------------------------ сервисы
    def _post(self, url, body, headers=None):
        req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode(),
                                     headers=headers or self.h)
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    self.units = r.headers.get('Units') or self.units
                    return r.status, r.headers, r.read().decode()
            except urllib.error.HTTPError as e:
                if e.code in (500, 502, 503, 504) and attempt < 4:
                    time.sleep(3 * (attempt + 1)); continue
                return e.code, e.headers, e.read().decode()
            except (urllib.error.URLError, TimeoutError):
                if attempt < 4:
                    time.sleep(3 * (attempt + 1)); continue
                raise

    def call(self, service, method, params, version=None):
        ver = version or ('v501' if service in V501_FIRST else 'v5')
        status, hdr, text = self._post(API + ver + '/' + service, {'method': method, 'params': params})
        data = json.loads(text) if text else {}
        if 'error' in data:
            err = data['error']
            code = int(err.get('error_code', 0))
            if code == 3500 and ver == 'v5':
                return self.call(service, method, params, version='v501')
            if code == 53:
                raise DirectError(code, 'токен не подходит (ошибка авторизации 53). Получите новый токен по README, '
                                        'раздел «Ключи», и проверьте, что заявка на доступ к API одобрена.', err)
            if code == 58:
                raise DirectError(code, 'заявка на доступ к API ещё не одобрена или регистрация не завершена '
                                        '(ошибка 58). Проверьте «Настройки API → Мои заявки».', err)
            raise DirectError(code, err.get('error_detail') or err.get('error_string'), err)
        res = data.get('result', {})
        # предупреждения внутри *Results – печатаем, чтобы «успех с warning» не прошёл незамеченным
        for k, v in res.items():
            if k.endswith('Results') and isinstance(v, list):
                for item in v:
                    for w in item.get('Warnings') or []:
                        print(f'  ⚠ {service}.{method} Warning {w.get("Code")}: {w.get("Message")} {w.get("Details", "")}',
                              file=sys.stderr)
                    for e in item.get('Errors') or []:
                        print(f'  ✖ {service}.{method} Error {e.get("Code")}: {e.get("Message")} {e.get("Details", "")}',
                              file=sys.stderr)
        return res

    # ------------------------------------------------------------ отчёты
    def report(self, fields, date_from, date_to, report_type='CUSTOM_REPORT', goals=None,
               attribution='AUTO', filters=None, include_vat=True):
        """Отчёт Reports API. Возвращает список словарей. Конверсии: ключ 'conv' (сумма по целям), 'revenue'."""
        h = dict(self.h, processingMode='auto', returnMoneyInMicros='false',
                 skipReportHeader='true', skipReportSummary='true')
        params = {
            'SelectionCriteria': {'DateFrom': date_from, 'DateTo': date_to},
            'FieldNames': list(fields),
            'ReportName': f'dima_{report_type}_{int(time.time() * 1000)}',
            'ReportType': report_type, 'DateRangeType': 'CUSTOM_DATE',
            'Format': 'TSV', 'IncludeVAT': 'YES' if include_vat else 'NO',
        }
        if filters:
            params['SelectionCriteria']['Filter'] = filters
        if goals:
            params['Goals'] = [str(g) for g in goals]
            params['AttributionModels'] = [attribution]
            if 'Conversions' not in params['FieldNames']:
                params['FieldNames'].append('Conversions')
        for _ in range(60):
            status, hdr, text = self._post(API + 'v5/reports', {'params': params}, headers=h)
            if status in (201, 202):
                time.sleep(int(hdr.get('retryIn', 5)) if hdr else 5); continue
            if status != 200:
                raise DirectError(status, text[:400], text)
            rows = list(csv.DictReader(io.StringIO(text), delimiter='\t'))
            for r in rows:
                conv = 0
                for k in list(r):
                    if k.startswith('Conversions'):
                        v = r[k]
                        conv += int(v) if v not in ('--', '', None) else 0
                r['conv'] = conv
            return rows
        raise DirectError(0, 'отчёт не готов за 5 минут', '')


# ------------------------------------------------------------ командная строка
def _cli():
    args = sys.argv[1:]
    if not args:
        print(__doc__); return
    cmd = args[0]
    if cmd == 'check':
        d = Direct(args[1] if len(args) > 1 else None)
        r = d.call('clients', 'get', {'FieldNames': ['Login', 'ClientId']})
        print('Токен работает. Кабинет:', ', '.join(c['Login'] for c in r.get('Clients', [])))
        print('Баллы API (использовано/осталось/суточный лимит):', d.units)
    elif cmd == 'campaigns':
        d = Direct(args[1] if len(args) > 1 else None)
        r = d.call('campaigns', 'get', {
            'SelectionCriteria': {},
            'FieldNames': ['Id', 'Name', 'Type', 'State', 'Status', 'StatusClarification'],
            'UnifiedCampaignFieldNames': ['BiddingStrategy'],
            'TextCampaignFieldNames': ['BiddingStrategy']})
        for c in r.get('Campaigns', []):
            bs = (c.get('UnifiedCampaign') or c.get('TextCampaign') or {}).get('BiddingStrategy') or {}
            def brief(side):
                s = bs.get(side) or {}
                t = s.get('BiddingStrategyType', '–')
                inner = next((v for k, v in s.items() if isinstance(v, dict)), {}) or {}
                lim = inner.get('WeeklySpendLimit')
                goal = inner.get('GoalId')
                cpa = inner.get('Cpa') or inner.get('AverageCpa')
                extra = []
                if lim: extra.append(f'лимит {lim / 1_000_000:.0f} ₽/нед')
                if cpa: extra.append(f'CPA {cpa / 1_000_000:.0f} ₽')
                if goal: extra.append(f'цель {goal}')
                return t + (f' ({", ".join(extra)})' if extra else '')
            print(f"{c['Id']}  {c['State']:<10} {c['Type']:<18} {c['Name']}")
            print(f"      поиск: {brief('Search')}")
            print(f"      сети:  {brief('Network')}")
    elif cmd == 'autotargeting':
        if len(args) < 3:
            raise SystemExit('нужно: autotargeting LOGIN GROUP_ID [...]')
        d = Direct(args[1])
        # Проверено на живом API 25.09.2026: в keywords.get есть AutotargetingCategories, AutotargetingBrandOptions,
        # AutotargetingMode (структуры AutotargetingSettings из справки в get НЕТ – ошибка 8000).
        r = d.call('keywords', 'get', {'SelectionCriteria': {'AdGroupIds': [int(x) for x in args[2:]]},
                   'FieldNames': ['Id', 'AdGroupId', 'Keyword', 'State', 'AutotargetingCategories',
                                  'AutotargetingBrandOptions', 'AutotargetingMode']})
        for k in r.get('Keywords', []):
            if k['Keyword'] != '---autotargeting':
                continue
            cats = {i['Category']: i['Value'] for i in (k.get('AutotargetingCategories') or {}).get('Items', [])}
            bo = k.get('AutotargetingBrandOptions') or {}
            brands = {i['Option']: i['Value'] for i in bo.get('Items', [])} if 'Items' in bo else bo
            mode = f", режим {k['AutotargetingMode']}" if k.get('AutotargetingMode') else ''
            print(f"группа {k['AdGroupId']} (Id условия {k['Id']}, {k['State']}{mode}):")
            print('   категории:', ', '.join(f'{c}={v}' for c, v in cats.items()))
            if brands:
                print('   бренды:   ', ', '.join(f'{c}={v}' for c, v in brands.items()))
    else:
        print(__doc__)


if __name__ == '__main__':
    try:
        _cli()
    except DirectError as e:
        raise SystemExit(f'Ошибка API Директа {e.code}: {e.detail}')
