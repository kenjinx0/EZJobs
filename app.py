# app.py
import os
import sqlite3
import logging
import random
import time
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
from bs4 import BeautifulSoup
from fake_useragent import UserAgent  # You'll need to install this: pip install fake-useragent
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

logger = logging.getLogger()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')

# --- Enhanced User-Agent Rotation ---
_ua_instance = None
_ua_fallback_list = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.107 Safari/537.36',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 14_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1',
    'Mozilla/5.0 (iPad; CPU OS 14_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36 Edg/91.0.864.59',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.88 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:99.0) Gecko/20100101 Firefox/99.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:99.0) Gecko/20100101 Firefox/99.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.4 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36 Edg/99.0.1150.36'
]

def get_random_user_agent():
    """Return a random user agent string to use in requests."""
    global _ua_instance
    try:
        if _ua_instance is None:
            logger.info("Initializing UserAgent...")
            try:
                # It's good practice to specify a fallback User-Agent for the UserAgent constructor itself
                _ua_instance = UserAgent(fallback=random.choice(_ua_fallback_list), verify_ssl=False)
            except Exception as e_ua_init:
                logger.warning(f"fake_useragent.UserAgent() initialization failed: {e_ua_init}. Switching to manual fallback list.")
                _ua_instance = "fallback_mode" # Sentinel value

        if _ua_instance != "fallback_mode" and _ua_instance is not None:
            return _ua_instance.random
    except Exception as e_ua_random: # Catch errors during .random access or if _ua_instance became invalid
        logger.warning(f"fake_useragent.random failed or UserAgent instance issue: {e_ua_random}. Using manual fallback list.")
    
    # Fallback to our own list
    return random.choice(_ua_fallback_list)

# --- Enhanced Proxy Rotation System ---
class ProxyManager:
    def __init__(self):
        # IMPORTANT: Replace these with your actual, working proxies.
        # The format for proxies with authentication is:
        # {'http': 'http://user:pass@host:port', 'https': 'http://user:pass@host:port'}
        # Using free or unreliable proxies will likely lead to continued blocking.
        # Consider paid residential or mobile proxies for difficult sites.
        self.proxies_config = [
            # Example (these will NOT work, replace them):
            # {'http': 'http://your_proxy_user:your_proxy_pass@proxy.example.com:8080', 
            #  'https': 'http://your_proxy_user:your_proxy_pass@proxy.example.com:8080'},
            None,  # Allows for direct connection as one of the options
        ]
        if len(self.proxies_config) == 1 and self.proxies_config[0] is None:
            logger.warning("ProxyManager initialized with only a direct connection. Proxy rotation will not be effective.")
        
        self.failed_counts = {str(p): 0 for p in self.proxies_config}
        self.max_failures_per_proxy = 3 # Consecutive failures before cooldown
        self.cooldown_period = 300  # 5 minutes in seconds
        self.proxy_cooldown_until = {str(p): 0 for p in self.proxies_config}

    def get_proxy(self):
        """Get a healthy proxy. Prioritizes proxies not on cooldown."""
        now = time.time()
        
        eligible_proxies = []
        for p in self.proxies_config:
            if self.proxy_cooldown_until.get(str(p), 0) < now:
                eligible_proxies.append(p)
        
        if not eligible_proxies:
            # All configured proxies might be on cooldown.
            # If 'None' (direct connection) was an option and it's also on cooldown, this is problematic.
            # We might have to wait or use a proxy that just came off cooldown.
            # For simplicity, if all are on cooldown, let's pick the one whose cooldown expires soonest.
            # Or, if 'None' is an option and not explicitly on cooldown (or cooldown expired), prefer it.
            if None in self.proxies_config and self.proxy_cooldown_until.get(str(None), 0) < now:
                 logger.warning("All proxies on cooldown, trying direct connection if available and not on cooldown.")
                 return None

            logger.warning("No proxies immediately available (all might be on cooldown). Trying to pick one or defaulting to None.")
            # Fallback: if all are on cooldown, maybe pick the one that will be available soonest
            # This part can be made more sophisticated, for now, if no eligible, return None
            # This signals to make_request that proxy acquisition failed for this attempt
            return None 

        # Simple random choice among eligible proxies
        return random.choice(eligible_proxies)
    
    def mark_failed(self, proxy):
        """Mark a proxy as failed. If it fails too many times, put it on cooldown."""
        proxy_str = str(proxy) # Ensure 'None' becomes 'None' string key
        self.failed_counts[proxy_str] = self.failed_counts.get(proxy_str, 0) + 1
        
        if self.failed_counts[proxy_str] >= self.max_failures_per_proxy:
            logger.warning(f"Proxy {proxy_str} failed {self.failed_counts[proxy_str]} times. Putting on cooldown for {self.cooldown_period}s.")
            self.proxy_cooldown_until[proxy_str] = time.time() + self.cooldown_period
            self.failed_counts[proxy_str] = 0 # Reset count for next active period
    
    def mark_success(self, proxy):
        """If a proxy succeeds, reset its failure count and ensure it's not on cooldown."""
        proxy_str = str(proxy)
        if proxy_str in self.failed_counts:
            self.failed_counts[proxy_str] = 0
        if proxy_str in self.proxy_cooldown_until:
             self.proxy_cooldown_until[proxy_str] = 0 # Mark as active

proxy_manager = ProxyManager()

# --- Enhanced Request Function ---
def make_request(url, headers=None, timeout=25, retries=3): # Increased timeout
    """Make a request with anti-blocking measures"""
    
    current_request_headers = headers.copy() if headers is not None else {}

    if 'User-Agent' not in current_request_headers:
        current_request_headers['User-Agent'] = get_random_user_agent()

    default_headers_to_ensure = {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Cache-Control': 'max-age=0, no-cache, no-store', # Aggressive cache prevention
        'Pragma': 'no-cache', # For HTTP/1.0 caches
        'DNT': '1', # Do Not Track
    }
    for key, value in default_headers_to_ensure.items():
        current_request_headers.setdefault(key, value) # Add only if not already present

    # Initial delay before the first attempt for this URL
    initial_jitter = random.uniform(2.5, 7.5) # Increased jitter range
    logger.info(f"Initial delay of {initial_jitter:.2f}s before requesting {url}")
    time.sleep(initial_jitter)
    
    for attempt in range(retries):
        current_proxy = proxy_manager.get_proxy()
        
        # Delay between retries for the same URL
        if attempt > 0:
            backoff_delay = (2 ** attempt) + random.uniform(1.5, 4.0) # Increased random part
            logger.info(f"Retrying {url} (attempt {attempt + 1}/{retries}). Waiting {backoff_delay:.2f}s.")
            time.sleep(backoff_delay)
        
        ua_to_log = current_request_headers.get('User-Agent', 'N/A')
        logger.info(f"Requesting {url} (attempt {attempt + 1}) with proxy: {current_proxy}, User-Agent: ...{ua_to_log[-30:]}")
            
        try:
            response = requests.get(
                url, 
                headers=current_request_headers, 
                proxies=current_proxy, # requests handles None proxy as direct connection
                timeout=timeout,
                allow_redirects=True
            )
            
            # Check for common blocking indicators
            # LinkedIn's 999, Cloudflare 403s, Akamai 403s, Rate limits 429, Server errors 503
            # Unauthorized 401, Proxy Auth Required 407, Gone 410 (sometimes used for blocking)
            if response.status_code in [401, 403, 407, 410, 429, 503, 999]:
                logger.warning(f"Blocking status {response.status_code} for {url} with proxy {current_proxy}. Body snippet: {response.text[:200]}")
                if current_proxy is not None: # Only mark actual proxies, not direct connections (None) as 'failed' this way
                    proxy_manager.mark_failed(current_proxy)
                continue # Try next attempt with potentially different proxy
                
            # Basic CAPTCHA/bot detection page check (can be made more sophisticated)
            text_check = response.text.lower()
            if any(phrase in text_check for phrase in ["captcha", "are you a robot", "verify you are human", "access denied", "pardon our interruption"]):
                logger.warning(f"CAPTCHA or bot detection page suspected for {url} with proxy {current_proxy}. Body snippet: {response.text[:200]}")
                if current_proxy is not None:
                    proxy_manager.mark_failed(current_proxy)
                continue
                
            # If we reach here, the request seems to have succeeded
            if current_proxy is not None:
                proxy_manager.mark_success(current_proxy)
            return response
            
        except requests.exceptions.ProxyError as e:
            logger.warning(f"ProxyError for {url} with proxy {current_proxy}: {e}")
            if current_proxy is not None:
                proxy_manager.mark_failed(current_proxy)
        except requests.exceptions.SSLError as e:
            logger.warning(f"SSLError for {url} with proxy {current_proxy}: {e}")
            if current_proxy is not None: # SSL errors can be due to bad proxies
                proxy_manager.mark_failed(current_proxy)
        except requests.exceptions.ConnectTimeout as e:
            logger.warning(f"ConnectTimeout for {url} with proxy {current_proxy}: {e}")
            if current_proxy is not None:
                proxy_manager.mark_failed(current_proxy)
        except requests.exceptions.ReadTimeout as e:
            logger.warning(f"ReadTimeout for {url} with proxy {current_proxy}: {e}")
            if current_proxy is not None:
                proxy_manager.mark_failed(current_proxy)
        except requests.exceptions.Timeout as e: # General timeout
            logger.warning(f"Timeout for {url} with proxy {current_proxy}: {e}")
            if current_proxy is not None:
                proxy_manager.mark_failed(current_proxy)
        except requests.RequestException as e: # Catch-all for other requests issues
            logger.warning(f"Generic RequestException for {url} with proxy {current_proxy}: {e}")
            if current_proxy is not None: # Can be due to proxy or network
                proxy_manager.mark_failed(current_proxy)
        # Loop continues to the next attempt (if any) with a new proxy
    
    logger.error(f"All {retries} retries failed for {url}")
    return None # Return None if all retries fail

# --- Database and Utility Functions (largely unchanged, ensure DB path is correct) ---
DB_NAME = 'ezjobs.db'

def similar(a, b):
    a = a.lower()
    b = b.lower()
    if a == b: return 1.0
    if a in b: return 0.8 # Prioritize if search term is substring
    if b in a: return 0.7 # Slight less if query is substring of title
    
    a_words = set(a.split())
    b_words = set(b.split())
    if not a_words: return 0.0
    common_words = a_words.intersection(b_words)
    return len(common_words) / len(a_words)

def store_job(job):
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, company TEXT,
                location TEXT, experience TEXT, description TEXT,
                posted_date TEXT, url TEXT UNIQUE, source TEXT -- Added UNIQUE for URL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, job_id INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (job_id) REFERENCES jobs (id)
            )
        ''')
        
        try:
            cursor.execute('''
                INSERT INTO jobs (title, company, location, experience, description, posted_date, url, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                job['title'], job['company'], job['location'],
                job.get('experience', 'Not specified'), job.get('description', ''),
                job['posted_date'], job['url'], job['source']
            ))
            job_id = cursor.lastrowid
            conn.commit()
            logger.info(f"Stored new job: {job['title']} from {job['source']}")
            return job_id
        except sqlite3.IntegrityError: # Handles UNIQUE constraint violation for URL
            logger.info(f"Job with URL {job['url']} already exists.")
            cursor.execute('SELECT id FROM jobs WHERE url = ?', (job['url'],))
            existing_job = cursor.fetchone()
            return existing_job[0] if existing_job else None
        finally:
            conn.close()
            
    except Exception as e:
        logger.error(f"Error storing job in database: {e} (Job: {job.get('url')})")
        return None

def record_job_view(user_id, job_id):
    if not job_id: return # Don't record if job_id is None
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO user_history (user_id, job_id) VALUES (?, ?)
        ''', (user_id, job_id))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Error recording job view (user: {user_id}, job: {job_id}): {e}")

def get_user_job_history(user_id, limit=30):
    # ... (function remains the same)
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT j.id, j.title, j.company, j.location, j.experience, j.description, j.posted_date, j.url, j.source 
        FROM jobs j
        JOIN user_history h ON j.id = h.job_id
        WHERE h.user_id = ?
        ORDER BY h.timestamp DESC
        LIMIT ?
    ''', (user_id, limit))
    
    jobs_data = []
    columns = [desc[0] for desc in cursor.description]
    for row in cursor.fetchall():
        jobs_data.append(dict(zip(columns, row)))
    
    conn.close()
    return jobs_data

# --- Crawler Functions (largely unchanged, but will benefit from improved make_request) ---
# Note: The hardcoded cookies in these crawlers are very fragile.
# They will expire or be invalid for other users/IPs.
# For robust scraping, cookie handling needs to be dynamic (e.g. via sessions or headless browser interaction).

def crawl_linkedin(search_term, location, time_filter, experience_level):
    logger.info(f"Crawling LinkedIn for: {search_term}, location: {location}, time: {time_filter}, exp: {experience_level}")
    jobs = []
    search_url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={quote(search_term)}"

    if location and location.lower() not in ['all locations', 'location', 'india']: # 'india' might be default or too broad
        if location.lower() == 'remote':
            search_url += "&f_WT=2" # 2 for Remote
        # Add other location handling if necessary, LinkedIn uses geo IDs mostly
        # else: search_url += f"&location={quote(location)}" # This might not be the correct param for guest API

    # Time filter (f_TPR: Time Posted Range)
    # r86400 (24 hours), r604800 (Past Week), r2592000 (Past Month)
    if time_filter and time_filter.lower() not in ['any time', 'when posted']:
        if '24 hours' in time_filter.lower() or 'today' in time_filter.lower(): search_url += "&f_TPR=r86400"
        elif 'week' in time_filter.lower(): search_url += "&f_TPR=r604800"
        elif 'month' in time_filter.lower(): search_url += "&f_TPR=r2592000"
    
    # Experience level (f_E)
    # 1 (Internship), 2 (Entry level), 3 (Associate), 4 (Mid-Senior level), 5 (Director), 6 (Executive)
    if experience_level and experience_level.lower() not in ['all experience', 'experience']:
        if experience_level.lower() == 'intern': search_url += "&f_E=1"
        elif experience_level.lower() == 'fresher' or 'entry' in experience_level.lower(): search_url += "&f_E=2"
        # Add more mappings as needed

    search_url += "&start=0" # Pagination starts at 0

    headers = {
        'User-Agent': get_random_user_agent(),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://www.linkedin.com/jobs/search/',
        # 'Cookie': 'li_gc=...; JSESSIONID=...' # These are example stale cookies. Real ones are needed or omit.
        # LinkedIn is very hard to scrape without advanced techniques (JS rendering, valid auth cookies).
        # The guest API might be slightly more lenient but still heavily protected.
    }
    
    response = make_request(search_url, headers=headers)
    
    if response and response.status_code == 200:
        soup = BeautifulSoup(response.text, 'html.parser')
        # LinkedIn guest API returns HTML snippets directly usually
        job_cards = soup.find_all('li') # Or 'div', class_='base-card' if using the /jobs/search/ page
        
        for card in job_cards[:15]: # Limit results
            try:
                title_elem = card.find('h3', class_='base-search-card__title')
                company_elem = card.find('h4', class_='base-search-card__subtitle')
                location_elem = card.find('span', class_='job-search-card__location')
                date_elem = card.find('time', class_='job-search-card__listdate')
                link_elem = card.find('a', class_='base-card__full-link')

                if not title_elem and not link_elem: # Try different selectors if the above fail (structure might vary)
                    title_elem = card.find('span', class_='sr-only') # Often job title is here for screen readers
                    link_elem = card.find('a', class_='job-card-list__title') # common class for title link
                    if link_elem:
                         title_text = link_elem.text.strip()
                    else:
                         title_text = title_elem.text.strip() if title_elem else "N/A"

                    if not company_elem: company_elem = card.find('div', class_='job-card-container__company-name')
                    if not location_elem: location_elem = card.find('ul', class_='job-card-container__metadata-wrapper') # Location often inside this
                
                if title_elem and link_elem: # Ensure core elements are found
                    title = title_elem.text.strip()
                    company = company_elem.text.strip() if company_elem else "Not specified"
                    location_text = location_elem.text.strip() if location_elem else "Not specified"
                    posted_date = date_elem['datetime'] if date_elem and date_elem.has_attr('datetime') else (date_elem.text.strip() if date_elem else "Recent")
                    url = link_elem['href']
                    if not url.startswith('http'): url = "https://www.linkedin.com" + url # Ensure full URL

                    job = {
                        'title': title, 'company': company, 'location': location_text,
                        'posted_date': posted_date, 'url': url, 'source': 'LinkedIn',
                        'experience': experience_level if experience_level and experience_level.lower() != 'all experience' else 'Not specified'
                    }
                    if similar(search_term, title) > 0.3 or search_term.lower() in title.lower():
                        job_id = store_job(job)
                        if job_id: job['id'] = job_id # Add db id if stored
                        jobs.append(job)
            except Exception as e:
                logger.error(f"Error parsing LinkedIn job card: {e} - Card HTML: {str(card)[:200]}")
                continue
    else:
        status = response.status_code if response else "No response"
        logger.warning(f"LinkedIn request failed. Status: {status}. URL: {search_url}")
    return jobs


def crawl_indeed(search_term, location, time_filter, experience_level):
    logger.info(f"Crawling Indeed for: {search_term}, location: {location}, time: {time_filter}, exp: {experience_level}")
    jobs = []
    # Indeed URL structure: q=query, l=location, fromage=days_old, explvl=experience
    search_url = f"https://www.indeed.com/jobs?q={quote(search_term)}"
    
    if location and location.lower() not in ['all locations', 'location', 'india']:
        search_url += f"&l={quote(location)}"
    if location and location.lower() == 'remote': # Indeed uses 'remote' as a location query
        search_url += f"&l=remote"


    if time_filter and time_filter.lower() not in ['any time', 'when posted']:
        if '24 hours' in time_filter.lower() or 'today' in time_filter.lower(): search_url += "&fromage=1"
        elif '3 days' in time_filter.lower(): search_url += "&fromage=3"
        elif '7 days' in time_filter.lower() or 'week' in time_filter.lower(): search_url += "&fromage=7"
        elif '14 days' in time_filter.lower(): search_url += "&fromage=14" # Indeed has 14 days
        elif 'month' in time_filter.lower(): search_url += "&fromage=30" # approx month

    # Indeed uses 'explvl' parameter: entry_level, mid_level, senior_level
    if experience_level and experience_level.lower() not in ['all experience', 'experience']:
        if 'fresher' in experience_level.lower() or 'entry' in experience_level.lower():
            search_url += "&explvl=entry_level"
        # Add other levels if needed, e.g. mid_level, senior_level

    headers = {
        'User-Agent': get_random_user_agent(),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Referer': 'https://www.indeed.com/',
        # 'Cookie': 'CTK=...; INDEED_CSRF_TOKEN=...' # Example stale cookies
    }
    
    response = make_request(search_url, headers=headers)
    
    if response and response.status_code == 200:
        soup = BeautifulSoup(response.text, 'html.parser')
        # Indeed's job cards are often within 'div' with class 'job_seen_beacon' or similar
        # The mosaic-provider-jobcards structure is common now
        job_cards = soup.select('div.job_seen_beacon, td.result, div.jobsearch-SerpJobCard') # More general selectors
        
        for card in job_cards[:15]:
            try:
                title_elem = card.select_one('h2.jobTitle > a, a.jcs-JobTitle') # Common title selectors
                if not title_elem: # Fallback for other structures
                    title_elem = card.find('a', attrs={'data-tn-element': 'jobTitle'})

                company_elem = card.select_one('span.companyName, [data-testid="company-name"]')
                location_elem = card.select_one('div.companyLocation, [data-testid="text-location"]')
                date_elem = card.select_one('span.date, span.css-10pe3me') # date can be tricky

                if title_elem:
                    title = title_elem.text.strip()
                    base_url = "https://www.indeed.com"
                    url = title_elem.get('href')
                    if url and not url.startswith('http'):
                        url = base_url + url
                    elif not url and title_elem.get('id','').startswith('jl_'): # job link id
                        jk = title_elem.get('id').split('_')[1]
                        url = f"{base_url}/viewjob?jk={jk}"


                    company = company_elem.text.strip() if company_elem else "Not specified"
                    location_text = location_elem.text.strip() if location_elem else "Not specified"
                    posted_date = date_elem.text.strip() if date_elem else "Recent"
                    
                    job = {
                        'title': title, 'company': company, 'location': location_text,
                        'posted_date': posted_date, 'url': url, 'source': 'Indeed',
                        'experience': experience_level if experience_level and experience_level.lower() != 'all experience' else 'Not specified'
                    }
                    if similar(search_term, title) > 0.3 or search_term.lower() in title.lower():
                        job_id = store_job(job)
                        if job_id: job['id'] = job_id
                        jobs.append(job)
            except Exception as e:
                logger.error(f"Error parsing Indeed job card: {e} - Card HTML: {str(card)[:200]}")
                continue
    else:
        status = response.status_code if response else "No response"
        logger.warning(f"Indeed request failed. Status: {status}. URL: {search_url}")
    return jobs

def crawl_naukri(search_term, location, time_filter, experience_level):
    logger.info(f"Crawling Naukri for: {search_term}, location: {location}, time: {time_filter}, exp: {experience_level}")
    jobs = []
    # Naukri URL: keywords, location, experience, freshness(days)
    # Example: https://www.naukri.com/software-developer-jobs-in-bangalore?experience=2&freshness=3
    base_naukri_url = "https://www.naukri.com/"
    search_parts = [quote(search_term.replace(" ", "-")).lower() + "-jobs"]

    if location and location.lower() not in ['all locations', 'location', 'india']:
        if location.lower() != 'remote': # Naukri handles remote via wfh=true or specific keywords
            search_parts.append(f"in-{quote(location.replace(' ', '-')).lower()}")
    
    search_path = "-".join(search_parts)
    search_url = f"{base_naukri_url}{search_path}"
    
    query_params = {}
    if location and location.lower() == 'remote':
        query_params['wfhType'] = '100' # 100 seems to be for fully remote

    # Experience: &experience= (e.g., 0 for freshers, 1 for 1 year)
    if experience_level and experience_level.lower() not in ['all experience', 'experience']:
        if 'fresher' in experience_level.lower() or 'entry' in experience_level.lower() or 'intern' in experience_level.lower():
            query_params['experience'] = '0'
        # Add more specific year parsing if needed, e.g. "2 years" -> "2"
            
    # Time filter (freshness): &freshness= (e.g., 1 for 1 day, 7 for 7 days)
    if time_filter and time_filter.lower() not in ['any time', 'when posted']:
        if '24 hours' in time_filter.lower() or 'today' in time_filter.lower(): query_params['freshness'] = '1'
        elif '3 days' in time_filter.lower(): query_params['freshness'] = '3'
        elif '7 days' in time_filter.lower() or 'week' in time_filter.lower(): query_params['freshness'] = '7'
        elif '15 days' in time_filter.lower(): query_params['freshness'] = '15'
        elif '30 days' in time_filter.lower() or 'month' in time_filter.lower(): query_params['freshness'] = '30'

    if query_params:
        search_url += "?" + "&".join([f"{k}={v}" for k, v in query_params.items()])

    headers = {
        'User-Agent': get_random_user_agent(),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Referer': 'https://www.naukri.com/',
        'Appid': '109', # Naukri sometimes uses this, might help
        'Systemid': 'Naukri',
        # 'Cookie': '_t_ds=...; _t_us=...' # Example stale cookies
    }
    response = make_request(search_url, headers=headers)

    if response and response.status_code == 200:
        soup = BeautifulSoup(response.text, 'html.parser')
        # Naukri structure: 'article.jobTuple' or 'div.jobTuple'
        job_cards = soup.select('article.jobTuple, div.jobTuple.bgWhite.br4.mb-8') # Common selectors for job cards
        
        for card in job_cards[:15]:
            try:
                title_elem = card.select_one('a.title')
                company_elem = card.select_one('a.subTitle, div.companyInfo.subheading > a')
                experience_elem = card.select_one('span.expwdth, li.experience > span')
                location_elem = card.select_one('span.locWdth, li.location > span')
                # Date is often not directly available or needs complex parsing from description or a "posted x days ago" text.
                # Sometimes it's in a 'span' with class 'fleft date' or inside 'div.jobTupleFooter'
                date_text_elem = card.select_one('span.fleft.fw500, span.job-post-day')
                posted_date = date_text_elem.text.strip() if date_text_elem else "Recent"


                if title_elem:
                    title = title_elem.text.strip()
                    url = title_elem.get('href')
                    company = company_elem.text.strip() if company_elem else "Not specified"
                    exp_text = experience_elem.text.strip() if experience_elem else "Not specified"
                    location_text = location_elem.text.strip() if location_elem else "Not specified"
                    
                    job = {
                        'title': title, 'company': company, 'location': location_text,
                        'experience': exp_text, 'posted_date': posted_date,
                        'url': url, 'source': 'Naukri.com'
                    }
                    if similar(search_term, title) > 0.3 or search_term.lower() in title.lower():
                        job_id = store_job(job)
                        if job_id: job['id'] = job_id
                        jobs.append(job)
            except Exception as e:
                logger.error(f"Error parsing Naukri job card: {e} - Card HTML: {str(card)[:200]}")
                continue
    else:
        status = response.status_code if response else "No response"
        logger.warning(f"Naukri request failed. Status: {status}. URL: {search_url}")
    return jobs

def crawl_upwork(search_term, location, time_filter, experience_level):
    logger.info(f"Crawling Upwork for: {search_term}, location: {location}, time: {time_filter}, exp: {experience_level}")
    jobs = []
    # Upwork: q=query, contractor_tier= (1:Entry, 2:Intermediate, 3:Expert)
    # Location filtering on Upwork is complex, often by client location or if job is US-only.
    # For simplicity, global search by query and experience.
    search_url = f"https://www.upwork.com/nx/jobs/search/?q={quote(search_term)}"

    # Upwork tiers: 1 (Entry Level), 2 (Intermediate), 3 (Expert)
    # Corresponds to &contractor_tier=1,2,3
    if experience_level and experience_level.lower() not in ['all experience', 'experience']:
        if 'fresher' in experience_level.lower() or 'entry' in experience_level.lower() or 'intern' in experience_level.lower():
            search_url += "&contractor_tier=1"
        # Add other tiers if needed

    # Time filter (posted_date_range): today, yesterday,
    # Or custom range. Upwork's UI uses JS for this, direct URL params might be limited.
    # `&sort=recency` is often available.
    search_url += "&sort=recency" # Sort by newest

    headers = {
        'User-Agent': get_random_user_agent(),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Referer': 'https://www.upwork.com/nx/jobs/search/',
        # 'Cookie': 'visitor_id=...; OptanonConsent=...' # Example stale cookies
        # Upwork is heavily JS-driven. Scraping with requests/BS4 is very challenging.
        # A 410 "Gone" error often means the endpoint requires JS, auth, or is blocked.
    }
    response = make_request(search_url, headers=headers)

    if response and response.status_code == 200:
        soup = BeautifulSoup(response.text, 'html.parser')
        # Upwork job cards are usually <section> or <article> elements within a feed.
        # Classes like 'job-tile', 'up-card-section', 'job-details-card' are common.
        job_cards = soup.select('section.job-tile, article.job-tile, div.up-card-section') 

        for card in job_cards[:15]:
            try:
                title_elem = card.select_one('h2.job-title > a, h3.job-title > a, a.job-title-link')
                # Company/Client info is often not directly listed or is "Client"
                # Price/Budget might be in 'div[data-test="budget"]' or similar
                # Date posted in 'span[data-test="posted-on"]'
                
                if title_elem:
                    title = title_elem.text.strip()
                    url = title_elem.get('href')
                    if url and not url.startswith('http'):
                        url = "https://www.upwork.com" + url # Ensure full URL

                    # Extracting other details like company (client), location (often "Remote"), posted date
                    # is highly dependent on specific Upwork card structure which changes.
                    # For simplicity, we'll focus on title and URL.
                    posted_date_elem = card.select_one('small span[data-test="posted-on"], div[data-test="JobTileHeader"] span:nth-child(2)')
                    posted_date = posted_date_elem.text.strip() if posted_date_elem else "Recent"
                    
                    job = {
                        'title': title, 'company': "Upwork Client", # Generic for Upwork
                        'location': "Remote (typically)", # Most Upwork jobs are remote
                        'posted_date': posted_date, 'url': url, 'source': 'Upwork',
                        'experience': experience_level if experience_level and experience_level.lower() != 'all experience' else 'Not specified'
                    }
                    if similar(search_term, title) > 0.3 or search_term.lower() in title.lower():
                        job_id = store_job(job)
                        if job_id: job['id'] = job_id
                        jobs.append(job)
            except Exception as e:
                logger.error(f"Error parsing Upwork job card: {e} - Card HTML: {str(card)[:200]}")
                continue
    else:
        status = response.status_code if response else "No response"
        logger.warning(f"Upwork request failed. Status: {status}. URL: {search_url}")
        if response and response.status_code == 410:
             logger.error("Upwork returned 410 (Gone). This endpoint might be deprecated or requires JavaScript/authentication. Scraping Upwork with simple requests is very difficult.")
    return jobs

# --- Flask App (largely unchanged) ---
app = Flask(__name__)
CORS(app)
executor = ThreadPoolExecutor(max_workers=4) # Number of concurrent crawlers

@app.route('/api/jobs', methods=['GET'])
def get_jobs():
    query = request.args.get('query', 'software engineer')
    location = request.args.get('location', 'all locations')
    time_filter = request.args.get('posted', 'any time') # e.g., 'last 24 hours', 'last week'
    experience = request.args.get('experience', 'all experience') # e.g., 'fresher', 'intern'
    user_id = request.args.get('user_id', 'anonymous') # For history tracking

    # Log the incoming request details
    logger.info(f"API request: query='{query}', location='{location}', time='{time_filter}', experience='{experience}', user_id='{user_id}'")


    all_jobs = []
    # Submit crawler tasks to the thread pool
    # Note: ThreadPoolExecutor might not be ideal if crawlers are heavily I/O bound and get blocked often,
    # as threads might wait long. Async (aiohttp) could be better but requires rewriting crawlers.
    
    # Forcing crawlers to run sequentially for now to reduce simultaneous load / easier debugging of blocks
    # futures = [
    #     executor.submit(crawl_linkedin, query, location, time_filter, experience),
    #     executor.submit(crawl_indeed, query, location, time_filter, experience),
    #     executor.submit(crawl_naukri, query, location, time_filter, experience),
    #     executor.submit(crawl_upwork, query, location, time_filter, experience),
    # ]
    # for future in futures:
    #     try:
    #         all_jobs.extend(future.result(timeout=120)) # Add timeout for each crawler result
    #     except Exception as e:
    #         logger.error(f"A crawler failed or timed out: {e}")

    # Sequential execution for better rate limiting and easier debugging initially
    # You can switch back to ThreadPoolExecutor once individual crawlers are stable
    crawl_functions = [
        (crawl_linkedin, "LinkedIn"),
        (crawl_indeed, "Indeed"),
        (crawl_naukri, "Naukri"),
        (crawl_upwork, "Upwork") # Upwork is very likely to fail with this method
    ]

    for func, name in crawl_functions:
        try:
            logger.info(f"Starting crawler: {name}")
            # Pass all relevant filters to each crawler
            jobs_from_source = func(query, location, time_filter, experience)
            all_jobs.extend(jobs_from_source)
            logger.info(f"Finished crawler: {name}, found {len(jobs_from_source)} jobs.")
        except Exception as e:
            logger.error(f"Crawler {name} threw an exception: {e}")
        time.sleep(random.uniform(3, 8)) # Add a delay between different job sites

    # Deduplicate jobs by URL (though DB store_job handles this, good for API response)
    unique_jobs = {job['url']: job for job in all_jobs}.values()
    
    # For personalization (example): If user_id provided, get history
    # This part is not fully implemented for actual recommendations, just an idea
    if user_id != 'anonymous':
        user_history = get_user_job_history(user_id)
        # Could use user_history to re-rank or filter `unique_jobs`
        logger.info(f"User {user_id} has {len(user_history)} jobs in history.")

    logger.info(f"Total unique jobs found: {len(unique_jobs)}")
    return jsonify(list(unique_jobs))

@app.route('/api/jobs/<int:job_id>/viewed', methods=['POST'])
def job_viewed(job_id):
    user_id = request.json.get('user_id', 'anonymous')
    record_job_view(user_id, job_id)
    return jsonify({'status': 'success', 'message': f'Job {job_id} view recorded for user {user_id}'}), 200

if __name__ == '__main__':
    # Ensure database tables are created on startup
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, company TEXT,
            location TEXT, experience TEXT, description TEXT,
            posted_date TEXT, url TEXT UNIQUE, source TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, job_id INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (job_id) REFERENCES jobs (id)
        )
    ''')
    conn.commit()
    conn.close()
    logger.info(f"Database {DB_NAME} initialized.")
    
    app.run(debug=True, host='0.0.0.0', port=os.environ.get("PORT", 5001))