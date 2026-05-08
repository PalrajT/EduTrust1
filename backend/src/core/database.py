from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import ConnectionFailure
from typing import Optional
from urllib.parse import quote_plus, unquote, urlsplit, urlunsplit

from src.core.config import settings
from src.core.logging import logger


class Database:
    """MongoDB Database Connection Manager"""
    
    client: Optional[AsyncIOMotorClient] = None
    db = None
    available: bool = False


db = Database()


def _normalize_mongodb_url(mongodb_url: str) -> str:
    """Re-encode MongoDB credentials so raw special characters do not break parsing."""
    parts = urlsplit(mongodb_url)

    if not parts.username and not parts.password:
        return mongodb_url

    username = quote_plus(unquote(parts.username or "")) if parts.username else ""
    password = quote_plus(unquote(parts.password or "")) if parts.password else ""

    if username and password:
        netloc = f"{username}:{password}@{parts.hostname}"
    elif username:
        netloc = f"{username}@{parts.hostname}"
    else:
        netloc = parts.hostname or ""

    if parts.port:
        netloc = f"{netloc}:{parts.port}"

    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _redact_mongodb_url(mongodb_url: str) -> str:
    """Hide credentials while keeping the host visible for diagnostics."""
    parts = urlsplit(mongodb_url)

    if not parts.hostname:
        return mongodb_url

    netloc = parts.hostname
    if parts.port:
        netloc = f"{netloc}:{parts.port}"

    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


async def connect_to_mongo():
    """Connect to MongoDB"""
    try:
        normalized_url = _normalize_mongodb_url(settings.MONGODB_URL)
        logger.info(f"Connecting to MongoDB at {_redact_mongodb_url(normalized_url)}")
        db.client = AsyncIOMotorClient(normalized_url)
        db.db = db.client[settings.MONGODB_DB_NAME]
        db.available = False

        # Test the connection
        try:
            await db.client.admin.command('ping')
            db.available = True
            logger.info("✅ Successfully connected to MongoDB")

            # Create indexes
            await create_indexes()
        except Exception as e:
            logger.warning(f"⚠️ MongoDB connection test failed: {e}. Continuing without DB for now.")
            db.available = False
            db.db = None

    except ConnectionFailure as e:
        logger.error(f"❌ Failed to connect to MongoDB: {e}")
        # Do not raise to allow app to start in development without DB
        logger.warning("Continuing without a live MongoDB connection. Some endpoints will fail until DB is available.")
        db.available = False
        db.db = None

    return db.available


async def close_mongo_connection():
    """Close MongoDB connection"""
    if db.client:
        db.client.close()
        db.client = None
        db.db = None
        db.available = False
        logger.info("MongoDB connection closed")


async def create_indexes():
    """Create database indexes for performance"""
    try:
        # Users collection indexes
        await db.db.users.create_index("email", unique=True)
        await db.db.users.create_index("username", unique=True)
        
        # Certificates collection indexes
        await db.db.certificates.create_index("certificate_id", unique=True)
        await db.db.certificates.create_index("student_name")
        await db.db.certificates.create_index("institution")
        await db.db.certificates.create_index("issue_date")
        await db.db.certificates.create_index([("student_name", "text"), ("institution", "text")])
        
        # Verifications collection indexes
        await db.db.verifications.create_index("verification_id", unique=True)
        await db.db.verifications.create_index("created_at")
        await db.db.verifications.create_index("status")
        
        # Blacklist collection indexes
        await db.db.blacklist.create_index("certificate_id", unique=True)
        await db.db.blacklist.create_index("created_at")
        
        logger.info("✅ Database indexes created successfully")
        
    except Exception as e:
        error_msg = str(e)
        if "createIndex" in error_msg and ("8000" in error_msg or "AtlasError" in error_msg):
            logger.warning("⚠️ MongoDB Atlas user lacks 'createIndex' permission")
            logger.warning("📖 Fix: Update user role to 'Atlas admin' in Database Access")
            logger.warning("📄 See FIX_ATLAS_PERMISSIONS.md for detailed instructions")
            logger.info("ℹ️ Application will continue - indexes may already exist or will be created later")
        else:
            logger.error(f"Error creating indexes: {e}")


def get_database():
    """Get database instance"""
    if not db.available or db.db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is temporarily unavailable"
        )

    return db.db
