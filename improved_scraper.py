# Add the missing get_random_user_agent function and enhance anti-blocking measures
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

logger = logging.getLogger()
logging.basicConfig(level=logging.INFO)

# Enhanced user agent rotation with more realistic browser fingerprints
def get_random_user_agent():
    """Return a random user agent string to use in requests."""
    try:
        # Try to use fake_useragent which provides more up-to-date and diverse agents
        ua = UserAgent()
        return ua.random
    except:
        # Fallback to our own list if fake_useragent fails
        user_agents = [
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
        return random.choice(user_agents)

# Proxy rotation system
class ProxyManager:
    def __init__(self):
        # You would typically load these from a file or service
        # For this demo, we'll use a small list of example proxies
        # In production, you should use actual working proxies
        self.proxies = [
            None,  # Direct connection (no proxy) as fallback
            {'http': 'http://proxy1.example.com:8080', 'https': 'http://proxy1.example.com:8080'},
            {'http': 'http://proxy2.example.com:8080', 'https': 'http://proxy2.example.com:8080'},
            {'http': 'http://proxy3.example.com:8080', 'https': 'http://proxy3.example.com:8080'},
        ]
        self.current_index = 0
        self.failed_attempts = {}
    
    def get_proxy(self):
        """Get the next proxy in rotation"""
        proxy = self.proxies[self.current_index]
        self.current_index = (self.current_index + 1) % len(self.proxies)
        return proxy
    
    def mark_failed(self, proxy):
        """Mark a proxy as failed"""
        if proxy in self.failed_attempts:
            self.failed_attempts[str(proxy)] += 1
        else:
            self.failed_attempts[str(proxy)] = 1
    
    def should_retry(self, proxy):
        """Determine if a proxy should be retried based on failure count"""
        if str(proxy) not in self.failed_attempts:
            return True
        return self.failed_attempts[str(proxy)] < 3  # Retry up to 3 times

proxy_manager = ProxyManager()

# Enhanced request function with anti-blocking measures
def make_request(url, headers=None, proxy=None, timeout=15, retries=3):
    """Make a request with anti-blocking measures"""
    if headers is None:
        headers = {
            'User-Agent': get_random_user_agent(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': 'https://www.google.com/',  # Mimic coming from Google search
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Cache-Control': 'max-age=0',
            'DNT': '1',  # Do Not Track
            'Pragma': 'no-cache',
        }
    
    # Add cookie consent for European sites
    if 'linkedin.com' in url:
        headers['Cookie'] = 'li_gc=MTsyMTsxNjgxODIwODE5OzI7MDIxVytTS3JrVzROL0xaYVpvMGh2Zz09'
    elif 'indeed.com' in url:
        headers['Cookie'] = 'indeed_csrf=; CTK=; OptanonAlertBoxClosed=2023-01-01'
    
    # Use current proxy or get a new one
    current_proxy = proxy if proxy else proxy_manager.get_proxy()
    
    # Add jitter to prevent detection of regular patterns
    jitter = random.uniform(1.0, 5.0)
    time.sleep(jitter)
    
    for attempt in range(retries):
        try:
            # Add increasing delay between retries
            if attempt > 0:
                backoff_delay = (2 ** attempt) + random.uniform(0, 1)
                time.sleep(backoff_delay)
            
            logger.info(f"Requesting {url} with proxy: {current_proxy}")
            response = requests.get(
                url, 
                headers=headers, 
                proxies=current_proxy, 
                timeout=timeout,
                allow_redirects=True
            )
            
            # Check for common blocking indicators
            if response.status_code in [403, 429, 503, 999]:
                logger.warning(f"Possible blocking detected: {response.status_code} from {url}")
                if current_proxy:
                    proxy_manager.mark_failed(current_proxy)
                
                # Try a different proxy
                current_proxy = proxy_manager.get_proxy()
                continue
                
            # Success
            return response
            
        except requests.RequestException as e:
            logger.warning(f"Request failed for {url}: {e}")
            if current_proxy:
                proxy_manager.mark_failed(current_proxy)
            
            # Try a different proxy
            current_proxy = proxy_manager.get_proxy()
    
    # If all retries fail, return a dummy response
    logger.error(f"All retries failed for {url}")
    return None

# Similar function to original
def similar(a, b):
    """Simple similarity function for basic fuzzy matching."""
    a = a.lower()
    b = b.lower()
    
    # If exact match, return 1.0
    if a == b:
        return 1.0
    
    # If b contains a, return 0.8
    if a in b:
        return 0.8
    
    # Count common words
    a_words = set(a.split())
    b_words = set(b.split())
    common_words = a_words.intersection(b_words)
    
    if not a_words:
        return 0.0
    
    # Return ratio of common words to total words in a
    return len(common_words) / len(a_words)

# Store job in database
def store_job(job):
    """Store a job in the database."""
    try:
        conn = sqlite3.connect('ezjobs.db')
        cursor = conn.cursor()
        
        # Check if table exists, if not create it
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                company TEXT,
                location TEXT,
                experience TEXT,
                description TEXT,
                posted_date TEXT,
                url TEXT,
                source TEXT
            )
        ''')
        
        # Create user history table if it doesn't exist
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                job_id INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (job_id) REFERENCES jobs (id)
            )
        ''')
        
        # Check if job with same URL already exists
        cursor.execute('SELECT id FROM jobs WHERE url = ?', (job['url'],))
        existing_job = cursor.fetchone()
        
        if existing_job:
            # Job already exists, don't insert again
            conn.close()
            return existing_job[0]
            
        # Insert job
        cursor.execute('''
            INSERT INTO jobs (title, company, location, experience, description, posted_date, url, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            job['title'],
            job['company'],
            job['location'],
            job.get('experience', 'Not specified'),
            job.get('description', ''),
            job['posted_date'],
            job['url'],
            job['source']
        ))
        
        job_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return job_id
        
    except Exception as e:
        logger.error(f"Error storing job in database: {e}")
        return None

# Record job view
def record_job_view(user_id, job_id):
    """Record when a user views a job."""
    try:
        conn = sqlite3.connect('ezjobs.db')
        cursor = conn.cursor()
        
        # Insert into user history
        cursor.execute('''
            INSERT INTO user_history (user_id, job_id)
            VALUES (?, ?)
        ''', (user_id, job_id))
        
        conn.commit()
        conn.close()
        
    except Exception as e:
        logger.error(f"Error recording job view: {e}")

app = Flask(__name__)
CORS(app)  # if you want CORS enabled

def get_user_job_history(user_id, limit=30):
    conn = sqlite3.connect('ezjobs.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT j.* FROM jobs j
        JOIN user_history h ON j.id = h.job_id
        WHERE h.user_id = ?
        ORDER BY h.timestamp DESC
        LIMIT ?
    ''', (user_id, limit))
    
    jobs = []
    for row in cursor.fetchall():
        jobs.append({
            'id': row[0],
            'title': row[1],
            'company': row[2],
            'location': row[3],
            'experience': row[4],
            'description': row[5],
            'posted_date': row[6],
            'url': row[7],
            'source': row[8]
        })
    
    conn.close()
    return jobs

# Enhanced LinkedIn crawler with better browser emulation
def crawl_linkedin(search_term, location, time_filter, experience_level):
    logger.info(f"Crawling LinkedIn for: {search_term}, location: {location}")
    jobs = []
    
    try:
        # Format search URL
        search_url = f"https://www.linkedin.com/jobs/search/?keywords={quote(search_term)}"
        
        if location and location.lower() not in ['all locations', 'location']:
            if location.lower() == 'remote':
                search_url += "&f_WT=2"
            elif location.lower() == 'onsite':
                search_url += "&f_WT=1"
            else:
                search_url += f"&location={quote(location)}"
        
        # Add time filter
        if time_filter and time_filter.lower() not in ['any time', 'when posted']:
            if '24 hours' in time_filter or 'today' in time_filter.lower():
                search_url += "&f_TPR=r86400"
            elif '3 days' in time_filter:
                search_url += "&f_TPR=r259200"
            elif 'week' in time_filter.lower():
                search_url += "&f_TPR=r604800"
            elif 'month' in time_filter.lower():
                search_url += "&f_TPR=r2592000"
        
        # Add experience level filter
        if experience_level and experience_level.lower() not in ['all experience', 'experience']:
            if experience_level.lower() == 'intern':
                search_url += "&f_E=1"
            elif experience_level.lower() == 'fresher':
                search_url += "&f_E=2"
        
        # Enhance headers with more browser-like behavior
        headers = {
            'User-Agent': get_random_user_agent(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Referer': 'https://www.google.com/search?q=linkedin+jobs',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'cross-site',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0',
            'Cookie': 'li_gc=MTsyMTsxNjgxODIwODE5OzI7MDIxVytTS3JrVzROL0xaYVpvMGh2Zz09; JSESSIONID="ajax:8675309"; lidc="b=VB86:s=V:r=V:a=V:p=V:g=2715:u=1:x=1:i=1620151896:t=1620238296:v=2:sig=AQHZBLn1SXOO3l_caS2dZDnZAlBNIoLr"; lang=v=2&lang=en-us'
        }
        
        # Make the request with anti-blocking measures
        response = make_request(search_url, headers=headers)
        
        if response and response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            job_cards = soup.find_all('div', class_='base-card')
            
            for card in job_cards[:10]:  # Limit to 10 jobs per source
                try:
                    title_elem = card.find('h3', class_='base-search-card__title')
                    company_elem = card.find('h4', class_='base-search-card__subtitle')
                    location_elem = card.find('span', class_='job-search-card__location')
                    date_elem = card.find('time', class_='job-search-card__listdate')
                    link_elem = card.find('a', class_='base-card__full-link')
                    
                    if title_elem and company_elem and link_elem:
                        title = title_elem.text.strip()
                        company = company_elem.text.strip()
                        location_text = location_elem.text.strip() if location_elem else "Not specified"
                        posted_date = date_elem.get('datetime') if date_elem else "Recent"
                        url = link_elem.get('href')
                        
                        job = {
                            'title': title,
                            'company': company,
                            'location': location_text,
                            'posted_date': posted_date,
                            'url': url,
                            'source': 'LinkedIn',
                            'experience': experience_level if experience_level and experience_level.lower() != 'all experience' else 'Not specified'
                        }
                        
                        # Check if job matches search criteria with fuzzy matching
                        if similar(search_term, title) > 0.3 or search_term.lower() in title.lower():
                            jobs.append(job)
                            # Store job in database
                            store_job(job)
                except Exception as e:
                    logger.error(f"Error parsing LinkedIn job card: {e}")
                    continue
        else:
            logger.warning(f"LinkedIn request failed or returned invalid status code")
    
    except Exception as e:
        logger.error(f"Error crawling LinkedIn: {e}")
    
    return jobs

# Enhanced Indeed crawler with better browser emulation
def crawl_indeed(search_term, location, time_filter, experience_level):
    logger.info(f"Crawling Indeed for: {search_term}, location: {location}")
    jobs = []
    
    try:
        # Format search URL
        search_url = f"https://www.indeed.com/jobs?q={quote(search_term)}"
        
        if location and location.lower() not in ['all locations', 'location']:
            if location.lower() == 'remote':
                search_url += "&remotejob=032b3046-06a3-4876-8dfd-474eb5e7ed11"
            else:
                search_url += f"&l={quote(location)}"
        
        # Add time filter
        if time_filter and time_filter.lower() not in ['any time', 'when posted']:
            if '24 hours' in time_filter or 'today' in time_filter.lower():
                search_url += "&fromage=1"
            elif '3 days' in time_filter:
                search_url += "&fromage=3"
            elif 'week' in time_filter.lower():
                search_url += "&fromage=7"
            elif 'month' in time_filter.lower():
                search_url += "&fromage=30"
        
        # Add experience level filter as part of search query
        if experience_level and experience_level.lower() not in ['all experience', 'experience']:
            search_url += f"&sc=0kf%3Ajt({quote(experience_level.lower())})%3B"
        
        # Enhance headers with more browser-like behavior
        headers = {
            'User-Agent': get_random_user_agent(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Referer': 'https://www.google.com/search?q=indeed+jobs',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'cross-site',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0',
            'Cookie': 'CTK=1g6u1h8tut9vh800; INDEED_CSRF_TOKEN=GvR7JsfaNfJ9QJ1nv2Cg7KjA3xw50Rpl; OptanonConsent=isGpcEnabled=0; indeed_rcc="LOCALE_HOME:CTK:LV"; PPID=eyJraWQiOiI4YzM2Z; OptanonAlertBoxClosed=2023-01-01'
        }
        
        # Make the request with anti-blocking measures
        response = make_request(search_url, headers=headers)
        
        if response and response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            job_cards = soup.find_all('div', class_='job_seen_beacon')
            
            for card in job_cards[:10]:  # Limit to 10 jobs per source
                try:
                    title_elem = card.find('h2', class_='jobTitle')
                    company_elem = card.find('span', class_='companyName')
                    location_elem = card.find('div', class_='companyLocation')
                    date_elem = card.find('span', class_='date')
                    
                    if title_elem and title_elem.find('a'):
                        link = title_elem.find('a')
                        job_id = link.get('data-jk', '')
                        url = f"https://www.indeed.com/viewjob?jk={job_id}" if job_id else ""
                        
                        title = title_elem.text.strip()
                        company = company_elem.text.strip() if company_elem else "Not specified"
                        location_text = location_elem.text.strip() if location_elem else "Not specified"
                        posted_date = date_elem.text.strip() if date_elem else "Recent"
                        
                        job = {
                            'title': title,
                            'company': company,
                            'location': location_text,
                            'posted_date': posted_date,
                            'url': url,
                            'source': 'Indeed',
                            'experience': experience_level if experience_level and experience_level.lower() != 'all experience' else 'Not specified'
                        }
                        
                        # Check if job matches search criteria with fuzzy matching
                        if similar(search_term, title) > 0.3 or search_term.lower() in title.lower():
                            jobs.append(job)
                            # Store job in database
                            store_job(job)
                except Exception as e:
                    logger.error(f"Error parsing Indeed job card: {e}")
                    continue
        else:
            logger.warning(f"Indeed request failed or returned invalid status code")
    
    except Exception as e:
        logger.error(f"Error crawling Indeed: {e}")
    
    return jobs

# Enhanced Naukri crawler with better browser emulation
def crawl_naukri(search_term, location, time_filter, experience_level):
    logger.info(f"Crawling Naukri for: {search_term}, location: {location}")
    jobs = []
    
    try:
        # Format search URL
        search_url = f"https://www.naukri.com/jobs-in-india?k={quote(search_term)}"
        
        if location and location.lower() not in ['all locations', 'location']:
            if location.lower() == 'india':
                # Already searching in India
                pass
            elif location.lower() == 'remote':
                search_url += "&wfh=true"
            else:
                search_url += f"&l={quote(location)}"
        
        # Add experience level filter
        if experience_level and experience_level.lower() not in ['all experience', 'experience']:
            if experience_level.lower() == 'fresher':
                search_url += "&exp=0"
            elif experience_level.lower() == 'intern':
                search_url += "&exp=0&it=intern"
            elif experience_level.lower() == 'volunteer':
                search_url += "&ctc=0"
        
        # Enhance headers with more browser-like behavior
        headers = {
            'User-Agent': get_random_user_agent(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Referer': 'https://www.google.com/search?q=naukri+jobs',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'cross-site',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0',
            'Cookie': '_t_ds=178401891653311702-178401891; _t_us=C84A47A2; JSESSIONID=A6CAFD9D1D; _abck=B9CC4E4DED; ak_bmsc=8BF73209; _gat=1; bm_sv=AE2BC'
        }
        
        # Make the request with anti-blocking measures
        response = make_request(search_url, headers=headers)
        
        if response and response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            job_cards = soup.find_all('article', class_='jobTuple')
            
            for card in job_cards[:10]:  # Limit to 10 jobs per source
                try:
                    title_elem = card.find('a', class_='title')
                    company_elem = card.find('a', class_='subTitle')
                    experience_elem = card.find('span', {'title': 'Experience'})
                    location_elem = card.find('span', {'title': 'Location'})
                    date_elem = card.find('span', {'class': 'fleft'})
                    
                    if title_elem and company_elem:
                        title = title_elem.text.strip()
                        company = company_elem.text.strip()
                        url = title_elem.get('href', '')
                        location_text = location_elem.text.strip() if location_elem else "Not specified"
                        exp_text = experience_elem.text.strip() if experience_elem else "Not specified"
                        posted_date = date_elem.text.strip() if date_elem else "Recent"
                        
                        job = {
                            'title': title,
                            'company': company,
                            'location': location_text,
                            'experience': exp_text,
                            'posted_date': posted_date,
                            'url': url,
                            'source': 'Naukri.com'
                        }
                        
                        # Check if job matches search criteria with fuzzy matching
                        if similar(search_term, title) > 0.3 or search_term.lower() in title.lower():
                            jobs.append(job)
                            # Store job in database
                            store_job(job)
                except Exception as e:
                    logger.error(f"Error parsing Naukri job card: {e}")
                    continue
        else:
            logger.warning(f"Naukri request failed or returned invalid status code")
    
    except Exception as e:
        logger.error(f"Error crawling Naukri: {e}")
    
    return jobs

# Enhanced Upwork crawler with better browser emulation
def crawl_upwork(search_term, location, time_filter, experience_level):
    logger.info(f"Crawling Upwork for: {search_term}")
    jobs = []
    
    try:
        # Format search URL
        search_url = f"https://www.upwork.com/search/jobs/?q={quote(search_term)}"
        
        # Add experience level filter
        if experience_level and experience_level.lower() not in ['all experience', 'experience']:
            if experience_level.lower() == 'fresher':
                search_url += "&contractor_tier=ENTRY_LEVEL"
            elif experience_level.lower() == 'intern':
                search_url += "&contractor_tier=ENTRY_LEVEL"
        
        # Enhance headers with more browser-like behavior
        headers = {
            'User-Agent': get_random_user_agent(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Referer': 'https://www.google.com/search?q=upwork+jobs',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'cross-site',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0',
            'Cookie': 'visitor_id=118.189.113.78.1581922140589000; lang=en; cookie_prefix=; cookie_domain=.upwork.com; visitor_id=118.189.113.78.1581922140589