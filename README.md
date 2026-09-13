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
```

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
