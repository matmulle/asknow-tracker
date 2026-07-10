import os

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

load_dotenv()

engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)
