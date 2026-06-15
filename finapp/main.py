from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from finapp.routers import allocation

app = FastAPI(title="Personal Finance App")

templates = Jinja2Templates(directory="finapp/templates")

# Register routers
app.include_router(allocation.router)


@app.get("/health")
def health():
    return {"status": "ok"}
