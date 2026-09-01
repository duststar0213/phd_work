# Interactive prototype / 交互式原型

Vue.js frontend + Python FastAPI backend (venv) + OpenAI API.

Vue.js 前端 + Python FastAPI 后端（venv）+ OpenAI API。

```
backend/     Python API
frontend/    Vue 3 + Vite
```

## 1. Backend / 后端（venv）

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Put your [OpenAI API key](https://platform.openai.com/api-keys) in `backend/.env`, then:

在 `backend/.env` 填入 [OpenAI API 密钥](https://platform.openai.com/api-keys)，然后：

```bash
python main.py
```

Health check: http://127.0.0.1:8000/api/health

## 2. Frontend / 前端

In a second terminal:

另开一个终端：

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173

## Useful files / 常用文件

| File | Role |
| --- | --- |
| `backend/main.py` | Chat endpoint that calls OpenAI |
| `frontend/src/components/ChatPanel.vue` | Chat UI |
| `frontend/src/api/chat.js` | `fetch('/api/chat')` helper |
