"""
Settings router: HTTP endpoints for settings, categories, CSV import/export, and backup.

Endpoints:
- GET /settings: Settings page
- POST /settings: Update settings profile
- GET /settings/categories: List categories (JSON)
- POST /settings/categories: Add/rename/hide/reorder categories
- POST /settings/import/csv: Upload and preview CSV for import
- POST /settings/import/confirm: Confirm and import CSV rows
- GET /api/needs-category: Get uncategorized transactions inbox
- GET /settings/export: Export transactions as CSV
- POST /settings/backup: Create database backup
"""
from datetime import date, datetime
from typing import Optional
from fastapi import APIRouter, Depends, UploadFile, File, Form, Body, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from pydantic import BaseModel

from finapp.deps import get_account_context, AccountContext
from finapp.services import settings as settings_svc
from finapp.services import csv_import as csv_import_svc
from finapp.services.export_csv import export_transactions_csv
from finapp.services.ledger import create_transaction
from finapp.services.backup import create_backup

# Initialize templates
templates = Jinja2Templates(directory="finapp/templates")

router = APIRouter(tags=["settings"])


class SettingsUpdateRequest(BaseModel):
    """Settings profile update request - all fields optional."""
    user_name: Optional[str] = None
    debt_method: Optional[str] = None
    review_day: Optional[str] = None
    currency_symbol: Optional[str] = None
    hourly_wage_cents: Optional[int] = None
    pay_frequency: Optional[str] = None
    pay_day: Optional[int] = None


@router.get("/settings", response_class=HTMLResponse)
def get_settings(
    request: Request,
    ctx: AccountContext = Depends(get_account_context),
) -> str:
    """
    Get settings page.
    Renders settings.html with profile, categories, CSV import form, and needs-category badge.
    """
    settings = settings_svc.get_settings(ctx)
    categories = settings_svc.list_categories(ctx, include_inactive=True)
    csv_column_map = settings_svc.get_csv_column_map(ctx)
    needs_count = csv_import_svc.needs_category_count(ctx)

    # Build category list as dicts to avoid lazy-load issues
    categories_list = []
    for cat in categories:
        categories_list.append({
            "id": cat.id,
            "name": cat.name,
            "emoji": cat.emoji,
            "sort_order": cat.sort_order,
            "kind": cat.kind,
            "is_system": cat.is_system,
            "is_active": cat.is_active,
        })

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "settings": settings,
            "categories": categories_list,
            "csv_column_map": csv_column_map,
            "needs_category_count": needs_count,
        },
    )


@router.post("/settings")
def update_settings(
    body: SettingsUpdateRequest,
    ctx: AccountContext = Depends(get_account_context),
) -> dict:
    """
    Update settings profile.

    Request body (all fields optional):
    {
        "user_name": "Joseph",
        "debt_method": "avalanche",
        "review_day": "sunday",
        ...
    }

    Returns:
        {"status": "ok", "user_name": ..., "debt_method": ..., ...}
    """
    updated = settings_svc.update_settings(ctx, **body.dict(exclude_unset=True))

    return {
        "status": "ok",
        "user_name": updated.user_name,
        "debt_method": updated.debt_method,
        "review_day": updated.review_day,
        "currency_symbol": updated.currency_symbol,
        "hourly_wage_cents": updated.hourly_wage_cents,
        "pay_frequency": updated.pay_frequency,
        "pay_day": updated.pay_day,
    }


@router.get("/settings/categories")
def get_categories(
    ctx: AccountContext = Depends(get_account_context),
) -> list:
    """
    Get list of all categories (active and inactive).

    Returns:
        List of {"id", "name", "emoji", "sort_order", "kind", "is_system", "is_active"}
    """
    categories = settings_svc.list_categories(ctx, include_inactive=True)

    return [
        {
            "id": cat.id,
            "name": cat.name,
            "emoji": cat.emoji,
            "sort_order": cat.sort_order,
            "kind": cat.kind,
            "is_system": cat.is_system,
            "is_active": cat.is_active,
        }
        for cat in categories
    ]


@router.post("/settings/categories")
def manage_categories(
    body: dict = Body(...),
    ctx: AccountContext = Depends(get_account_context),
) -> dict:
    """
    Manage categories: add, rename, hide, or reorder.

    Action dispatch:
    - action == "add": fields name, emoji (optional), kind (optional, default "spending")
    - action == "rename": fields category_id, name
    - action == "hide": field category_id
    - action == "reorder": field ordered_ids (list of ints)

    Returns:
        {"status": "ok", ...action-specific fields...}
    """
    action = body.get("action")

    if action == "add":
        name = body.get("name")
        emoji = body.get("emoji")
        kind = body.get("kind", "spending")
        cat = settings_svc.add_category(ctx, name, emoji, kind)
        return {
            "status": "ok",
            "id": cat.id,
            "name": cat.name,
        }

    elif action == "rename":
        category_id = body.get("category_id")
        name = body.get("name")
        settings_svc.rename_category(ctx, category_id, name)
        return {"status": "ok"}

    elif action == "hide":
        category_id = body.get("category_id")
        settings_svc.hide_category(ctx, category_id)
        return {"status": "ok"}

    elif action == "reorder":
        ordered_ids = body.get("ordered_ids", [])
        settings_svc.reorder_categories(ctx, ordered_ids)
        return {"status": "ok"}

    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")


@router.post("/settings/import/csv")
async def import_csv(
    file: UploadFile = File(...),
    map_date: str = Form(...),
    map_amount: str = Form(...),
    map_payee: str = Form(...),
    map_direction: str = Form(""),
    spent_is_negative: str = Form("true"),
    ctx: AccountContext = Depends(get_account_context),
) -> dict:
    """
    Upload CSV for preview.

    Form fields:
    - file: CSV file upload
    - map_date: CSV column name for date
    - map_amount: CSV column name for amount
    - map_payee: CSV column name for payee
    - map_direction: CSV column name for direction (optional)
    - spent_is_negative: "true"/"false" (default "true")

    Returns:
        {
            "rows": [
                {
                    "row_index": int,
                    "date": "YYYY-MM-DD",
                    "amount_cents": int,
                    "direction": "in"|"out",
                    "payee": str,
                    "import_hash": str,
                    "is_duplicate": bool,
                    "selected": bool
                },
                ...
            ],
            "duplicate_count": int
        }
    """
    # Read file content
    content = (await file.read()).decode("utf-8")

    # Build column map
    column_map = {
        "date": map_date,
        "amount": map_amount,
        "payee": map_payee,
    }
    if map_direction and map_direction.strip():
        column_map["direction"] = map_direction

    # Save mapping
    settings_svc.save_csv_column_map(ctx, column_map)

    # Parse CSV
    rows = csv_import_svc.parse_csv(content)

    # Build preview
    spent_neg = spent_is_negative.lower() in ("true", "1", "yes", "on")
    preview = csv_import_svc.build_preview(ctx, rows, column_map, spent_is_negative=spent_neg)

    # Convert preview to JSON-serializable format
    preview_rows = []
    duplicate_count = 0
    for p in preview:
        if p.is_duplicate:
            duplicate_count += 1
        preview_rows.append({
            "row_index": p.row_index,
            "date": p.date.isoformat(),
            "amount_cents": p.amount_cents,
            "direction": p.direction,
            "payee": p.payee,
            "import_hash": p.import_hash,
            "is_duplicate": p.is_duplicate,
            "selected": p.selected,
        })

    return {
        "rows": preview_rows,
        "duplicate_count": duplicate_count,
    }


@router.post("/settings/import/confirm")
def confirm_import(
    body: dict = Body(...),
    ctx: AccountContext = Depends(get_account_context),
) -> dict:
    """
    Confirm and import CSV rows into the ledger.

    Request body:
    {
        "rows": [
            {
                "date": "2026-06-01",
                "amount_cents": 4500,
                "direction": "out",
                "payee": "Coffee Shop"
            },
            ...
        ]
    }

    Returns:
        {"imported": number_of_rows}
    """
    rows = body.get("rows", [])
    imported_count = 0

    for row in rows:
        txn_date = date.fromisoformat(row["date"])
        amount_cents = row["amount_cents"]
        direction = row["direction"]
        payee = row.get("payee")

        create_transaction(
            ctx,
            date=txn_date,
            amount_cents=amount_cents,
            direction=direction,
            payee=payee,
            is_imported=True,
        )
        imported_count += 1

    return {"imported": imported_count}


@router.get("/api/needs-category")
def get_needs_category(
    ctx: AccountContext = Depends(get_account_context),
) -> dict:
    """
    Get uncategorized transactions (Needs Category inbox).

    Returns:
        {
            "count": int,
            "transactions": [
                {
                    "id": int,
                    "date": "YYYY-MM-DD",
                    "amount_cents": int,
                    "direction": "in"|"out",
                    "payee": str
                },
                ...
            ]
        }
    """
    txns = csv_import_svc.list_needs_category(ctx)
    count = csv_import_svc.needs_category_count(ctx)

    return {
        "count": count,
        "transactions": [
            {
                "id": t.id,
                "date": t.date.isoformat(),
                "amount_cents": t.amount_cents,
                "direction": t.direction,
                "payee": t.payee or "",
            }
            for t in txns
        ],
    }


@router.get("/settings/export")
def export_csv(
    ctx: AccountContext = Depends(get_account_context),
) -> Response:
    """
    Export transactions as CSV.

    Returns:
        CSV file with media type text/csv
    """
    csv_text = export_transactions_csv(ctx)

    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=transactions.csv"},
    )


@router.post("/settings/backup")
def backup_database(
    ctx: AccountContext = Depends(get_account_context),
) -> dict:
    """
    Create a database backup using SQLite Online Backup API.

    Returns:
        {"backup_path": str}
    """
    import os

    # Get database path from SQLAlchemy engine URL
    url = ctx.db.get_bind().url
    source_path = url.database

    # Create backups directory
    backups_dir = os.path.join(os.path.dirname(source_path) or ".", "backups")

    # Create backup with current timestamp
    now = datetime.now()
    backup_path = create_backup(source_path, backups_dir, now.year, now.month)

    return {"backup_path": backup_path}
