import requests
import re
import urllib.parse
import time
from google.oauth2 import service_account
from googleapiclient.discovery import build

# --- CONFIGURATION ---
DOCUMENT_ID = "1p_eW5DW3mTNbQAuF92vwhmVLh8rdz87m8wzAaLE5lXM"
SERVICE_ACCOUNT_FILE = 'credentials.json'
WIKI_LINK_BASE = "https://wiki.sql.com.my/wiki/"

def fetch_wiki_data():
    all_pages = []
    gap_continue = ""
    base_api_url = "https://wiki.sql.com.my/api.php?action=query&generator=allpages&gaplimit=50&prop=revisions&rvprop=content&format=json&origin=*"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36"
    }

    print("Fetching data from Wiki...")
    while True:
        url = base_api_url
        if gap_continue:
            url += f"&gapcontinue={urllib.parse.quote(gap_continue)}"
        
        try:
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            if "query" in data and "pages" in data["query"]:
                pages = data["query"]["pages"]
                for pid in pages:
                    all_pages.append(pages[pid])
            
            if "continue" in data and "gapcontinue" in data["continue"]:
                gap_continue = data["continue"]["gapcontinue"]
                print(f"Batch success. Next: {gap_continue}")
                time.sleep(2) # Politeness delay to avoid 10060 errors
            else:
                break
        except requests.exceptions.RequestException as e:
            print(f"\n[!] Connection issue: {e}. Retrying in 5s...")
            time.sleep(5)
            continue
            
    return all_pages

def sanitize_content(title, raw_content):
    # 1. Generate Friendly URL
    s2u = title.replace(" ", "_")
    enc = urllib.parse.quote(s2u).replace("%5F", "_").replace("%2E", ".").replace("%2D", "-") \
                                 .replace("%28", "(").replace("%29", ")").replace("%2F", "/") \
                                 .replace("%3A", ":")
    final_url = WIKI_LINK_BASE + enc

    # 2. Sanitization
    sanitized = raw_content
    if sanitized:
        sanitized = re.sub(r'<br\s*/?>', '\n', sanitized, flags=re.IGNORECASE)
        sanitized = re.sub(r'<[^>]*>', '', sanitized)
        sanitized = re.sub(r'\[\[File:[^\]]*\]\]', '(PICTURE)', sanitized)
        
        lines = sanitized.split("\n")
        clean_lines = []
        for line in lines:
            t = line.strip()
            if t.startswith("{|") or t.startswith("|-") or t.startswith("|}"):
                continue
            clean_lines.append(line)
        
        sanitized = "\n".join(clean_lines)
        
        replacements = [
            (r'\{\|', ""), (r'\|\}', ""), (r'\|-', ""), (r'\| ', ""), (r'\|\|', " "),
            (r'==', " "), (r'!', ""), (r'#top\|\[top\]', ""), (r'\[\[#top\|\[top\]\]\]', ""),
            (r"'''", ""), (r"''", ""), (r'\[\[', ""), (r'\]\]', ""), (r'&nbsp;', " ")
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
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, 
        scopes=['https://www.googleapis.com/auth/documents']
    )
    service = build('docs', 'v1', credentials=creds)

    # Get current length for cleanup
    doc = service.documents().get(documentId=DOCUMENT_ID).execute()
    content = doc.get('body').get('content')
    current_end_index = content[-1].get('endIndex') - 1
    
    # 1. Clear old content
    requests_list = []
    if current_end_index > 1:
        requests_list.append({
            'deleteContentRange': {
                'range': {'startIndex': 1, 'endIndex': current_end_index}
            }
        })
    
    # 2. Insert new text (Now safely under limits)
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
        print(f"Error during update: {e}")

if __name__ == "__main__":
    raw_pages = fetch_wiki_data()
    all_content = ""
    for page in raw_pages:
        title = page.get('title', 'Untitled')
        content = page.get('revisions', [{}])[0].get('*', '')
        all_content += sanitize_content(title, content)
    
    push_to_docs(all_content)