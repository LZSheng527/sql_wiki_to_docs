import requests
import re
import urllib.parse
import time
import os
import json
from datetime import datetime, timedelta, timezone
from google.oauth2 import service_account
from googleapiclient.discovery import build

# --- CONFIGURATION ---
DOCUMENT_ID = "1p_eW5DW3mTNbQAuF92vwhmVLh8rdz87m8wzAaLE5lXM"
WIKI_LINK_BASE = "https://wiki.sql.com.my/wiki/"
SERVICE_ACCOUNT_FILE = 'credentials.json'

def fetch_wiki_data():
    all_pages = []
    gap_continue = ""
    apikey = os.environ.get("ZENROWS_API_KEY")
    
    if not apikey:
        print("ZENROWS_API_KEY missing! Check GitHub Secrets.")
        return []

    print("Fetching data from Wiki via ZenRows Proxy...")
    
    while True:
        # Build the Wiki API URL
        wiki_url = "https://wiki.sql.com.my/api.php?action=query&generator=allpages&gaplimit=50&prop=revisions&rvprop=content&format=json&origin=*"
        if gap_continue:
            wiki_url += f"&gapcontinue={urllib.parse.quote(gap_continue)}"
        
        # ZenRows API Request
        proxy_url = "https://api.zenrows.com/v1/"
        params = {
            "apikey": apikey,
            "url": wiki_url,
            "premium_proxy": "true",
            "proxy_country": "my"
        }

        try:
            response = requests.get(proxy_url, params=params, timeout=60)
            
            if response.status_code != 200:
                print(f"[!] Fetch Error: {response.status_code}. Details: {response.text[:100]}")
                break

            data = response.json()
            if "query" in data:
                pages = data["query"]["pages"]
                for pid in pages:
                    all_pages.append(pages[pid])
                print(f"  > Collected {len(all_pages)} pages total...")

            if "continue" in data and "gapcontinue" in data["continue"]:
                gap_continue = data["continue"]["gapcontinue"]
            else:
                break

        except Exception as e:
            print(f"[!] Error during fetch: {e}")
            break
            
    return all_pages

def sanitize_content(title, raw_content):
    # Friendly URL generation
    s2u = title.replace(" ", "_")
    enc = urllib.parse.quote(s2u).replace("%5F", "_").replace("%2E", ".").replace("%2D", "-") \
                                 .replace("%28", "(").replace("%29", ")").replace("%2F", "/") \
                                 .replace("%3A", ":")
    final_url = WIKI_LINK_BASE + enc

    sanitized = raw_content
    if sanitized:
        # Basic HTML/Wiki cleaning
        sanitized = re.sub(r'<br\s*/?>', '\n', sanitized, flags=re.IGNORECASE)
        sanitized = re.sub(r'<[^>]*>', '', sanitized)
        sanitized = re.sub(r'\[\[File:[^\]]*\]\]', '(PICTURE)', sanitized)
        
        # Remove Wiki table syntax
        lines = [l for l in sanitized.split("\n") if not any(l.strip().startswith(x) for x in ["{|", "|-", "|}"])]
        sanitized = "\n".join(lines)
        
        replacements = [
            (r'\{\|', ""), (r'\|\}', ""), (r'\|-', ""), (r'\| ', ""), (r'\|\|', " "),
            (r'==', " "), (r'!', ""), (r'#top\|\[top\]', ""), (r"'''", ""), (r"''", ""), 
            (r'\[\[', ""), (r'\]\]', ""), (r'&nbsp;', " ")
        ]
        for pattern, replacement in replacements:
            sanitized = re.sub(pattern, replacement, sanitized)
        
        sanitized = "\n".join([l for l in sanitized.split("\n") if l.strip() != ""])

    # Redirect and length logic
    is_redirect = raw_content.strip().startswith("#REDIRECT")
    char_count = len(sanitized) if sanitized else 0
    
    if is_redirect:
        match = re.search(r'\[\[(.*?)\]\]', raw_content)
        target = match.group(1).replace(" ", "_") if match else ""
        final_body = f"Redirect to {WIKI_LINK_BASE}{target}"
    elif char_count == 0:
        final_body = "*(No content)*"
    elif char_count > 25000:
        headers = [l for l in raw_content.split("\n") if l.strip().startswith("==")]
        final_body = "**Note: Content >25k. Headings only:**\n" + "\n".join(headers) if headers else "**Note: Content >25k.**"
    else:
        final_body = sanitized

    return f"### {title}\n**Wiki Link:** {final_url}\n\n**Instructions:**\n{final_body}\n\n" + ("-"*30) + "\n\n"

def push_to_docs(full_text):
    print("Connecting to Google Docs...")
    
    # MALAYSIA TIMEZONE FIX (UTC+8)
    malaysia_tz = timezone(timedelta(hours=8))
    timestamp = datetime.now(malaysia_tz).strftime("%d-%b-%Y %H:%M")
    
    header = f"Last Updated: {timestamp} (Malaysia Time)\n---\n\n"
    final_text = header + full_text

    # Load Credentials
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if creds_json:
        info = json.loads(creds_json)
        creds = service_account.Credentials.from_service_account_info(info, scopes=['https://www.googleapis.com/auth/documents'])
    else:
        creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=['https://www.googleapis.com/auth/documents'])
    
    service = build('docs', 'v1', credentials=creds)
    
    # Get current document end index to clear it
    doc = service.documents().get(documentId=DOCUMENT_ID).execute()
    current_end_index = doc.get('body').get('content')[-1].get('endIndex') - 1

    if not full_text.strip():
        print("Warning: Content empty. Skipping update.")
        return

    requests_list = []
    # Clear doc if not empty
    if current_end_index > 1:
        requests_list.append({'deleteContentRange': {'range': {'startIndex': 1, 'endIndex': current_end_index}}})
    
    # Insert new text
    requests_list.append({'insertText': {'location': {'index': 1}, 'text': final_text}})

    try:
        service.documents().batchUpdate(documentId=DOCUMENT_ID, body={'requests': requests_list}).execute()
        print(f"Success! Updated Doc with Malaysia Timestamp: {timestamp}")
    except Exception as e:
        print(f"Error during Google Docs Update: {e}")

if __name__ == "__main__":
    raw_pages = fetch_wiki_data()
    if not raw_pages:
        print("No pages fetched. Stopping.")
    else:
        all_content = ""
        # Sort pages alphabetically by title
        sorted_pages = sorted(raw_pages, key=lambda x: x.get('title', ''))
        
        for page in sorted_pages:
            title = page.get('title', 'Untitled')
            content = page.get('revisions', [{}])[0].get('*', '')
            all_content += sanitize_content(title, content)
        
        push_to_docs(all_content)