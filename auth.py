"""
Authentication Management.

This module handles creating, sending, and verifying One-Time Passwords (OTPs)
so users can log in with their @nyu.edu emails without needing a password.
It also issues JSON Web Tokens (JWT) which securely keep users logged in.
"""
import os
import random
import string
import jwt
from datetime import datetime, timedelta, timezone
from fastapi import Request, HTTPException, Depends
import resend

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
JWT_SECRET = os.environ.get("JWT_SECRET", "super-secret-default-key-do-not-use")

# In-memory OTP store for simplicity (email -> {otp, expiry})
# Note: In a real production app, this should be stored in Redis or Postgres 
# so it isn't lost if the server restarts.
OTP_SESSIONS = {}

def generate_otp() -> str:
    """Generates a random 6-digit string to be used as a one-time password."""
    return ''.join(random.choices(string.digits, k=6))

async def send_otp(email: str):
    """
    Creates an OTP for the provided email and attempts to send it.
    If the email isn't an NYU email, it stops and throws an error.
    """
    # Enforce NYU email requirement
    if not email.endswith("@nyu.edu"):
        raise ValueError("Only @nyu.edu emails are allowed.")
    
    # Create the OTP and set an expiration time of 5 minutes from now
    otp = generate_otp()
    expires = datetime.now() + timedelta(minutes=5)
    
    # Store the OTP internally so we can verify it later
    OTP_SESSIONS[email] = {"otp": otp, "expires": expires}

    # If the developer provided a Resend API key, we send a real email
    if RESEND_API_KEY:
        resend.api_key = RESEND_API_KEY
        resend.Emails.send({
            "from": "onboarding@resend.dev", # Resend's free testing sender
            "to": email,
            "subject": "Your NYU Course Search Login Code",
            "html": f"<p>Your login code is: <strong>{otp}</strong></p>"
        })
        return None
    else:
        # Fallback for local development if you don't have an email API set up.
        # This securely prints the OTP to the terminal running the server, 
        # so you can just copy-paste it without needing an actual email.
        print("*" * 50)
        print(f"RESEND_API_KEY not found. MOCK EMAIL SENT.")
        print(f"TO: {email}")
        print(f"OTP: {otp}")
        print("*" * 50)
        return otp

def verify_otp_and_create_jwt(email: str, otp: str) -> str:
    """
    Checks if the user-provided OTP matches the one we generated and hasn't expired.
    If it matches, it creates and returns a secure JWT (JSON Web Token).
    """
    session = OTP_SESSIONS.get(email)
    
    # Ensure a session exists, the OTP is correct, and it hasn't expired yet
    if not session or session["otp"] != otp or session["expires"] < datetime.now():
        raise ValueError("Invalid or expired OTP.")
    
    # Cleanup: Delete the OTP so it can't be reused
    del OTP_SESSIONS[email]
    
    # Create JWT (A digital passport saying who the user is)
    payload = {
        "sub": email,
        # Token stays valid and keeps the user logged in for 7 days
        "exp": datetime.now(timezone.utc) + timedelta(days=7) 
    }
    
    # The token is signed using our secret key to prevent forgery
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def decode_jwt(token: str):
    """Safely decodes the JWT to view the data inside. Returns None if it fails."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None

async def get_current_user_email(request: Request):
    """
    A FastAPI Dependency. Checks the user's browser cookies for a valid session token.
    Returns their email address if logged in, otherwise returns None.
    """
    # Look for the 'nyu_session' cookie that was set during login
    token = request.cookies.get("nyu_session")
    if not token:
        return None
        
    payload = decode_jwt(token)
    return payload.get("sub") if payload else None

async def require_current_user(request: Request) -> str:
    """
    Similar to get_current_user_email, but throws an HTTP 401 Unauthorized 
    error if the user isn't logged in. Helps protect private routes.
    """
    email = await get_current_user_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return email
