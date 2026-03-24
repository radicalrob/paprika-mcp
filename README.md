# paprika-mcp

A Python [MCP](https://modelcontextprotocol.io/) server that gives Claude (or
any MCP-compatible client) full access to the
[Paprika Recipe Manager 3](https://www.paprikaapp.com/) cloud API.

Search your recipe collection, create and update recipes, manage grocery lists,
sync your full library to a local cache for instant offline search, and more —
all from natural language in Claude.

## What Can It Do?

This MCP covers most of Paprika's useful API endpoints:

- **14 tools** covering the full recipe lifecycle (list, search, get, create,
  update, categorize) plus grocery lists, categories, and cache management
- **Local cache with sync** — run `paprika_sync` once to download your entire
  library as JSON. After that, search is instant and offline. The cache auto-
  refreshes when you ask it to.
- **Name Lookup** — find recipes by exact or partial name
- **Grocery list push** — send ingredient lists to any of your Paprika grocery
  lists, with per-store targeting
- **Photo support** — attach photos to recipes from a file path, base64 data,
  or URL
- **Offline fallback** — can also read `.paprikarecipes` export files directly
- **CLI mode** — use it as a command-line tool for quick queries and debugging

## Tools

| Tool | Description |
|------|-------------|
| `paprika_auth` | Test authentication |
| `paprika_list_recipes` | List all recipe UIDs and hashes |
| `paprika_get_recipe` | Get a full recipe by UID |
| `paprika_search_recipes` | Search by keyword across name, ingredients, categories, notes, directions |
| `paprika_get_recipe_by_name` | Find a recipe by name (exact or fuzzy) |
| `paprika_grocery_lists` | List available grocery lists |
| `paprika_list_groceries` | View items on a grocery list |
| `paprika_push_groceries` | Add items to a grocery list |
| `paprika_list_categories` | List recipe categories |
| `paprika_update_recipe` | Update metadata on an existing recipe (safe — won't touch ingredients/directions) |
| `paprika_categorize_recipes` | Batch-assign categories to multiple recipes |
| `paprika_create_recipe` | Create a new recipe with full details and optional photo |
| `paprika_sync` | Sync all recipes to a local JSON cache |
| `paprika_cache_info` | Check cache status (exists, age, recipe count) |

## Setup

### Prerequisites

- Python 3.10+
- A [Paprika Recipe Manager 3](https://www.paprikaapp.com/) account with cloud sync enabled
- [Claude desktop app](https://claude.ai/download) (or any MCP-compatible client)

### Install dependencies

```bash
pip install mcp requests
```

### Configure Claude

Add this to your Claude desktop config at
`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or
`%APPDATA%/Claude/claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "paprika-api": {
      "command": "python3",
      "args": ["/path/to/paprika-mcp/paprika_mcp.py"],
      "env": {
        "PAPRIKA_EMAIL": "your-email@example.com",
        "PAPRIKA_PASSWORD": "your-paprika-password"
      }
    }
  }
}
```

Restart Claude. You should see the paprika-api tools appear in your tool list.

### First use

Ask Claude to sync your recipe library:

> "Sync my Paprika recipes"

This downloads your full collection to a local JSON cache (can take a few
minutes for large libraries). After that, all searches are instant.

## Usage Examples

> "Search my Paprika recipes for chicken tikka"

> "What recipes do I have with chickpeas and tahini?"

> "Create a new recipe for sourdough focaccia with these ingredients and directions..."

> "Add the ingredients from my Dal Makhani recipe to my Trader Joes grocery list"

> "What's on my Paprika grocery list?"

> "Categorize these 5 recipes as 'Weeknight Dinner'"

## Configuration

| Environment Variable | Description | Required |
|---------------------|-------------|----------|
| `PAPRIKA_EMAIL` | Your Paprika account email | Yes |
| `PAPRIKA_PASSWORD` | Your Paprika account password | Yes |
| `PAPRIKA_CACHE_DIR` | Custom cache directory (default: `~/.paprika-mcp`) | No |

## Local Cache

The server maintains a local JSON cache of your recipe library at
`~/.paprika-mcp/paprika_recipe_cache.json` (or wherever `PAPRIKA_CACHE_DIR`
points). This enables instant offline search without hitting the Paprika API
for every query. Run `paprika_sync` to refresh it after adding or changing
recipes in Paprika.

## CLI Mode

The API client also works as a standalone command-line tool:

```bash
export PAPRIKA_EMAIL="your-email@example.com"
export PAPRIKA_PASSWORD="your-password"

python paprika_api.py auth                        # Test authentication
python paprika_api.py recipes                     # List all recipe names
python paprika_api.py search "chickpea tahini"    # Search recipes
python paprika_api.py recipe <uid>                # Get full recipe
python paprika_api.py groceries list              # List grocery items
python paprika_api.py groceries push items.json   # Push grocery items
python paprika_api.py local export.paprikarecipes search "keyword"  # Search offline export
```

## Files

| File | Description |
|------|-------------|
| `paprika_mcp.py` | MCP server (stdio) — exposes all 14 tools |
| `paprika_api.py` | Paprika API client — handles auth, caching, gzip, REST calls. Usable as a library or CLI. |
| `api-notes.md` | Community reverse-engineered Paprika API reference |

## API Notes

The Paprika API is not officially documented. This server is built on
community reverse-engineering efforts. See `api-notes.md` for endpoint
details. The API could change without notice, though it has been stable for
years.

## License

MIT — see [LICENSE](LICENSE).

## Credits

API knowledge built on prior work by the Paprika community:
[mattdsteele](https://gist.github.com/mattdsteele/7386ec363badfdeaad05a418b9a1f30a),
[joshstrange/paprika-api](https://github.com/joshstrange/paprika-api),
[johnwbyrd/kappari](https://github.com/johnwbyrd/kappari).
