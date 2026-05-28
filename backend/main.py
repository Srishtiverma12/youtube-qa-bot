from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
from groq import Groq
from jose import JWTError, jwt
from passlib.context import CryptContext
import re, os, json, sqlite3
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Find Your YT Way", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
SECRET_KEY = os.environ.get("SECRET_KEY", "supersecretkey123changeme")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

client = Groq(api_key=GROQ_API_KEY)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/login")

# ── Database ──────────────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), "database.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            video_id TEXT NOT NULL,
            video_title TEXT NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_db()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ── Auth Helpers ──────────────────────────────────────────────────
def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def create_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")
        if not email:
            raise HTTPException(status_code=401, detail="Invalid token")
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        conn.close()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return dict(user)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

# ── Models ────────────────────────────────────────────────────────
class SignupRequest(BaseModel):
    name: str
    email: str
    password: str

class LoginRequest(BaseModel):
    email: str
    password: str

class VideoRequest(BaseModel):
    url: str

class QuestionRequest(BaseModel):
    video_id: str
    transcript: str
    video_title: str
    question: str
    chat_history: list = []

# ── Helpers ───────────────────────────────────────────────────────
def extract_video_id(url: str):
    patterns = [
        r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([^&\n?#]+)",
        r"youtube\.com/shorts/([^&\n?#]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

def get_transcript(video_id: str):
    try:
        ytt = YouTubeTranscriptApi()
        try:
            transcript_list = ytt.fetch(video_id, languages=["en"])
            lang = "en"
        except NoTranscriptFound:
            transcripts = ytt.list(video_id)
            transcript_obj = list(transcripts)[0]
            transcript_list = transcript_obj.fetch()
            lang = transcript_obj.language_code
        full_text = " ".join([entry.text for entry in transcript_list])
        return full_text, lang
    except TranscriptsDisabled:
        raise HTTPException(status_code=400, detail="This video has captions disabled.")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not fetch transcript: {str(e)}")

# ── Routes ────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    path = os.path.join(os.path.dirname(__file__), "..", "frontend", "index.html")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

@app.get("/health")
def health():
    return {"status": "healthy"}

@app.post("/api/signup")
def signup(req: SignupRequest):
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")
    if not req.email or "@" not in req.email:
        raise HTTPException(status_code=400, detail="Enter a valid email.")
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="Name cannot be empty.")
    conn = get_db()
    existing = conn.execute("SELECT id FROM users WHERE email=?", (req.email,)).fetchone()
    if existing:
        conn.close()
        raise HTTPException(status_code=400, detail="Email already registered. Please login.")
    hashed = hash_password(req.password)
    conn.execute("INSERT INTO users (name, email, password) VALUES (?, ?, ?)",
                 (req.name.strip(), req.email.lower().strip(), hashed))
    conn.commit()
    conn.close()
    token = create_token({"sub": req.email.lower().strip()})
    return {"token": token, "name": req.name.strip(), "email": req.email.lower().strip()}

@app.post("/api/login")
def login(req: LoginRequest):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email=?", (req.email.lower().strip(),)).fetchone()
    conn.close()
    if not user or not verify_password(req.password, user["password"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    token = create_token({"sub": req.email.lower().strip()})
    return {"token": token, "name": user["name"], "email": user["email"]}

@app.get("/api/me")
def get_me(current_user: dict = Depends(get_current_user)):
    return {"name": current_user["name"], "email": current_user["email"]}

@app.post("/api/load-video")
def load_video(req: VideoRequest, current_user: dict = Depends(get_current_user)):
    video_id = extract_video_id(req.url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Please enter a valid YouTube URL.")
    transcript, lang = get_transcript(video_id)
    snippet = transcript[:3000]
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": "You analyze YouTube video transcripts. Return ONLY valid JSON in English language, no markdown, no extra text."
                },
                {
                    "role": "user",
                    "content": f"""Analyze this transcript and return JSON only in ENGLISH.
The title and all suggested questions MUST be in English only.

Return this exact format:
{{"title":"video title in english","suggested_questions":["question 1 in english","question 2 in english","question 3 in english","question 4 in english","question 5 in english"]}}

Transcript: {snippet}"""
                }
            ],
            max_tokens=300,
            temperature=0.1
        )
        text = response.choices[0].message.content.strip()
        text = text.replace("```json", "").replace("```", "").strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            text = text[start:end]
        info = json.loads(text)
    except Exception:
        info = {
            "title": f"YouTube Video ({video_id})",
            "suggested_questions": [
                "What is the main topic of this video?",
                "Summarize the key points",
                "What are the important takeaways?",
                "Were any examples given?",
                "What was the conclusion?"
            ]
        }
    return {
        "video_id": video_id,
        "title": info.get("title", "YouTube Video"),
        "transcript": transcript,
        "transcript_length": len(transcript),
        "language": lang,
        "suggested_questions": info.get("suggested_questions", []),
        "thumbnail_url": f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg"
    }

@app.post("/api/ask")
def ask_question(req: QuestionRequest, current_user: dict = Depends(get_current_user)):
    try:
        messages = [
            {
                "role": "system",
                "content": f"""You are a helpful YouTube video Q&A assistant.
Always respond in English only.

Video Title: {req.video_title}
Transcript: {req.transcript[:6000]}

Rules:
- Answer ONLY from the transcript content
- Always answer in English
- If not in transcript say: "This wasn't covered in the video."
- Be concise and helpful
- Use bullet points for lists"""
            }
        ]
        for msg in req.chat_history[-8:]:
            if msg.get("role") in ["user", "assistant"] and msg.get("content"):
                messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": req.question})

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            max_tokens=800,
            temperature=0.5
        )
        answer = response.choices[0].message.content

        conn = get_db()
        conn.execute(
            "INSERT INTO history (user_id, video_id, video_title, question, answer) VALUES (?, ?, ?, ?, ?)",
            (current_user["id"], req.video_id, req.video_title, req.question, answer)
        )
        conn.commit()
        conn.close()

        return {"answer": answer}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI error: {str(e)}")

@app.get("/api/history")
def get_history(current_user: dict = Depends(get_current_user)):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM history WHERE user_id=? ORDER BY created_at DESC LIMIT 20",
        (current_user["id"],)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]