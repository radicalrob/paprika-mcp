# Paprika Recipe Manager 3 — API Reference

Community-reverse-engineered. Not officially supported by the Paprika developer.

## Authentication

**Endpoint:** `POST https://www.paprikaapp.com/api/v1/account/login`
**Content-Type:** `multipart/form-data`
**Fields:** `email`, `password`
**Response:**
```json
{"result": {"token": "<jwt_token>"}}
```

Use the token as `Authorization: Bearer <token>` on all subsequent requests.
Tokens are long-lived (weeks).

## Recipes

### List all (lightweight)
`GET /api/v2/sync/recipes/`
Returns UIDs and hashes only — use this to know what exists.
```json
{"result": [{"uid": "ABC-123", "hash": "def456"}, ...]}
```

### Get full recipe
`GET /api/v2/sync/recipe/<uid>/`
Returns the complete recipe object.

### Recipe fields
uid, name, ingredients, directions, description, notes, nutritional_info,
servings, difficulty, prep_time, cook_time, total_time, source, source_url,
image_url, photo, photo_hash, photo_large, categories (list of strings),
rating (int 0-5), created, hash

## Grocery List

### List items
`GET /api/v2/sync/groceries/`

### Push items
`POST /api/v2/sync/groceries/`
**Format:** Gzip-compressed JSON array sent as multipart form data (field name: "data")

### Grocery item fields
- `uid` — UUID string (generate fresh for new items)
- `name` — display name
- `ingredient` — ingredient text (often same as name)
- `aisle` — category/aisle string for grouping
- `aisle_uid` — UID for the aisle (can be empty)
- `quantity` — amount string (e.g., "2 cans")
- `purchased` — boolean (false for new items)
- `order_flag` — integer for ordering (0 is fine)
- `recipe` — recipe name (optional, for attribution)
- `recipe_uid` — recipe UID (optional)
- `separate` — boolean (false)
- `list_uid` — grocery list UID (can be empty)
- `instruction` — special instructions (optional)

## Local file format

A `.paprikarecipes` file is a ZIP archive. Each entry is a `.paprikarecipe`
file, which is gzip-compressed JSON with the same recipe fields as the API.

## Known issues

- Grocery POST may return "Invalid Data" for unknown reasons. The community
  hasn't fully mapped all required fields. If this happens, try including
  more fields with empty-string defaults.
- Large collections (1000+) are slow to fetch via API since each recipe
  requires a separate GET call.
- The API may change without notice since it's not officially documented.

## Sources

- https://github.com/johnwbyrd/kappari
- https://github.com/soggycactus/paprika-3-mcp
- https://github.com/joshstrange/paprika-api
- https://gist.github.com/mattdsteele/7386ec363badfdeaad05a418b9a1f30a
