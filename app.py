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


def migrate_cash_ledger_if_needed(conn):
    """
    Checks if the cash_ledger table still uses the old CHECK constraint without 'WITHDRAWAL'.
    If so, migrates the table to the updated schema preserving all existing records.
    """
    schema_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='cash_ledger'"
    ).fetchone()

    if schema_row and schema_row["sql"] and "WITHDRAWAL" not in schema_row["sql"]:
        conn.execute("ALTER TABLE cash_ledger RENAME TO cash_ledger_old")
        conn.execute("""
            CREATE TABLE cash_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_type TEXT CHECK(entry_type IN ('INFLOW', 'EXPENSE', 'WITHDRAWAL')),
                category_or_item TEXT NOT NULL,
                amount REAL NOT NULL,
                notes TEXT,
                timestamp TEXT NOT NULL
            )
        """)
        conn.execute("""
            INSERT INTO cash_ledger (id, entry_type, category_or_item, amount, notes, timestamp)
            SELECT id, entry_type, category_or_item, amount, notes, timestamp FROM cash_ledger_old
        """)
        conn.execute("DROP TABLE cash_ledger_old")


def init_db():
    with get_db() as conn:
        # 1. Budget requirements table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS budget_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_name TEXT UNIQUE,
                category TEXT,
                budgeted_amount REAL NOT NULL
            )
        """)

        # 2. Cashflow & expenditure ledger
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cash_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_type TEXT CHECK(entry_type IN ('INFLOW', 'EXPENSE', 'WITHDRAWAL')),
                category_or_item TEXT NOT NULL,
                amount REAL NOT NULL,
                notes TEXT,
                timestamp TEXT NOT NULL
            )
        """)

        # Handle automatic schema upgrade if db file previously existed with old constraint
        migrate_cash_ledger_if_needed(conn)

        # 3. Seed baseline requirements (Total: KES 55,000)
        count_budget = conn.execute(
            "SELECT COUNT(*) FROM budget_items"
        ).fetchone()[0]

        if count_budget == 0:
            requirements = [
                ("Cement (30 bags @ 550)", "Masonry", 16500.0),
                ("Kokoto (Tinga Moja)", "Masonry", 12000.0),
                ("General Labour", "Labour", 15000.0),
                ("Food for Workers", "Labour", 5000.0),
                ("Electricity Materials", "Finishes", 2500.0),
                ("Windows", "Finishes", 1500.0),
                ("Chicken Wire", "Masonry", 1000.0),
                ("Window Labour", "Labour", 500.0),
                ("Water Fetching", "Labour", 300.0),
                ("Window Pati (Putty)", "Finishes", 200.0),
                ("Miscellaneous", "Contingency", 500.0),
            ]
            conn.executemany(
                """
                INSERT INTO budget_items (item_name, category, budgeted_amount)
                VALUES (?, ?, ?)
                """,
                requirements,
            )
        else:
            conn.execute("""
                UPDATE budget_items 
                SET budgeted_amount = 12000.0 
                WHERE item_name LIKE 'Kokoto%'
            """)
            conn.execute("""
                INSERT OR IGNORE INTO budget_items (item_name, category, budgeted_amount)
                VALUES ('Miscellaneous', 'Contingency', 500.0)
            """)

        # 4. Seed initial savings + planned contributions
        count_ledger = conn.execute(
            "SELECT COUNT(*) FROM cash_ledger"
        ).fetchone()[0]
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if count_ledger == 0:
            initial_inflows = [
                ("INFLOW", "Starting Savings", 15604.0, "Savings as of 17th Sept 2026", now),
                ("INFLOW", "September Savings", 7000.0, "Planned contribution - Sept 2026", now),
                ("INFLOW", "October Savings", 7000.0, "Planned contribution - Oct 2026", now),
                ("INFLOW", "November Savings", 7000.0, "Planned contribution - Nov 2026", now),
                ("INFLOW", "December Savings", 7000.0, "Planned contribution - Dec 2026", now),
                ("WITHDRAWAL", "Personal Withdrawal", 700.0, "Withdrawn from savings for personal use", now),
            ]
            conn.executemany(
                """
                INSERT INTO cash_ledger (entry_type, category_or_item, amount, notes, timestamp)
                VALUES (?, ?, ?, ?, ?)
                """,
                initial_inflows,
            )
        else:
            exists_withdrawal = conn.execute(
                "SELECT COUNT(*) FROM cash_ledger WHERE category_or_item = 'Personal Withdrawal'"
            ).fetchone()[0]
            if exists_withdrawal == 0:
                conn.execute(
                    """
                    INSERT INTO cash_ledger (entry_type, category_or_item, amount, notes, timestamp)
                    VALUES ('WITHDRAWAL', 'Personal Withdrawal', 700.0, 'Withdrawn from savings for personal use', ?)
                    """,
                    (now,),
                )

        conn.commit()


# Run initialization when module loads (critical for Gunicorn on Render)
init_db()


@app.route("/")
def index():
    with get_db() as conn:
        total_budget = conn.execute(
            "SELECT COALESCE(SUM(budgeted_amount), 0) FROM budget_items"
        ).fetchone()[0]
        expected_cash = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM cash_ledger WHERE entry_type = 'INFLOW'"
        ).fetchone()[0]
        total_spent = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM cash_ledger WHERE entry_type = 'EXPENSE'"
        ).fetchone()[0]
        total_withdrawn = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM cash_ledger WHERE entry_type = 'WITHDRAWAL'"
        ).fetchone()[0]

        net_cash_available = expected_cash - total_withdrawn
        projected_deficit = max(0.0, total_budget - net_cash_available)
        cash_in_hand = net_cash_available - total_spent

        budget_query = """
            SELECT 
                b.id,
                b.item_name,
                b.category,
                b.budgeted_amount,
                COALESCE(SUM(e.amount), 0) AS total_paid,
                (b.budgeted_amount - COALESCE(SUM(e.amount), 0)) AS balance_left
            FROM budget_items b
            LEFT JOIN cash_ledger e ON b.item_name = e.category_or_item AND e.entry_type = 'EXPENSE'
            GROUP BY b.id
            ORDER BY b.category, b.budgeted_amount DESC
        """
        items = conn.execute(budget_query).fetchall()

        history = conn.execute(
            """
            SELECT entry_type, category_or_item, amount, notes, timestamp 
            FROM cash_ledger 
            ORDER BY id DESC LIMIT 15
            """
        ).fetchall()

    return render_template(
        "index.html",
        total_budget=total_budget,
        expected_cash=expected_cash,
        projected_deficit=projected_deficit,
        total_spent=total_spent,
        cash_in_hand=cash_in_hand,
        items=items,
        history=history,
    )


@app.route("/add_transaction", methods=["POST"])
def add_transaction():
    entry_type = request.form.get("entry_type", "").strip().upper()
    try:
        amount = float(request.form.get("amount", 0.0))
    except (ValueError, TypeError):
        amount = 0.0
    notes = request.form.get("notes", "").strip()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if entry_type == "EXPENSE":
        category_or_item = request.form.get("expense_item")
    elif entry_type == "WITHDRAWAL":
        category_or_item = request.form.get("withdrawal_reason") or "Personal Withdrawal"
    else:
        category_or_item = request.form.get("inflow_source") or "Monthly Contribution"

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