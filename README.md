# Confluence Page Exporter

Export Confluence pages using REST API as `.doc` or `.md` for given ID while preserving the hierarchy.

## Features
- Export pages as Word (`.doc`) or Markdown (`.md`)
- Preserve page hierarchy as nested directories
- Export all historical versions of pages
- Support multiple root page IDs in a single run

## Requirements
- Python 3.10+
- `requests` package
- `html2text` package (for Markdown export)

## Installation

```bash
pip install -r requirement.txt
```

## Configuration

Create a `config.json` file (see `config_example.json`):

```json
{
    "url": "https://your-domain.atlassian.net",
    "email": "useremail",
    "token": "api_token",
    "pageId": "000000"
}
```

### Config options

| Key | Required | Description |
|-----|----------|-------------|
| `url` | Yes | Confluence base URL |
| `email` | Yes | Username or email |
| `token` | Yes | API token or password |
| `pageId` | Yes | Default root page ID |
| `pageIds` | No | List of root page IDs to export (overrides `pageId`) |
| `format` | No | Export format: `"doc"` (default) or `"markdown"` |
| `export_versions` | No | Export all historical versions (`true`/`false`, default `false`). Only applies to Markdown format. |

## Usage

```bash
python main.py
```

### Output structure

**Word export (default):**
```
output/
  PageTitle_ID.doc
  PageTitle_ID/
    ChildPage_ID.doc
    ChildPage_ID/
      ...
```

**Markdown export with version history:**
```
output/
  PageTitle_ID.md
  versions/
    v1_2024-01-15/
      PageTitle_v1_2024-01-15.md
    v2_2024-02-20/
      PageTitle_v2_2024-02-20.md
  ChildPageTitle_ID/
    ChildPageTitle_ID.md
    versions/
      ...
```

## Reference
- https://developer.atlassian.com/cloud/confluence/rest/v2/intro/#about
- https://developer.atlassian.com/server/confluence/confluence-server-rest-api/
- https://developer.atlassian.com/server/confluence/expansions-in-the-rest-api/
