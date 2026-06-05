"""MongoDB connection helpers with async fire-and-forget wrappers."""

import asyncio
import logging
from typing import Any, Dict, Optional, Tuple

import certifi

logger = logging.getLogger(__name__)

try:
    from pymongo import MongoClient

    MONGODB_AVAILABLE = True
except ImportError:
    MongoClient = None
    MONGODB_AVAILABLE = False
    logger.warning("[Warning] PyMongo not available. To enable: pip install pymongo>=4.6.0")


def _connect_mongo(uri: str) -> Any:
    """Build Mongo client with Atlas-friendly TLS defaults for cloud runtimes."""
    base_kwargs = {
        "serverSelectionTimeoutMS": 20000,
        "connectTimeoutMS": 20000,
        "socketTimeoutMS": 20000,
        "retryWrites": True,
        "maxPoolSize": 10,
        "minPoolSize": 1,
        "appname": "sandy-heroku-agent",
    }
    client = MongoClient(uri, tls=True, tlsCAFile=certifi.where(), **base_kwargs)
    client.admin.command("ping")
    return client


def init_mongo_connection(
    mongodb_uri: str, mongodb_db_name: str
) -> Tuple[Optional[Any], Optional[Any]]:
    """Initialize MongoDB connection and return (mongo_client, mongo_db)."""
    if not MONGODB_AVAILABLE:
        logger.warning("[MongoDB] PyMongo not installed, using JSON memory for now")
        return None, None

    if not mongodb_uri:
        logger.warning("[MongoDB] MONGODB_URI not set, using JSON memory for now")
        return None, None

    try:
        mongo_client = _connect_mongo(mongodb_uri)
        mongo_db = mongo_client[mongodb_db_name]
        logger.info("[MongoDB] connected (db=%s)", mongodb_db_name)
        return mongo_client, mongo_db

    except Exception as first_error:
        logger.warning("[MongoDB] primary TLS connection failed: %s", first_error)

        try:
            mongo_client = MongoClient(
                mongodb_uri,
                serverSelectionTimeoutMS=20000,
                connectTimeoutMS=20000,
                socketTimeoutMS=20000,
                tls=True,
                tlsAllowInvalidCertificates=True,
                retryWrites=True,
                maxPoolSize=10,
                minPoolSize=1,
                appname="sandy-heroku-agent-fallback",
            )
            mongo_client.admin.command("ping")
            mongo_db = mongo_client[mongodb_db_name]
            logger.info(
                "[MongoDB] connected with fallback TLS mode (db=%s)", mongodb_db_name
            )
            return mongo_client, mongo_db

        except Exception as second_error:
            logger.error("[MongoDB] connection failed: %s", second_error)
            logger.error(
                "[MongoDB] Hint: check Atlas Network Access allowlist and URI credentials"
            )
            logger.error("[MongoDB] Falling back to JSON memory")
            return None, None


async def save_document_async(
    mongo_db: Any,
    collection: str,
    doc_id: str,
    data: Dict[str, Any],
) -> None:
    """Fire-and-forget async MongoDB upsert. Runs sync driver in thread pool."""
    if mongo_db is None:
        return
    loop = asyncio.get_running_loop()

    def _upsert() -> None:
        doc = {**data, "_id": doc_id}
        mongo_db[collection].replace_one({"_id": doc_id}, doc, upsert=True)

    try:
        await loop.run_in_executor(None, _upsert)
    except Exception as e:
        logger.warning("[MongoDB] async save failed (%s/%s): %s", collection, doc_id, e)


async def find_one_async(
    mongo_db: Any,
    collection: str,
    filter_dict: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Async MongoDB find_one. Runs sync driver in thread pool."""
    if mongo_db is None:
        return None
    loop = asyncio.get_running_loop()

    def _find() -> Optional[Dict[str, Any]]:
        doc = mongo_db[collection].find_one(filter_dict)
        if doc:
            doc.pop("_id", None)
        return doc

    try:
        return await loop.run_in_executor(None, _find)
    except Exception as e:
        logger.warning("[MongoDB] async find failed (%s): %s", collection, e)
        return None
