# Фабрика Агентов — плагины

Публичная витрина [Фабрики Агентов](https://github.com/boryan54): готовые плагины
Claude Code с командой специализированных агентов и их скиллами.

## Установка

```
/plugin marketplace add boryan54/agents-factory-plugins
/plugin install af-meta@agents-factory
```

Или в `.claude/settings.json` проекта:

```json
{
  "extraKnownMarketplaces": {
    "agents-factory": { "source": { "source": "github", "repo": "boryan54/agents-factory-plugins" } }
  },
  "enabledPlugins": { "af-web@agents-factory": true }
}
```

## Отделы

| Плагин | Отдел | Агенты |
|---|---|---|
| `af-code` | Код | `analyst`, `builder`, `coder`, `deployer`, `spec-writer`, `tester` |
| `af-content` | ПродКонтент | `presenter`, `video-maker` |
| `af-marketing` | Маркетинг | `market-analyst`, `tg-analyst` |
| `af-meta` | Мета | `creator`, `evaluator`, `skill-scout` |
| `af-office` | Офис | `lawyer`, `secretary` |
| `af-ops` | Операционный | `process-writer` |
| `af-web` | Веб | `site-builder`, `web-analyst` |

## Как это устроено

Каждый агент — персонаж со своей ролью, характером и историей версий. Источник
правды — приватный репозиторий фабрики; сюда автоматически выкладывается только
сборка плагинов (`py tools/publish.py`). Вручную здесь ничего не правится:
правки затрутся следующей публикацией.
