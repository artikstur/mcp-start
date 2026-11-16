# IFMIB KFU MCP

MCP-сервер для получения информации об Институте фундаментальной медицины и биологии КФУ и расписании учебных групп.

## Что умеет

* искать информацию об ИФМиБ КФУ;
* получать расписание группы;
* использовать OpenRouter для понимания вопроса пользователя;
* вызывать MCP-инструменты автоматически.

## Запуск

Нужен Python 3.12+.

### 1. Создать виртуальное окружение

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
```

### 2. Установить зависимости

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 3. Создать `.env`

Скопировать `.env.example` в `.env` и указать OpenRouter API key:

```env
REQUEST_TIMEOUT=20
MAX_CRAWL_PAGES=40

OPENROUTER_API_KEY=твой_ключ
OPENROUTER_MODEL=openai/gpt-4o-mini
OPENROUTER_EMBEDDING_MODEL=openai/text-embedding-3-small
OPENROUTER_MAX_TOKENS=2048
OPENROUTER_INPUT_PRICE_PER_MILLION=0.15
OPENROUTER_OUTPUT_PRICE_PER_MILLION=0.60
OPENROUTER_EMBEDDING_PRICE_PER_MILLION=0.02
MCP_START_USER_ID=local-user
MCP_START_ROLE=student
```

Расписание загружается параллельно (до 6 файлов одновременно) и кэшируется
локально на 15 минут. Фактические ответы подтверждаются вызванными MCP-
инструментами; ссылки на найденные документы добавляются в ответ. TLS-
сертификаты КФУ проверяются. Если в вашей сети нужен отдельный CA bundle,
укажите его путь в `KPFU_CA_BUNDLE`.

Результат расписания помечается как неполный, если хотя бы один опубликованный
файл не удалось загрузить или нельзя разобрать по группам. В таком случае бот
не утверждает, что группа или занятия отсутствуют.

### 4. Запустить FastAPI

В первом окне PowerShell:

```powershell
.venv\Scripts\python.exe -m uvicorn app.api:app --reload
```

Swagger:

http://127.0.0.1:8000/docs

### 5. Запустить AI-агента

Во втором окне PowerShell:

```powershell
.venv\Scripts\python.exe -m app.agent "Кто директор ИФМиБ КФУ?"
```

Можно задавать другие вопросы:

```powershell
.venv\Scripts\python.exe -m app.agent "Какие кафедры есть в ИФМиБ?"
```

```powershell
.venv\Scripts\python.exe -m app.agent "Какое расписание у группы 123456?"
```

На macOS/Linux используйте `.venv/bin/python` вместо `.venv\Scripts\python.exe`.
`student` — роль по умолчанию; `teacher` дополнительно может запрашивать список
страниц. Доступ к каждому инструменту проверяется в коде.

### Документы и семантический поиск

В `docs/corpus/` находятся текстовые снимки пяти открытых страниц КФУ с URL
источника. Создайте или обновите индекс embeddings и измерьте recall@3:

```bash
.venv/bin/python -m app.rag --build
.venv/bin/python -m app.rag --evaluate --k 3
```

MCP-инструмент `search_docs` возвращает ID фрагментов, выдержки и URL. Финальный
ответ валидируется по JSON-схеме; неверный формат получает одну повторную
попытку.

### Проверка безопасности

Запустите 10 атак на текущей модели и запишите фактический ASR:

```bash
.venv/bin/python -m app.security_eval
```

Добавьте контроль ложных отказов на обычных вопросах:

```bash
.venv/bin/python -m app.security_eval --include-normal
```

Результаты сохраняются в `eval/security_results.jsonl` и
`eval/security_results.summary.json`. `audit.jsonl` содержит роль, инструмент,
решение и названия полей аргументов без их значений; журнал исключён из Git.

### История и память

Агент хранит историю в SQLite (`MCP_START_DB`, по умолчанию
`data/mcp_start.sqlite3`). Укажите пользователя и сессию, чтобы разделять
диалоги:

```powershell
$env:MCP_START_USER_ID = "student-1"
$env:MCP_START_ROLE = "student"
.venv\Scripts\python.exe -m app.agent --session-id chat-1 "Я учусь в ИВТ-21"
```

Идентификатор и роль читаются из конфигурации процесса, а не из аргументов
пользовательского запроса. Для локального teacher-показа оператор может
установить `MCP_START_ROLE=teacher`. Это настройка доверенного локального
процесса; удалённому многопользовательскому сервису нужна отдельная
аутентификация и серверная выдача ролей.

Если агент предложит запомнить группу, курс или предмет, сохранение произойдёт
только после отдельного ответа «да». Ответ «нет» отклоняет сохранение. Команда
«забудь» запрашивает подтверждение, после которого удаляются история и факты
этого `user_id`. История сессий очищается через 7 дней, факты — через 180 дней.
Старые сообщения при превышении оценочного бюджета 2 000 токенов сворачиваются
с сохранением четырёх последних сообщений. Сводки и оценки контекста пишутся в
`steps.jsonl`.

### Диалоговые сценарии

Запустить локальные проверки датасета и правил выдачи контекста:

```powershell
.venv\Scripts\python.exe run.py
```

`golden.jsonl` содержит 10 сценариев по 2–3 реплики. `run.py` проверяет
`context_ok` и межпользовательские утечки. Это офлайн-проверка политики
контекста; `tool_ok`, `honest` и стоимость модели отображаются как `null`,
поскольку она не вызывает OpenRouter. Для тестов на stdlib:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Структура

```text
app/
├── agent.py          # OpenRouter + MCP
├── api.py            # FastAPI
├── mcp_server.py     # MCP tools
├── config.py
├── http.py
└── sources/
    ├── institute.py  # поиск информации об институте
    └── schedule.py   # расписание

skills/
├── institute-info/
└── schedule/
```
