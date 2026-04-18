"""
Course Scraper Script.

This script fetches course listings directly from the NYU Bulletins website.
It parses the HTML, extracts course details (ID, Title, Description), 
generates a mathematical "vector" embedding using Nomic, and saves 
it all into the PostgreSQL database.
"""
import asyncio
import httpx
from bs4 import BeautifulSoup
from sentence_transformers import SentenceTransformer
from database import pool

BASE_URL = "https://bulletins.nyu.edu"

# Load the AI model used to understand the meaning of the course text.
# We use all-MiniLM-L6-v2 because it is extremely memory efficient (~80MB) for cloud hosting.
print("Loading sentence transformer model (all-MiniLM-L6-v2)...")
model = SentenceTransformer("all-MiniLM-L6-v2")

# Browser-like headers to make the scraper look like a human visitor
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

async def scrape_subjects():
    """
    Step 1: Get a list of all course subjects (e.g., Math, Computer Science)
    by looking at the links on the main courses page.
    """
    async with httpx.AsyncClient(headers=HEADERS) as client:
        response = await client.get(f"{BASE_URL}/courses/")
        soup = BeautifulSoup(response.text, "html.parser")
        
        subjects = []
        # Find all link tags (<a>) where the href starts with "/courses/"
        for a in soup.select('a[href^="/courses/"]'):
            href = a.get("href")
            text = a.get_text(strip=True)
            
            # Avoid the home link itself and prevent duplicates
            if href != "/courses/" and {"href": href, "text": text} not in subjects:
                subjects.append({"href": href, "text": text})
        
        return subjects

async def scrape_courses_for_subject(subject_href: str):
    """
    Step 2: Given a specific subject link, fetch its page and read all 
    listed courses, extracting their codes, titles, and descriptions.
    """
    async with httpx.AsyncClient(headers=HEADERS) as client:
        response = await client.get(f"{BASE_URL}{subject_href}")
        soup = BeautifulSoup(response.text, "html.parser")
        
        courses = []
        # Clean the href to get a simple subject name (e.g., "/courses/math/" -> "math")
        subject_name = subject_href.replace("/courses/", "").replace("/", "")
        
        # Course information is typically grouped inside div elements with the class 'courseblock'
        for block in soup.select('.courseblock'):
            # Extract basic text details based on their HTML classes
            code_el = block.select_one('.detail-code')
            title_el = block.select_one('.detail-title')
            desc_el = block.select_one('.courseblockextra')
            
            # If any essential information is missing, skip this course
            if not code_el or not title_el or not desc_el:
                continue
                
            courses.append({
                "id": code_el.get_text(strip=True),
                "title": title_el.get_text(strip=True),
                "description": desc_el.get_text(strip=True),
                "subject": subject_name
            })
            
        return courses

async def run_scraper():
    """
    Main Execution Function.
    Coordinates fetching subjects, getting their courses, calculating
    embeddings, and saving them to the database.
    """
    print("Scraping subjects...")
    subjects = await scrape_subjects()
    print(f"Found {len(subjects)} subjects.")
    
    # Open the database connection pool specifically for this script run
    await pool.open()
    
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            for subject in subjects:
                print(f"Scraping subject: {subject['text']}")
                courses = await scrape_courses_for_subject(subject["href"])
                
                for c in courses:
                    # Clean input text for the embedding model
                    doc_text = f"{c['title']} {c['description']}"
                    
                    # Convert the text into an array of numbers (the vector embedding)
                    embedding = model.encode(doc_text).tolist()
                    vector_str = "[" + ",".join(map(str, embedding)) + "]"
                    
                    # Save the course. If a course with this ID already exists, update it.
                    await cur.execute("""
                        INSERT INTO courses (id, title, description, subject, embedding)
                        VALUES (%s, %s, %s, %s, %s::vector)
                        ON CONFLICT (id) DO UPDATE SET
                            title = EXCLUDED.title,
                            description = EXCLUDED.description,
                            embedding = EXCLUDED.embedding
                    """, (c["id"], c["title"], c["description"], c["subject"], vector_str))
                    
                await conn.commit()
                print(f"Inserted {len(courses)} courses for {subject['text']}")
                # Wait 1 second before moving to the next subject to be polite to NYU's server
                await asyncio.sleep(1)

# If this script is run directly from the terminal, start the scraper
if __name__ == "__main__":
    asyncio.run(run_scraper())
