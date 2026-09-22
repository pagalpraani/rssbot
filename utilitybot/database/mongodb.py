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

            # Run migration on startup
            await self.migrate_promoted_admins()
            await self.migrate_bypassed_users()

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


    # --- Night Mode Settings ---
    async def get_night_settings(self, group_id: int):
        cached = settings_cache.get_night_settings(group_id)
        if cached is not None:
            return cached
        settings = await self.db.night_mode.find_one({"group_id": group_id})

        # Define the schema with default values
        defaults = {
            "group_id": group_id,
            "enabled": False,
            "mode": "standard",
            "start": "23:00",
            "end": "06:00",
            "timezone": config.DEFAULT_TIMEZONE,
            "whitelist": [],
            "paused_until": None,
            "last_status": "inactive",
            "start_message_id": None,
            "end_message_id": None,
        }

        if settings:
            # Merge the defaults with the retrieved settings
            defaults.update(settings)

        settings_cache.set_night_settings(group_id, defaults)
        return defaults

    async def update_night_settings(self, group_id: int, data: dict):
        await self.db.night_mode.update_one(
            {"group_id": group_id},
            {"$set": data},
            upsert=True
        )
        settings_cache.invalidate_night_settings(group_id)

    async def toggle_night_mode(self, group_id: int, state: bool):
        await self.db.night_mode.update_one(
            {"group_id": group_id},
            {"$set": {"enabled": state}},
            upsert=True
        )
        settings_cache.invalidate_night_settings(group_id)

    async def get_all_night_mode_groups(self):
        return self.db.night_mode.find({"enabled": True, "start": {"$exists": True}, "end": {"$exists": True}})

    def get_cheering_channels(self):
        """
        Retrieves all channels that have cheerings enabled.
        """
        return self.db.settings.find({"cheering_enabled": True})

    async def get_rpost_channels(self):
        """
        Retrieves all channels that have active recursive posts.
        """
        return self.db.settings.find({"rpost": {"$exists": True, "$ne": None}})

    # --- Misban (Anti-Betrayal) Settings ---
    async def get_misban_settings(self, chat_id: int) -> dict:
        return await self.db.misban.find_one({"_id": chat_id}) or {}

    async def update_misban_settings(self, chat_id: int, data: dict):
        await self.db.misban.update_one({"_id": chat_id}, {"$set": data}, upsert=True)

    # --- Roles & Migration ---
    async def set_user_role(self, chat_id: int, user_id: int, role_name: str, user_name: str = None):
        """
        Assigns a role to a user in a chat.
        """
        data = {
            "role": role_name,
            "updated_at": datetime.now(timezone.utc)
        }
        if user_name:
            data["user_name"] = user_name

        await self.db.roles.update_one(
            {"chat_id": chat_id, "user_id": user_id},
            {
                "$set": data,
                "$setOnInsert": {
                    "created_at": datetime.now(timezone.utc)
                }
            },
            upsert=True
        )

    async def get_user_role(self, chat_id: int, user_id: int) -> str:
        """
        Retrieves a user's role in a chat. Returns None if no role assigned.
        """
        doc = await self.db.roles.find_one({"chat_id": chat_id, "user_id": user_id})
        return doc.get("role") if doc else None

    async def remove_user_role(self, chat_id: int, user_id: int):
        """
        Removes a user's role from the database.
        """
        await self.db.roles.delete_one({"chat_id": chat_id, "user_id": user_id})

    async def get_chat_staff(self, chat_id: int) -> list:
        """
        Retrieves all users with assigned roles in a chat.
        """
        cursor = self.db.roles.find({"chat_id": chat_id})
        return await cursor.to_list(length=None)

    async def migrate_promoted_admins(self):
        """
        Migrates legacy promoted_admins to the new roles collection.
        Default role for migrated admins is 'Admin' (as they were promoted via bot).
        """
        count = await self.db.promoted_admins.estimated_document_count()
        if count == 0:
            return

        log.info(f"Migrating {count} promoted admins to Roles system...")
        cursor = self.db.promoted_admins.find({})
        async for doc in cursor:
            chat_id = doc.get("chat_id")
            user_id = doc.get("user_id")
            if chat_id and user_id:
                # Check if already exists to avoid overwriting newer data
                exists = await self.db.roles.find_one({"chat_id": chat_id, "user_id": user_id})
                if not exists:
                    await self.set_user_role(chat_id, user_id, "Admin")

        # Safely rename old collection instead of dropping
        try:
            await self.db.promoted_admins.rename("promoted_admins_backup", dropTarget=True)
            log.info("Migration complete. 'promoted_admins' renamed to 'promoted_admins_backup'.")
        except Exception as e:
            log.warning(f"Could not rename 'promoted_admins': {e}. It may remain in DB.")

    # --- Legacy Promoted Admins (Deprecated, kept for reference if needed during migration debugging) ---
    async def add_promoted_admin(self, chat_id: int, user_id: int):
        # Now uses roles
        await self.set_user_role(chat_id, user_id, "Admin")

    async def remove_promoted_admin(self, chat_id: int, user_id: int):
        # Now uses roles
        await self.remove_user_role(chat_id, user_id)

    async def is_promoted_admin(self, chat_id: int, user_id: int) -> bool:
        # Now checks role
        role = await self.get_user_role(chat_id, user_id)
        return role == "Admin"

    # --- Managed Groups (User Shares) ---
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

    # --- Violations ---
    async def log_violation(self, chat_id: int, user_id: int, action: str, channels_missing: list, message_id: int):
        await self.db.violations.insert_one({
            "chat_id": chat_id,
            "user_id": user_id,
            "action": action,
            "channels_missing": channels_missing,
            "message_id": message_id,
            "reason": "not_subscribed",
            "timestamp": datetime.now(timezone.utc)
        })

    # --- Database Indexes ---
    async def setup_indexes(self):
        """
        Creates necessary indexes for performance.
        Each index creation is individually guarded so a stale definition
        mismatch does not crash startup — it logs a warning and continues.
        """
        # TTL index for violations
        retention_days = config.RETENTION_DAYS
        if retention_days > 0:
            await self.db.violations.create_index(
                "timestamp", expireAfterSeconds=retention_days * 86400, background=True # days to seconds
            )
            log.info(f"TTL index for 'violations' collection set to {retention_days} days.")

        # Performance Indexes
        await self.db.poster_users.create_index([("chat_id", 1), ("user_id", 1)], background=True)
        await self.db.poster_users.create_index("user_id", background=True)
        await self.db.night_mode.create_index("group_id", background=True)
        await self.db.roles.create_index([("chat_id", 1), ("user_id", 1)], background=True)
        await self.db.poster_queue.create_index("chat_id", background=True)

        # Filters: every message triggers find({chat_id}) — critical
        await self.db.filters.create_index("chat_id", background=True)
        await self.db.filters.create_index([("chat_id", 1), ("trigger", 1)], unique=True, background=True)

        # Notes: fetched by (chat_id, note_name) and by chat_id for lists
        await self.db.notes.create_index("chat_id", background=True)
        await self.db.notes.create_index([("chat_id", 1), ("note_name", 1)], unique=True, background=True)

        # Clean messages: scheduler scans by delete_at every tick
        await self.db.clean_messages.create_index("delete_at", background=True)

        # Contacts: queried by ticket_id
        await self.db.contacts.create_index("ticket_id", unique=True, sparse=True, background=True)

        # Settings: already uses _id but clean_messages and misban benefit from chat_id
        await self.db.misban.create_index("_id")  # already _id, but explicit

        # user_shares: queried by _id (auto-indexed) but the managed_groups
        # sub-array is searched with $elemMatch — a multikey index helps
        await self.db.user_shares.create_index("managed_groups.chat_id", background=True)

        # settings: sparse indexes for scheduler full-collection scans
        # get_cheering_channels: find({cheering_enabled: true})  — runs every hour
        await self.db.settings.create_index(
            "cheering_enabled",
            partialFilterExpression={"cheering_enabled": True},
            background=True
        )
        # get_rpost_channels: find({rpost: {$exists: true, $ne: null}}) — every minute
        await self.db.settings.create_index(
            "rpost",
            partialFilterExpression={"rpost": {"$exists": True}},
            background=True
        )

        # notes: get_repeated_notes scans by repeat_interval every minute
        await self.db.notes.create_index(
            "repeat_interval",
            partialFilterExpression={"repeat_interval": {"$exists": True}},
            background=True
        )

        # users: soft-delete queries use _id (primary key — auto-indexed)
        # No extra index needed since we now use {"_id": user_id} throughout.

        # blocked_contacts: is_contact_banned uses _id (auto-indexed)
        # No extra index needed since we now use {"_id": user_id} throughout.

        # Analytics & Stats
        await self.db.global_users.create_index("user_id", unique=True, background=True)
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

    # --- Poster Settings ---
    async def get_poster_settings(self, chat_id: int) -> dict:
        return await self.db.poster_settings.find_one({"_id": chat_id}) or {}

    async def update_poster_settings(self, chat_id: int, data: dict):
        await self.db.poster_settings.update_one(
            {"_id": chat_id},
            {"$set": data},
            upsert=True
        )

    # --- Poster Users (Whitelist) ---
    async def get_poster_user(self, chat_id: int, user_id: int) -> dict:
        return await self.db.poster_users.find_one({"chat_id": chat_id, "user_id": user_id})

    async def update_poster_user(self, chat_id: int, user_id: int, status: str, added_by: int = None):
        """
        Status: 'trusted' or 'pending'
        """
        data = {
            "status": status,
            "updated_at": datetime.now(timezone.utc)
        }
        if added_by:
            data["added_by"] = added_by

        await self.db.poster_users.update_one(
            {"chat_id": chat_id, "user_id": user_id},
            {
                "$set": data,
                "$setOnInsert": {"created_at": datetime.now(timezone.utc)}
            },
            upsert=True
        )

    async def remove_poster_user(self, chat_id: int, user_id: int):
        await self.db.poster_users.delete_one({"chat_id": chat_id, "user_id": user_id})

    async def get_poster_users(self, chat_id: int) -> list:
        cursor = self.db.poster_users.find({"chat_id": chat_id})
        return await cursor.to_list(length=None)

    # --- Poster Queue ---
    async def add_poster_queue(self, chat_id: int, user_id: int, from_chat_id: int, message_id: int, content_type: str, file_id: str = None, text: str = None, entities: list = None):
        """
        Adds a post to the approval queue.
        """
        queue_item = {
            "chat_id": chat_id,
            "user_id": user_id,
            "from_chat_id": from_chat_id,
            "original_message_id": message_id,
            "content_type": content_type,
            "file_id": file_id,
            "text": text,
            "entities": entities,
            "status": "waiting",
            "created_at": datetime.now(timezone.utc)
        }
        result = await self.db.poster_queue.insert_one(queue_item)
        return result.inserted_id

    async def get_poster_queue_item(self, object_id):
        return await self.db.poster_queue.find_one({"_id": object_id})

    async def update_poster_queue_status(self, object_id, status: str, approved_by: int = None):
        data = {"status": status, "updated_at": datetime.now(timezone.utc)}
        if approved_by:
            data["approved_by"] = approved_by
        await self.db.poster_queue.update_one({"_id": object_id}, {"$set": data})

    # --- Notes ---
    async def save_note(self, chat_id: int, note_name: str, content: str, media_id: str = None, media_type: str = None, is_admin_only: bool = False, is_protected: bool = False, is_private: bool = False, repeat_interval: int = None, delete_previous: bool = False):
        """
        Saves or updates a note in the database.
        """
        note_data = {
            "chat_id": chat_id,
            "note_name": note_name,
            "content": content,
            "media_id": media_id,
            "media_type": media_type,
            "is_admin_only": is_admin_only,
            "is_protected": is_protected,
            "is_private": is_private,
            "repeat_interval": repeat_interval,
            "delete_previous": delete_previous,
            "last_message_id": None, # Initialize last_message_id
            "last_sent": datetime.now(timezone.utc),
            "created_at": datetime.now(timezone.utc)
        }
        await self.db.notes.update_one(
            {"chat_id": chat_id, "note_name": note_name},
            {"$set": note_data},
            upsert=True
        )

    async def get_note(self, chat_id: int, note_name: str) -> dict:
        """
        Retrieves a note from the database.
        """
        return await self.db.notes.find_one({"chat_id": chat_id, "note_name": note_name})

    async def clear_note(self, chat_id: int, note_name: str) -> bool:
        """
        Deletes a note from the database.
        Returns True if a note was deleted, False otherwise.
        """
        result = await self.db.notes.delete_one({"chat_id": chat_id, "note_name": note_name})
        return result.deleted_count > 0

    async def list_notes(self, chat_id: int) -> list:
        """
        Lists all notes in a given chat.
        """
        cursor = self.db.notes.find({"chat_id": chat_id})
        return await cursor.to_list(length=None)

    async def clear_all_notes(self, chat_id: int):
        """
        Deletes all notes in a given chat.
        """
        await self.db.notes.delete_many({"chat_id": chat_id})

    # --- Filters ---
    async def add_filter(self, chat_id: int, trigger: str, content: str, media_id: str = None, media_type: str = None, is_exact: bool = False, is_prefix: bool = False, is_admin_only: bool = False, is_user_only: bool = False, is_protected: bool = False, is_replytag: bool = False):
        """
        Adds or updates a filter in the database.
        """
        filter_data = {
            "chat_id": chat_id,
            "trigger": trigger,
            "content": content,
            "media_id": media_id,
            "media_type": media_type,
            "is_exact": is_exact,
            "is_prefix": is_prefix,
            "is_admin_only": is_admin_only,
            "is_user_only": is_user_only,
            "is_protected": is_protected,
            "is_replytag": is_replytag,
            "created_at": datetime.now(timezone.utc)
        }
        await self.db.filters.update_one(
            {"chat_id": chat_id, "trigger": trigger},
            {"$set": filter_data},
            upsert=True
        )
        settings_cache.invalidate_filters(chat_id)

    async def get_all_filters(self, chat_id: int) -> list:
        """
        Retrieves all filters for a given chat.
        """
        cached = settings_cache.get_filters(chat_id)
        if cached is not None:
            return cached
        cursor = self.db.filters.find({"chat_id": chat_id})
        filters = await cursor.to_list(length=None)
        settings_cache.set_filters(chat_id, filters)
        return filters

    async def remove_filter(self, chat_id: int, trigger: str) -> bool:
        """
        Removes a filter from the database.
        Returns True if a filter was deleted, False otherwise.
        """
        result = await self.db.filters.delete_one({"chat_id": chat_id, "trigger": trigger})
        settings_cache.invalidate_filters(chat_id)
        return result.deleted_count > 0

    async def remove_all_filters(self, chat_id: int):
        """
        Removes all filters in a given chat.
        """
        await self.db.filters.delete_many({"chat_id": chat_id})
        settings_cache.invalidate_filters(chat_id)

    async def get_repeated_notes(self, limit: int = 500) -> list:
        """
        Retrieves all notes that have a repeat interval.
        Capped at *limit* rows to prevent unbounded memory growth on large
        deployments. The partial index on repeat_interval keeps this fast.
        """
        cursor = self.db.notes.find(
            {"repeat_interval": {"$ne": None}},
            limit=limit
        )
        return await cursor.to_list(length=limit)

    async def update_note_last_sent(self, chat_id: int, note_name: str, last_message_id: int = None):
        """
        Updates the last_sent timestamp and last_message_id for a note.
        """
        update_data = {"last_sent": datetime.now(timezone.utc)}
        if last_message_id is not None:
            update_data["last_message_id"] = last_message_id

        await self.db.notes.update_one(
            {"chat_id": chat_id, "note_name": note_name},
            {"$set": update_data}
        )

    # --- Message Cleaning ---
    async def log_cleaned_message(self, chat_id: int, message_id: int, clean_type: str, delay_seconds: int = 300):
        """
        Logs a message to be cleaned by the scheduler.
        """
        delete_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
        await self.db.clean_messages.insert_one({
            "chat_id": chat_id,
            "message_id": message_id,
            "type": clean_type,
            "created_at": datetime.now(timezone.utc),
            "delete_at": delete_at
        })

    async def get_messages_to_clean(self) -> list:
        """
        Retrieves all messages that are due to be cleaned (delete_at <= now).
        """
        now = datetime.now(timezone.utc)
        cursor = self.db.clean_messages.find({"delete_at": {"$lte": now}})
        return await cursor.to_list(length=None)

    async def remove_cleaned_message(self, object_id):
        """
        Removes a record from the clean_messages collection.
        """
        await self.db.clean_messages.delete_one({"_id": object_id})

    # --- User Verification ---
    async def verify_user(self, user_id: int, phone_number: str, first_name: str, verified_by: str = None):
        """
        Marks a user as verified.
        """
        data = {
            "first_name": first_name,
            "verified_at": datetime.now(timezone.utc),
            "verification_pending": False
        }
        if phone_number is not None:
             data["phone_number"] = phone_number

        if verified_by:
            data["verified_by"] = verified_by

        await self.db.verified_users.update_one(
            {"_id": user_id},
            {"$set": data},
            upsert=True
        )

    async def set_verification_pending(self, user_id: int, status: bool):
        """
        Sets the verification pending status to prevent spamming prompts.
        """
        await self.db.verified_users.update_one(
            {"_id": user_id},
            {"$set": {"verification_pending": status}},
            upsert=True
        )

    async def is_verification_pending(self, user_id: int) -> bool:
        """
        Checks if verification prompt was already sent.
        """
        doc = await self.db.verified_users.find_one({"_id": user_id})
        return doc.get("verification_pending", False) if doc else False

    async def migrate_bypassed_users(self):
        """
        Migrates legacy 'BYPASSED_BY_OWNER' phone numbers to 'verified_by: owner' metadata.
        """
        count = await self.db.verified_users.count_documents({"phone_number": "BYPASSED_BY_OWNER"})
        if count == 0:
            return

        log.info(f"Migrating {count} bypassed users to new metadata format...")
        result = await self.db.verified_users.update_many(
             {"phone_number": "BYPASSED_BY_OWNER"},
             {
                 "$set": {"verified_by": "owner", "phone_number": None}
             }
        )
        log.info(f"Migration complete. {result.modified_count} users updated.")

    async def is_user_verified(self, user_id: int) -> bool:
        """
        Checks if a user is verified.
        """
        user = await self.db.verified_users.find_one({"_id": user_id})
        # Must check for verified_at because we now create docs for pending users too
        return bool(user and user.get("verified_at"))

    async def unverify_user(self, user_id: int):
        """
        Removes a user's verification status.
        """
        await self.db.verified_users.delete_one({"_id": user_id})

    async def get_verified_user(self, user_id: int) -> dict:
        """
        Retrieves verification details for a user.
        """
        return await self.db.verified_users.find_one({"_id": user_id})

    async def get_verified_users_bulk(self, user_ids: list[int]) -> dict:
        """
        Retrieves verification details for multiple users in bulk.
        Returns a dictionary mapping user_id to verification details.
        """
        cursor = self.db.verified_users.find({"_id": {"$in": user_ids}})
        verified_users = await cursor.to_list(length=None)
        return {user["_id"]: user for user in verified_users}

    # --- Contact Module ---

    async def get_last_contact_time(self, user_id: int):
        """
        Returns the datetime of the user's last contact submission, or None.
        Uses the contacts collection — finds the most recent ticket by this user.
        """
        doc = await self.db.contacts.find_one(
            {"user_id": user_id},
            sort=[("created_at", -1)]
        )
        return doc["created_at"] if doc else None

    async def get_contact_cooldown_remaining(self, user_id: int, cooldown_seconds: int = 300) -> int:
        """
        Returns the number of seconds the user must still wait before sending
        another contact message, or 0 if the cooldown has expired.
        """
        last = await self.get_last_contact_time(user_id)
        if last is None:
            return 0
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        remaining = cooldown_seconds - elapsed
        return max(0, int(remaining))

    async def add_contact_ticket(self, ticket_id: str, user_id: int, username: str, message: str, media: list | dict = None):
        """
        Creates a new contact ticket.
        """
        await self.db.contacts.insert_one({
            "ticket_id": ticket_id,
            "user_id": user_id,
            "username": username,
            "message": message,
            "media": media,
            "status": "open",
            "created_at": datetime.now(timezone.utc),
            "replied_at": None,
            "admin_id": None
        })

    async def get_contact_ticket(self, ticket_id: str):
        return await self.db.contacts.find_one({"ticket_id": ticket_id})

    async def close_contact_ticket(self, ticket_id: str, admin_id: int):
        await self.db.contacts.update_one(
            {"ticket_id": ticket_id},
            {"$set": {"status": "closed", "admin_id": admin_id, "replied_at": datetime.now(timezone.utc)}}
        )

    async def is_contact_banned(self, user_id: int) -> bool:
        doc = await self.db.blocked_contacts.find_one({"_id": user_id})
        return bool(doc)

    async def ban_contact_user(self, user_id: int, reason: str = "abuse"):
        await self.db.blocked_contacts.update_one(
            {"_id": user_id},
            {"$set": {"reason": reason, "banned_at": datetime.now(timezone.utc)}},
            upsert=True
        )

    async def unban_contact_user(self, user_id: int):
        await self.db.blocked_contacts.delete_one({"_id": user_id})

    # --- Soft Delete (GDPR) ---
    async def soft_delete_user(self, user_id: int):
        """
        Marks a user as deleted (soft delete).
        """
        await self.db.users.update_one(
            {"_id": user_id},
            {
                "$set": {
                    "deleted": True,
                    "deleted_at": datetime.now(timezone.utc)
                }
            },
            upsert=True
        )

    async def restore_user(self, user_id: int):
        """
        Restores a soft-deleted user.
        """
        await self.db.users.update_one(
            {"_id": user_id},
            {
                "$set": {
                    "deleted": False,
                    "deleted_at": None
                }
            }
        )

    async def is_user_deleted(self, user_id: int) -> bool:
        """
        Checks if a user is soft-deleted.
        """
        user = await self.db.users.find_one({"_id": user_id})
        return bool(user and user.get("deleted"))

    # --- Analytics & Global Users ---
    async def track_global_user(self, user_id: int, username: str, full_name: str, first_seen: datetime, interaction_type: str = "private", chat_id: int = None, chat_title: str = None):
        """
        Adds a new unique user to the global_users collection.
        """
        insert_doc = {
            "username": username,
            "full_name": full_name,
            "first_seen": first_seen,
            "interaction_type": interaction_type
        }
        if chat_id:
            insert_doc["first_seen_chat_id"] = chat_id
        if chat_title:
            insert_doc["first_seen_chat_title"] = chat_title

        await self.db.global_users.update_one(
            {"user_id": user_id},
            {
                "$setOnInsert": insert_doc
            },
            upsert=True
        )

    async def get_all_global_users(self):
        """Retrieves all global users."""
        cursor = self.db.global_users.find({})
        return await cursor.to_list(length=None)

    async def get_global_user_info(self, user_id: int):
        return await self.db.global_users.find_one({"user_id": user_id})

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

    async def link_bot_chat_to_rss(self, chat_id: int):
        """Marks a chat as linked to the RSS dashboard."""
        await self.db.bot_chats.update_one(
            {"chat_id": chat_id},
            {"$set": {"rss_linked": True, "updated_at": datetime.now(timezone.utc)}}
        )

    async def get_all_bot_chats(self):
        """Returns all RSS-linked chats for the dashboard."""
        cursor = self.db.bot_chats.find({"rss_linked": {"$ne": False}})
        return await cursor.to_list(length=None)

    async def get_unlinked_bot_chats(self):
        """Returns chats known to the bot but not currently in the RSS dashboard."""
        cursor = self.db.bot_chats.find({"rss_linked": False})
        return await cursor.to_list(length=None)

    # --- Analytics Persistence ---
    async def save_analytics_batch(self, update_data: dict):
        """
        Saves batched analytics counters to the database.
        Uses $inc to atomically increment counters.
        """
        if not update_data:
            return

        # Using a single document for global stats is efficient enough for these aggregate metrics.
        # Structure will be something like:
        # { "_id": "global_stats", "commands.ban": 5, "moderation.kicks": 2, "admin_actions.123456": 10 }
        await self.db.analytics_events.update_one(
            {"_id": "global_stats"},
            {"$inc": update_data},
            upsert=True
        )

    async def get_global_analytics(self) -> dict:
        """Retrieves all time global analytics."""
        return await self.db.analytics_events.find_one({"_id": "global_stats"}) or {}

    # --- RSS Module ---
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

    async def get_feed_settings(self, chat_id: int, feed_url: str) -> dict:
        """Retrieves settings for a specific RSS feed."""
        return await self.db.rss_feeds.find_one({"chat_id": chat_id, "feed_url": feed_url})


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

    async def is_rss_item_processed(self, chat_id: int, feed_url: str, item_id: str) -> bool:
        """Checks if an RSS item has already been processed."""
        doc = await self.db.rss_processed.find_one({"chat_id": chat_id, "feed_url": feed_url, "item_id": item_id})
        return bool(doc)

    async def mark_rss_item_processed(self, chat_id: int, feed_url: str, item_id: str):
        """Marks an RSS item as processed (upsert — safe for concurrent tasks)."""
        await self.db.rss_processed.update_one(
            {"chat_id": chat_id, "feed_url": feed_url, "item_id": item_id},
            {"$setOnInsert": {"chat_id": chat_id, "feed_url": feed_url,
                              "item_id": item_id, "processed_at": datetime.now(timezone.utc)}},
            upsert=True
        )

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
