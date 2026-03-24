#!/usr/bin/env python3
"""
Lightweight Paprika Recipe Manager 3 API client.

Supports:
  - Authentication (email/password → JWT)
  - Listing and fetching recipes
  - Searching recipes by keyword across name, ingredients, categories, notes
  - Pushing grocery items to the grocery list
  - Reading a local .paprikarecipes export file as fallback

Environment variables:
  PAPRIKA_EMAIL    — your Paprika account email
  PAPRIKA_PASSWORD — your Paprika account password

Usage as a CLI:
  python paprika_api.py auth                        # Test authentication
  python paprika_api.py recipes                     # List all recipe names
  python paprika_api.py search "chickpea tahini"    # Search recipes
  python paprika_api.py recipe <uid>                # Get full recipe
  python paprika_api.py groceries push items.json   # Push grocery items
  python paprika_api.py groceries list              # List current grocery items
  python paprika_api.py local <file.paprikarecipes> search "keyword"

Usage as a library:
  from paprika_api import PaprikaAPI, PaprikaLocalFile

  api = PaprikaAPI()  # reads credentials from env
  api.login()
  recipes = api.list_recipes()
  recipe = api.get_recipe(uid)
  results = api.search_recipes("chickpea tahini", limit=20)
  api.push_grocery_items([{"name": "Chickpeas", "quantity": "2 cans", "aisle": "Canned"}])
"""

import gzip
import hashlib
import io
import json
import os
import re
import sys
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin
from typing import Optional

try:
    import requests
except ImportError:
    requests = None


BASE_URL = "https://www.paprikaapp.com/api/v2/"


class PaprikaAPIError(Exception):
    """Raised when the Paprika API returns an error."""
    pass


class PaprikaAPI:
    """Client for the Paprika Recipe Manager 3 cloud API."""

    # Default cache location — override with PAPRIKA_CACHE_DIR env var
    DEFAULT_CACHE_DIR = os.path.expanduser("~/.paprika-mcp")
    CACHE_FILENAME = "paprika_recipe_cache.json"

    def __init__(self, email: Optional[str] = None, password: Optional[str] = None,
                 cache_dir: Optional[str] = None):
        if requests is None:
            raise ImportError("The 'requests' library is required. Install with: pip install requests")
        self.email = email or os.environ.get("PAPRIKA_EMAIL")
        self.password = password or os.environ.get("PAPRIKA_PASSWORD")
        if not self.email or not self.password:
            raise PaprikaAPIError(
                "Paprika credentials not found. Set PAPRIKA_EMAIL and PAPRIKA_PASSWORD "
                "environment variables, or pass email/password to the constructor."
            )
        self.token = None
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "paprika-mcp/1.0",
            "Accept": "*/*",
        })
        self._recipe_cache = {}

        # Disk cache
        self._cache_dir = cache_dir or os.environ.get("PAPRIKA_CACHE_DIR", self.DEFAULT_CACHE_DIR)
        self._cache_path = os.path.join(self._cache_dir, self.CACHE_FILENAME)
        self._disk_cache: list[dict] | None = None

    def login(self) -> str:
        """Authenticate and store the JWT token. Returns the token."""
        resp = self.session.post(
            urljoin(BASE_URL, "../v1/account/login"),
            data={"email": self.email, "password": self.password},
        )
        resp.raise_for_status()
        body = resp.json()
        if "result" not in body or "token" not in body["result"]:
            raise PaprikaAPIError(f"Unexpected login response: {body}")
        self.token = body["result"]["token"]
        self.session.headers["Authorization"] = f"Bearer {self.token}"
        return self.token

    def _ensure_auth(self):
        if not self.token:
            self.login()

    def _get(self, endpoint: str) -> dict:
        self._ensure_auth()
        resp = self.session.get(urljoin(BASE_URL, endpoint))
        resp.raise_for_status()
        body = resp.json()
        if "result" not in body:
            raise PaprikaAPIError(f"Unexpected response from {endpoint}: {body}")
        return body["result"]

    def _upload_photo_entity(self, recipe_uid: str, filename: str,
                              photo_bytes: bytes) -> dict:
        """Upload a photo as a separate Paprika photo entity.

        Photos in Paprika are their own sync entity type (like recipes, groceries).
        Each photo has: uid, filename, recipe_uid, order_flag, name, hash.
        The image binary is sent as a separate multipart part alongside the
        gzipped JSON metadata.
        """
        photo_uid = str(uuid.uuid4()).upper()
        photo_hash = hashlib.sha256(photo_bytes).hexdigest().upper()

        photo_entity = {
            "uid": photo_uid,
            "filename": filename,
            "recipe_uid": recipe_uid,
            "order_flag": 0,
            "name": "",
            "hash": photo_hash,
        }

        return self._post_gzipped(f"sync/photo/{photo_uid}/", photo_entity,
                                   photo_bytes=photo_bytes)

    def _post_gzipped(self, endpoint: str, data: dict,
                       photo_bytes: bytes = None) -> dict:
        """POST gzip-compressed JSON as multipart form data.

        If photo_bytes is provided, it's sent as a separate 'photo_upload'
        multipart part alongside the gzipped recipe JSON 'data' part.
        """
        self._ensure_auth()
        json_bytes = json.dumps(data).encode("utf-8")
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
            gz.write(json_bytes)
        compressed = buf.getvalue()

        files = {"data": ("data.gz", compressed, "application/gzip")}
        if photo_bytes:
            files["photo_upload"] = ("photo.jpg", photo_bytes, "image/jpeg")

        resp = self.session.post(
            urljoin(BASE_URL, endpoint),
            files=files,
        )
        resp.raise_for_status()
        body = resp.json()
        return body.get("result", body)

    # ── Disk Cache ─────────────────────────────────────────────

    def _load_disk_cache(self) -> list[dict] | None:
        """Load recipes from the local JSON cache file, if it exists."""
        if self._disk_cache is not None:
            return self._disk_cache
        if os.path.exists(self._cache_path):
            with open(self._cache_path, "r") as f:
                data = json.load(f)
            self._disk_cache = data.get("recipes", [])
            # Also populate the in-memory UID cache
            for r in self._disk_cache:
                if "uid" in r:
                    self._recipe_cache[r["uid"]] = r
            return self._disk_cache
        return None

    def _save_disk_cache(self, recipes: list[dict]) -> str:
        """Save recipes to the local JSON cache file. Returns the cache path."""
        os.makedirs(self._cache_dir, exist_ok=True)
        data = {
            "synced_at": datetime.now().isoformat(),
            "count": len(recipes),
            "recipes": recipes,
        }
        with open(self._cache_path, "w") as f:
            json.dump(data, f, indent=1)
        self._disk_cache = recipes
        return self._cache_path

    def cache_info(self) -> dict:
        """Return info about the local cache (exists, path, age, count)."""
        if os.path.exists(self._cache_path):
            with open(self._cache_path, "r") as f:
                data = json.load(f)
            synced_at = data.get("synced_at", "unknown")
            count = data.get("count", len(data.get("recipes", [])))
            return {
                "cached": True,
                "path": self._cache_path,
                "synced_at": synced_at,
                "recipe_count": count,
            }
        return {"cached": False, "path": self._cache_path}

    def sync_recipes(self, progress_callback=None) -> dict:
        """Full sync: fetch all recipes from the API and save to disk cache.
        Returns cache info dict."""
        recipes = self.get_all_recipes(progress_callback=progress_callback)
        path = self._save_disk_cache(recipes)
        return {
            "synced": len(recipes),
            "cache_path": path,
            "synced_at": datetime.now().isoformat(),
        }

    # ── Categories ─────────────────────────────────────────────

    def list_categories(self) -> list[dict]:
        """List all recipe categories (UIDs and names)."""
        return self._get("sync/categories/")

    def get_category(self, uid: str) -> dict:
        """Fetch a single category by UID."""
        return self._get(f"sync/category/{uid}/")

    def get_all_categories(self) -> list[dict]:
        """Fetch all categories with full details."""
        listing = self.list_categories()
        categories = []
        for item in listing:
            try:
                cat = self.get_category(item["uid"])
                categories.append(cat)
            except Exception:
                # If individual fetch fails, use the listing data
                categories.append(item)
        return categories

    def resolve_category_uid(self, name: str) -> str | None:
        """Look up a category UID by name (case-insensitive partial match)."""
        categories = self.get_all_categories()
        name_lower = name.lower()
        # Exact match first
        for c in categories:
            if c.get("name", "").lower() == name_lower:
                return c["uid"]
        # Partial match
        for c in categories:
            if name_lower in c.get("name", "").lower():
                return c["uid"]
        return None

    def resolve_category_uids(self, names: list[str]) -> list[str]:
        """Resolve a list of category names to UIDs."""
        all_cats = self.get_all_categories()
        uids = []
        for name in names:
            name_lower = name.lower()
            found = None
            for c in all_cats:
                if c.get("name", "").lower() == name_lower:
                    found = c["uid"]
                    break
            if not found:
                for c in all_cats:
                    if name_lower in c.get("name", "").lower():
                        found = c["uid"]
                        break
            if found:
                uids.append(found)
        return uids

    # ── Recipe Update ─────────────────────────────────────────

    # ── Recipe Creation ──────────────────────────────────────────

    def create_recipe(self, recipe_data: dict, photo_path: str = None,
                       photo_base64: str = None, image_url: str = None) -> dict:
        """Create a brand-new recipe in Paprika.

        recipe_data should contain at minimum:
          - name: str
          - ingredients: str (newline-separated)
          - directions: str (paragraphs separated by \\n\\n)

        Optional fields: source, source_url, servings, prep_time, cook_time,
        total_time, categories (list of category UIDs or names), rating,
        difficulty, notes, nutritional_info, description.

        Photo can be provided as:
          - photo_path: path to an image file on disk
          - photo_base64: raw base64-encoded image data (no data: prefix)
          - image_url: URL to download the image from (also sets thumbnail)

        Photos are uploaded via a three-part mechanism:
          1. image_url in recipe JSON → thumbnail in list view
          2. photo metadata (UUID filename + SHA-256 hash) + image bytes
             as 'photo_upload' multipart part with the recipe POST
          3. Separate photo entity POST to sync/photo/{uid}/ with
             the same image bytes → zoomable photo in detail view

        Returns the API response dict.
        """
        import base64
        import hashlib

        new_uid = str(uuid.uuid4()).upper()

        # Resolve category names to UIDs if they look like names (not UUIDs)
        categories = recipe_data.get("categories", [])
        if categories and isinstance(categories[0], str) and "-" not in categories[0]:
            categories = self.resolve_category_uids(categories)

        recipe = {
            "uid": new_uid,
            "name": recipe_data.get("name", ""),
            "source": recipe_data.get("source", ""),
            "source_url": recipe_data.get("source_url", ""),
            "servings": recipe_data.get("servings", ""),
            "prep_time": recipe_data.get("prep_time", ""),
            "cook_time": recipe_data.get("cook_time", ""),
            "total_time": recipe_data.get("total_time", ""),
            "categories": categories,
            "rating": recipe_data.get("rating", 0),
            "difficulty": recipe_data.get("difficulty", ""),
            "ingredients": recipe_data.get("ingredients", ""),
            "directions": recipe_data.get("directions", ""),
            "notes": recipe_data.get("notes", ""),
            "nutritional_info": recipe_data.get("nutritional_info", ""),
            "description": recipe_data.get("description", ""),
            "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "photo": "",
            "photo_hash": "",
            "image_url": image_url or recipe_data.get("image_url", None),
            "photo_url": None,
            "scale": None,
            "on_favorites": recipe_data.get("on_favorites", False),
            "in_trash": False,
        }

        # Resolve photo bytes from whichever source is provided
        photo_bytes = None
        if photo_path and os.path.exists(photo_path):
            with open(photo_path, "rb") as f:
                photo_bytes = f.read()
        elif image_url:
            try:
                img_resp = requests.get(image_url, timeout=30, headers={
                    "User-Agent": "paprika-mcp/1.0",
                    "Accept": "image/*",
                })
                img_resp.raise_for_status()
                photo_bytes = img_resp.content
                if len(photo_bytes) <= 100:
                    photo_bytes = None
            except Exception:
                pass
        elif photo_base64:
            b64 = photo_base64
            if "," in b64:
                b64 = b64.split(",", 1)[1]
            photo_bytes = base64.b64decode(b64)

        # If we have photo bytes, set the photo metadata in the recipe.
        # Keep image_url for the thumbnail preview, AND upload the photo
        # entity separately for the real zoomable photo.
        photo_filename = None
        if photo_bytes:
            photo_filename = str(uuid.uuid4()).upper() + ".jpg"
            photo_hash = hashlib.sha256(photo_bytes).hexdigest().upper()
            recipe["photo"] = photo_filename
            recipe["photo_hash"] = photo_hash

        # Compute hash
        recipe["hash"] = hashlib.sha256(
            json.dumps(recipe, sort_keys=True).encode("utf-8")
        ).hexdigest()

        # Step 1: Push recipe to Paprika (with photo bytes for thumbnail)
        result = self._post_gzipped(f"sync/recipe/{new_uid}/", recipe,
                                     photo_bytes=photo_bytes)

        # Step 2: Also upload photo as a separate entity (for zoomable photo)
        if photo_bytes and photo_filename:
            try:
                self._upload_photo_entity(new_uid, photo_filename, photo_bytes)
            except Exception as e:
                # Photo upload failed but recipe was created
                result = {"photo_upload_error": str(e), "recipe_created": True}

        # Cache locally
        self._recipe_cache[new_uid] = recipe
        if self._disk_cache is not None:
            self._disk_cache.append(recipe)
            self._save_disk_cache(self._disk_cache)

        return {
            "uid": new_uid,
            "name": recipe["name"],
            "has_photo": recipe.get("photo") is not None,
            "result": result,
        }

    # ── Recipe Update ─────────────────────────────────────────

    SAFE_UPDATE_FIELDS = {"categories", "rating", "notes", "prep_time", "cook_time",
                          "total_time", "difficulty", "servings", "source", "source_url"}

    def update_recipe(self, uid: str, updates: dict) -> dict:
        """Update safe metadata fields on a recipe. Refuses to touch ingredients or directions.

        Allowed fields: categories, rating, notes, prep_time, cook_time,
                       total_time, difficulty, servings, source, source_url
        """
        unsafe = set(updates.keys()) - self.SAFE_UPDATE_FIELDS
        if unsafe:
            raise PaprikaAPIError(
                f"Refusing to update protected fields: {unsafe}. "
                f"Allowed fields: {self.SAFE_UPDATE_FIELDS}"
            )

        # Fetch current recipe — bypass cache to get fresh data
        self._ensure_auth()
        recipe = self._get(f"sync/recipe/{uid}/")

        # Apply updates
        for key, value in updates.items():
            recipe[key] = value

        # Recompute hash so Paprika recognizes the change
        recipe["hash"] = hashlib.sha256(
            json.dumps(recipe, sort_keys=True).encode("utf-8")
        ).hexdigest()

        # Push back via gzipped POST
        result = self._post_gzipped(f"sync/recipe/{uid}/", recipe)

        # Update in-memory and disk caches
        self._recipe_cache[uid] = recipe
        if self._disk_cache is not None:
            for i, r in enumerate(self._disk_cache):
                if r.get("uid") == uid:
                    self._disk_cache[i] = recipe
                    break
            self._save_disk_cache(self._disk_cache)

        return result

    def update_recipe_photo(self, uid: str, photo_path: str = None,
                            photo_base64: str = None, image_url: str = None) -> dict:
        """Attach or replace the photo on an existing recipe.

        Photo can be provided as:
          - photo_path: path to an image file on disk
          - photo_base64: raw base64-encoded image data
          - image_url: URL to download the image from (also sets thumbnail)

        Uses the same three-part photo mechanism as create_recipe:
          1. image_url → thumbnail in list view
          2. photo metadata + bytes in recipe POST → inline photo
          3. Separate photo entity POST → zoomable photo in detail view
        """
        import base64 as b64_mod
        import hashlib

        # Fetch current recipe
        self._ensure_auth()
        recipe = self._get(f"sync/recipe/{uid}/")

        # Resolve photo bytes
        photo_bytes = None
        if photo_path and os.path.exists(photo_path):
            with open(photo_path, "rb") as f:
                photo_bytes = f.read()
        elif image_url:
            try:
                img_resp = requests.get(image_url, timeout=30, headers={
                    "User-Agent": "paprika-mcp/1.0",
                    "Accept": "image/*",
                })
                img_resp.raise_for_status()
                photo_bytes = img_resp.content
                if len(photo_bytes) <= 100:
                    raise PaprikaAPIError(
                        f"Downloaded image too small ({len(photo_bytes)} bytes), likely an error page")
            except PaprikaAPIError:
                raise
            except Exception as e:
                raise PaprikaAPIError(f"Failed to download image from {image_url}: {e}")
        elif photo_base64:
            b64 = photo_base64
            if "," in b64:
                b64 = b64.split(",", 1)[1]
            photo_bytes = b64_mod.b64decode(b64)

        if not photo_bytes:
            raise PaprikaAPIError("No valid photo data provided or resolved.")

        # Set photo metadata on the recipe
        photo_filename = str(uuid.uuid4()).upper() + ".jpg"
        photo_hash = hashlib.sha256(photo_bytes).hexdigest().upper()
        recipe["photo"] = photo_filename
        recipe["photo_hash"] = photo_hash
        if image_url:
            recipe["image_url"] = image_url

        # Recompute hash
        recipe["hash"] = hashlib.sha256(
            json.dumps(recipe, sort_keys=True).encode("utf-8")
        ).hexdigest()

        # Step 1: Push updated recipe with photo bytes
        result = self._post_gzipped(f"sync/recipe/{uid}/", recipe,
                                     photo_bytes=photo_bytes)

        # Step 2: Upload photo as separate entity (zoomable photo)
        try:
            self._upload_photo_entity(uid, photo_filename, photo_bytes)
        except Exception as e:
            result = {"photo_entity_error": str(e), "recipe_updated": True}

        # Update caches
        self._recipe_cache[uid] = recipe
        if self._disk_cache is not None:
            for i, r in enumerate(self._disk_cache):
                if r.get("uid") == uid:
                    self._disk_cache[i] = recipe
                    break
            self._save_disk_cache(self._disk_cache)

        return result

    # ── Recipes ──────────────────────────────────────────────

    def list_recipes(self) -> list[dict]:
        """List all recipes (UIDs and hashes only — lightweight)."""
        return self._get("sync/recipes/")

    def get_recipe(self, uid: str) -> dict:
        """Fetch full recipe details by UID."""
        if uid in self._recipe_cache:
            return self._recipe_cache[uid]
        recipe = self._get(f"sync/recipe/{uid}/")
        self._recipe_cache[uid] = recipe
        return recipe

    def get_all_recipes(self, progress_callback=None) -> list[dict]:
        """Fetch all recipes with full details. This makes one API call per recipe,
        so it can be slow for large collections. Results are cached."""
        listing = self.list_recipes()
        recipes = []
        for i, item in enumerate(listing):
            uid = item["uid"]
            recipe = self.get_recipe(uid)
            recipes.append(recipe)
            if progress_callback:
                progress_callback(i + 1, len(listing), recipe.get("name", ""))
        return recipes

    def search_recipes(self, query: str, limit: int = 20, recipes: list[dict] = None) -> list[dict]:
        """Search recipes by keyword across name, ingredients, categories, notes, and description.

        If `recipes` is provided, searches that list (useful with cached/local data).
        Otherwise tries the disk cache first, then falls back to API (slow).
        """
        if recipes is None:
            # Try disk cache first — avoids 2000+ API calls
            cached = self._load_disk_cache()
            if cached:
                recipes = cached
            else:
                recipes = self.get_all_recipes()

        terms = query.lower().split()
        scored = []
        for r in recipes:
            searchable = " ".join([
                str(r.get("name", "")),
                str(r.get("ingredients", "")),
                str(r.get("categories", "")),
                str(r.get("notes", "")),
                str(r.get("description", "")),
                str(r.get("directions", "")),
            ]).lower()

            # Score: count how many search terms match
            matches = sum(1 for t in terms if t in searchable)
            if matches > 0:
                # Boost for name matches
                name = r.get("name", "").lower()
                name_matches = sum(1 for t in terms if t in name)
                score = matches + (name_matches * 2)
                scored.append((score, r))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:limit]]

    # ── Grocery Lists ────────────────────────────────────────

    def list_grocery_lists(self) -> list[dict]:
        """Get all grocery lists (name, uid, order_flag, etc.)."""
        return self._get("sync/grocerylists/")

    def get_all_grocery_lists(self) -> list[dict]:
        """Fetch all grocery lists. The listing endpoint returns full details."""
        return self.list_grocery_lists()

    def resolve_list_uid(self, list_name: str) -> str | None:
        """Look up a grocery list UID by name (case-insensitive partial match)."""
        lists = self.get_all_grocery_lists()
        name_lower = list_name.lower()
        # Try exact match first
        for gl in lists:
            if gl.get("name", "").lower() == name_lower:
                return gl["uid"]
        # Then partial
        for gl in lists:
            if name_lower in gl.get("name", "").lower():
                return gl["uid"]
        return None

    # ── Grocery Items ─────────────────────────────────────────

    def list_grocery_items(self) -> list[dict]:
        """Get current grocery list items."""
        return self._get("sync/groceries/")

    def push_grocery_items(self, items: list[dict], list_name: str = None) -> dict:
        """Push grocery items to the Paprika grocery list.

        Each item should have at minimum:
          - name: str (display name)
          - ingredient: str (ingredient text)
          - aisle: str (category/aisle for grouping)

        Optional fields:
          - quantity: str (e.g., "2 cans")
          - recipe: str (recipe name this came from)
          - recipe_uid: str (UID of source recipe)

        If list_name is provided (e.g., "Trader Joes"), resolves it
        to a list_uid and applies it to all items.
        """
        # Resolve list name to UID if provided
        target_list_uid = ""
        if list_name:
            target_list_uid = self.resolve_list_uid(list_name) or ""
            if not target_list_uid:
                raise PaprikaAPIError(
                    f"Grocery list '{list_name}' not found. "
                    f"Available lists: {[gl.get('name') for gl in self.get_all_grocery_lists()]}"
                )

        prepared = []
        for item in items:
            grocery_item = {
                "uid": item.get("uid", str(uuid.uuid4()).upper()),
                "name": item.get("name", item.get("ingredient", "")),
                "ingredient": item.get("ingredient", item.get("name", "")),
                "aisle": item.get("aisle", ""),
                "quantity": item.get("quantity", ""),
                "recipe": item.get("recipe", ""),
                "recipe_uid": item.get("recipe_uid", ""),
                "order_flag": item.get("order_flag", 0),
                "purchased": False,
                "separate": item.get("separate", False),
                "aisle_uid": item.get("aisle_uid", ""),
                "list_uid": target_list_uid or item.get("list_uid", ""),
                "instruction": item.get("instruction", ""),
            }
            prepared.append(grocery_item)

        return self._post_gzipped("sync/groceries/", prepared)

    # ── Convenience ──────────────────────────────────────────

    def recipe_summary(self, recipe: dict) -> dict:
        """Extract a concise summary from a full recipe for display."""
        return {
            "uid": recipe.get("uid", ""),
            "name": recipe.get("name", ""),
            "categories": recipe.get("categories", []),
            "prep_time": recipe.get("prep_time", ""),
            "cook_time": recipe.get("cook_time", ""),
            "total_time": recipe.get("total_time", ""),
            "servings": recipe.get("servings", ""),
            "rating": recipe.get("rating", 0),
            "ingredients_preview": (recipe.get("ingredients", "") or "")[:300],
        }


class PaprikaLocalFile:
    """Read recipes from a local .paprikarecipes export file.

    This is the fallback when the API is unavailable.
    A .paprikarecipes file is a ZIP archive containing individual
    .paprikarecipe files, each of which is gzip-compressed JSON.
    """

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.recipes = []
        self._load()

    def _load(self):
        with zipfile.ZipFile(self.filepath, "r") as zf:
            for name in zf.namelist():
                with zf.open(name) as f:
                    data = gzip.decompress(f.read())
                    recipe = json.loads(data)
                    self.recipes.append(recipe)

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """Search recipes using the same logic as PaprikaAPI.search_recipes."""
        # Reuse the search logic
        api = object.__new__(PaprikaAPI)
        return PaprikaAPI.search_recipes(api, query, limit=limit, recipes=self.recipes)

    def get_recipe_by_name(self, name: str) -> Optional[dict]:
        """Find a recipe by exact or partial name match."""
        name_lower = name.lower()
        # Try exact match first
        for r in self.recipes:
            if r.get("name", "").lower() == name_lower:
                return r
        # Then partial
        for r in self.recipes:
            if name_lower in r.get("name", "").lower():
                return r
        return None

    def all_recipes(self) -> list[dict]:
        return self.recipes


# ── CLI ──────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]

    if command == "auth":
        api = PaprikaAPI()
        token = api.login()
        print(f"Authenticated successfully. Token: {token[:20]}...")

    elif command == "recipes":
        api = PaprikaAPI()
        listing = api.list_recipes()
        print(f"Found {len(listing)} recipes")
        for item in listing[:20]:
            print(f"  {item['uid']}")

    elif command == "search":
        if len(sys.argv) < 3:
            print("Usage: paprika_api.py search <query>")
            sys.exit(1)
        query = " ".join(sys.argv[2:])
        api = PaprikaAPI()
        results = api.search_recipes(query)
        for r in results:
            print(f"  {r['name']}  |  {r.get('categories', '')}")

    elif command == "recipe":
        if len(sys.argv) < 3:
            print("Usage: paprika_api.py recipe <uid>")
            sys.exit(1)
        api = PaprikaAPI()
        recipe = api.get_recipe(sys.argv[2])
        print(json.dumps(recipe, indent=2))

    elif command == "groceries":
        if len(sys.argv) < 3:
            print("Usage: paprika_api.py groceries [list|push <file.json>]")
            sys.exit(1)
        subcmd = sys.argv[2]
        api = PaprikaAPI()
        if subcmd == "list":
            items = api.list_grocery_items()
            for item in items:
                status = "[x]" if item.get("purchased") else "[ ]"
                print(f"  {status} {item.get('name', '')} — {item.get('quantity', '')} ({item.get('aisle', '')})")
        elif subcmd == "push":
            if len(sys.argv) < 4:
                print("Usage: paprika_api.py groceries push <file.json>")
                sys.exit(1)
            with open(sys.argv[3]) as f:
                items = json.load(f)
            result = api.push_grocery_items(items)
            print(f"Pushed {len(items)} items. Response: {result}")

    elif command == "local":
        if len(sys.argv) < 4:
            print("Usage: paprika_api.py local <file.paprikarecipes> search <query>")
            sys.exit(1)
        filepath = sys.argv[2]
        subcmd = sys.argv[3]
        local = PaprikaLocalFile(filepath)
        if subcmd == "search":
            query = " ".join(sys.argv[4:])
            results = local.search(query)
            for r in results:
                print(f"  {r['name']}  |  {r.get('categories', '')}")
        else:
            print(f"Unknown local subcommand: {subcmd}")

    else:
        print(f"Unknown command: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
