from datetime import datetime
import os
import sqlite3
from flask import Flask, redirect, render_template, request, url_for

app = Flask(__name__)

# Ensure the database file path resolves to the project folder on Render
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_NAME = os.path.join(BASE_DIR, "gedo_house.db")


def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def migrate_database_if_needed(conn):
    """Ensure schema constraints support OUTFLOW and purge unwanted baseline entries."""
    # 1. Update cash_ledger schema constraint to support OUTFLOW
    schema_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='cash_ledger'"
    ).fetchone()

    if schema_row and schema_row["sql"] and "OUTFLOW" not in schema_row["sql"]:
        conn.execute("ALTER TABLE cash_ledger RENAME TO cash_ledger_old")
        conn.execute(
            """
            CREATE TABLE cash_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_type TEXT CHECK(entry_type IN ('INFLOW', 'OUTFLOW', 'EXPENSE', 'WITHDRAWAL')),
                category_or_item TEXT NOT NULL,
                amount REAL NOT NULL,
                notes TEXT,
                timestamp TEXT NOT NULL
            )
        """
        )
        conn.execute(
            """
            INSERT INTO cash_ledger (id, entry_type, category_or_item, amount, notes, timestamp)
            SELECT id, entry_type, category_or_item, amount, notes, timestamp FROM cash_ledger_old
        """
        )
        conn.execute("DROP TABLE cash_ledger_old")

    # 2. Convert any historical EXPENSE / WITHDRAWAL records to OUTFLOW
    conn.execute(
        """
        UPDATE cash_ledger 
        SET entry_type = 'OUTFLOW' 
        WHERE entry_type IN ('EXPENSE', 'WITHDRAWAL')
    """
    )

    # 3. Permanently remove the requested items from budget_items table
    conn.execute(
        """
        DELETE FROM budget_items 
        WHERE item_name IN (
            'Electricity Materials',
            'Windows',
            'Window Labour',
            'Kokoto (Tinga Moja)'
        ) OR item_name LIKE 'Kokoto%'
    """
    )


def init_db():
    with get_db() as conn:
        # 1. Budget requirements table
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS budget_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_name TEXT UNIQUE,
                category TEXT,
                budgeted_amount REAL NOT NULL
            )
        """
        )

        # 2. Cashflow & expenditure ledger
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cash_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_type TEXT CHECK(entry_type IN ('INFLOW', 'OUTFLOW', 'EXPENSE', 'WITHDRAWAL')),
                category_or_item TEXT NOT NULL,
                amount REAL NOT NULL,
                notes TEXT,
                timestamp TEXT NOT NULL
            )
        """
        )

        # Apply schema updates and cleanups
        migrate_database_if_needed(conn)

        # 3. Seed baseline requirements (excluding Kokoto, Electricity Materials, Windows, Window Labour)
        count_budget = conn.execute(
            "SELECT COUNT(*) FROM budget_items"
        ).fetchone()[0]

        if count_budget == 0:
            requirements = [
                ("Cement (30 bags @ 950)", "Masonry", 28500.0),
                ("General Labour", "Labour", 15000.0),
                ("Food for Workers", "Labour", 5000.0),
                ("Chicken Wire", "Masonry", 1000.0),
                ("Water Fetching", "Labour", 300.0),
                ("Window体制 Pati (Putty)", "Finishes", 200.0),
                ("Miscellaneous", "Contingency", 500.0),
            ]
            conn.executemany(
                """
                INSERT INTO budget_items (item_name, category, budgeted_amount)
                VALUES (?, ?, ?)
                """,
                requirements,
            )

        # 4. Seed initial starting savings baseline (KES 10,050.00)
        count_ledger = conn.execute(
            "SELECT COUNT(*) FROM cash_ledger"
        ).fetchone()[0]
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if count_ledger == 0:
            conn.execute(
                """
                INSERT INTO cash_ledger (entry_type, category_or_item, amount, notes, timestamp)
                VALUES ('INFLOW', 'Starting Savings', 10050.0, 'Baseline savings', ?)
                """,
                (now,),
            )

        conn.commit()


# Run initialization when module loads
init_db()


@app.route("/")
def index():
    with get_db() as conn:
        # Total Project Cost: sum of active budget items
        total_project_cost = conn.execute(
            "SELECT COALESCE(SUM(budgeted_amount), 0) FROM budget_items"
        ).fetchone()[0]

        # Total Cash Inflow (Deposits / New cash added)
        total_inflow = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM cash_ledger WHERE entry_type = 'INFLOW'"
        ).fetchone()[0]

        # Total Cash Outflow (Actual money spent)
        cash_outflow = conn.execute(
            """
            SELECT COALESCE(SUM(amount), 0) 
            FROM cash_ledger 
            WHERE entry_type IN ('OUTFLOW', 'EXPENSE', 'WITHDRAWAL')
        """
        ).fetchone()[0]

        # Savings = all inflows minus actual outflows
        savings = total_inflow - cash_outflow

        # Deficit needed to cover remaining project cost
        deficit = max(0.0, total_project_cost - savings)

        # Budget items breakdown query
        budget_query = """
            SELECT 
                b.id,
                b.item_name,
                b.category,
                b.budgeted_amount,
                COALESCE(SUM(e.amount), 0) AS total_paid,
                (b.budgeted_amount - COALESCE(SUM(e.amount), 0)) AS balance_left
            FROM budget_items b
            LEFT JOIN cash_ledger e 
                ON b.item_name = e.category_or_item 
                AND e.entry_type IN ('OUTFLOW', 'EXPENSE')
            GROUP BY b.id
            ORDER BY b.category, b.budgeted_amount DESC
        """
        items = conn.execute(budget_query).fetchall()

        # Recent transactions history
        history = conn.execute(
            """
            SELECT entry_type, category_or_item, amount, notes, timestamp 
            FROM cash_ledger 
            ORDER BY id DESC LIMIT 15
        """
        ).fetchall()

    return render_template(
        "index.html",
        savings=savings,
        cash_outflow=cash_outflow,
        total_project_cost=total_project_cost,
        deficit=deficit,
        items=items,
        history=history,
    )


@app.route("/add_item", methods=["POST"])
def add_item():
    """Endpoint to add new requirements and dynamically update total budget."""
    item_name = request.form.get("item_name", "").strip()
    category = request.form.get("category", "Masonry").strip()
    try:
        budgeted_amount = float(request.form.get("budgeted_amount", 0.0))
    except (ValueError, TypeError):
        budgeted_amount = 0.0

    if item_name and budgeted_amount > 0:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO budget_items (item_name, category, budgeted_amount)
                VALUES (?, ?, ?)
                ON CONFLICT(item_name) DO UPDATE SET 
                    category=excluded.category,
                    budgeted_amount=excluded.budgeted_amount
                """,
                (item_name, category, budgeted_amount),
            )
            conn.commit()

    return redirect(url_for("index"))


@app.route("/add_transaction", methods=["POST"])
def add_transaction():
    entry_type = request.form.get("entry_type", "").strip().upper()
    try:
        amount = float(request.form.get("amount", 0.0))
    except (ValueError, TypeError):
        amount = 0.0
    notes = request.form.get("notes", "").strip()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Match selection to OUTFLOW with expense type vs INFLOW (savings top-up)
    if entry_type in ("OUTFLOW", "EXPENSE"):
        entry_type = "OUTFLOW"
        category_or_item = (
            request.form.get("expense_item") or "Direct / General Outflow"
        )
    else:
        entry_type = "INFLOW"
        category_or_item = (
            request.form.get("inflow_source") or "Cash Deposit / Top-up"
        )

    if amount > 0:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO cash_ledger (entry_type, category_or_item, amount, notes, timestamp)
                VALUES (?, ?, ?, ?, ?)
                """,
                (entry_type, category_or_item, amount, notes, now),
            )
            conn.commit()

    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True, port=5000)