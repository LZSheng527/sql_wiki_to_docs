import requests
import re
import urllib.parse
import time
import os
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build

# --- CONFIGURATION ---
DOCUMENT_ID = "1p_eW5DW3mTNbQAuF92vwhmVLh8rdz87m8wzAaLE5lXM"
WIKI_LINK_BASE = "https://wiki.sql.com.my/wiki/"
SERVICE_ACCOUNT_FILE = 'credentials.json'

def fetch_wiki_data():
    all_pages = []
    gap_continue = ""
    ant_key = os.environ.get("SCRAPERANT_API_KEY")
    
    if not ant_key:
        print("ScraperAnt API Key missing! Using direct connection (might 403 on GitHub).")

    print("Fetching data from Wiki...")
    while True:
        target_url = "https://wiki.sql.com.my/api.php?action=query&generator=allpages&gaplimit=50&prop=revisions&rvprop=content&format=json&origin=*"
        if gap_continue:
            target_url += f"&gapcontinue={urllib.parse.quote(gap_continue)}"
        
        # Use ScraperAnt if Key is available, otherwise try direct
        if ant_key:
            proxy_url = "https://api.scraperant.com/v2/general"
            params = {"url": target_url, "x-api-key": ant_key, "browser": "false"}
            response = requests.get(proxy_url, params=params, timeout=60)
        else:
            headers = {"User-Agent": "Mozilla/5.0"}
            response = requests.get(target_url, headers=headers, timeout=30)

        if response.status_code != 200:
            print(f"[!] Fetch Error: {response.status_code}")
            break

        data = response.json()
        if "query" in data:
            pages = data["query"]["pages"]
            for pid in pages: all_pages.append(pages[pid])
            print(f"  > Collected {len(pages)} pages.")

        if "continue" in data and "gapcontinue" in data["continue"]:
            gap_continue = data["continue"]["gapcontinue"]
            time.sleep(1)
        else:
            break
            
    return all_pages

def sanitize_content(title, raw_content):
    s2u = title.replace(" ", "_")
    enc = urllib.parse.quote(s2u).replace("%5F", "_").replace("%2E", ".").replace("%2D", "-").replace("%28", "(").replace("%29", ")").replace("%2F", "/").replace("%3A", ":")
    final_url = WIKI_LINK_BASE + enc

    sanitized = raw_content
    if sanitized:
        sanitized = re.sub(r'<br\s*/?>', '\n', sanitized, flags=re.IGNORECASE)
        sanitized = re.sub(r'<[^>]*>', '', sanitized)
        sanitized = re.sub(r'\[\[File:[^\]]*\]\]', '(PICTURE)', sanitized)
        lines = [l for l in sanitized.split("\n") if not any(l.strip().startswith(x) for x in ["{|", "|-", "|}"])]
        sanitized = "\n".join(lines)
        replacements = [(r'\{\|', ""), (r'\|\}', ""), (r'\|-', ""), (r'\| ', ""), (r'\|\|', " "), (r'==', " "), (r'!', ""), (r"'''", ""), (r"''", ""), (r'\[\[', ""), (r'\]\]', ""), (r'&nbsp;', " ")]
        for pattern, rep in replacements: sanitized = re.sub(pattern, rep, sanitized)
        sanitized = "\n".join([l for l in sanitized.split("\n") if l.strip() != ""])

    char_count = len(sanitized) if sanitized else 0
    if raw_content.strip().startswith("#REDIRECT"):
        match = re.search(r'\[\[(.*?)\]\]', raw_content)
        target = match.group(1).replace(" ", "_") if match else ""
        final_body = f"Redirect to {WIKI_LINK_BASE}{target}"
    elif char_count > 25000:
        hdrs = [l for l in raw_content.split("\n") if l.strip().startswith("==")]
        final_body = "**Note: Content >25k. Headings only:**\n" + "\n".join(hdrs) if hdrs else "**Note: Content >25k.**"
    else:
        final_body = sanitized if sanitized else "*(No content)*"

    return f"### {title}\n**Wiki Link:** {final_url}\n\n**Instructions:**\n{final_body}\n\n" + ("-"*30) + "\n\n"

def push_to_docs(full_text):
    print("Connecting to Google Docs...")
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if creds_json:
        info = json.loads(creds_json)
        creds = service_account.Credentials.from_service_account_info(info, scopes=['https://www.googleapis.com/auth/documents'])
    else:
        creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=['https://www.googleapis.com/auth/documents'])
    
    service = build('docs', 'v1', credentials=creds)
    doc = service.documents().get(documentId=DOCUMENT_ID).execute()
    current_end_index = doc.get('body').get('content')[-1].get('endIndex') - 1

    # Safety check: Never try to insert an empty string
    if not full_text.strip():
        print("No content to insert. Skipping update.")
        return

    requests = []
    if current_end_index > 1:
        requests.append({'deleteContentRange': {'range': {'startIndex': 1, 'endIndex': current_end_index}}})
    requests.append({'insertText': {'location': {'index': 1}, 'text': full_text}})

    service.documents().batchUpdate(documentId=DOCUMENT_ID, body={'requests': requests}).execute()
    print("Update complete!")

if __name__ == "__main__":
    pages = fetch_wiki_data()
    if not pages:
        print("No data fetched from Wiki. Ending process.")
    else:
        all_content = "".join([sanitize_content(p.get('title', ''), p.get('revisions', [{}])[0].get('*', '')) for p in pages])
        push_to_docs(all_content)