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
    base_api_url = "https://wiki.sql.com.my/api.php?action=query&generator=allpages&gaplimit=50&prop=revisions&rvprop=content&format=json&origin=*"
    
    # Advanced headers to mimic a real Windows Chrome browser
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://wiki.sql.com.my/",
        "Origin": "https://wiki.sql.com.my"
    }

    print("Fetching data from Wiki...")
    
    retry_count = 0
    while True:
        url = base_api_url + (f"&gapcontinue={urllib.parse.quote(gap_continue)}" if gap_continue else "")
        
        response = requests.get(url, headers=headers, timeout=30)
        
        if response.status_code == 403:
            print(f"[!] 403 Forbidden. Attempt {retry_count + 1}/2")
            if retry_count < 1:
                retry_count += 1
                time.sleep(10) # Wait a bit before one last try
                continue
            else:
                print("Server is strictly blocking GitHub Actions. Stopping script.")
                return [] # Exit gracefully

        response.raise_for_status()
        data = response.json()
        
        if "query" in data:
            pages = data["query"]["pages"]
            for pid in pages:
                all_pages.append(pages[pid])
        
        if "continue" in data and "gapcontinue" in data["continue"]:
            gap_continue = data["continue"]["gapcontinue"]
            print(f"Batch success. Next: {gap_continue}")
            time.sleep(2)
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
        replacements = [(r'\{\|', ""), (r'\|\}', ""), (r'\|-', ""), (r'\| ', ""), (r'\|\|', " "), (r'==', " "), (r'!', ""), (r'#top\|\[top\]', ""), (r"'''", ""), (r"''", ""), (r'\[\[', ""), (r'\]\]', ""), (r'&nbsp;', " ")]
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
    # --- CREDENTIAL LOGIC ---
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if creds_json:
        info = json.loads(creds_json)
        creds = service_account.Credentials.from_service_account_info(info, scopes=['https://www.googleapis.com/auth/documents'])
    else:
        creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=['https://www.googleapis.com/auth/documents'])
    
    service = build('docs', 'v1', credentials=creds)
    doc = service.documents().get(documentId=DOCUMENT_ID).execute()
    current_end_index = doc.get('body').get('content')[-1].get('endIndex') - 1

    requests = []
    if current_end_index > 1:
        requests.append({'deleteContentRange': {'range': {'startIndex': 1, 'endIndex': current_end_index}}})
    requests.append({'insertText': {'location': {'index': 1}, 'text': full_text}})

    service.documents().batchUpdate(documentId=DOCUMENT_ID, body={'requests': requests}).execute()
    print("Update complete!")

if __name__ == "__main__":
    pages = fetch_wiki_data()
    all_content = "".join([sanitize_content(p.get('title', ''), p.get('revisions', [{}])[0].get('*', '')) for p in pages])
    push_to_docs(all_content)