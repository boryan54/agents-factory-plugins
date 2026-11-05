# API Яндекс Директа: рецепты и ловушки (проверено на живых кабинетах, 2026)

Обёртка `scripts/direct_api.py` закрывает большую часть ловушек автоматически. Ключ – в
`~/.secrets/yandex-direct.env`, заголовок `Client-Login` – логин кабинета клиента.

## 0. Доступ
- Нужна одобренная заявка на API в кабинете Директа и OAuth-токен (см. README пакета).
- Агентский сервис `agencyclients` работает только у агентских аккаунтов; у рекламодателя – ошибка 54.
- Песочница часто не подключена – тестировать только чтением на боевом API.
- Токен Директа не даёт доступ к API Метрики – нужен отдельный токен со scope Метрики.
- Баллы: суточный лимит индивидуален (заголовок `Units`: использовано/осталось/лимит), ошибка стоит 20
  баллов, не больше 5 параллельных запросов, Reports – 20 запросов за 10 секунд.

## 1. Версии
- ЕПК-кампании (`UnifiedCampaign`), их стратегии, комбинаторные объявления (`ResponsiveAd`) – только
  `/json/v501/`. В v5 – ошибка 3500 «используйте v501» (обёртка повторяет сама).
- `ads.add` с `TextAd` сейчас создаёт `RESPONSIVE_AD`; `get` с `TextAdFieldNames` на комбинаторном вернёт
  только первый заголовок/текст/картинку. Читать `ResponsiveAdFieldNames`.
- **`ads.update` с `TextAd` на комбинаторном объявлении оставит один заголовок и одну картинку.** Править
  только через `ResponsiveAd`.

## 2. Формат массивов (разный в разных методах!)
| Метод | Массив |
|---|---|
| `ads.add` → `ResponsiveAd.AdImageHashes` | плоский список `["h1","h2"]` |
| `ads.update` → `ResponsiveAd.AdImageHashes` | обёртка `{"Items": ["h1","h2"]}` |
| `keywords.update` → `AutotargetingCategories` | плоский список объектов (с `Items` – ошибка 8000) |
| `keywords.get` → `AutotargetingCategories` | приходит с `Items` |
| `bidmodifiers.add` → `DemographicsAdjustments` | множественное число, массив |
| `bidmodifiers.get` → `DemographicsAdjustment` | единственное число; обязателен `SelectionCriteria.Levels` |

## 3. Рецепты

**Кампании со стратегией**
```json
POST /json/v501/campaigns
{"method":"get","params":{"SelectionCriteria":{},"FieldNames":["Id","Name","State","Status"],
 "UnifiedCampaignFieldNames":["BiddingStrategy"]}}
```
Оплата за конверсии: `BiddingStrategyType = PAY_FOR_CONVERSION`, структура
`PayForConversion {Cpa, GoalId, WeeklySpendLimit}`. Недельный бюджет ≥ 10 × CPA по справке; API в
отдельных кабинетах отклонял и меньше 20 × CPA – читать ответ. Суммы в API – в микро-единицах (× 1 000 000).

**Комбинаторное объявление-клон с другими картинками (A/B)**
```json
POST /json/v501/ads
{"method":"add","params":{"Ads":[{"AdGroupId":123,"ResponsiveAd":{
  "Titles":["…"],"Texts":["…"],"Href":"https://…","SitelinkSetId":1,
  "AdImageHashes":["h_1x1","h_16x9","h_3x4"],"AdExtensionIds":[1,2]}}]}}
```
Затем `ads.moderate`. Ошибка 7001 – в группе уже 3 неархивных объявления (остановленные тоже считаются):
освобождать слот архивацией или менять картинки в проигравшем через `ads.update` + `ads.resume`.

**Загрузка картинки**
```json
POST /json/v5/adimages {"method":"add","params":{"AdImages":[{"ImageData":"<base64>","Name":"client-2026-10-main-A-1x1"}]}}
```
Картинка должна быть точной пропорции (например 1344×768 – это не 16:9, ошибка 5004). `Name` – говорящее:
по нему читается срез «Изображение» в Мастере отчётов. После заливки: `adimages.get → Associated: YES`.
Живую картинку можно скачать: `https://avatars.mds.yandex.net/get-direct/<num>/<hash>/wx1080`
(номер и хеш – из `PreviewUrl`).

**Автотаргетинг**
- Условие показа `Keyword = "---autotargeting"` в сервисе `keywords`, Id = `20` + AdGroupId.
- Читать: `FieldNames: ["Id","AdGroupId","Keyword","State","AutotargetingCategories","AutotargetingBrandOptions","AutotargetingMode"]`.
- Сузить после создания группы:
```json
POST /json/v5/keywords {"method":"update","params":{"Keywords":[{"Id":20123456789,
 "AutotargetingCategories":[{"Category":"EXACT","Value":"YES"},{"Category":"ALTERNATIVE","Value":"NO"},
 {"Category":"COMPETITOR","Value":"NO"},{"Category":"BROADER","Value":"NO"},{"Category":"ACCESSORY","Value":"NO"}]}]}}
```
- Справка описывает новую структуру записи `AutotargetingSettings` (Categories Exact/Narrow/Alternative/
  Accessory/Broader + BrandOptions); передавать её вместе со старой нельзя. Если старая начнёт
  отклоняться – перейти на новую.
- `keywords.suspend` автотаргетинга на Поиске – ошибка 8305 (он обязателен).

**Площадки-исключения**
```json
POST /json/v501/campaigns {"method":"get","params":{"SelectionCriteria":{"Ids":[1]},"FieldNames":["Id","ExcludedSites"]}}
POST /json/v501/campaigns {"method":"update","params":{"Campaigns":[{"Id":1,"ExcludedSites":{"Items":["a.ru","com.x.y"]}}]}}
```
- Поле уровня кампании (не внутри `TextCampaign`/`UnifiedCampaign`).
- Запрос **атомарный**: одна отвергнутая площадка – не применилось ничего.
- `yandex.ru`, `ya.ru` исключить нельзя (ошибка 5006) – отфильтровать заранее.
- Сравнение без учёта регистра: дубль в другом регистре → ошибка 9802 и откат всего запроса.
  Мерджить старый список с новым по `.lower()`.
- Лимит 1 000 (медийные – 40). После update перечитать и сверить поштучно.

**Минус-фразы**: `campaigns.update → NegativeKeywords {"Items":[…]}` – передаётся ВЕСЬ список (прочитать,
добавить, записать). Словоформы уже стоящих молча отбрасываются – перечитать.

## 4. Отчёты (Reports API)
- `POST /json/v5/reports`, заголовки `processingMode: auto`, `returnMoneyInMicros: false`,
  `skipReportHeader/Summary: true`. Ответ 201/202 – отчёт готовится, повторить через `retryIn` секунд.
- `ReportName` уникален на каждый запрос – иначе вернётся кэш старого отчёта.
- **Всегда `Goals`** (иначе конверсии суммируются по всем целям – завышение в разы) и **всегда
  `AttributionModels`** (по умолчанию Reports считает LC, а стратегия – AUTO). Модели: AUTO, LSCCD, FCCD, LC.
- Колонки `Conversions_<goal>_<model>`, значение `--` = 0. Отчёт без колонок конверсий – это не ноль,
  а неправильный запрос.
- `Query` и `TargetingCategory` – только в `SEARCH_QUERY_PERFORMANCE_REPORT`. `CriterionType` (KEYWORD /
  AUTOTARGETING / RETARGETING) – в CUSTOM_REPORT.
- Реклама в Алисе AI: запрос – прочерк, площадка «Яндекс».
- В окнах с оплатой за конверсии «цена клика» бывает огромной – это списание за конверсии.

## 5. Прочие ловушки
- **Предупреждение ≠ успех.** `StrategyPriority` при автостратегии возвращает Warning 10161 «Приоритет не
  будет применён». Обёртка печатает все Warnings.
- Непереданные дни расписания (`TimeTargeting`) заполняются коэффициентом 100.
- Причины отклонения модерации API часто отдаёт только как «Отклонено» – детали в интерфейсе.
- Блокировка группы «по законодательству страны таргетинга» через API не видна (ACCEPTED, ELIGIBLE).
- Изменения автотаргетинга применяются до суток; модерация новых объявлений – 1–2 дня (статус OFF/MODERATION).

Источники: [API – ads.add](https://yandex.ru/dev/direct/doc/ru/ads/add),
[ads.update](https://yandex.ru/dev/direct/doc/ru/ads/update),
[переход ТГО → комбинаторные](https://yandex.com/dev/direct/doc/ru/update-tga),
[keywords.add](https://yandex.ru/dev/direct/doc/ru/keywords/add),
[баллы](https://yandex.ru/dev/direct/doc/ru/concepts/units),
[ограничения](https://yandex.ru/dev/direct/doc/ru/restrictions),
[спецификация отчётов](https://yandex.ru/dev/direct/doc/ru/spec),
[Хабр – ИИ-агент и API Директа, 02.09.2026](https://habr.com/ru/articles/1077680/).
