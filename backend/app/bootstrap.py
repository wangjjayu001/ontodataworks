"""Explicit fresh-database bootstrap, reusing the application's actual migrations."""
import asyncio

from .main import app, lifespan
from .migrate_v4 import main as migrate_v4
from .migrate_v5 import main as migrate_v5


async def initialize_baseline():
    async with lifespan(app):
        pass


def main():
    asyncio.run(initialize_baseline())
    migrate_v4()
    migrate_v5()
    print("OntoDataWorks schema 5 initialized; no business data or approvals imported.")


if __name__ == "__main__":
    main()
