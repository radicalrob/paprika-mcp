#!/usr/bin/env python3
"""
MCP server that wraps paprika_api.py, exposing Paprika Recipe Manager
operations as tools Claude can call.

Runs as a stdio MCP server — launched locally on the user's machine,
so it has full network access to the Paprika cloud API.

Required env vars:
  PAPRIKA_EMAIL    — Paprika account email
  PAPRIKA_PASSWORD — Paprika account password
"""

import json
import os
import sys

from mcp.server.fastmcp import FastMCP

# Ensure paprika_api.py can be imported from the same directory
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# Optional: point to paprika_api.py in a different location
_api_path = os.environ.get("PAPRIKA_API_PATH", "")
if _api_path:
    sys.path.insert(0, os.path.dirname(_api_path))

from paprika_api import PaprikaAPI, PaprikaLocalFile, PaprikaAPIError

mcp = FastMCP(
    "paprika-api",
    instructions="Paprika Recipe Manager 3 — recipes, search, and grocery list",
)

# Lazy-initialized client (authenticates on first use)
_client: PaprikaAPI | None = None


def _get_client() -> PaprikaAPI:
    global _client
    if _client is None:
        _client = PaprikaAPI()
    return _client


# ── Tools ────────────────────────────────────────────────────


@mcp.tool()
def paprika_auth() -> str:
    """Test authentication against the Paprika API. Returns a success message with a token preview."""
    client = _get_client()
    token = client.login()
    return f"Authenticated successfully. Token: {token[:20]}..."


@mcp.tool()
def paprika_list_recipes() -> str:
    """List all recipes in the Paprika account. Returns UIDs and hashes (lightweight — no full details)."""
    client = _get_client()
    listing = client.list_recipes()
    return json.dumps({"count": len(listing), "recipes": listing}, indent=2)


@mcp.tool()
def paprika_get_recipe(uid: str) -> str:
    """Fetch full recipe details by UID. Returns complete recipe data including ingredients, directions, notes, etc."""
    client = _get_client()
    recipe = client.get_recipe(uid)
    return json.dumps(recipe, indent=2)


@mcp.tool()
def paprika_search_recipes(query: str, limit: int = 20) -> str:
    """Search recipes by keyword across name, ingredients, categories, notes, and directions.

    Args:
        query: Search terms (space-separated, all must match). Example: "chickpea tahini"
        limit: Maximum number of results to return (default 20)
    """
    client = _get_client()
    results = client.search_recipes(query, limit=limit)
    summaries = [client.recipe_summary(r) for r in results]
    return json.dumps({"count": len(summaries), "results": summaries}, indent=2)


@mcp.tool()
def paprika_grocery_lists() -> str:
    """List all grocery lists (names and UIDs). Use this to see available lists
    like 'Trader Joes', 'Wegmans', 'Whole Foods', etc."""
    client = _get_client()
    lists = client.get_all_grocery_lists()
    summaries = [{"uid": gl.get("uid"), "name": gl.get("name"), "order_flag": gl.get("order_flag")} for gl in lists]
    return json.dumps({"count": len(summaries), "lists": summaries}, indent=2)


@mcp.tool()
def paprika_list_groceries(list_name: str = "") -> str:
    """List grocery items, optionally filtered to a specific list and/or only unpurchased items.

    Args:
        list_name: Optional list name to filter by (e.g. "Trader Joes"). If empty, returns all items.
    """
    client = _get_client()
    items = client.list_grocery_items()

    # Filter to specific list if requested
    if list_name:
        list_uid = client.resolve_list_uid(list_name)
        if list_uid:
            items = [i for i in items if i.get("list_uid") == list_uid]

    # Only show unpurchased (To Buy) items
    to_buy = [i for i in items if not i.get("purchased", False)]
    return json.dumps({"count": len(to_buy), "items": to_buy}, indent=2)


@mcp.tool()
def paprika_push_groceries(items: list[dict], list_name: str = "") -> str:
    """Push items to a Paprika grocery list.

    Args:
        items: List of grocery items. Each item should have:
            - name (str): Display name, e.g. "Chickpeas"
            - quantity (str, optional): e.g. "2 cans"
            - aisle (str, optional): Category for grouping, e.g. "Canned Goods"
            - recipe (str, optional): Recipe name this came from
        list_name: Target grocery list name (e.g. "Trader Joes", "Wegmans", "Whole Foods").
                   If empty, items go to the default grocery list.
    """
    client = _get_client()
    result = client.push_grocery_items(items, list_name=list_name or None)
    return json.dumps({"pushed": len(items), "list": list_name or "(default)", "response": result}, indent=2)


@mcp.tool()
def paprika_get_recipe_by_name(name: str) -> str:
    """Search for a recipe by name and return its full details.
    Uses the local cache if available (fast), otherwise falls back to API.

    Args:
        name: Recipe name (or partial name) to search for
    """
    client = _get_client()
    results = client.search_recipes(name, limit=5)
    if not results:
        return json.dumps({"error": f"No recipe found matching '{name}'"})

    # Return the top match (highest relevance score)
    best = results[0]
    return json.dumps(best, indent=2)


@mcp.tool()
def paprika_list_categories() -> str:
    """List all recipe categories (names and UIDs)."""
    client = _get_client()
    categories = client.get_all_categories()
    summaries = [{"uid": c.get("uid"), "name": c.get("name")} for c in categories]
    return json.dumps({"count": len(summaries), "categories": summaries}, indent=2)


@mcp.tool()
def paprika_update_recipe(uid: str, categories: list[str] = None, rating: int = None,
                          notes: str = None, photo_path: str = "",
                          photo_base64: str = "", image_url: str = "") -> str:
    """Update metadata and/or photo on an existing recipe. CANNOT modify ingredients or directions.

    Args:
        uid: Recipe UID to update
        categories: List of category NAMES (e.g. ["Italian", "Weeknight"]). These will be
                    resolved to UIDs automatically. Replaces all existing categories.
        rating: Rating from 0-5
        notes: Recipe notes text
        photo_path: Absolute path to an image file to set as the recipe photo.
                    Supports JPEG, PNG, etc.
        photo_base64: Base64-encoded image data to set as the recipe photo.
                      Can include or omit the data:image/jpeg;base64, prefix.
        image_url: URL of an image to download and set as the recipe photo.
    """
    client = _get_client()
    updates = {}

    if categories is not None:
        # Resolve category names to UIDs
        cat_uids = client.resolve_category_uids(categories)
        if len(cat_uids) != len(categories):
            all_cats = client.get_all_categories()
            available = [c.get("name") for c in all_cats]
            return json.dumps({"error": f"Could not resolve all category names. Available: {available}"})
        updates["categories"] = cat_uids

    if rating is not None:
        updates["rating"] = rating

    if notes is not None:
        updates["notes"] = notes

    # Handle metadata updates first (if any)
    if updates:
        result = client.update_recipe(uid, updates)
    else:
        result = None

    # Handle photo update
    has_photo_update = bool(photo_path or photo_base64 or image_url)
    photo_result = None
    if has_photo_update:
        photo_result = client.update_recipe_photo(
            uid,
            photo_path=photo_path or None,
            photo_base64=photo_base64 or None,
            image_url=image_url or None,
        )

    if not updates and not has_photo_update:
        return json.dumps({"error": "No updates provided"})

    recipe = client.get_recipe(uid)
    changes = list(updates.keys())
    if has_photo_update:
        changes.append("photo")

    return json.dumps({
        "updated": True,
        "recipe": recipe.get("name"),
        "uid": uid,
        "changes": changes,
        "response": result,
        "photo_response": photo_result,
    }, indent=2)


@mcp.tool()
def paprika_categorize_recipes(recipes: list[dict]) -> str:
    """Batch-update categories on multiple recipes at once.

    Args:
        recipes: List of objects, each with:
            - uid (str): Recipe UID
            - categories (list[str]): Category NAMES to assign
    """
    client = _get_client()
    all_cats = client.get_all_categories()
    cat_name_to_uid = {}
    for c in all_cats:
        cat_name_to_uid[c.get("name", "").lower()] = c["uid"]

    results = []
    for item in recipes:
        uid = item["uid"]
        cat_names = item.get("categories", [])
        cat_uids = [cat_name_to_uid[n.lower()] for n in cat_names if n.lower() in cat_name_to_uid]

        if len(cat_uids) != len(cat_names):
            missing = [n for n in cat_names if n.lower() not in cat_name_to_uid]
            results.append({"uid": uid, "error": f"Unknown categories: {missing}"})
            continue

        try:
            client.update_recipe(uid, {"categories": cat_uids})
            recipe = client.get_recipe(uid)
            results.append({"uid": uid, "name": recipe.get("name"), "updated": True})
        except Exception as e:
            results.append({"uid": uid, "error": str(e)})

    return json.dumps({"processed": len(results), "results": results}, indent=2)


@mcp.tool()
def paprika_create_recipe(name: str, ingredients: str, directions: str,
                          source: str = "", source_url: str = "",
                          servings: str = "", prep_time: str = "",
                          cook_time: str = "", total_time: str = "",
                          categories: list[str] = None,
                          rating: int = 0, difficulty: str = "",
                          notes: str = "", nutritional_info: str = "",
                          description: str = "", photo_path: str = "",
                          photo_base64: str = "",
                          image_url: str = "") -> str:
    """Create a new recipe in Paprika with full details and optional photo.

    Args:
        name: Recipe title
        ingredients: Newline-separated ingredient list
        directions: Directions text (paragraphs separated by blank lines)
        source: Attribution (e.g., cookbook name, "Claude", URL)
        source_url: URL if applicable
        servings: e.g., "4"
        prep_time: e.g., "15 min"
        cook_time: e.g., "30 min"
        total_time: e.g., "45 min"
        categories: List of category NAMES (resolved to UIDs automatically)
        rating: 0-5
        difficulty: Optional difficulty string
        notes: Tips, variations, make-ahead advice
        nutritional_info: Optional
        description: Short description
        photo_path: Absolute path to an image file to embed as the recipe photo.
                    Supports JPEG, PNG, etc. If empty, recipe is created without a photo.
        photo_base64: Base64-encoded image data to embed as the recipe photo.
                      Use this when you have the image data in memory rather than on disk.
                      Can include or omit the data:image/jpeg;base64, prefix.
        image_url: URL of an image to download and use as the recipe photo.
                   The MCP server downloads it directly. This URL also becomes
                   the thumbnail in Paprika's recipe list. The downloaded image
                   bytes are uploaded as both the recipe thumbnail and a separate
                   zoomable photo entity.
    """
    client = _get_client()
    recipe_data = {
        "name": name,
        "ingredients": ingredients,
        "directions": directions,
        "source": source,
        "source_url": source_url,
        "servings": servings,
        "prep_time": prep_time,
        "cook_time": cook_time,
        "total_time": total_time,
        "categories": categories or [],
        "rating": rating,
        "difficulty": difficulty,
        "notes": notes,
        "nutritional_info": nutritional_info,
        "description": description,
    }
    result = client.create_recipe(recipe_data, photo_path=photo_path or None,
                                   photo_base64=photo_base64 or None,
                                   image_url=image_url or None)
    # Check if photo actually made it into the recipe
    has_photo = result.get("has_photo", False)
    return json.dumps({
        "created": True,
        "uid": result["uid"],
        "name": result["name"],
        "has_photo": has_photo,
        "response": result.get("result"),
    }, indent=2)


@mcp.tool()
def paprika_sync() -> str:
    """Sync all recipes from the Paprika cloud API to a local JSON cache.
    This downloads every recipe (can take a few minutes for large collections)
    but only needs to be done once — after that, search is instant.
    Call this to refresh the cache when you've added or changed recipes in Paprika."""
    client = _get_client()
    result = client.sync_recipes(
        progress_callback=lambda i, total, name: None
    )
    return json.dumps(result, indent=2)


@mcp.tool()
def paprika_cache_info() -> str:
    """Check the status of the local recipe cache — whether it exists, how old it is, and how many recipes it contains."""
    client = _get_client()
    info = client.cache_info()
    return json.dumps(info, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")
