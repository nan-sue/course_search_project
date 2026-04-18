"""
Database Configuration and Setup.

This module initializes the connection to our PostgreSQL database
and uses the `pgvector` extension to allow us to perform semantic
searches (search based on meaning rather than just exact words).
"""
import os

import psycopg
from pgvector.psycopg import register_vector_async
from psycopg_pool import AsyncConnectionPool

# The connection string tells psycopg where our database is located.
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/course_search")

# By using a connection pool, we reuse active database connections 
# instead of heavily opening and closing them for every single web request.
pool = AsyncConnectionPool(DATABASE_URL, min_size=1, max_size=10, open=False)

async def init_db():
    """
    Initializes the database by creating necessary tables and indexes.
    Call this once when the application starts up!
    """
    await pool.open()
    
    async with pool.connection() as conn:
        # Now properly register Postgres vector support into psycopg
        await register_vector_async(conn)

        async with conn.cursor() as cur:
            # 1. Enable pgvector
            await cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")

            # Reset courses table if vector dimensions changed (e.g. 768 -> 384)
            # This handles switching models without manual database deletion.
            try:
                await cur.execute("""
                    SELECT atttypmod FROM pg_attribute 
                    WHERE attrelid = 'courses'::regclass AND attname = 'embedding';
                """)
                res = await cur.fetchone()
                if res and res[0] != 384:
                    print("Vector dimensions changed (detected old 768-dim table). Resetting courses table...")
                    await cur.execute("DROP TABLE IF EXISTS courses CASCADE;")
            except psycopg.errors.UndefinedTable:
                # Table doesn't exist yet, which is fine
                pass

            # 2. Setup Tables
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    email VARCHAR(255) UNIQUE NOT NULL,
                    is_admin BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS courses (
                    id VARCHAR(255) PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    subject VARCHAR(50),
                    embedding VECTOR(384)
                );
            """)

            # 4. Create an 'ivfflat' index.
            # Instead of comparing a search term to all 17,000 courses line-by-line (which is slow),
            # an ivfflat index groups similar courses together so PostgreSQL can find matches instantly.
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS courses_embedding_ivfflat_idx 
                ON courses USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
            """)

            # 5. Saved Courses Table: Tracks which courses a user bookmarked
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS saved_courses (
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    course_id VARCHAR(255) REFERENCES courses(id) ON DELETE CASCADE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, course_id)
                );
            """)

            # 6. Upvotes Table: Tracks what searches yielded helpful results
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS upvotes (
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    course_id VARCHAR(255) REFERENCES courses(id) ON DELETE CASCADE,
                    search_query TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, course_id)
                );
            """)

            await conn.commit()

async def get_db_connection():
    """
    FastAPI dependency that yields a valid, vector-ready database connection.
    This ensures any route that asks for a database connection gets one properly formatted.
    """
    async with pool.connection() as conn:
        await register_vector_async(conn)
        yield conn
