from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from finapp.routers import allocation, dashboard, budget, transactions, debt, savings, missions

app = FastAPI(title="Personal Finance App")

templates = Jinja2Templates(directory="finapp/templates")

# Register routers
app.include_router(allocation.router)
app.include_router(dashboard.router)
app.include_router(budget.router)
app.include_router(transactions.router)
app.include_router(debt.router)
app.include_router(savings.router)
app.include_router(missions.router)


@app.get("/health")
def health():
    return {"status": "ok"}
