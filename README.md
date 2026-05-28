# 🎬 Find Your YT Way

A full-stack AI-powered YouTube Q&A web app — paste any YouTube video URL and have a conversation about its content using AI.

🌐 **Live Demo:** https://youtube-qa-bot-production.up.railway.app

---

## ✨ Features

- 🔐 User Authentication (Signup / Login / Logout)
- 🎥 YouTube Transcript fetching (real captions)
- 🤖 AI-powered Q&A using Groq (LLaMA 3.3)
- 💬 Full chat history per session
- 📊 Q&A history saved per user
- 🌙 Dark cinematic UI design
- 🚀 Deployed on Railway

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python, FastAPI |
| AI Model | Groq API (LLaMA 3.3 70B) |
| Transcripts | YouTube Transcript API |
| Auth | JWT Tokens + bcrypt |
| Database | SQLite |
| Frontend | HTML, CSS, JavaScript |
| Deployment | Railway |

---

## 📁 Project Structure
youtube-qa-bot/
├── backend/
│   ├── main.py          ← FastAPI app (API + Auth + DB)
│   └── requirements.txt
├── frontend/
│   └── index.html       ← Full UI (Landing + Login + Dashboard)
├── requirements.txt     ← Railway dependencies
├── railway.toml         ← Railway config
└── README.md

---

## 🚀 Local Setup

### 1. Clone the repo
```bash
git clone https://github.com/Srishtiverma12/youtube-qa-bot.git
cd youtube-qa-bot
```

### 2. Virtual environment banao
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 3. `.env` file banao
```bash
# backend/.env
GROQ_API_KEY=your_groq_api_key_here
SECRET_KEY=your_secret_key_here
```

### 4. Server start karo
```bash
uvicorn backend.main:app --reload --port 8000
```

### 5. Browser mein kholo
http://localhost:8000

---

## 🔑 Environment Variables

| Variable | Description |
|----------|-------------|
| `GROQ_API_KEY` | Groq API key from console.groq.com |
| `SECRET_KEY` | Any random secret string for JWT |

---


### Landing Page
> Clean dark theme with feature highlights

### Dashboard
> Paste YouTube URL → Get transcript → Ask anything

---

## 🙏 Built With

- [FastAPI](https://fastapi.tiangolo.com)
- [Groq](https://console.groq.com)
- [YouTube Transcript API](https://github.com/jdepoix/youtube-transcript-api)
- [Railway](https://railway.app)

---

Made by Srishti Verma
