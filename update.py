import sqlite3

conn = sqlite3.connect("gedo_house.db")
c = conn.cursor()

c.execute("""
    UPDATE budget_items 
    SET item_name = 'Cement (30 bags @ 950)',
        budgeted_amount = 28500.0
    WHERE id = 1
""")

conn.commit()
conn.close()
print("Success! Cement price updated in budget_items.")