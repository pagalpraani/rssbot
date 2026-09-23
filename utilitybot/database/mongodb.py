# =============================================================================
# Module: Mongodb
# Path: utilitybot/database/mongodb.py
# Description: Database models and connection management for MongoDB interaction.
# =============================================================================

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from .. import config
from ..utils.logger import get_logger
import asyncio
from pymongo.errors import ConnectionFailure
from ..utils import settings_cache
from datetime import datetime, timedelta, timezone

log = get_logger(__name__)

class MongoDB:
    def __init__(self, uri: str, db_name: str):
        self.client = AsyncIOMotorClient(uri)
        self.db = self.client[db_name]

    async def connect(self):
        """
        Establishes a connection to the MongoDB server and performs a health check.
        """
        log.info("Attempting to connect to MongoDB...")
        try:
            await self.client.admin.command('ping')
            log.info("MongoDB connection successful.")

        except ConnectionFailure as e:
            log.error(f"MongoDB connection failed: {e}")
            raise

    async def close(self):
        """
        Closes the MongoDB connection.
        """
        self.client.close()
        log.info("MongoDB connection closed.")

    # --- Settings ---
    async def get_settings(self, chat_id: int) -> dict:
        cached = settings_cache.get_settings(chat_id)
        if cached is not None:
            return cached
        data = await self.db.settings.find_one({"_id": chat_id}) or {}
        settings_cache.set_settings(chat_id, data)
        return data

    async def update_settings(self, chat_id: int, data: dict, upsert: bool = True):
        # Shallow-copy so we never mutate the caller's dict
        data = {**data, "updated_at": datetime.now(timezone.utc)}
        if upsert:
            # Add created_at field if this is a new document
            await self.db.settings.update_one(
                {"_id": chat_id},
                {"$set": data, "$setOnInsert": {"created_at": datetime.now(timezone.utc)}},
                upsert=True
            )
        else:
            await self.db.settings.update_one({"_id": chat_id}, {"$set": data})
        settings_cache.invalidate_settings(chat_id)


    async def add_managed_group(self, user_id: int, chat_id: int, chat_title: str, chat_type: str = "group"):
        """
        Adds a group to the user's managed list.
        """
        group_data = {
            "chat_id": chat_id,
            "chat_title": chat_title,
            "chat_type": chat_type,
            "added_at": datetime.now(timezone.utc)
        }
        await self.db.user_shares.update_one(
            {"_id": user_id},
            {"$addToSet": {"managed_groups": group_data}},
            upsert=True
        )

    async def get_managed_groups(self, user_id: int) -> list:
        """
        Retrieves the list of groups managed by a user.
        """
        user_doc = await self.db.user_shares.find_one({"_id": user_id})
        if user_doc:
            return user_doc.get("managed_groups", [])
        return []

    async def remove_managed_group(self, user_id: int, chat_id: int) -> bool:
        """
        Removes a group from the user's managed list.
        Returns True if the document was found and modified.
        """
        result = await self.db.user_shares.update_one(
            {"_id": user_id},
            {"$pull": {"managed_groups": {"chat_id": chat_id}}}
        )
        return result.modified_count > 0

    async def is_group_managed_by_user(self, user_id: int, chat_id: int) -> bool:
        """
        Checks if a user manages a specific group.
        Uses $elemMatch so MongoDB does the scan server-side.
        """
        doc = await self.db.user_shares.find_one(
            {"_id": user_id, "managed_groups": {"$elemMatch": {"chat_id": chat_id}}},
            projection={"_id": 1},  # only fetch the _id field, nothing else
        )
        return doc is not None

    async def setup_indexes(self):
        """
        Creates necessary indexes for performance.
        Each index creation is individually guarded so a stale definition
        mismatch does not crash startup — it logs a warning and continues.
        """
        # user_shares: queried by _id (auto-indexed) but the managed_groups
        # sub-array is searched with $elemMatch — a multikey index helps
        await self.db.user_shares.create_index("managed_groups.chat_id", background=True)

        # Analytics & Stats
        await self.db.bot_chats.create_index("chat_id", unique=True, background=True)

        # RSS: these are hit on every poll cycle — critical for performance
        # Deduplicate rss_processed before indexing — old code used insert_one
        # which could create duplicates; dedup must run before index creation.
        try:
            pipeline = [
                {"$group": {
                    "_id": {"chat_id": "$chat_id", "feed_url": "$feed_url", "item_id": "$item_id"},
                    "ids": {"$push": "$_id"},
                    "count": {"$sum": 1}
                }},
                {"$match": {"count": {"$gt": 1}}}
            ]
            dupes = await self.db.rss_processed.aggregate(pipeline).to_list(length=None)
            if dupes:
                log.info(f"Deduplicating {len(dupes)} rss_processed groups...")
                for group in dupes:
                    keep = group["ids"][0]
                    remove = group["ids"][1:]
                    await self.db.rss_processed.delete_many({"_id": {"$in": remove}})
                log.info("rss_processed deduplication complete.")
        except Exception as e:
            log.warning(f"rss_processed dedup error (non-fatal): {e}")

        # rss_feeds: scheduler loads all feeds, filters by chat_id
        try:
            await self.db.rss_feeds.create_index("chat_id", background=True)
            await self.db.rss_feeds.create_index(
                [("chat_id", 1), ("feed_url", 1)], unique=True, background=True
            )
        except Exception as e:
            log.warning(f"rss_feeds index error (non-fatal): {e}")

        # rss_processed: non-unique compound index — fast lookup, safe on existing data
        # unique=True is intentionally omitted: upsert in mark_rss_item_processed
        # prevents new duplicates without risking a startup crash on old data.
        # NOTE: no TTL index here anymore. Records used to expire after 90 days,
        # which meant a feed keeping the same items in its XML past that point
        # would have them re-sent as "new". Retention is now persistent and
        # bounded per-feed by enforce_processed_cap() (see RSS_PROCESSED_CACHE_CAP).
        try:
            # Drop the old single-field TTL index from previous deployments, if present —
            # otherwise it keeps silently expiring items every 90 days regardless of the
            # change below, since Mongo doesn't remove an index just because the code
            # stopped re-creating it.
            existing = await self.db.rss_processed.index_information()
            for idx_name, idx_info in existing.items():
                if idx_info.get("key") == [("processed_at", 1)] and "expireAfterSeconds" in idx_info:
                    await self.db.rss_processed.drop_index(idx_name)
                    log.info(f"Dropped legacy 90-day TTL index '{idx_name}' on rss_processed.")
        except Exception as e:
            log.warning(f"rss_processed legacy TTL index cleanup error (non-fatal): {e}")

        try:
            await self.db.rss_processed.create_index(
                [("chat_id", 1), ("feed_url", 1), ("item_id", 1)],
                background=True
            )
            await self.db.rss_processed.create_index(
                [("chat_id", 1), ("feed_url", 1), ("processed_at", 1)],
                background=True
            )
        except Exception as e:
            log.warning(f"rss_processed index error (non-fatal): {e}")

        # rss_settings: looked up by chat_id on every feed process
        try:
            await self.db.rss_settings.create_index("chat_id", background=True)
        except Exception as e:
            log.warning(f"rss_settings index error (non-fatal): {e}")

        log.info("Database indexes setup complete.")

    # --- Chat Tracking ---
    async def add_bot_chat(self, chat_id: int, title: str, username: str, link: str, chat_type: str = "group"):
        """Logs when the bot is added to a new chat, or re-links an existing one."""
        await self.db.bot_chats.update_one(
            {"chat_id": chat_id},
            {
                "$set": {
                    "title": title,
                    "username": username,
                    "link": link,
                    "type": chat_type,
                    "rss_linked": True,
                    "updated_at": datetime.now(timezone.utc)
                },
                "$setOnInsert": {
                    "added_at": datetime.now(timezone.utc)
                }
            },
            upsert=True
        )

    async def remove_bot_chat(self, chat_id: int):
        """Removes a chat when the bot leaves or is kicked."""
        await self.db.bot_chats.delete_one({"chat_id": chat_id})

    async def unlink_bot_chat_from_rss(self, chat_id: int):
        """Marks a chat as unlinked from the RSS dashboard without deleting the record.
        The chat remains discoverable in the Add New Chat list."""
        await self.db.bot_chats.update_one(
            {"chat_id": chat_id},
            {"$set": {"rss_linked": False, "updated_at": datetime.now(timezone.utc)}}
        )

    async def get_all_bot_chats(self):
        """Returns all RSS-linked chats for the dashboard."""
        cursor = self.db.bot_chats.find({"rss_linked": {"$ne": False}})
        return await cursor.to_list(length=None)

    async def get_unlinked_bot_chats(self):
        """Returns chats known to the bot but not currently in the RSS dashboard."""
        cursor = self.db.bot_chats.find({"rss_linked": False})
        return await cursor.to_list(length=None)

    async def add_rss_feed(self, chat_id: int, feed_url: str):
        """Adds an RSS feed to a channel."""
        await self.db.rss_feeds.update_one(
            {"chat_id": chat_id, "feed_url": feed_url},
            {"$set": {"chat_id": chat_id, "feed_url": feed_url},
             "$setOnInsert": {
                 "created_at": datetime.now(timezone.utc),
                 "status": "Activated",
                 "media_mode": "Enable",
                 "link_preview": True,
                 "post_title_enabled": True,
                 "notification": "Normal",
                 "author_enabled": True,
                 "watermark_enabled": True,
                 "download_enabled": True,
                 "time_interval": 0,  # 0 means use chat default
                 "last_checked": datetime.fromtimestamp(0, tz=timezone.utc),
                 "custom_title": None,
                 "custom_hashtags": None,
                 "feed_title": None,
                 "length_limit": 0,
                 "source_format": "feed_title_and_link"
             }},
            upsert=True
        )

    async def update_feed_settings(self, chat_id: int, feed_url: str, settings: dict):
        """Updates settings for a specific RSS feed."""
        await self.db.rss_feeds.update_one(
            {"chat_id": chat_id, "feed_url": feed_url},
            {"$set": settings}
        )

    async def update_feed_download(self, chat_id: int, feed_url: str, enabled: bool):
        """Updates the download_enabled flag for an RSS feed."""
        await self.db.rss_feeds.update_one(
            {"chat_id": chat_id, "feed_url": feed_url},
            {"$set": {"download_enabled": enabled}}
        )

    async def update_feed_watermark(self, chat_id: int, feed_url: str, enabled: bool):
        """Updates the watermark_enabled flag for an RSS feed."""
        await self.db.rss_feeds.update_one(
            {"chat_id": chat_id, "feed_url": feed_url},
            {"$set": {"watermark_enabled": enabled}}
        )

    async def remove_rss_feed(self, chat_id: int, feed_url: str):
        """Removes an RSS feed from a channel."""
        await self.db.rss_feeds.delete_one({"chat_id": chat_id, "feed_url": feed_url})

    async def remove_all_rss_feeds_by_chat(self, chat_id: int):
        """Removes all RSS feeds from a channel."""
        await self.db.rss_feeds.delete_many({"chat_id": chat_id})

    async def get_rss_feeds(self) -> list:
        """Retrieves all RSS feeds configured."""
        cursor = self.db.rss_feeds.find({})
        return await cursor.to_list(length=None)

    async def get_rss_feeds_by_chat(self, chat_id: int) -> list:
        """Retrieves RSS feeds for a specific channel."""
        cursor = self.db.rss_feeds.find({"chat_id": chat_id})
        return await cursor.to_list(length=None)

    async def record_feed_failure(self, chat_id: int, feed_url: str, fail_count: int, retry_after: datetime) -> None:
        """Increment transient failure counter and set the next retry timestamp."""
        await self.db.rss_feeds.update_one(
            {"chat_id": chat_id, "feed_url": feed_url},
            {"$set": {"fail_count": fail_count, "retry_after": retry_after}}
        )

    async def clear_feed_failures(self, chat_id: int, feed_url: str) -> None:
        """Reset failure counter after a successful fetch."""
        await self.db.rss_feeds.update_one(
            {"chat_id": chat_id, "feed_url": feed_url},
            {"$unset": {"fail_count": "", "retry_after": ""}}
        )

    async def update_rss_settings(self, chat_id: int, settings: dict):
        """Updates RSS processing settings (logo, custom text, footer link) for a channel."""
        await self.db.rss_settings.update_one(
            {"chat_id": chat_id},
            {"$set": settings},
            upsert=True
        )

    async def get_rss_settings(self, chat_id: int) -> dict:
        """Retrieves RSS settings for a channel."""
        doc = await self.db.rss_settings.find_one({"chat_id": chat_id})
        return doc or {}

    async def get_processed_item_ids(self, chat_id: int, feed_url: str) -> set:
        """Fetch all processed item_ids for a feed in one query."""
        cursor = self.db.rss_processed.find(
            {"chat_id": chat_id, "feed_url": feed_url},
            {"item_id": 1, "_id": 0}
        )
        docs = await cursor.to_list(length=None)
        return {d["item_id"] for d in docs}

    async def enforce_processed_cap(self, chat_id: int, feed_url: str, max_count: int):
        """
        Keeps only the newest `max_count` processed-item records for a feed,
        deleting the oldest overflow. Since retention is now persistent (no
        TTL), this is what keeps rss_processed from growing unbounded for a
        long-running feed.
        """
        if max_count <= 0:
            return
        count = await self.db.rss_processed.count_documents({"chat_id": chat_id, "feed_url": feed_url})
        overflow = count - max_count
        if overflow <= 0:
            return
        cursor = self.db.rss_processed.find(
            {"chat_id": chat_id, "feed_url": feed_url},
            {"_id": 1}
        ).sort("processed_at", 1).limit(overflow)
        ids = [doc["_id"] async for doc in cursor]
        if ids:
            await self.db.rss_processed.delete_many({"_id": {"$in": ids}})



# --- Singleton Instance ---
db = MongoDB(config.MONGO_URI, config.DB_NAME)

async def get_db() -> AsyncIOMotorDatabase:
    return db.db
