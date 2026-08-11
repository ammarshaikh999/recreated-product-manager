import streamlit as st
import requests
import pandas as pd
import re
from bs4 import BeautifulSoup, NavigableString
from urllib.parse import urlparse

try:
    from config import SITES
except ImportError:
    SITES = {}

st.set_page_config(page_title="Recreated Product Manager", page_icon="🔎", layout="wide")

TARGET_PHRASE = "This is a Recreated Product"

# Matches the requested phrase regardless of capitalization and allows flexible whitespace.
PHRASE_RE = re.compile(
    r"\bthis\s+is\s+a\s+recreated\s+product\b",
    re.IGNORECASE
)

def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url.rstrip("/") + "/"

def api_base(site_url: str) -> str:
    site_url = normalize_url(site_url)
    return site_url + "wp-json/wc/v3"

def get_auth():
    return st.session_state.consumer_key.strip(), st.session_state.consumer_secret.strip()

def wc_request(method, site_url, endpoint, **kwargs):
    ck, cs = get_auth()
    url = api_base(site_url) + endpoint
    return requests.request(
        method, url, auth=(ck, cs),
        timeout=45,
        headers={"User-Agent": "Recreated-Product-Manager/1.0"},
        **kwargs
    )

def slug_from_product_url(product_url):
    p = urlparse(product_url)
    path = p.path.rstrip("/")
    if not path:
        return ""
    return path.split("/")[-1]

def find_product(site_url, product_url):
    slug = slug_from_product_url(product_url)
    if not slug:
        return None, "Could not determine product slug."

    # Exact slug lookup.
    r = wc_request("GET", site_url, f"/products?slug={requests.utils.quote(slug)}&per_page=10")
    if r.ok:
        items = r.json()
        if items:
            return items[0], None

    # Search fallback.
    r = wc_request("GET", site_url, f"/products?search={requests.utils.quote(slug)}&per_page=50")
    if r.ok:
        items = r.json()
        target = product_url.rstrip("/")
        for item in items:
            permalink = (item.get("permalink") or "").rstrip("/")
            if permalink == target:
                return item, None
        if len(items) == 1:
            return items[0], None

    try:
        msg = r.json().get("message", r.text[:300])
    except Exception:
        msg = r.text[:300]
    return None, f"Product not found / API error: {msg}"

def fetch_live_product_html(product_url):
    """Fetch the public product page so scanner can see text rendered by theme/page builder."""
    try:
        r = requests.get(
            product_url,
            timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/151.0 Safari/537.36"
            }
        )
        if r.ok:
            return r.text
    except Exception:
        pass
    return ""

def phrase_matches_in_html(html):
    soup = BeautifulSoup(html or "", "html.parser")
    # Remove non-visible/script content.
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    text = " ".join(soup.get_text(" ", strip=True).split())
    return PHRASE_RE.findall(text)

def html_contains_phrase(html):
    soup = BeautifulSoup(html or "", "html.parser")
    text = soup.get_text(" ", strip=True)
    return bool(PHRASE_RE.search(text))

def find_spec_heading(soup):
    # Prefer headings and block elements whose visible text is exactly/mostly "Product Specifications".
    candidates = []
    for tag in soup.find_all(["h1","h2","h3","h4","h5","h6","strong","b","div","p","span"]):
        txt = " ".join(tag.get_text(" ", strip=True).split())
        if re.fullmatch(r"product\s+specifications\s*:?", txt, re.IGNORECASE):
            candidates.append(tag)

    if candidates:
        # Prefer semantic headings first, then strong/b, then generic blocks.
        priority = {"h1":0,"h2":0,"h3":0,"h4":0,"h5":0,"h6":0,"strong":1,"b":1,"p":2,"div":3,"span":4}
        return sorted(candidates, key=lambda x: priority.get(x.name, 5))[0]

    # Fallback: locate a text node containing the heading phrase.
    for node in soup.find_all(string=re.compile(r"product\s+specifications", re.I)):
        if PHRASE_RE.search(node):
            continue
        return node
    return None

def add_phrase_to_specifications(html):
    soup = BeautifulSoup(html or "", "html.parser")

    if html_contains_phrase(html):
        return html, "already_present"

    heading = find_spec_heading(soup)
    if heading is None:
        return html, "specifications_not_found"

    # Case A: the heading is a normal tag.
    if getattr(heading, "name", None):
        new_tag = soup.new_tag("p")
        strong = soup.new_tag("strong")
        strong.string = TARGET_PHRASE
        new_tag.append(strong)

        # If the heading itself contains the entire specifications section,
        # insert immediately after the heading inside that container.
        heading.insert_after(new_tag)
        return str(soup), "added"

    # Case B: heading is a NavigableString inside a parent.
    if isinstance(heading, NavigableString):
        parent = heading.parent
        if parent is None:
            return html, "specifications_not_found"

        # Split the text node at the heading, preserving everything after it.
        raw = str(heading)
        m = re.search(r"product\s+specifications\s*:?", raw, re.I)
        if not m:
            return html, "specifications_not_found"

        before = raw[:m.end()]
        after = raw[m.end():]
        before_node = NavigableString(before)
        after_node = NavigableString(after) if after else None

        # Keep the exact position: immediately after "Product Specifications:".
        heading.replace_with(before_node)

        new_tag = soup.new_tag("p")
        strong = soup.new_tag("strong")
        strong.string = TARGET_PHRASE
        new_tag.append(strong)

        before_node.insert_after(new_tag)
        if after_node:
            new_tag.insert_after(after_node)

        return str(soup), "added"

    return html, "specifications_not_found"

def update_product(site_url, product_id, field_name, new_html):
    """Update only the WooCommerce field containing Product Specifications."""
    r = wc_request(
        "PUT", site_url, f"/products/{product_id}",
        json={field_name: new_html}
    )
    if r.ok:
        return True, None
    try:
        msg = r.json().get("message", r.text[:500])
    except Exception:
        msg = r.text[:500]
    return False, msg

def get_specifications_source(product):
    """
    The screenshot shows Product Specifications inside WooCommerce's
    Product short description field, so prefer short_description.
    Fall back to description only if the heading is actually there.
    """
    short_html = product.get("short_description", "") or ""
    desc_html = product.get("description", "") or ""

    short_soup = BeautifulSoup(short_html, "html.parser")
    desc_soup = BeautifulSoup(desc_html, "html.parser")

    short_text = " ".join(short_soup.get_text(" ", strip=True).split())
    desc_text = " ".join(desc_soup.get_text(" ", strip=True).split())

    heading_re = re.compile(r"\bproduct\s+specifications\s*:?", re.IGNORECASE)

    if heading_re.search(short_text):
        return "short_description", short_html

    if heading_re.search(desc_text):
        return "description", desc_html

    # If no heading is detectable, don't guess where to write.
    return None, None

def scan_one(site_url, product_url):
    product, err = find_product(site_url, product_url)
    if err:
        # Even if the REST API cannot find it, try the public URL.
        live = fetch_live_product_html(product_url)
        matches = phrase_matches_in_html(live)
        if matches:
            return {
                "URL": product_url, "Product": "",
                "ID": "", "Status": "✅ Found",
                "Detected Text": matches[0],
                "Details": "Found on the live product page."
            }
        return {
            "URL": product_url, "Product": "", "ID": "",
            "Status": "❌ Error", "Detected Text": "",
            "Details": err
        }

    # IMPORTANT: WooCommerce stores the Product Specifications shown in
    # the user's screenshot in Product Short Description.
    short_html = product.get("short_description", "") or ""
    desc_html = product.get("description", "") or ""

    short_matches = phrase_matches_in_html(short_html)
    if short_matches:
        return {
            "URL": product_url,
            "Product": product.get("name", ""),
            "ID": product.get("id", ""),
            "Status": "✅ Found",
            "Detected Text": short_matches[0],
            "Details": "Found in WooCommerce Product short description."
        }

    desc_matches = phrase_matches_in_html(desc_html)
    if desc_matches:
        return {
            "URL": product_url,
            "Product": product.get("name", ""),
            "ID": product.get("id", ""),
            "Status": "✅ Found",
            "Detected Text": desc_matches[0],
            "Details": "Found in WooCommerce Product description."
        }

    # Frontend fallback for theme/Elementor/custom rendering.
    live = fetch_live_product_html(product_url)
    live_matches = phrase_matches_in_html(live)

    if live_matches:
        return {
            "URL": product_url,
            "Product": product.get("name", ""),
            "ID": product.get("id", ""),
            "Status": "✅ Found",
            "Detected Text": live_matches[0],
            "Details": "Found on the live product page."
        }

    return {
        "URL": product_url,
        "Product": product.get("name", ""),
        "ID": product.get("id", ""),
        "Status": "❌ Not Found",
        "Detected Text": "",
        "Details": "Phrase not found in short description, description, or live product page."
    }

def process_one(site_url, product_url, dry_run=True):
    product, err = find_product(site_url, product_url)
    if err:
        return {"URL": product_url, "Product": "", "ID": "", "Status": "❌ Error", "Details": err}

    # First check both editable WooCommerce fields for an existing phrase.
    short_html = product.get("short_description", "") or ""
    desc_html = product.get("description", "") or ""

    if html_contains_phrase(short_html):
        return {
            "URL": product_url, "Product": product.get("name",""),
            "ID": product.get("id",""), "Status": "⏭️ Already Present",
            "Details": "Phrase already exists in Product short description; no duplicate added."
        }

    if html_contains_phrase(desc_html):
        return {
            "URL": product_url, "Product": product.get("name",""),
            "ID": product.get("id",""), "Status": "⏭️ Already Present",
            "Details": "Phrase already exists in Product description; no duplicate added."
        }

    # The screenshot confirms the specifications live in Product short description.
    field_name, source_html = get_specifications_source(product)

    if field_name is None:
        return {
            "URL": product_url, "Product": product.get("name",""),
            "ID": product.get("id",""), "Status": "⚠️ Specifications Not Found",
            "Details": "Could not locate 'Product Specifications:' in Product short description or Product description."
        }

    new_html, state = add_phrase_to_specifications(source_html)

    if state != "added":
        return {
            "URL": product_url, "Product": product.get("name",""),
            "ID": product.get("id",""), "Status": "⚠️ Specifications Not Found",
            "Details": "Found the field, but could not safely insert the phrase immediately after 'Product Specifications:'."
        }

    if dry_run:
        return {
            "URL": product_url, "Product": product.get("name",""),
            "ID": product.get("id",""), "Status": "👀 Preview Only",
            "Details": f"Would add the phrase to WooCommerce {field_name.replace('_', ' ')}. No changes were sent."
        }

    ok, msg = update_product(site_url, product["id"], field_name, new_html)
    return {
        "URL": product_url, "Product": product.get("name",""),
        "ID": product.get("id",""),
        "Status": "✅ Updated" if ok else "❌ Update Failed",
        "Details": (
            f"Phrase added to {field_name.replace('_', ' ')} immediately after Product Specifications."
            if ok else str(msg)
        )
    }

# ---------- UI ----------
st.title("🔎 Recreated Product Manager")
st.caption("Scan WooCommerce products and add “This is a Recreated Product” immediately after Product Specifications. The tool targets Product short description first, then Product description.")

with st.sidebar:
    st.header("🔐 WooCommerce Connection")

    site_options = ["-- Manual Login --"] + list(SITES.keys())
    selected_site = st.selectbox("Select Store", site_options)

    if selected_site != "-- Manual Login --":
        selected = SITES[selected_site]
        st.session_state.selected_site_url = selected["url"]
        st.session_state.consumer_key = selected["consumer_key"]
        st.session_state.consumer_secret = selected["consumer_secret"]
        st.success(f"Loaded: {selected_site}")
    else:
        st.session_state.selected_site_url = st.text_input(
            "Site URL", placeholder="https://example.com"
        )
        st.session_state.consumer_key = st.text_input(
            "Consumer Key", type="password",
            help="WooCommerce REST API key with Read/Write permission."
        )
        st.session_state.consumer_secret = st.text_input(
            "Consumer Secret", type="password"
        )

    st.info("Add your stores in config.py. Each store needs URL, Consumer Key and Consumer Secret.")

    st.header("⚙️ Options")
    dry_run = st.checkbox("Preview / Dry Run", value=True)
    st.caption("Keep this ON first. Turn it OFF only when you are ready to update products.")

tab1, tab2 = st.tabs(["🔍 Scan Products", "✏️ Add Phrase to URLs"])

def url_input_box(widget_prefix):
    pasted = st.text_area(
        "Paste product URLs (one URL per line)",
        height=220,
        placeholder="https://example.com/product/product-1/\nhttps://example.com/product/product-2/",
        key=f"{widget_prefix}_urls"
    )
    uploaded = st.file_uploader(
        "Or upload a TXT/CSV file",
        type=["txt", "csv"],
        key=f"{widget_prefix}_file"
    )
    urls = [x.strip() for x in pasted.splitlines() if x.strip()]
    if uploaded:
        data = uploaded.getvalue().decode("utf-8", errors="ignore")
        if uploaded.name.lower().endswith(".csv"):
            try:
                df = pd.read_csv(uploaded)
                # Use a URL column if available, otherwise take the first column.
                col = next((c for c in df.columns if "url" in str(c).lower()), df.columns[0])
                urls += [str(x).strip() for x in df[col].dropna().tolist() if str(x).strip()]
            except Exception:
                urls += [x.strip() for x in data.splitlines() if x.strip()]
        else:
            urls += [x.strip() for x in data.splitlines() if x.strip()]
    return list(dict.fromkeys([u for u in urls if u.startswith(("http://","https://"))]))

with tab1:
    st.subheader("Scan for the phrase")
    site = st.text_input("WordPress/WooCommerce site URL", value=st.session_state.get("selected_site_url", ""), placeholder="https://example.com")
    urls = url_input_box('scan')

    if st.button("🔍 Scan Products", type="primary", use_container_width=True):
        if not site or not st.session_state.consumer_key or not st.session_state.consumer_secret:
            st.error("Enter site URL, Consumer Key and Consumer Secret first.")
        elif not urls:
            st.error("Add at least one product URL.")
        else:
            rows = []
            st.session_state.pop("scan_df", None)
            progress = st.progress(0)
            for i, u in enumerate(urls):
                rows.append(scan_one(site, u))
                progress.progress((i+1)/len(urls))
            df = pd.DataFrame(rows)
            st.session_state["scan_df"] = df

    if "scan_df" in st.session_state:
        df = st.session_state["scan_df"]
        c1,c2,c3 = st.columns(3)
        c1.metric("Total", len(df))
        c2.metric("Found", int((df["Status"]=="✅ Found").sum()))
        c3.metric("Not Found", int((df["Status"]=="❌ Not Found").sum()))
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.download_button(
            "⬇️ Download Scan Report CSV",
            df.to_csv(index=False).encode("utf-8"),
            "recreated_product_scan_report.csv",
            "text/csv"
        )

with tab2:
    st.subheader("Add “This is a Recreated Product”")
    site2 = st.text_input("WordPress/WooCommerce site URL", value=st.session_state.get("selected_site_url", ""), key="site2", placeholder="https://example.com")
    urls2 = url_input_box('update')

    st.warning("Recommended: run Preview / Dry Run first. The tool will never add the phrase twice.")

    if st.button("✏️ Process URLs", type="primary", use_container_width=True):
        if not site2 or not st.session_state.consumer_key or not st.session_state.consumer_secret:
            st.error("Enter site URL, Consumer Key and Consumer Secret first.")
        elif not urls2:
            st.error("Add at least one product URL.")
        else:
            rows = []
            st.session_state.pop("update_df", None)
            progress = st.progress(0)
            for i, u in enumerate(urls2):
                rows.append(process_one(site2, u, dry_run=dry_run))
                progress.progress((i+1)/len(urls2))
            df2 = pd.DataFrame(rows)
            st.session_state["update_df"] = df2

    if "update_df" in st.session_state:
        df2 = st.session_state["update_df"]
        st.dataframe(df2, use_container_width=True, hide_index=True)
        st.download_button(
            "⬇️ Download Update Report CSV",
            df2.to_csv(index=False).encode("utf-8"),
            "recreated_product_update_report.csv",
            "text/csv"
        )

st.divider()
st.caption("Important: this version targets WooCommerce Product short description first (the field shown in your screenshot), then Product description. Test with Preview / Dry Run before live updates.")
