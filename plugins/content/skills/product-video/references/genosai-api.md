# Genosai REST API — шпаргалка для product-video

Снято с живого `GET /v1/models` 2026-07-18. При сомнениях — перепроверить тем же запросом.

## Базовое

- Хост: `https://api.genosai.io` (прод; live-ключи `sdk_live_*` работают только здесь).
- Ключ: `source <корень фабрики>/.secrets/genosai.env` → `GENOSAI_API_KEY`, заголовок `Authorization: Bearer …`.
- Баланс: `GET /v1/balance` → `{total, main, bonus}`.
- Загрузка референсов: `POST /v1/uploads` (multipart `file`) → `{url}` на s3.genosai.io.
- Создание задачи: `POST /v1/createTask` `{"model": "...", "input": {...}}` → `data.taskId`.
- Статус: `GET /v1/taskInfo?taskId=…` → `data.status` (`succeeded`/`failed`/…),
  `data.result.media_urls[]`, `data.cost` (кредиты по факту).
- Список моделей: `GET /v1/models` → категории `photo`, `video`, `tts`, `music`.

## Модели пайплайна

### chatgpt-image-2 (раскадровка)
- `input`: `prompt` (до 20 000 симв.), `aspect_ratio` (`16:9` для раскадровки),
  `resolution`: `1K` | `2K` | `4K` — для сетки 3×3 брать `4K` (тайл выйдет 1280×720).
- Референсы: `input.image_urls` (до 16; jpeg/png/webp) — сюда URL карточки товара
  после `/v1/uploads`; при наличии референсов включается режим auto-edit.
- Цена: от 6 кредитов, растёт с resolution (4K ≈ 24).

### veo-3.1-fast / veo-3.1-lite (оживление кадров)
- `input`: `prompt`, `aspect_ratio` (`16:9` | `9:16` | `Auto`),
  `resolution` (`720p` | `1080p` | `4k` — нижний регистр «k»!),
  `duration` — строка `"4"` | `"6"` | `"8"`,
  `image_urls` (до 2) — первый URL = стартовый кадр.
- Цена: fast ≈ 180, lite ≈ 150 кредитов за клип (зависит от resolution).
- Запасные видеомодели, если veo лежит: `kling-3.0` (5/10 сек, image_urls до 2),
  `seedance-2.0` (4–15 сек, до 1080p, дешевле: ≈ 72).

### gemini-3.1-flash-tts (закадровая озвучка)
- `input`: `text` (обязателен), `voice`, `accent`, `style`, `pace`, `temperature` (0–2).
- Голоса (30): жен. — Kore (дефолт), Sulafat, Leda, Aoede, Callirrhoe…;
  муж. — Puck, Charon, Algieba, Orus, Fenrir…
- `style`: `Vocal Smile` (дефолт) | `Newscaster` | `Whisper` | `Empathetic` |
  `Promo/Hype` | `Deadpan`. Для товарных роликов: `Promo/Hype` или `Vocal Smile`.
- `pace`: `Natural` | `Rapid Fire` | `The Drift` | `Staccato`.
- Диалог на 2 голоса: поля `speakers[]` + `dialogue_turns[]` (для этого скилла не нужно).
- Цена: 1.5 кредита за 100 символов, минимум 5 кредитов за запрос.
- Темп (ЗАМЕРЕНО 2026-07-18, style Promo/Hype + pace Natural): ~2 слова/сек чистой речи,
  плюс ~0.5–1 сек тишины по краям файла → в сцену 4 сек влезает максимум 6–7 русских слов.

### suno-v5.5 (музыка)
- `input`: `prompt` (до 500 симв. в обычном режиме), `instrumental: true` —
  ОБЯЗАТЕЛЬНО для этого скилла (музыка без слов), `custom_mode`, `vocal_gender`.
- Отдаёт **2 трека** в `media_urls` за 16 кредитов (фикс).

## Смета (ФАКТ с прода 2026-07-18, ролик Janssen)

| Статья | Кредиты |
|---|---|
| Раскадровка chatgpt-image-2 4K | 16 |
| Видео 9 × veo-3.1-lite **720p** | **270 (по 30 за клип!)** |
| Озвучка 9 × TTS (с перегенерациями) | 105 |
| Музыка suno-v5.5 (2 трека) | 16 |
| **Итого черновик 720p** | **~350** |

Дефолтные цены из /v1/models (veo lite 150, fast 180) — это старшие разрешения;
на 720p видео дешевле в ~5 раз. Черновик 720p+lite — очень дешёвый, смело итерировать.

Фактические суммы — из `cost` в taskInfo; `product_video.py status` их складывает.

## Поведение при сбоях

- 401 — не тот хост или ключ; 402 — пополнить кредиты; 400 — сверить параметры с этим файлом.
- Задача висит >5 мин → пересоздать (вотчдог в скрипте это делает сам, 3 попытки).
- SSLEOFError на системном python — лечится свежей сессией + `Connection: close` (в скрипте есть).
