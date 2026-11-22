# Sample code for the Status Board example. Line numbers here are referenced by usecases.json "call" fields.
def run(db, mailer):
    rows = db.query("SELECT * FROM status_history WHERE week = current_week()")
    mailer.send_weekly(rows)
