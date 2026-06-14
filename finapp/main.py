from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI(title="Personal Finance App")

templates = Jinja2Templates(directory="finapp/templates")


@app.get("/health")
def health():
    return {"status": "ok"}
