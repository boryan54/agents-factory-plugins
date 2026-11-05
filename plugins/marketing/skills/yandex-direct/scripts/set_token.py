#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Помощник: сохранить токен API Яндекс Директа без ручного редактирования файлов.

    python3 set_token.py            (Windows: py set_token.py)

Что делает:
  1. Просит вставить токен. Ввод скрыт – символы на экране не видны, это нормально.
  2. Проверяет токен запросом к API Директа (только чтение).
  3. Сохраняет его в ~/.secrets/yandex-direct.env с правами «только для вас».
Токен никуда, кроме этого файла и серверов Яндекса, не отправляется.
"""
import getpass, json, os, re, stat, sys, urllib.error, urllib.request

PATH = os.path.expanduser('~/.secrets/yandex-direct.env')


def check(token, login=None):
    h = {'Authorization': 'Bearer ' + token, 'Accept-Language': 'ru',
         'Content-Type': 'application/json; charset=utf-8'}
    if login:
        h['Client-Login'] = login
    body = json.dumps({'method': 'get', 'params': {'FieldNames': ['Login']}}).encode()
    req = urllib.request.Request('https://api.direct.yandex.com/json/v5/clients', data=body, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        data = json.loads(e.read().decode() or '{}')
    except urllib.error.URLError as e:
        return None, f'нет связи с api.direct.yandex.com ({e.reason}). Проверьте интернет и попробуйте ещё раз.'
    if 'error' in data:
        code = int(data['error'].get('error_code', 0))
        hints = {
            53: 'токен не подходит. Скопируйте его заново целиком (шаг 3 инструкции) – без пробелов и без «&token_type».',
            58: 'заявка на доступ к API ещё не одобрена. Проверьте статус: Директ → «Настройки API» → «Мои заявки».',
            513: 'этот аккаунт Яндекса не подключён к Директу. Получайте токен под тем логином, где есть кампании.',
            54: 'нет прав на этот кабинет. Попросите владельца добавить вас представителем.',
        }
        return None, f'ошибка {code}: ' + hints.get(code, data['error'].get('error_detail') or data['error'].get('error_string', ''))
    logins = [c.get('Login') for c in data.get('result', {}).get('Clients', [])]
    return logins, None


def main():
    print('Вставьте токен API Яндекс Директа и нажмите Enter.')
    print('(ввод скрыт – после вставки на экране ничего не появится, это нормально)')
    raw = getpass.getpass('Токен: ').strip()
    m = re.search(r'access_token=([^&\s]+)', raw)      # если вставили всю адресную строку – вытащим токен
    token = m.group(1) if m else raw
    if not token or ' ' in token or not token.isascii() or len(token) < 20:
        sys.exit('Это не похоже на токен. Скопируйте из адресной строки часть после «access_token=» до знака «&».')

    print('Проверяю токен в Яндекс Директе…')
    logins, err = check(token)
    if err:
        sys.exit('Не получилось: ' + err)

    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    try:
        os.chmod(os.path.dirname(PATH), stat.S_IRWXU)
    except OSError:
        pass
    with open(PATH, 'w', encoding='utf-8') as f:
        f.write('# Токен API Яндекс Директа. Никому не отправляйте этот файл.\n')
        f.write(f'YANDEX_DIRECT_TOKEN={token}\n')
    try:
        os.chmod(PATH, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    print(f'Готово. Токен работает, кабинет: {", ".join(logins) or "–"}.')
    print(f'Сохранён в {PATH}')
    print('Теперь напишите в Claude Code: «Дирик, покажи мои кампании в Директе».')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nОтменено.')
