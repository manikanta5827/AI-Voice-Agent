"""Run all SQL files in migrations/ in order against DATABASE_URL."""
import asyncio
import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

load_dotenv()


async def main():
    conn = await asyncpg.connect(os.getenv("DATABASE_URL"))
    files = sorted(Path("migrations").glob("*.sql"))
    for f in files:
        print(f"Applying {f.name}...")
        await conn.execute(f.read_text())
    await conn.close()
    print("Done.")


asyncio.run(main())
