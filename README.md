# Interactive prototype

Vue.js frontend + Python FastAPI backend (venv) + OpenAI API.

```
backend/     Python API
frontend/    Vue 3 + Vite
```

## 1. Backend (venv)

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Put your [OpenAI API key](https://platform.openai.com/api-keys) in `backend/.env`, then:

```bash
python main.py
```

Health check: http://127.0.0.1:8000/api/health

## 2. Frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173

## Useful files

| File | Role |
| --- | --- |
| `backend/main.py` | Sessions, canvas, rationale-label API |
| `frontend/src/components/CanvasBoard.vue` | Infinite canvas after login |
| `frontend/src/components/RationaleModule.vue` | Reflection → labels |
| `frontend/src/api/rationale.js` | Label helpers + embedding client |
