import streamlit as st
import requests
import pandas as pd
import re
from bs4 import BeautifulSoup
from urllib.parse import urlparse

try:
    from config import SITES
except ImportError:
    SITES = {}

st.set_page_config(page_title="Recreated Product Manager", page_icon="🔎", layout="wide")

TARGET_PHRASE = "This is a Recreated Product"
PHRASE_RE = re.compile(r"\bthis\s+is\s+a\s+recreated\s+product\b", re.IGNORECASE)

def normalize_url(url):
    url = (url or "").strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url.rstrip("/") + "/"

def api_base(site_url):
    return normalize_url(site_url) + "wp-json/wc/v3"

def wc_request(method, site_url, endpoint, **kwargs):
    ck = st.session_state.consumer_key.strip()
    cs = st.session_state.consumer_secret.strip()
    return requests.request(
        method,
        api_base(site_url) + endpoint,
        auth=(ck, cs),
        timeout=45,
        headers={"User-Agent": "Recreated-Product-Manager/2.0"},
        **kwargs
    )

def product_slug(product_url):
    path = urlparse(product_url).path.rstrip("/")
    return path.split("/")[-1] if path else ""

def find_product(site_url, product_url):
    slug = product_slug(product_url)
    if not slug:
        return None, "Invalid product URL."

    r = wc_request("GET", site_url, f"/products?slug={requests.utils.quote(slug)}&per_page=10")
    if r.ok:
        products = r.json()
        if products:
            return products[0], None

    r = wc_request("GET", site_url, f"/products?search={requests.utils.quote(slug)}&per_page=50")
    if r.ok:
        target = product_url.rstrip("/")
        products = r.json()
        for p in products:
            if (p.get("permalink") or "").rstrip("/") == target:
                return p, None
        if len(products) == 1:
            return products[0], None

    try:
        message = r.json().get("message", r.text[:300])
    except Exception:
        message = r.text[:300]
    return None, str(message)

def clean_visible_text(html):
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())

def has_phrase(html):
    return bool(PHRASE_RE.search(clean_visible_text(html)))

def get_spec_html(product):
    # The user's Product Specifications are in WooCommerce Product short description.
    short = product.get("short_description") or ""
    if re.search(r"product\s+specifications\s*:?", clean_visible_text(short), re.I):
        return short, "short_description"

    # Fallback for stores where the same section is in the normal description.
    desc = product.get("description") or ""
    if re.search(r"product\s+specifications\s*:?", clean_visible_text(desc), re.I):
        return desc, "description"

    return "", None

def add_phrase_after_specs(html):
    soup = BeautifulSoup(html or "", "html.parser")

    # Agar phrase pehle se mojood hai to dobara add na karo
    if has_phrase(html):
        return html, False, "Already present"

    # Product Specifications heading find karo
    heading = None

    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "div"]):
        text = " ".join(tag.get_text(" ", strip=True).split())

        if re.fullmatch(r"product\s+specifications\s*:?", text, re.I):
            heading = tag
            break

    # Strong / bold heading fallback
    if heading is None:
        for tag in soup.find_all(["strong", "b"]):
            text = " ".join(tag.get_text(" ", strip=True).split())

            if re.fullmatch(r"product\s+specifications\s*:?", text, re.I):
                heading = tag.parent if tag.parent else tag
                break

    if heading is None:
        return html, False, "Product Specifications not found"

    bold = soup.new_tag("strong")
    bold.string = TARGET_PHRASE

    br = soup.new_tag("br")

    # Material wala next block find karo
    next_block = heading.find_next_sibling(["p", "div"])

    if next_block:
        # Phrase ko Material ke same paragraph me add karo
        next_block.insert(0, br)
        next_block.insert(0, bold)

        return str(soup), True, "Added"

    # Fallback
    heading.append(soup.new_tag("br"))
    heading.append(bold)

    return str(soup), True, "Added"

def update_product(site_url, product_id, field, html):
    payload = {field: html}
    r = wc_request("PUT", site_url, f"/products/{product_id}", json=payload)
    if r.ok:
        return True, ""
    try:
        message = r.json().get("message", r.text[:500])
    except Exception:
        message = r.text[:500]
    return False, str(message)

def scan_product(site_url, url):
    product, error = find_product(site_url, url)
    if error:
        return {"ID": "", "URL": url, "Recreated Product": "NO", "Details": error}

    content, field = get_spec_html(product)
    found = has_phrase(content)

    return {
        "ID": product.get("id", ""),
        "URL": url,
        "Recreated Product": "YES" if found else "NO"
    }

def process_product(site_url, url):
    product, error = find_product(site_url, url)
    if error:
        return {"ID": "", "URL": url, "Status": "ERROR", "Details": error}

    content, field = get_spec_html(product)
    if not field:
        return {
            "ID": product.get("id", ""),
            "URL": url,
            "Status": "SKIPPED",
            "Details": "Product Specifications not found"
        }

    # Automatic skip: if phrase already exists, DO NOT update the product.
    if has_phrase(content):
        return {
            "ID": product.get("id", ""),
            "URL": url,
            "Status": "SKIPPED",
            "Details": "Already has This is a Recreated Product"
        }

    new_content, added, details = add_phrase_after_specs(content)
    if not added:
        return {
            "ID": product.get("id", ""),
            "URL": url,
            "Status": "SKIPPED",
            "Details": details
        }

    ok, message = update_product(site_url, product["id"], field, new_content)
    return {
        "ID": product.get("id", ""),
        "URL": url,
        "Status": "ADDED" if ok else "ERROR",
        "Details": "This is a Recreated Product added" if ok else message
    }

def parse_urls(pasted, uploaded):
    urls = [x.strip() for x in (pasted or "").splitlines() if x.strip()]

    if uploaded:
        raw = uploaded.getvalue().decode("utf-8", errors="ignore")
        if uploaded.name.lower().endswith(".csv"):
            try:
                df = pd.read_csv(uploaded)
                column = next((c for c in df.columns if "url" in str(c).lower()), df.columns[0])
                urls.extend(str(x).strip() for x in df[column].dropna().tolist())
            except Exception:
                urls.extend(x.strip() for x in raw.splitlines() if x.strip())
        else:
            urls.extend(x.strip() for x in raw.splitlines() if x.strip())

    return list(dict.fromkeys(
        u for u in urls if u.startswith(("http://", "https://"))
    ))

# ---------------- UI ----------------
st.title("🔎 Recreated Product Manager")
st.caption("Simple: Scan = YES/NO. Add = automatically skip existing products and add only where missing.")

with st.sidebar:
    st.header("🔐 WooCommerce Connection")

    options = ["-- Manual Login --"] + list(SITES.keys())
    selected = st.selectbox("Select Store", options)

    if selected != "-- Manual Login --":
        store = SITES[selected]
        st.session_state.selected_site_url = store["url"]
        st.session_state.consumer_key = store["consumer_key"]
        st.session_state.consumer_secret = store["consumer_secret"]
        st.success(selected)
    else:
        st.session_state.selected_site_url = st.text_input(
            "Site URL",
            value=st.session_state.get("selected_site_url", ""),
            placeholder="https://example.com"
        )
        st.session_state.consumer_key = st.text_input("Consumer Key", type="password")
        st.session_state.consumer_secret = st.text_input("Consumer Secret", type="password")

tab_scan, tab_add = st.tabs(["🔍 Scan", "✏️ Add to URLs"])

with tab_scan:
    st.subheader("Check Products")
    st.caption("Using the Site URL, Consumer Key and Consumer Secret from the left sidebar.")
    site = st.session_state.get("selected_site_url", "").strip()
    pasted = st.text_area(
        "Product URLs — one per line",
        height=180,
        key="scan_urls"
    )
    uploaded = st.file_uploader(
        "Upload TXT/CSV (optional)",
        type=["txt", "csv"],
        key="scan_file"
    )

    if st.button("🔍 Scan", type="primary", use_container_width=True):
        urls = parse_urls(pasted, uploaded)

        if not site or not st.session_state.get("consumer_key") or not st.session_state.get("consumer_secret"):
            st.error("Enter the site and WooCommerce API credentials.")
        elif not urls:
            st.error("Enter at least one product URL.")
        else:
            rows = []
            progress = st.progress(0)
            for i, url in enumerate(urls):
                rows.append(scan_product(site, url))
                progress.progress((i + 1) / len(urls))

            df = pd.DataFrame(rows, columns=["ID", "URL", "Recreated Product", "Details"] if any("Details" in x for x in rows) else ["ID", "URL", "Recreated Product"])
            st.dataframe(df, use_container_width=True, hide_index=True)

            st.download_button(
                "⬇️ Download CSV",
                df.to_csv(index=False).encode("utf-8"),
                "recreated_product_scan.csv",
                "text/csv"
            )

with tab_add:
    st.subheader("Add to Products")
    st.caption("Using the Site URL, Consumer Key and Consumer Secret from the left sidebar.")
    st.info("No Dry Run. No Preview switch. The tool automatically skips products that already have the phrase.")

    site2 = st.session_state.get("selected_site_url", "").strip()
    pasted2 = st.text_area(
        "Product URLs — one per line",
        height=180,
        key="add_urls"
    )
    uploaded2 = st.file_uploader(
        "Upload TXT/CSV (optional)",
        type=["txt", "csv"],
        key="add_file"
    )

    if st.button("✏️ Add Missing Phrase", type="primary", use_container_width=True):
        urls = parse_urls(pasted2, uploaded2)

        if not site2 or not st.session_state.get("consumer_key") or not st.session_state.get("consumer_secret"):
            st.error("Enter the site and WooCommerce API credentials.")
        elif not urls:
            st.error("Enter at least one product URL.")
        else:
            rows = []
            progress = st.progress(0)

            for i, url in enumerate(urls):
                rows.append(process_product(site2, url))
                progress.progress((i + 1) / len(urls))

            df2 = pd.DataFrame(rows, columns=["ID", "URL", "Status", "Details"])
            st.dataframe(df2, use_container_width=True, hide_index=True)

            st.download_button(
                "⬇️ Download CSV",
                df2.to_csv(index=False).encode("utf-8"),
                "recreated_product_add_results.csv",
                "text/csv"
            )

st.divider()
st.caption("Connection is taken from the left sidebar. Existing phrase = SKIPPED. Missing phrase = ADDED.")
