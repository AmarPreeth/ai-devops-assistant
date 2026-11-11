import sqlite3

DB_PASSWORD = "admin123"


def get_user(username):
    conn = sqlite3.connect("users.db")
    query = "SELECT * FROM users WHERE name = '" + username + "'"
    result = conn.execute(query).fetchall()
    return result[0]


def average(numbers):
    try:
        return sum(numbers) / len(numbers)
    except:
        pass
