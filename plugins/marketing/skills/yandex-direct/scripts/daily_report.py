#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ежедневный отчёт «Дирика» по кабинету Яндекс Директа – 8 срезов, только ЧТЕНИЕ (в кабинете ничего не меняет).

    python3 daily_report.py LOGIN --goal GOAL_ID [--days 7] [--attribution AUTO] [--campaigns ID,ID] [--out report.md]

  LOGIN      логин кабинета клиента (Client-Login)
  --goal     ID цели Метрики, по которой считаем результат (заявка / оплата). Можно несколько через запятую.
             Без цели отчёт считает ВСЕ цели счётчика – это завышает конверсии в разы, поэтому цель обязательна.
  --days     окно в днях (по умолчанию 7). Решения об отключении – только по окну ≥ 7 дней:
             конверсии дописываются 2–3 дня, короткое окно режет рабочие площадки.

Что в отчёте:
  1. Кампании: показы, клики, расход, конверсии, CPA
  2. Типы условий показа: ключи / автотаргетинг / ретаргетинг – куда реально уходят деньги
  3. Площадки РСЯ с пометками «в бан» (≥10 кликов без конверсий, CTR > 5 % на приложениях/DSP = фрод)
  4. Поисковые запросы с пометкой «кандидат в минус» (≥5 кликов без конверсий) – решать по окну 30 дней
  5. Устройства  6. Пол и возраст  7. Регионы  8. Объявления (A/B внутри групп)
Пороги – справка, а не приказ: на выборке < 100 кликов агент ничего не отключает.
"""
import argparse, datetime as dt, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from direct_api import Direct, DirectError  # noqa: E402

BAN_CLICKS = 10          # площадка: столько кликов без единой конверсии → кандидат в бан
MINUS_CLICKS = 5         # запрос: столько кликов без конверсий → кандидат в минус (решать по 30 дням)
FRAUD_CTR = 5.0          # CTR в сетях выше этого на приложениях/DSP → подозрение на фрод
NOISE_CLICKS = 100       # меньше – шум, выводов не делаем


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def agg(rows, key):
    out = {}
    for r in rows:
        k = key(r)
        a = out.setdefault(k, {'impr': 0, 'clicks': 0, 'cost': 0.0, 'conv': 0})
        a['impr'] += int(f(r.get('Impressions')))
        a['clicks'] += int(f(r.get('Clicks')))
        a['cost'] += f(r.get('Cost'))
        a['conv'] += r.get('conv', 0)
    return out


def line(name, a, extra=''):
    ctr = a['clicks'] / a['impr'] * 100 if a['impr'] else 0
    cpa = f"{a['cost'] / a['conv']:.0f} ₽" if a['conv'] else '–'
    return f"| {name} | {a['impr']} | {a['clicks']} | {ctr:.2f} % | {a['cost']:.0f} ₽ | {a['conv']} | {cpa} |{extra}"


HEAD = '| {} | Показы | Клики | CTR | Расход | Конв. | CPA |'
SEP = '|---|---|---|---|---|---|---|'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('login')
    ap.add_argument('--goal', required=True)
    ap.add_argument('--days', type=int, default=7)
    ap.add_argument('--attribution', default='AUTO')
    ap.add_argument('--campaigns', default='')
    ap.add_argument('--out')
    a = ap.parse_args()

    d = Direct(a.login)
    goals = [g.strip() for g in a.goal.split(',') if g.strip()]
    to = dt.date.today() - dt.timedelta(days=1)
    fr = to - dt.timedelta(days=a.days - 1)
    F, T = fr.isoformat(), to.isoformat()
    flt = [{'Field': 'CampaignId', 'Operator': 'IN', 'Values': a.campaigns.split(',')}] if a.campaigns else None
    rep = lambda fields, rt='CUSTOM_REPORT', extra=None: d.report(
        fields + ['Impressions', 'Clicks', 'Cost'], F, T, report_type=rt, goals=goals,
        attribution=a.attribution, filters=(flt or []) + (extra or []) or None)

    out = [f'# Отчёт Директа · {a.login} · {F} – {T} ({a.days} дн.) · цель {", ".join(goals)} · атрибуция {a.attribution}', '']
    if a.days < 7:
        out += ['> ⚠ Окно короче 7 дней: конверсии ещё дописываются. Площадки и запросы по этому окну НЕ отключать.', '']

    # 1. Кампании
    rows = rep(['CampaignId', 'CampaignName'])
    camp = agg(rows, lambda r: r['CampaignName'])
    tot = {'impr': 0, 'clicks': 0, 'cost': 0.0, 'conv': 0}
    for v in camp.values():
        for k in tot: tot[k] += v[k]
    out += ['## 1. Кампании', HEAD.format('Кампания'), SEP]
    out += [line(k, v) for k, v in sorted(camp.items(), key=lambda x: -x[1]['cost'])]
    out += [line('**Итого**', tot), '']
    if tot['clicks'] < NOISE_CLICKS:
        out += [f'> Всего {tot["clicks"]} кликов – меньше {NOISE_CLICKS}. Это шум: выводов об эффективности не делаем.', '']

    # 2. Типы условий показа
    try:
        rows = rep(['CriterionType'])
        ct = agg(rows, lambda r: r['CriterionType'])
        out += ['## 2. Куда уходят деньги: ключи / автотаргетинг / ретаргетинг', HEAD.format('Тип'), SEP]
        out += [line(k, v, f' {v["cost"] / tot["cost"] * 100:.0f} % расхода' if tot['cost'] else '')
                for k, v in sorted(ct.items(), key=lambda x: -x[1]['cost'])]
        out += ['', '> Если автотаргетинг забирает больше половины расхода и дороже за конверсию – сужать его '
                'категории (keywords.update), а не добивать минус-словами.', '']
    except DirectError as e:
        out += [f'## 2. Типы условий – недоступно: {e}', '']

    # 3. Площадки РСЯ
    rows = rep(['Placement'], extra=[{'Field': 'AdNetworkType', 'Operator': 'EQUALS', 'Values': ['AD_NETWORK']}])
    pl = agg(rows, lambda r: r['Placement'])
    out += ['## 3. Площадки РСЯ (топ-30 по расходу)', HEAD.format('Площадка') + ' Пометка |', SEP + '---|']
    bans = []
    for k, v in sorted(pl.items(), key=lambda x: -x[1]['cost'])[:30]:
        ctr = v['clicks'] / v['impr'] * 100 if v['impr'] else 0
        mark = ''
        if k.lower() in ('yandex.ru', 'ya.ru', 'dsp.yandex.ru'):
            mark = 'не исключается / не банить'
        elif v['clicks'] >= BAN_CLICKS and v['conv'] == 0 and a.days >= 7:
            mark = 'В БАН?'; bans.append(k)
        elif ctr > FRAUD_CTR and (k.startswith(('com.', 'ru.')) or 'dsp' in k) and v['clicks'] >= 5:
            mark = 'ФРОД? CTR аномальный'; bans.append(k)
        out.append(line(k, v, f' {mark} |'))
    out += ['', f'Кандидатов в бан: {len(bans)}. Перед исключением: окно ≥ 7 дней, сверка с другими целями заявки, '
            'ExcludedSites – дедуп по регистру, перечитать после update.', '']

    # 4. Поисковые запросы
    try:
        rows = rep(['Query'], rt='SEARCH_QUERY_PERFORMANCE_REPORT')
        q = agg(rows, lambda r: r['Query'])
        cand = [(k, v) for k, v in q.items() if v['clicks'] >= MINUS_CLICKS and v['conv'] == 0]
        out += ['## 4. Поисковые запросы: кандидаты в минус (решать по окну 30 дней, фразами, не одиночными словами)',
                HEAD.format('Запрос'), SEP]
        out += [line(k, v) for k, v in sorted(cand, key=lambda x: -x[1]['cost'])[:30]] or ['| нет | | | | | | |']
        out += ['', '> ⚠ Это кандидаты, а не список на удаление. Названия ваших продуктов и брендов – ядро кампании, '
                'их НЕ минусуют, даже если за неделю нет конверсий: смотрите окно 30 дней и базовую частоту '
                '(сколько кликов в среднем приходится на одну конверсию; «ноль» доказан только после ~3× этого числа).', '']
    except DirectError as e:
        out += [f'## 4. Запросы – недоступно: {e}', '']

    # 5–7. Срезы
    for title, field, name in (('5. Устройства', 'Device', 'Устройство'),
                               ('6. Пол и возраст', None, 'Пол · возраст'),
                               ('7. Регионы (топ-15)', 'LocationOfPresenceName', 'Регион')):
        try:
            if field:
                rows = rep([field]); g = agg(rows, lambda r, fl=field: r[fl])
            else:
                rows = rep(['Gender', 'Age']); g = agg(rows, lambda r: f"{r['Gender']} · {r['Age']}")
            items = sorted(g.items(), key=lambda x: -x[1]['cost'])
            if field == 'LocationOfPresenceName':
                items = items[:15]
            out += [f'## {title}', HEAD.format(name), SEP] + [line(k, v) for k, v in items] + ['']
        except DirectError as e:
            out += [f'## {title} – недоступно: {e}', '']
    out += ['> Возрастные метки Директа неточны: часть взрослой аудитории попадает в «0–17». '
            'Корректировки по возрасту – только на большой выборке и с проверкой по CRM.', '']

    # 8. Объявления
    rows = rep(['AdGroupName', 'AdId'])
    ads = agg(rows, lambda r: f"{r['AdGroupName']} · {r['AdId']}")
    out += ['## 8. Объявления (A/B внутри групп)', HEAD.format('Группа · объявление'), SEP]
    out += [line(k, v) for k, v in sorted(ads.items())] + ['',
            '> Решение по A/B картинок – `rsya_ab.py report` (z-тест, от 3 000 показов на вариант, p < 0,05).', '']

    text = '\n'.join(out)
    if a.out:
        open(a.out, 'w', encoding='utf-8').write(text)
        print('сохранено:', a.out)
    print(text)


if __name__ == '__main__':
    try:
        main()
    except DirectError as e:
        raise SystemExit(f'Ошибка API Директа {e.code}: {e.detail}')
