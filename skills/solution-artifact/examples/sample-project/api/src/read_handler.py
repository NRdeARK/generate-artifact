# Sample code for the Status Board example. Line numbers here are referenced by usecases.json "call" fields.
def handle_read(db):
    rows = db.query("SELECT * FROM latest_status LIMIT 50")
    return [dict(r) for r in rows]
