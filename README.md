# Recreated Product Manager

A Streamlit tool for WooCommerce that:

1. Scans product URLs for the phrase "This is a Recreated Product", ignoring capitalization and normal whitespace differences.
2. Adds the exact phrase "This is a Recreated Product" to the Product Specifications area.
3. Does not add a duplicate if the phrase already exists.
4. Supports pasted URLs and TXT/CSV files.
5. Has Preview/Dry Run mode before live updates.
6. Exports CSV reports.

## Requirements

- Python 3.10+
- WooCommerce REST API credentials with Read/Write permission
- WordPress/WooCommerce site URL

## Install

```bash
pip install -r requirements.txt
```

## Run

```bash
streamlit run app.py
```

Then open the local Streamlit URL shown in the terminal.

## WooCommerce API key

In WordPress:

WooCommerce -> Settings -> Advanced -> REST API -> Add key

Use:
- Description: Recreated Product Manager
- User: your admin/shop manager user
- Permissions: Read/Write

Copy the Consumer Key and Consumer Secret into the tool.

## Workflow

### Scan
1. Enter site URL.
2. Enter API key/secret.
3. Paste product URLs.
4. Click Scan Products.
5. Download the CSV report if needed.

### Add phrase
1. Keep Preview / Dry Run ON.
2. Paste URLs.
3. Click Process URLs.
4. Review the results.
5. Turn Dry Run OFF.
6. Run Process URLs again to make live updates.

## Important

This version assumes "Product Specifications" is part of the WooCommerce product description HTML. If your website builds Product Specifications using a custom Elementor/ACF/shortcode field or a theme-specific custom field, the updater needs to target that field instead of `description`.

Always test on 1-2 products before bulk updating.
