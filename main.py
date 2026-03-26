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
        print("SCRAPERANT_API_KEY missing! Ensure it is set in GitHub Secrets.")
        return []

    print("Fetching data from Wiki via ScraperAnt Proxy...")
    
    while True:
        target_url = "https://wiki.sql.com.my/api.php?action=query&generator=allpages&gaplimit=50&prop=revisions&rvprop=content&format=json&origin=*"
        if gap_continue:
            target_url += f"&gapcontinue={urllib.parse.quote(gap_continue)}"
        
        proxy_url = "https://api.scraperant.com/v2/general"
        params = {"url": target_url, "x-api-key": ant_key, "browser": "false"}

        try:
            # Request through Proxy
            response = requests.get(proxy_url, params=params, timeout=60)
            
            if response.status_code != 200:
                print(f"[!] Proxy Error: {response.status_code}. Response: {response.text[:100]}")
                break

            data = response.json()
            if "query" in data:
                pages = data["query"]["pages"]
                for pid in pages:
                    all_pages.append(pages[pid])
                print(f"  > Collected {len(all_pages)} pages total...")

            if "continue" in data and "gapcontinue" in data["continue"]:
                gap_continue = data["continue"]["gapcontinue"]
                time.sleep(1) # Small delay to be safe
            else:
                break

        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as ce:
            print(f"[!] Network/DNS error: {ce}. Retrying in 10s...")
            time.sleep(10)
            continue # Try the same batch again
        except Exception as e:
            print(f"[!] Unexpected error during fetch: {e}")
            break
            
    return all_pages

def sanitize_content(title, raw_content):
    # 1. Generate Friendly URL
    s2u = title.replace(" ", "_")
    enc = urllib.parse.quote(s2u).replace("%5F", "_").replace("%2E", ".").replace("%2D", "-") \
                                 .replace("%28", "(").replace("%29", ")").replace("%2F", "/") \
                                 .replace("%3A", ":")
    final_url = WIKI_LINK_BASE + enc

    # 2. Sanitization Logic
    sanitized = raw_content
    if sanitized:
        sanitized = re.sub(r'<br\s*/?>', '\n', sanitized, flags=re.IGNORECASE)
        sanitized = re.sub(r'<[^>]*>', '', sanitized)
        sanitized = re.sub(r'\[\[File:[^\]]*\]\]', '(PICTURE)', sanitized)
        
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

    # 3. 25k Rule / Redirect Logic
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
    
    # Load Credentials from Secret (GitHub) or File (Local)
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if creds_json:
        info = json.loads(creds_json)
        creds = service_account.Credentials.from_service_account_info(info, scopes=['https://www.googleapis.com/auth/documents'])
    else:
        creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=['https://www.googleapis.com/auth/documents'])
    
    service = build('docs', 'v1', credentials=creds)
    
    # Get current doc length
    doc = service.documents().get(documentId=DOCUMENT_ID).execute()
    current_end_index = doc.get('body').get('content')[-1].get('endIndex') - 1

    # Safety check: if full_text is empty, don't clear the doc
    if not full_text.strip():
        print("Warning: Content is empty. Skipping update to protect the Document.")
        return

    requests_list = []
    # Clear existing content
    if current_end_index > 1:
        requests_list.append({
            'deleteContentRange': {
                'range': {'startIndex': 1, 'endIndex': current_end_index}
            }
        })
    
    # Insert new content
    requests_list.append({
        'insertText': {
            'location': {'index': 1}, 
            'text': full_text
        }
    })

    try:
        service.documents().batchUpdate(documentId=DOCUMENT_ID, body={'requests': requests_list}).execute()
        print(f"Success! Updated Doc with {len(full_text)} characters.")
    except Exception as e:
        print(f"Error during Google Docs BatchUpdate: {e}")

if __name__ == "__main__":
    raw_pages = fetch_wiki_data()
    
    if not raw_pages:
        print("No pages were fetched. Check ScraperAnt logs/credits.")
    else:
        all_content = ""
        for page in raw_pages:
            title = page.get('title', 'Untitled')
            content = page.get('revisions', [{}])[0].get('*', '')
            all_content += sanitize_content(title, content)
        
        push_to_docs(all_content)