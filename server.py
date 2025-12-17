from __future__ import annotations

import os
import subprocess
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional

# If conda PATH hides ollama, this helps (safe even if unnecessary)
os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "")

OLLAMA_BIN = os.environ.get("OLLAMA_BIN", "ollama")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")  # default
MODEL = os.environ.get("OLLAMA_MODEL", "llava")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatIn(BaseModel):
    prompt: str

SYSTEM = (
    "You are VirtuWill (Yoste). Be helpful, concise, and creative. "
    "If you don't know, say so."
)

def run_ollama(prompt: str) -> str:
    # Forces the backend to talk to your *local* ollama server instance
    env = os.environ.copy()
    env["OLLAMA_HOST"] = OLLAMA_HOST

    full_prompt = f"{SYSTEM}\n\nUSER:\n{prompt}\n\nASSISTANT:\n"

    out = subprocess.check_output(
        [OLLAMA_BIN, "run", MODEL, full_prompt],
        text=True,
        env=env
    )
    return out.strip()

@app.get("/health")
def health():
    # also validate ollama is reachable
    try:
        env = os.environ.copy()
        env["OLLAMA_HOST"] = OLLAMA_HOST
        v = subprocess.check_output([OLLAMA_BIN, "--version"], text=True, env=env).strip()
        return {"ok": True, "ollama": v, "model": MODEL, "host": OLLAMA_HOST}
    except Exception as e:
        return {"ok": False, "error": str(e), "model": MODEL, "host": OLLAMA_HOST}

@app.post("/chat")
async def chat(prompt: str = Form(""), files: Optional[List[UploadFile]] = File(None)):
    # For now: pass prompt only to llava; later: parse files and add to prompt.
    # Minimal safe behavior: include filenames so you can confirm uploads work.
    file_names = [f.filename for f in (files or [])]
    augmented = prompt
    if file_names:
        augmented += "\n\n[User attached files]\n" + "\n".join(f"- {n}" for n in file_names)

    return {"text": run_ollama(augmented)}
