import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

db_url = os.getenv("DATABASE_URL")

if not db_url:
    raise RuntimeError("DATABASE_URL is missing")

engine = create_engine(db_url)

try:
    with engine.connect() as connection:
        result = connection.execute(text("SELECT 1"))
        print("DB connection successful:", result.scalar())
except Exception as e:
    print("DB connection failed:", e)