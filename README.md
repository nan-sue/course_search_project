# NYU Course Search

A modern, semantic search engine for NYU courses. This application allows users to discover courses based on their "true meaning" using AI embeddings (Nomic), rather than just simple keywords.

## Features

- **Hybrid Semantic Search**: Combines literal keyword matching (titles/IDs) with AI-powered vector similarity.
- **OTP Authentication**: Secure login via `@nyu.edu` emails using One-Time Passwords (no permanent passwords required).
- **Personalized Experience**: Save courses to your profile and upvote searches that provided helpful results.
- **Live Schedule Integration**: Fetches real-time course timing, locations, and professor data directly from NYU's APIs.
- **Admin Dashboard**: Control center for tracking user statistics and triggering data scrapers.

## Tech Stack

- **Backend**: Python (FastAPI)
- **Frontend**: HTML + HTMX (for dynamic, no-reload interactions)
- **AI/Embeddings**: SentenceTransformers + Nomic Embed Text v1.5
- **Database**: PostgreSQL with the `pgvector` extension
- **Styling**: Vanilla CSS (NYU-branded theme)

## Getting Started

### 1. Prerequisites
- Python 3.10+
- PostgreSQL with `pgvector` installed (`brew install pgvector` on Mac)

### 2. Installation
```bash
# Clone the repository
# git clone <your-repo-url>
# cd course_search_project

# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Environment Variables
Create a `.env` file or export the following:
```env
DATABASE_URL=postgresql://user:password@localhost:5432/course_search
JWT_SECRET=your-secret-key-here
RESEND_API_KEY=optional-api-key-for-emails
```
*Note: If `RESEND_API_KEY` is missing, the app runs in **Dev Mode**, showing OTPs directly on the screen/terminal.*

### 4. Database Setup & Indexing
The database tables are automatically initialized when the app starts. To populate course data:
```bash
python scraper.py
```
*Wait for the scraper to finish; it will encode and index over 15,000 courses.*

### 5. Running the App
```bash
uvicorn main:app --reload
```
Visit [http://localhost:8000](http://localhost:8000) to start searching!

## Project Structure
- `main.py`: Core routing and application logic.
- `auth.py`: OTP and JWT authentication handling.
- `database.py`: Postgres connection and schema initialization.
- `scraper.py`: Asynchronous web crawler for NYU course data.
- `templates/`: HTML structures and HTMX components.
