import uvicorn
from fastapi import FastAPI
import threading, time

app = FastAPI()
@app.get("/ping")
def ping():
    return {"ok": True}

def run():
    uvicorn.run(app, host="127.0.0.1", port=7899, log_level="error")

t = threading.Thread(target=run, daemon=True)
t.start()
time.sleep(2)

import httpx
r = httpx.get("http://127.0.0.1:7899/ping", timeout=5)
print("STATUS:", r.status_code)
print("BODY:", r.text)
