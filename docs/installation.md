# Installation

OpenLLMAuth is a Python/FastAPI gateway. It can run from a checkout with `uv`
or from a standard virtual environment.

## Requirements

- Python 3.11 or newer
- `uv` recommended
- Network access to whichever model providers you configure

## Install With uv

```bash
git clone https://github.com/Es00bac/OpenLLMAuth.git
cd OpenLLMAuth
uv sync
```

Run the server:

```bash
uv run open-llm-auth serve --host 127.0.0.1 --port 8080
```

## Install With pip

```bash
git clone https://github.com/Es00bac/OpenLLMAuth.git
cd OpenLLMAuth
python -m venv .venv
. .venv/bin/activate
pip install -e .
open-llm-auth serve --host 127.0.0.1 --port 8080
```

## Verify Startup

```bash
curl http://127.0.0.1:8080/health
```

Expected response:

```json
{"status":"ok"}
```

Useful local pages:

- `http://127.0.0.1:8080/` - admin dashboard
- `http://127.0.0.1:8080/chat` - browser chat UI
- `http://127.0.0.1:8080/docs` - generated OpenAPI docs

## First Credential

Add an API key profile:

```bash
uv run open-llm-auth auth add-api-key openai --profile default
```

Set a default model:

```bash
uv run open-llm-auth models set-default openai/gpt-5.2
```

List usable models:

```bash
uv run open-llm-auth models list
```

## Local API Token

Protected routes require a bearer token unless you deliberately enable
anonymous local access. For normal use, configure a server token:

```bash
uv run open-llm-auth auth set-server-token
```

Then include the bearer token on protected API calls:

```bash
curl http://127.0.0.1:8080/v1/models \
  -H "Authorization: Bearer YOUR_LOCAL_TOKEN"
```
