from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
from groq import Groq
from jose import JWTError, jwt
from passlib.context import CryptContext
import re, os, json, sqlite3, httpx
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Find Your YT Way", version="4.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
SECRET_KEY = os.environ.get("SECRET_KEY", "supersecretkey123")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24
FAVOURITE_THRESHOLD = 3

client = Groq(api_key=GROQ_API_KEY)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/login")

# ── Database ──────────────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), "database.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS watch_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        video_id TEXT NOT NULL,
        video_title TEXT NOT NULL,
        thumbnail_url TEXT,
        channel_id TEXT,
        channel_name TEXT,
        channel_analyze_count INTEGER DEFAULT 1,
        video_analyze_count INTEGER DEFAULT 1,
        is_favourite INTEGER DEFAULT 0,
        last_watched TEXT DEFAULT CURRENT_TIMESTAMP,
        first_watched TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS qa_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        video_id TEXT NOT NULL,
        video_title TEXT NOT NULL,
        question TEXT NOT NULL,
        answer TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.commit()
    conn.close()

init_db()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ── Auth ──────────────────────────────────────────────────────────
def hash_password(p): return pwd_context.hash(p)
def verify_password(p, h): return pwd_context.verify(p, h)

def create_token(data):
    d = data.copy()
    d.update({"exp": datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)})
    return jwt.encode(d, SECRET_KEY, algorithm=ALGORITHM)

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

# ── YouTube Channel Info ──────────────────────────────────────────
def get_channel_info(video_id: str):
    try:
        url = f"https://www.googleapis.com/youtube/v3/videos?part=snippet&id={video_id}&key={YOUTUBE_API_KEY}"
        res = httpx.get(url, timeout=10)
        data = res.json()
        if data.get("items"):
            snippet = data["items"][0]["snippet"]
            return {
                "channel_id": snippet.get("channelId", ""),
                "channel_name": snippet.get("channelTitle", "Unknown Channel")
            }
    except Exception:
        pass
    return {"channel_id": "", "channel_name": "Unknown Channel"}

# ── Track Watch History ───────────────────────────────────────────
def track_video(user_id, video_id, video_title, thumbnail_url, channel_id, channel_name):
    conn = get_db()
    now = datetime.utcnow().isoformat()

    existing = conn.execute(
        "SELECT * FROM watch_history WHERE user_id=? AND video_id=?",
        (user_id, video_id)
    ).fetchone()

    # Count how many videos from this channel user has watched
    channel_count = conn.execute(
        "SELECT COUNT(DISTINCT video_id) FROM watch_history WHERE user_id=? AND channel_id=?",
        (user_id, channel_id)
    ).fetchone()[0]

    if existing:
        new_video_count = existing["video_analyze_count"] + 1
        # Auto favourite if same channel watched 3+ times OR same video analyzed 3+ times
        is_fav = 1 if (channel_count >= FAVOURITE_THRESHOLD or new_video_count >= FAVOURITE_THRESHOLD) else existing["is_favourite"]
        conn.execute("""
            UPDATE watch_history
            SET video_analyze_count=?, is_favourite=?, last_watched=?,
                video_title=?, channel_id=?, channel_name=?,
                channel_analyze_count=?
            WHERE user_id=? AND video_id=?
        """, (new_video_count, is_fav, now, video_title,
              channel_id, channel_name, channel_count + 1,
              user_id, video_id))
    else:
        # New video — check if channel already has 3+ videos watched
        is_fav = 1 if channel_count + 1 >= FAVOURITE_THRESHOLD else 0
        conn.execute("""
            INSERT INTO watch_history
            (user_id, video_id, video_title, thumbnail_url, channel_id, channel_name,
             channel_analyze_count, video_analyze_count, is_favourite, last_watched, first_watched)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
        """, (user_id, video_id, video_title, thumbnail_url,
              channel_id, channel_name, channel_count + 1, is_fav, now, now))

    conn.commit()
    conn.close()
    return {"channel_count": channel_count + 1, "is_favourite": is_fav}

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
def extract_video_id(url):
    patterns = [
        r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([^&\n?#]+)",
        r"youtube\.com/shorts/([^&\n?#]+)",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m: return m.group(1)
    return None

def get_transcript(video_id):
    try:
        ytt = YouTubeTranscriptApi()
        try:
            tlist = ytt.fetch(video_id, languages=["en"])
            lang = "en"
        except NoTranscriptFound:
            transcripts = ytt.list(video_id)
            obj = list(transcripts)[0]
            tlist = obj.fetch()
            lang = obj.language_code
        return " ".join([e.text for e in tlist]), lang
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
    if "@" not in req.email:
        raise HTTPException(status_code=400, detail="Enter a valid email.")
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="Name cannot be empty.")
    conn = get_db()
    if conn.execute("SELECT id FROM users WHERE email=?", (req.email,)).fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Email already registered. Please login.")
    conn.execute("INSERT INTO users (name, email, password) VALUES (?, ?, ?)",
                 (req.name.strip(), req.email.lower().strip(), hash_password(req.password)))
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
    channel_info = get_channel_info(video_id)
    thumbnail_url = f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg"

    snippet = transcript[:3000]
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "You analyze YouTube video transcripts. Return ONLY valid JSON in English, no markdown."},
                {"role": "user", "content": f'Return JSON only in ENGLISH: {{"title":"video title","suggested_questions":["q1","q2","q3","q4","q5"]}}. Transcript: {snippet}'}
            ],
            max_tokens=300, temperature=0.1
        )
        text = response.choices[0].message.content.strip()
        text = text.replace("```json", "").replace("```", "").strip()
        start, end = text.find("{"), text.rfind("}") + 1
        if start != -1 and end > start:
            text = text[start:end]
        info = json.loads(text)
    except Exception:
        info = {
            "title": f"YouTube Video ({video_id})",
            "suggested_questions": [
                "What is the main topic?",
                "Summarize the key points",
                "What are the takeaways?",
                "Were any examples given?",
                "What was the conclusion?"
            ]
        }

    video_title = info.get("title", "YouTube Video")

    # Track watch history + auto favourite logic
    track_result = track_video(
        current_user["id"], video_id, video_title,
        thumbnail_url, channel_info["channel_id"], channel_info["channel_name"]
    )

    return {
        "video_id": video_id,
        "title": video_title,
        "transcript": transcript,
        "transcript_length": len(transcript),
        "language": lang,
        "suggested_questions": info.get("suggested_questions", []),
        "thumbnail_url": thumbnail_url,
        "channel_name": channel_info["channel_name"],
        "channel_id": channel_info["channel_id"],
        "channel_analyze_count": track_result["channel_count"],
        "auto_favourited": track_result["is_favourite"] == 1
    }

@app.post("/api/ask")
def ask_question(req: QuestionRequest, current_user: dict = Depends(get_current_user)):
    try:
        messages = [{"role": "system", "content": f"""You are a helpful YouTube video Q&A assistant.
Always respond in English only.
Video Title: {req.video_title}
Transcript: {req.transcript[:6000]}
Rules:
- Answer ONLY from the transcript
- If not in transcript say: "This wasn't covered in the video."
- Be concise, use bullet points for lists"""}]

        for msg in req.chat_history[-8:]:
            if msg.get("role") in ["user", "assistant"] and msg.get("content"):
                messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": req.question})

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            max_tokens=800, temperature=0.5
        )
        answer = response.choices[0].message.content

        conn = get_db()
        conn.execute(
            "INSERT INTO qa_history (user_id, video_id, video_title, question, answer) VALUES (?, ?, ?, ?, ?)",
            (current_user["id"], req.video_id, req.video_title, req.question, answer)
        )
        conn.commit()
        conn.close()

        return {"answer": answer}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI error: {str(e)}")

@app.get("/api/watch-history")
def get_watch_history(current_user: dict = Depends(get_current_user)):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM watch_history WHERE user_id=? ORDER BY last_watched DESC LIMIT 50",
        (current_user["id"],)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/api/favourites")
def get_favourites(current_user: dict = Depends(get_current_user)):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM watch_history WHERE user_id=? AND is_favourite=1 ORDER BY last_watched DESC",
        (current_user["id"],)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/api/qa-history")
def get_qa_history(current_user: dict = Depends(get_current_user)):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM qa_history WHERE user_id=? ORDER BY created_at DESC LIMIT 30",
        (current_user["id"],)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/api/stats")
def get_stats(current_user: dict = Depends(get_current_user)):
    conn = get_db()
    total_videos = conn.execute(
        "SELECT COUNT(DISTINCT video_id) FROM watch_history WHERE user_id=?",
        (current_user["id"],)
    ).fetchone()[0]
    total_channels = conn.execute(
        "SELECT COUNT(DISTINCT channel_id) FROM watch_history WHERE user_id=?",
        (current_user["id"],)
    ).fetchone()[0]
    total_favourites = conn.execute(
        "SELECT COUNT(*) FROM watch_history WHERE user_id=? AND is_favourite=1",
        (current_user["id"],)
    ).fetchone()[0]
    total_questions = conn.execute(
        "SELECT COUNT(*) FROM qa_history WHERE user_id=?",
        (current_user["id"],)
    ).fetchone()[0]
    top_channels = conn.execute(
        """SELECT channel_name, COUNT(DISTINCT video_id) as video_count
           FROM watch_history WHERE user_id=? AND channel_id != ''
           GROUP BY channel_id ORDER BY video_count DESC LIMIT 5""",
        (current_user["id"],)
    ).fetchall()
    conn.close()
    return {
        "total_videos": total_videos,
        "total_channels": total_channels,
        "total_favourites": total_favourites,
        "total_questions": total_questions,
        "top_channels": [dict(r) for r in top_channels]
    }