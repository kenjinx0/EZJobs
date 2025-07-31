# Add the missing get_random_user_agent function
import os
import sqlite3
import logging
import random
from flask import Flask, request, jsonify
from flask_cors import CORS
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger()
logging.basicConfig(level=logging.INFO)

# Missing function - add this to fix the error
def get_random_user_agent():
    """Return a random user agent string to use in requests."""
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.107 Safari/537.36',
        'Mozilla/5.0 (iPhone; CPU iPhone OS 14_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1',
        'Mozilla/5.0 (iPad; CPU OS 14_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36 Edg/91.0.864.59',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36'
    ]
    return random.choice(user_agents)

# Also adding a missing similar function for fuzzy matching
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

# Adding another missing function for storing jobs in database
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

# Adding another missing function for recording job views
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

# LinkedIn crawler
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
            
        headers = {
            'User-Agent': get_random_user_agent(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': 'https://www.linkedin.com/',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Cache-Control': 'max-age=0',
        }
        
        response = requests.get(search_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
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
            logger.warning(f"LinkedIn returned status code: {response.status_code}")
    
    except Exception as e:
        logger.error(f"Error crawling LinkedIn: {e}")
    
    return jobs

# Indeed crawler
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
        
        headers = {
            'User-Agent': get_random_user_agent(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': 'https://www.indeed.com/',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        }
        
        response = requests.get(search_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
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
            logger.warning(f"Indeed returned status code: {response.status_code}")
    
    except Exception as e:
        logger.error(f"Error crawling Indeed: {e}")
    
    return jobs

# Naukri.com crawler
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
        
        headers = {
            'User-Agent': get_random_user_agent(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': 'https://www.naukri.com/',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        }
        
        response = requests.get(search_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
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
            logger.warning(f"Naukri returned status code: {response.status_code}")
    
    except Exception as e:
        logger.error(f"Error crawling Naukri: {e}")
    
    return jobs

# Upwork crawler
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
        
        headers = {
            'User-Agent': get_random_user_agent(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': 'https://www.upwork.com/',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        }
        
        response = requests.get(search_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            job_cards = soup.find_all('section', class_='air-card-hover')
            
            for card in job_cards[:10]:  # Limit to 10 jobs per source
                try:
                    title_elem = card.find('h2', class_='job-tile-title')
                    if title_elem and title_elem.find('a'):
                        title_link = title_elem.find('a')
                        title = title_link.text.strip()
                        url = f"https://www.upwork.com{title_link.get('href', '')}"
                        
                        # Extract other details
                        company = "Upwork Client"  # Upwork usually doesn't show company name directly
                        
                        # Look for location and posted date
                        details_container = card.find('div', class_='job-tile-meta')
                        location_text = "Remote"  # Most Upwork jobs are remote
                        posted_date = "Recent"
                        
                        if details_container:
                            time_elem = details_container.find('span', class_='js-posted-time')
                            if time_elem:
                                posted_date = time_elem.text.strip()
                        
                        job = {
                            'title': title,
                            'company': company,
                            'location': location_text,
                            'posted_date': posted_date,
                            'url': url,
                            'source': 'Upwork',
                            'experience': experience_level if experience_level and experience_level.lower() != 'all experience' else 'Not specified'
                        }
                        
                        # Check if job matches search criteria with fuzzy matching
                        if similar(search_term, title) > 0.3 or search_term.lower() in title.lower():
                            jobs.append(job)
                            # Store job in database
                            store_job(job)
                except Exception as e:
                    logger.error(f"Error parsing Upwork job card: {e}")
                    continue
        else:
            logger.warning(f"Upwork returned status code: {response.status_code}")
    
    except Exception as e:
        logger.error(f"Error crawling Upwork: {e}")
    
    return jobs

# API endpoint to get jobs
@app.route('/api/jobs', methods=['GET'])
def get_jobs():
    try:
        # Get parameters from request
        search_term = request.args.get('query', '')
        experience = request.args.get('experience', 'All Experience')
        location = request.args.get('location', 'All Locations')
        posted = request.args.get('posted', 'Any Time')
        user_id = request.args.get('user_id', 'anonymous')
        
        if not search_term:
            return jsonify({"error": "Search term is required"}), 400
        
        # Normalize parameters
        experience = experience if experience != 'Experience' else 'All Experience'
        location = location if location != 'Location' else 'All Locations'
        posted = posted if posted != 'When Posted' else 'Any Time'
        
        # Start crawlers in parallel using ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as executor:
            # Submit crawling tasks
            linkedin_future = executor.submit(crawl_linkedin, search_term, location, posted, experience)
            indeed_future = executor.submit(crawl_indeed, search_term, location, posted, experience)
            naukri_future = executor.submit(crawl_naukri, search_term, location, posted, experience)
            upwork_future = executor.submit(crawl_upwork, search_term, location, posted, experience)
            
            # Get results
            linkedin_jobs = linkedin_future.result()
            indeed_jobs = indeed_future.result()
            naukri_jobs = naukri_future.result()
            upwork_jobs = upwork_future.result()
        
        # Combine all jobs
        all_jobs = linkedin_jobs + indeed_jobs + naukri_jobs + upwork_jobs
        
        # If no exact matches found, try fuzzy search in the database
        if not all_jobs:
            conn = sqlite3.connect('ezjobs.db')
            cursor = conn.cursor()
            
            # Use LIKE query for fuzzy matching from database
            fuzzy_search_term = f"%{search_term}%"
            cursor.execute(
                "SELECT * FROM jobs WHERE title LIKE ? OR description LIKE ? LIMIT 20",
                (fuzzy_search_term, fuzzy_search_term)
            )
            
            for row in cursor.fetchall():
                job = {
                    'id': row[0],
                    'title': row[1],
                    'company': row[2],
                    'location': row[3],
                    'experience': row[4],
                    'description': row[5],
                    'posted_date': row[6],
                    'url': row[7],
                    'source': row[8]
                }
                
                # Apply filters on database results
                if (location == 'All Locations' or location.lower() in job['location'].lower() or 
                    (location.lower() == 'remote' and 'remote' in job['location'].lower()) or
                    (location.lower() == 'onsite' and 'remote' not in job['location'].lower())):
                    
                    if (experience == 'All Experience' or 
                        experience.lower() in job['experience'].lower() or
                        (experience.lower() == 'fresher' and ('0' in job['experience'] or 'entry' in job['experience'].lower())) or
                        (experience.lower() == 'intern' and 'intern' in job['experience'].lower())):
                        
                        all_jobs.append(job)
            
            conn.close()
        
        # Sort by relevance (similarity to search term)
        all_jobs.sort(key=lambda x: similar(search_term, x['title']), reverse=True)
        
        # Return results
        return jsonify({
            "query": search_term,
            "experience": experience,
            "location": location,
            "posted": posted,
            "count": len(all_jobs),
            "jobs": all_jobs
        })
    
    except Exception as e:
        logger.error(f"Error in get_jobs API: {e}")
        return jsonify({"error": str(e)}), 500

# API endpoint to record job view
@app.route('/api/view-job', methods=['POST'])
def view_job():
    try:
        data = request.get_json()
        user_id = data.get('user_id', 'anonymous')
        job_id = data.get('job_id')
        
        if not job_id:
            return jsonify({"error": "Job ID is required"}), 400
        
        # Record job view
        record_job_view(user_id, job_id)
        
        return jsonify({"success": True})
    
    except Exception as e:
        logger.error(f"Error in view_job API: {e}")
        return jsonify({"error": str(e)}), 500

# API endpoint to get user history
@app.route('/api/history', methods=['GET'])
def get_history():
    try:
        user_id = request.args.get('user_id', 'anonymous')
        
        # Get user history
        jobs = get_user_job_history(user_id)
        
        return jsonify({
            "user_id": user_id,
            "count": len(jobs),
            "jobs": jobs
        })
    
    except Exception as e:
        logger.error(f"Error in get_history API: {e}")
        return jsonify({"error": str(e)}), 500

# Run the app
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
