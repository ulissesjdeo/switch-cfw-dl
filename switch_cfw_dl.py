from base64 import b64decode
from html.parser import HTMLParser
from json import dump, load, dumps, loads
from os import path, remove
from re import sub, search, IGNORECASE, findall, compile, DOTALL
from sqlite3 import connect
from urllib.request import Request, urlopen
from urllib.parse import urlparse, parse_qs

from unicodedata import normalize, combining

JSON_FILE = 'games.json'
US_JSON_FILE = 'games_us.json'

class GameParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_game_row = False
        self.in_game_cell = False
        self.current_link = None
        self.games = []
        self.current_game = {}
        self.parsing_link = False
        self.region_text = ""
        self.parsing_region = False

    def handle_starttag(self, tag, attrs):
        if tag == 'tr' and ('class', 'post-row') in attrs:
            self.in_game_row = True
            self.current_game = {}
            self.region_text = ""
        elif self.in_game_row and tag == 'td':
            self.in_game_cell = True
        elif self.in_game_cell and tag == 'a':
            self.parsing_link = True
            for attr in attrs:
                if attr[0] == 'href':
                    link = attr[1]
                    if not link.startswith(('http://', 'https://')):
                        link = f"https://nswdl.com{'' if link.startswith('/') else '/'}{link}"
                    self.current_game['link'] = link
                    break
        elif tag == 'span' and self.in_game_cell and any(attr[0] == 'style' and 'color: red' in attr[1] for attr in attrs):
            self.parsing_region = True

    def handle_endtag(self, tag):
        if tag == 'tr' and self.in_game_row:
            self.in_game_row = False
            if self.current_game and 'name' in self.current_game and 'link' in self.current_game:
                if 'code' not in self.current_game:
                    self.current_game['code'] = "Unknown"
                if self.current_game.get('name') == '(Back to Top)':
                    return
                if self.current_game.get('name', '').startswith('- '):
                    self.current_game['name'] = self.current_game['name'][2:]
                elif self.current_game.get('name', '').startswith('– '):
                    self.current_game['name'] = self.current_game['name'][2:]
                self.current_game['regions'] = extract_regions_from_name(self.current_game.get('name', ''), self.region_text)
                self.games.append(self.current_game)
        elif tag == 'td' and self.in_game_cell:
            self.in_game_cell = False
        elif tag == 'a' and self.parsing_link:
            self.parsing_link = False
        elif tag == 'span' and self.parsing_region:
            self.parsing_region = False

    def handle_data(self, data):
        if self.parsing_link:
            clean_data = sub(r'<[^>]*>', '', data).strip()
            if clean_data:
                if 'name' not in self.current_game:
                    self.current_game['name'] = clean_data
        elif self.parsing_region:
            self.region_text += " " + data
        elif self.in_game_cell:
            if 'code' not in self.current_game:
                code_match = search(r'([0-9A-F]{16})', data)
                if code_match:
                    self.current_game['code'] = code_match.group(1)

class DownloadLinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.download_links = []
        self.in_download_section = False
        self.parsing_link = False
        self.current_link = ""
        self.current_text = ""
        self.download_section_tags = ['div', 'table']
        self.download_identifiers = [
            'download', 'téléchargement', 'descargar', 'herunterladen', 
            'scarica', 'baixar', 'скачать', '下载', 'ダウンロード'
        ]

    def handle_starttag(self, tag, attrs):
        if tag in self.download_section_tags:
            class_attr = next((attr[1] for attr in attrs if attr[0] == 'class'), '')
            id_attr = next((attr[1] for attr in attrs if attr[0] == 'id'), '')
            if any(dl_id.lower() in class_attr.lower() or dl_id.lower() in id_attr.lower() 
                  for dl_id in self.download_identifiers):
                self.in_download_section = True
        elif tag == 'tr' and self.in_download_section:
            self.in_download_section = True
        elif self.in_download_section and tag == 'a':
            self.parsing_link = True
            for attr in attrs:
                if attr[0] == 'href':
                    href = attr[1]
                    if any(domain in href.lower() for domain in [
                        'mega.nz', 'mediafire', 'drive.google', 'dropbox', '1fichier',
                        'uploadhaven', 'zippyshare', 'uptobox', 'google.com', 'pixeldrain',
                        'up-4ever', 'file-upload', 'sendcm', 'send.cm', 'clicknupload', 'frdl.is',
                        'buzzheavier', 'ouo.io', 'redirect-to'
                    ]):
                        self.current_link = href
                        break

    def handle_endtag(self, tag):
        if tag in self.download_section_tags and self.in_download_section:
            pass
        elif tag == 'a' and self.parsing_link:
            self.parsing_link = False
            if self.current_link and self.current_text and self.current_link not in [link for _, link in self.download_links]:
                clean_text = self.current_text.strip()
                if clean_text:
                    self.download_links.append((clean_text, self.current_link))
            self.current_link = ""
            self.current_text = ""

    def handle_data(self, data):
        if self.parsing_link:
            self.current_text += data

def extract_regions_from_name(game_name, region_text=""):
    regions = []
    combined_text = game_name + " " + region_text
    region_pattern = r'\b(JP|US|USA|EU|UK|AS|CH|KOR|TW|FR|DE|IT|ES|Asia|Japan|America|Europe|England|China|Korea|Taiwan|France|Germany|Italy|Spain)\b'
    explicit_regions = findall(region_pattern, combined_text, IGNORECASE)
    region_map = {
        'japan': 'JP', 'jp': 'JP',
        'us': 'US', 'usa': 'US', 'america': 'US',
        'eu': 'EU', 'europe': 'EU',
        'uk': 'UK', 'england': 'UK',
        'as': 'AS', 'asia': 'AS',
        'ch': 'CH', 'china': 'CH', 'chinese': 'CH',
        'kor': 'KOR', 'korea': 'KOR', 'korean': 'KOR', 'ko': 'KOR',
        'tw': 'TW', 'taiwan': 'TW',
        'fr': 'FR', 'france': 'FR', 'french': 'FR',
        'de': 'DE', 'germany': 'DE', 'german': 'DE',
        'it': 'IT', 'italy': 'IT', 'italian': 'IT',
        'es': 'ES', 'spain': 'ES', 'spanish': 'ES', 'spa': 'ES'
    }
    for region in explicit_regions:
        std_region = region_map.get(region.lower(), region.upper())
        if std_region not in regions:
            regions.append(std_region)
    if search(r'\bUSA\b|\bUS\b|(?<!\w)USA(?!\w)|(?<!\w)US(?!\w)|\(USA\)|\(US\)|\[USA\]|\[US\]', combined_text, IGNORECASE):
        if 'US' not in regions:
            regions.append('US')
    if search(r'\bEU\b|(?<!\w)EU(?!\w)|\(EU\)|\[EU\]', combined_text, IGNORECASE):
        if 'EU' not in regions:
            regions.append('EU')
    if search(r'\bJP\b|(?<!\w)JP(?!\w)|\(JP\)|\[JP\]', combined_text, IGNORECASE):
        if 'JP' not in regions:
            regions.append('JP')
    if not regions:
        regions = ['All']
        if 'US' not in regions:
            regions.append('US')
    return regions

def filter_us_games(games):
    return [game for game in games if 'US' in game.get('regions', []) or 'All' in game.get('regions', [])]

def add_regions_to_existing_games(games):
    updated_count = 0
    should_remake = True
    if not should_remake:
        for game in games:
            if 'regions' not in game or game['regions'] == ['Unknown']:
                game['regions'] = extract_regions_from_name(game.get('name', ''))
                if game['regions'] != ['Unknown']:
                    updated_count += 1
        return games, updated_count
    else:
        print("Detecting region issues. Rebuilding the game database...")
        return fetch_games_from_website(), len(games)

def fetch_games_from_website():
    url = "https://nsw2u.com/switch-posts"
    games = []
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        req = Request(url, headers=headers)
        with urlopen(req) as response:
            html = response.read().decode('utf-8')
            parser = GameParser()
            parser.feed(html)
            games = parser.games
    except Exception as e:
        print(f"Error fetching games: {e}")
    return games

def download_games():
    games = fetch_games_from_website()
    games = [game for game in games if game.get('name') != '(Back to Top)']
    us_games = filter_us_games(games)
    with open(JSON_FILE, 'w', encoding='utf-8') as f:
        dump(games, f, ensure_ascii=False, separators=(',', ':'))
    with open(US_JSON_FILE, 'w', encoding='utf-8') as f_us:
        dump(us_games, f_us, ensure_ascii=False, separators=(',', ':'))
    print(f"Full game list has been saved to '{JSON_FILE}'")
    print(f"US games list has been saved to '{US_JSON_FILE}'")

def remove_accents(input_str):
    nfkd_form = normalize('NFKD', input_str)
    return ''.join([c for c in nfkd_form if not combining(c)])

def load_games_to_db():
    conn = connect(':memory:')
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS games (
        name TEXT,
        link TEXT,
        code TEXT,
        regions TEXT
    )''')
    if path.exists(JSON_FILE):
        with open(JSON_FILE, 'r', encoding='utf-8') as f:
            games = load(f)
            for game in games:
                cursor.execute(
                    "INSERT INTO games VALUES (?, ?, ?, ?)",
                    (
                        game.get('name', ''),
                        game.get('link', ''),
                        game.get('code', 'Unknown'),
                        dumps(game.get('regions', ['Unknown']))
                    )
                )
    conn.commit()
    return conn

def search_game_by_name(conn, name_pattern):
    cursor = conn.cursor()
    cursor.execute("""
        SELECT name, link, code, regions 
        FROM games
        WHERE name LIKE ?
        ORDER BY name
    """, (f'%{name_pattern}%',))
    results = cursor.fetchall()
    return [
        {
            'name': row[0],
            'link': row[1],
            'code': row[2],
            'regions': loads(row[3])
        }
        for row in results
    ]

def parse_file_info(filename):
    info = {
        "type": "Unknown",
        "format": "Unknown",
        "version": "Unknown",
        "region": "Unknown"
    }
    lower_name = filename.lower()
    if ".nsp" in lower_name:
        info["format"] = "NSP"
    elif ".xci" in lower_name:
        info["format"] = "XCI"
    elif ".rar" in lower_name or ".zip" in lower_name:
        if ".nsp" in lower_name:
            info["format"] = "NSP (archived)"
        elif ".xci" in lower_name:
            info["format"] = "XCI (archived)"
        else:
            info["format"] = "Archive"
    if "update" in lower_name or "[v" in lower_name or "patch" in lower_name or any(x in lower_name for x in ["[v", "(v", "+v", "+update"]):
        info["type"] = "Update"
    elif "dlc" in lower_name or "addon" in lower_name:
        info["type"] = "DLC"
    else:
        info["type"] = "Base Game"
    version_patterns = [
        r'\[v(\d+\.?\d*(?:\.\d+)*)\]',  # [v1.2.3]
        r'\(v(\d+\.?\d*(?:\.\d+)*)\)',  # (v1.2.3)
        r'v(\d+\.?\d*(?:\.\d+)*)',       # v1.2.3
        r'\[(\d+\.?\d+(?:\.\d+)*)\]',   # [1.2.3]
        r'\((\d+\.?\d+(?:\.\d+)*)\)',   # (1.2.3)
        r'\[v?(\d+)\]',                 # [65536] or [v65536]
        r'\bv(\d+\.?\d*(?:\.\d+)*)\b',  # v1.2.3 (with word boundary)
    ]
    for pattern in version_patterns:
        version_match = search(pattern, filename)
        if version_match:
            version = version_match.group(1)
            if version.isdigit() and len(version) >= 5:
                num_version = int(version)
                major = num_version >> 16
                minor = (num_version >> 8) & 0xFF
                patch = num_version & 0xFF
                if major > 0:
                    version = f"{major}.{minor}.{patch}"
            info["version"] = version
            break
    region_patterns = {
        r'\[US\]|\(US\)|USA': 'US',
        r'\[EU\]|\(EU\)|EUR|Europe': 'EU', 
        r'\[JP\]|\(JP\)|JPN|Japan': 'JP',
        r'\[AS\]|\(AS\)|ASIA': 'AS',
        r'\[ALL\]|\(ALL\)|WW|World': 'ALL',
        r'\[KOR\]|\(KOR\)|Korea': 'KOR',
        r'\[CHN\]|\(CHN\)|China': 'CHN',
    }
    regions_found = []
    for pattern, region in region_patterns.items():
        if search(pattern, filename, IGNORECASE):
            regions_found.append(region)
    if regions_found:
        info["region"] = ",".join(regions_found)
    return info

def decode_redirect_url(redirect_url):
    try:
        parsed_url = urlparse(redirect_url)
        if 'redirect-to' in parsed_url.path or 'redirect' in parsed_url.path:
            query_params = parse_qs(parsed_url.query)
            if 'url' in query_params:
                encoded_url = query_params['url'][0]
                try:
                    padding = 4 - (len(encoded_url) % 4)
                    if padding < 4:
                        encoded_url += '=' * padding
                    decoded_bytes = b64decode(encoded_url)
                    decoded_url = decoded_bytes.decode('utf-8')
                    if 'ouo.io' in decoded_url:
                        ouo_parsed = urlparse(decoded_url)
                        if ouo_parsed.query:
                            ouo_params = parse_qs(ouo_parsed.query)
                            if 's' in ouo_params:
                                return ouo_params['s'][0]
                    return decoded_url
                except Exception as e:
                    return redirect_url
        return redirect_url
    except Exception as e:
        return redirect_url

def get_download_links(game_url):
    download_links = []
    detailed_links = []
    try:
        if not game_url:
            raise ValueError("Invalid game URL")
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        req = Request(game_url, headers=headers)
        with urlopen(req) as response:
            html = response.read().decode('utf-8')
            download_box_pattern = compile(r'<div\s+class=[\'"]download-box[\'"]>(.*?)</div>', IGNORECASE | DOTALL)
            download_boxes = download_box_pattern.findall(html)
            if download_boxes:
                for box_content in download_boxes:
                    table_pattern = compile(r'<table\s+class=[\'"]bti-table[\'"].*?<tbody>(.*?)</tbody>', IGNORECASE | DOTALL)
                    tables = table_pattern.findall(box_content)
                    section_headers = findall(r'<h4>(.*?)</h4>', box_content)
                    section_idx = 0
                    for table in tables:
                        if section_idx < len(section_headers):
                            section_idx += 1
                        row_pattern = compile(r'<tr>(.*?)</tr>', IGNORECASE | DOTALL)
                        rows = row_pattern.findall(table)
                        for row in rows:
                            if '<th>' in row:
                                continue
                            try:
                                cell_pattern = compile(r'<td>(.*?)</td>', IGNORECASE | DOTALL)
                                cells = cell_pattern.findall(row)
                                if len(cells) >= 3:
                                    category = cells[0].strip()
                                    filename = cells[1].strip()
                                    link_cell = cells[2]
                                    filename = sub(r'<[^>]*>', '', filename).strip()
                                    links_pattern = compile(r'<a\s+href=[\'"]([^\'"]+)[\'"][^>]*>([^<]+)</a>', IGNORECASE)
                                    links = links_pattern.findall(link_cell)
                                    file_info = parse_file_info(filename)
                                    if category.lower() in ["base", "update", "dlc", "old update"]:
                                        file_info["type"] = category
                                    for link_url, link_text in links:
                                        detailed_links.append((filename, link_url, file_info, link_text))
                            except Exception as e:
                                print(f"Error parsing row: {e}")
            if not detailed_links:
                parser = DownloadLinkParser()
                parser.feed(html)
                download_links = parser.download_links
                if not download_links:
                    print("No structured download tables found, trying alternative methods...")
                    nsp_links = findall(r'href=[\'"]?([^\'" >]+\.(?:nsp|xci|rar|zip)[^\'" >]*)', html, IGNORECASE)
                    redirect_links = findall(r'href=[\'"]?([^\'" >]*redirect-to[^\'" >]*)', html, IGNORECASE)
                    for link in nsp_links + redirect_links:
                        filename = path.basename(link.split('?')[0])
                        if not filename:
                            filename = "Download Link"
                        download_links.append((filename, link))
                for filename, link_url in download_links:
                    info = parse_file_info(filename)
                    detailed_links.append((filename, link_url, info, "Download"))
    except Exception as e:
        print(f"Error fetching download links: {e}")
        import traceback
        traceback.print_exc()
    return detailed_links

if __name__ == "__main__":
    if not path.exists(JSON_FILE) or not path.exists(US_JSON_FILE):
        print("Games list not found. Downloading game data...")
        download_games()
    first_run = True
    while True:
        if first_run:
            print("SWITCH-CFW-DL")
            first_run = False
        else:
            print("\nSWITCH-CFW-DL")
        print("1. Update games list")
        print("2. Search game by name")
        print("0. Exit")
        choice = input("Enter your choice: ")
        if choice == '1':
            print("Updating games list...")
            if path.exists(JSON_FILE): remove(JSON_FILE)
            if path.exists(US_JSON_FILE): remove(US_JSON_FILE)
            download_games()
            print("Games list updated successfully!")
        elif choice == '2':
            db_conn = load_games_to_db()
            search_term = input("Enter game name (or part of name) to search: ")
            results = search_game_by_name(db_conn, search_term)
            if results:
                print(f"\nFound {len(results)} games matching '{search_term}':")
                for i, game in enumerate(results, 1):
                    regions_str = ', '.join(game['regions'])
                    print(f"{i}. {game['name']} ({regions_str}) ({game['code']})")
                if len(results) > 1:
                    try:
                        selection = int(input("\nEnter number to see game details and download links (0 to return to menu): "))
                        if 1 <= selection <= len(results):
                            selected_game = results[selection-1]
                            print(f"\nFetching download links, please wait...")
                            download_links = get_download_links(selected_game['link'])
                            if download_links:
                                grouped_links = {}
                                for filename, link_url, info, link_text in download_links:
                                    if info["type"].lower() == "old update":
                                        continue
                                    decoded_url = decode_redirect_url(link_url)
                                    key = (filename, info["type"], info["format"], info["version"], info["region"])
                                    if key not in grouped_links:
                                        grouped_links[key] = []
                                    grouped_links[key].append((decoded_url, link_text))
                                print("\nDownload Links:")
                                for i, ((filename, type_info, format_info, version_info, region_info), links) in enumerate(grouped_links.items(), 1):
                                    print(f"{i}. {type_info} - {filename}")
                                    for j, (link_url, link_text) in enumerate(links, 1):
                                        print(f" {link_text} {link_url}")
                                    print()
                            else:
                                print("No download links found.")
                        elif selection != 0:
                            print("Invalid selection")
                    except ValueError:
                        print("Invalid input. Please enter a number.")
                else:
                    selected_game = results[0]
                    print(f"Fetching download links, please wait...")
                    download_links = get_download_links(selected_game['link'])
                    if download_links:
                        grouped_links = {}
                        for filename, link_url, info, link_text in download_links:
                            if info["type"].lower() == "old update":
                                continue
                            decoded_url = decode_redirect_url(link_url)
                            key = (filename, info["type"], info["format"], info["version"], info["region"])
                            if key not in grouped_links:
                                grouped_links[key] = []
                            grouped_links[key].append((decoded_url, link_text))
                        print("\nDownload Links:")
                        for i, ((filename, type_info, format_info, version_info, region_info), links) in enumerate(grouped_links.items(), 1):
                            print(f"{i}. {type_info} - {filename}")
                            for j, (link_url, link_text) in enumerate(links, 1):
                                print(f" {link_text} {link_url}")
                            print()
                    else:
                        print("No download links found.")
            else:
                print(f"No games found matching '{search_term}'")
        elif choice == '0':
            break
        else:
            print("Invalid option")
