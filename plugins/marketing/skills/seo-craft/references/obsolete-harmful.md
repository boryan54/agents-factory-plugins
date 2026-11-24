# Что устарело и что вредит

> Справочник скилла `seo-craft` · редакция базы 2026-11-24 · полный отчёт и исходные заметки: `docs/research/seo-2026/` фабрики.
> Пометки силы доказательств: [офиц.] – Яндекс/Google, [иссл.] – исследование, [практ.] – практики, [вендор] – материал продавца сервиса.

## Что устарело, а что вредит

| Практика | Статус в 2026 | Почему |
|---|---|---|
| Турбо-страницы | отключены 01.04.2025 | ставка на скорость и мобильную версию основного сайта ([vc.ru](https://vc.ru/id2552260/2526273-yandex-vebmaster-2025-obnovleniya-i-novye-funktsii-servisa)) |
| Старый интерфейс Wordstat и сравнение частот с архивом | закрыт в августе 2025 | новые фильтры ботов занижают частоты ([workspace.ru](https://workspace.ru/blog/yandeks-vordstat-2025-2026-rukovodstvo-dlya-seo-i-kontekstnoy-reklamy/)) |
| Оплата «за позиции» по инфо-запросам | устарела | позиция в топе не гарантирует клик при ИИ-ответе |
| Проценты «тошноты» и точные вхождения как KPI | устарели и вредны | «Тургенев» штрафует повторы и переоптимизацию ([workspace.ru](https://workspace.ru/blog/kak-ya-podklyuchila-turgenev-k-ii-pomoschniku-i-avtomatizirovala-proverku-tekstov/)) |
| Disallow для UTM и прочих GET-параметров | хуже Clean-param | теряются метрики, Яндекс сам отбрасывает utm и ysclid ([Справка](https://yandex.ru/support/webmaster/ru/robot-workings/clean-param)) |
| Клиентский рендеринг SPA | рискован | рендеринг JS в Яндексе в бете ([Справка](https://yandex.ru/support/webmaster/ru/yandex-indexing/rendering)) |
| FAQ и HowTo ради сниппетов Google | не работает | FAQ ограничены, HowTo убраны ([Google](https://developers.google.com/search/blog/2025/11/update-on-our-efforts)) |
| llms.txt и «спецразметка для ИИ» как фактор | миф | 97% файлов без запросов, Google: не нужно ([Ahrefs](https://ahrefs.com/blog/llmstxt-study/)) |
| Блокировка «всех ИИ-ботов» шаблоном | вредна | выключает сайт из ChatGPT, Perplexity и Алисы AI |
| Массовая сырая ИИ-генерация | вредна | просадка через 4–8 месяцев, scaled content abuse ([Ink.](https://incrussia.ru/news/avtory-vzyali-revansh-kak-ii-teksty-teryali-pozitsii-v-yandekse-i-google/)) |
| Накрутка ПФ | рискованна | санкция на 8–12 месяцев после прекращения ([SEOnews](https://m.seonews.ru/events/yandeks-vozobnovil-pokazatelnye-porki-za-nakrutku-povedencheskikh-faktorov/)) |
| Массовая аренда анкорных ссылок, PBN на дропах, паразитное SEO | рискованны | Минусинск; link spam, expired domain и site reputation abuse в Google ([Google](https://developers.google.com/search/docs/essentials/spam-policies)) |
| Накрутка отзывов в картах | малоэффективна | Яндекс снижает вес новых авторов, 2ГИС учитывает только подтверждённые визиты ([AdIndex](https://adindex.ru/news/digital/2026/08/18/348083.phtml)) |
| Google Analytics, GTM, reCAPTCHA, Google Forms | юридически рискованны | локализация первичного сбора ПД с 01.07.2025 ([vc.ru](https://vc.ru/marketing/2153541-zapret-na-google-analytics-v-rossii)) |
| Google Ads, Merchant Center, YouTube как каналы для РФ | закрыты или ограничены | реклама с 2022, YouTube блокируется с 10.02.2026 ([PPC Land](https://ppc.land/google-still-blocks-russia-based-advertisers-a-pause-dating-to-march-2022/)) |
| Директива Host | не упоминается в справке | главное зеркало задаётся 301 и настройкой в Вебмастере |
| «Возраст и авторитет домена решают» для Алисы | не подтверждено | по Ашманову важнее постраничные факторы ([Habr](https://habr.com/ru/articles/1072206/)) |
