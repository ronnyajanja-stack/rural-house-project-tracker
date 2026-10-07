import os
import sqlite3
from datetime import datetime

# Resolve the exact database path
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_NAME = os.path.join(BASE_DIR, "gedo_house.db")

conn = sqlite3.connect(DB_NAME)
c = conn.cursor()

# 1. Clear out ALL existing transaction ledger entries
c.execute("DELETE FROM cash_ledger")

# 2. Insert exactly KES 10,050.00 as the only transaction
now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
c.execute("""
    INSERT INTO cash_ledger (entry_type, category_or_item, amount, notes, timestamp)
    VALUES ('INFLOW', 'Starting Savings', 10050.0, 'Initial base savings', ?)
""", (now,))

# 3. Clean up the removed items from budget_items
c.execute("""
    DELETE FROM budget_items 
    WHERE item_name IN (
        'Electricity Materials',
        'Windows',
        'Window Labour',
        'Kokoto (Tinga Moja)'
    ) OR item_name LIKE 'Kokoto%'
""")

conn.commit()
conn.close()

print("Database reset successfully! Savings is now 10,050 and Outflow is 0.")