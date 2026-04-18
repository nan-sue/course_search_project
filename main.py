"""
Main Web Server Application.

This file uses FastAPI to run the website. It handles website routing, 
rendering HTMX templates, searching the database, and processing logins.
"""
import json
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Form, Depends, HTTPException, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import httpx
from sentence_transformers import SentenceTransformer

from database import init_db, pool
from auth import send_otp, verify_otp_and_create_jwt, get_current_user_email
from scraper import run_scraper
import asyncio

# ---------------------------------------------------------
# Artificial Intelligence Model Initialization
# ---------------------------------------------------------
# We use all-MiniLM-L6-v2 because it is extremely memory efficient (~80MB) for cloud hosting.
print("Loading sentence transformer model (all-MiniLM-L6-v2)...")
model = SentenceTransformer("all-MiniLM-L6-v2")

# ---------------------------------------------------------
# Application Setup
# ---------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Code inside here runs exactly once when the server starts up.
    We use it to initialize our Postgres database tables.
    """
    await init_db()
    yield
    # Close the database connections when the app shuts down
    await pool.close()

# Create the FastAPI app and tell it where our HTML pages live
app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory="templates")

# ---------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------
def get_recent_terms():
    """Calculates recent NYU academic term codes (used to fetch live class schedules)."""
    yr = datetime.now().year
    yys = [yr % 100, (yr - 1) % 100, (yr - 2) % 100]
    terms = []
    for yy in yys:
        for d in ['8', '6', '4', '2']:
            terms.append(f"1{yy:02d}{d}")
    return terms


# ---------------------------------------------------------
# Page Routes (URLs users can visit)
# ---------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def home(request: Request, email: str = Depends(get_current_user_email)):
    """Serves the main search page. Automatically checks if the user is logged in."""
    return templates.TemplateResponse(request=request, name="index.html", context={"request": request, "email": email})

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Serves the login page interface."""
    return templates.TemplateResponse(request=request, name="login.html", context={"request": request})

@app.get("/my-courses", response_class=HTMLResponse)
async def my_courses(request: Request, email: str = Depends(get_current_user_email)):
    """Shows a user's saved courses. Redirects to login if they aren't logged in."""
    if not email:
        return RedirectResponse("/login")
        
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # 1. Look up the user's ID using their email
            await cur.execute("SELECT id FROM users WHERE email=%s;", (email,))
            u_row = await cur.fetchone()
            if not u_row: return RedirectResponse("/login")
            uid = u_row[0]

            # 2. Fetch courses they have saved
            await cur.execute("""
                SELECT c.id, c.title, c.description, c.subject,
                       true as is_saved,
                       EXISTS(SELECT 1 FROM upvotes u WHERE u.course_id = c.id AND u.user_id = %s) as is_upvoted
                FROM courses c
                JOIN saved_courses s ON c.id = s.course_id
                WHERE s.user_id = %s
            """, (uid, uid))
            
            rows = await cur.fetchall()
            
            # Format results into a list of dictionaries for easier HTMX rendering
            courses = []
            for r in rows:
                courses.append({
                    "id": r[0], "title": r[1], "description": r[2], 
                    "subject": r[3], "is_saved": r[4], "is_upvoted": r[5]
                })

    return templates.TemplateResponse(request=request, name="my_courses.html", context={
        "request": request, "courses": courses, "email": email, "query": ""
    })

@app.get("/admin", response_class=HTMLResponse)
async def admin_panel(request: Request, email: str = Depends(get_current_user_email)):
    """Administrator dashboard showing database statistics."""
    if not email:
        return RedirectResponse("/login")
        
    stats = {}
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # Verify the user actually has administrator privileges
            await cur.execute("SELECT is_admin FROM users WHERE email=%s;", (email,))
            u_row = await cur.fetchone()
            if not u_row or not u_row[0]: 
                raise HTTPException(status_code=403, detail="Forbidden: You are not an admin.")

            # Collect metrics to show on the dashboard
            await cur.execute("SELECT count(*) FROM users")
            stats["users"] = (await cur.fetchone())[0]
            await cur.execute("SELECT count(*) FROM courses")
            stats["courses"] = (await cur.fetchone())[0]
            await cur.execute("SELECT count(*) FROM saved_courses")
            stats["saved"] = (await cur.fetchone())[0]
            await cur.execute("SELECT count(*) FROM upvotes")
            stats["upvotes"] = (await cur.fetchone())[0]

    return templates.TemplateResponse(request=request, name="admin.html", context={
        "request": request, "stats": stats, "email": email
    })


# ---------------------------------------------------------
# Dynamic API Routes (Called in the background via HTMX)
# ---------------------------------------------------------

@app.post("/auth/send-otp", response_class=HTMLResponse)
async def handle_send_otp(request: Request, email: str = Form(...)):
    """Step 1 of Login: Ask the server to email a code to the user."""
    try:
        mock_otp = await send_otp(email)
        # Returns just the HTML component for the OTP input, not a full page refresh
        return templates.TemplateResponse(request=request, name="components/otp_input.html", context={"request": request, "email": email, "mock_otp": mock_otp})
    except ValueError as e:
        return f"<div style='margin-bottom:20px; color:red; font-weight:bold;'>{e}</div>"

@app.post("/auth/verify", response_class=HTMLResponse)
async def handle_verify_otp(request: Request, email: str = Form(...), otp: str = Form(...)):
    """Step 2 of Login: Securely check if the code the user typed is correct."""
    try:
        # verify_otp_and_create_jwt throws an error if it's invalid
        token = verify_otp_and_create_jwt(email, otp)
        
        # Make sure this user exists in our database so they can save courses later
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("INSERT INTO users (email) VALUES (%s) ON CONFLICT DO NOTHING RETURNING id;", (email,))
                await conn.commit()

        # Create a response that sets the secure cookie, and tell the browser to redirect to home
        response = Response(status_code=204)
        response.set_cookie("nyu_session", token, httponly=True)
        response.headers["HX-Redirect"] = "/"
        return response
    except ValueError as e:
        return f"<div style='color:red;'>{e}</div>"

@app.post("/auth/logout")
async def logout():
    """Clears the session cookie, cleanly logging out the user."""
    response = Response(status_code=204)
    response.delete_cookie("nyu_session")
    response.headers["HX-Redirect"] = "/"
    return response


@app.post("/search", response_class=HTMLResponse)
async def search(request: Request, q: str = Form(...), email: str = Depends(get_current_user_email)):
    """
    The Core Engine: Processes a user's search using Hybrid Vector Search.
    Finds classes whose meaning matches the user's text query.
    """
    if not q:
        return "<div>Please enter a query.</div>"
    
    # 1. Convert their search query into a mathematical vector representation
    doc_text = f"{q}"
    embedding = model.encode(doc_text).tolist()
    # Format the Python list into a Postgres-compatible vector string
    vector_str = "[" + ",".join(map(str, embedding)) + "]"
    
    # 2. Get User ID if logged in (so we know if they have saved buttons pressed)
    user_id = None
    if email:
        async with pool.connection() as c:
            async with c.cursor() as cur:
                await cur.execute("SELECT id FROM users WHERE email=%s;", (email,))
                row = await cur.fetchone()
                if row: user_id = row[0]
    
    # 3. Perform Hybrid Search
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # We sort exact matches of Course Title or ID to the very top.
            # Then we use <=> (cosine distance operator) to sort everything else 
            # based on purely how 'close' the vectors are in meaning.
            await cur.execute("""
                SELECT c.id, c.title, c.description, c.subject,
                       EXISTS(SELECT 1 FROM saved_courses s WHERE s.course_id = c.id AND s.user_id = %s) as is_saved,
                       EXISTS(SELECT 1 FROM upvotes u WHERE u.course_id = c.id AND u.user_id = %s) as is_upvoted
                FROM courses c
                ORDER BY 
                    CASE 
                        WHEN c.id ILIKE %s THEN 0 
                        WHEN c.title ILIKE %s THEN 1 
                        ELSE 2 
                    END ASC,
                    c.embedding <=> %s::vector ASC
                LIMIT 50;
            """, (user_id, user_id, f"%{q}%", f"%{q}%", vector_str))
            
            rows = await cur.fetchall()
            
            # Format the output for our Jinja HTML Template
            courses = []
            for r in rows:
                courses.append({
                    "id": r[0], "title": r[1], "description": r[2], 
                    "subject": r[3], "is_saved": r[4], "is_upvoted": r[5]
                })

    # Return just the HTML required for the search results
    return templates.TemplateResponse(request=request, name="components/results.html", context={
        "request": request, "courses": courses, "email": email, "query": q
    })

@app.get("/course/details", response_class=HTMLResponse)
async def course_details(request: Request, code: str):
    """Fetches real-time class scheduling details (days, times, professors) from NYU."""
    terms = get_recent_terms()
    details = None
    
    async with httpx.AsyncClient() as client:
        # We try multiple recent academic terms (Fall, Spring, Summer) until we find a match
        for term in terms:
            try:
                # Ask the NYU API if the class code is active this term
                s_res = await client.post("https://bulletins.nyu.edu/class-search/api/?page=fose&route=search", json={
                    "other": {"srcdb": term},
                    "criteria": [{"field": "alias", "value": code}]
                })
                s_data = s_res.json()
                
                # If we got results, fetch the deeper schedule details for that specific class ID (CRN)
                if s_data.get("results"):
                    crns = [r["crn"] for r in s_data["results"] if r.get("code") == code]
                    if crns:
                        d_res = await client.post("https://bulletins.nyu.edu/class-search/api/?page=fose&route=details", json={
                            "group": f"code:{code}",
                            "key": "",
                            "srcdb": term,
                            "matched": f"crn:{','.join(crns)}"
                        })
                        d_data = d_res.json()
                        if d_data:
                            details = d_data
                            details["_foundInTerm"] = term
                            break
            except Exception:
                continue
                
    if not details:
        return "<div class='details-box'>Could not load live schedule details from API.</div>"
        
    # Return the HTML displaying the schedule
    return templates.TemplateResponse(request=request, name="components/course_details.html", context={"request": request, "details": details})


@app.post("/course/action", response_class=HTMLResponse)
async def course_action(request: Request, course_id: str = Form(...), action: str = Form(...), q: str = Form(''), email: str = Depends(get_current_user_email)):
    """Handles Save and Upvote button clikcks on the search results."""
    # Prevent anonymous users from saving features
    if not email:
        return "<span style='color:red;'>Login required</span>"
    
    is_active = False
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # 1. Fetch User ID
            await cur.execute("SELECT id FROM users WHERE email=%s;", (email,))
            u_row = await cur.fetchone()
            if not u_row: return "User missing"
            uid = u_row[0]

            # 2. Track what button the user clicked
            if action == 'save':
                await cur.execute("INSERT INTO saved_courses (user_id, course_id) VALUES (%s, %s) ON CONFLICT DO NOTHING;", (uid, course_id))
                is_active = True
            elif action == 'unsave':
                await cur.execute("DELETE FROM saved_courses WHERE user_id=%s AND course_id=%s;", (uid, course_id))
                is_active = False
            elif action == 'upvote':
                await cur.execute("INSERT INTO upvotes (user_id, course_id, search_query) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING;", (uid, course_id, q))
                is_active = True

            await conn.commit()

    # Return the refreshed button component instead of just text.
    # This ensures hx-vals and styles are correctly updated on the page.
    return templates.TemplateResponse(
        request=request, 
        name="components/action_button.html", 
        context={
            "course_id": course_id, 
            "type": 'upvote' if action == 'upvote' else 'save', 
            "is_active": is_active,
            "query": q
        }
    )


@app.post("/admin/scrape")
async def trigger_scrape(request: Request, email: str = Depends(get_current_user_email)):
    """Allows an administrator to manually launch the web scraper to update courses."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT is_admin FROM users WHERE email=%s;", (email,))
            u_row = await cur.fetchone()
            if not u_row or not u_row[0]: raise HTTPException(status_code=403, detail="Forbidden: Admin access required")
    
    # Run the web scraper in the background without blocking the response
    asyncio.create_task(run_scraper())
    return "<button class='btn' disabled>Scraper is running...</button>"
