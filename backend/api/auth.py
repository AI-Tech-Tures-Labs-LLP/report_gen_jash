"""Authentication API — register, login, token validation.

Endpoints:
    POST /auth/register  → create user, return JWT
    POST /auth/login     → verify credentials, return JWT
    GET  /auth/me        → validate JWT, return user profile
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

import bcrypt
import jwt
from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel, EmailStr

from core.config import JWT_SECRET, JWT_EXPIRY_DAYS
from db.mongo import get_db

logger = logging.getLogger("auth")
router = APIRouter(prefix="/auth", tags=["auth"])


# ─── Request / Response models ──────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str


class LoginRequest(BaseModel):
    email: str
    password: str


class AuthResponse(BaseModel):
    token: str
    user: dict


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    created_at: str


# ─── Helpers ────────────────────────────────────────────────────────────────

def _hash_password(password: str) -> str:
    """Hash a password with bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _check_password(password: str, hashed: str) -> bool:
    """Verify a password against a bcrypt hash."""
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


def _create_token(user_id: str, email: str) -> str:
    """Create a JWT token with user_id and email."""
    payload = {
        "user_id": user_id,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRY_DAYS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def _decode_token(token: str) -> dict:
    """Decode and validate a JWT token."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def get_current_user(authorization: str = Header(None)) -> dict:
    """FastAPI dependency: extract and validate the current user from the Authorization header."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.split(" ", 1)[1]
    payload = _decode_token(token)
    return {"user_id": payload["user_id"], "email": payload["email"]}


# ─── Endpoints ──────────────────────────────────────────────────────────────

@router.post("/register")
def register(req: RegisterRequest):
    """Create a new user account."""
    db = get_db()

    # Validate input
    if not req.email or not req.password or not req.name:
        raise HTTPException(status_code=400, detail="All fields are required")
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    # Check if email already exists
    existing = db.users.find_one({"email": req.email.lower().strip()})
    if existing:
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    # Create user
    now = datetime.now(timezone.utc)
    user_doc = {
        "email": req.email.lower().strip(),
        "password_hash": _hash_password(req.password),
        "name": req.name.strip(),
        "created_at": now,
    }
    result = db.users.insert_one(user_doc)
    user_id = str(result.inserted_id)

    # Generate token
    token = _create_token(user_id, user_doc["email"])

    return {
        "token": token,
        "user": {
            "id": user_id,
            "email": user_doc["email"],
            "name": user_doc["name"],
            "created_at": now.isoformat(),
        },
    }


@router.post("/login")
def login(req: LoginRequest):
    """Authenticate a user and return a JWT token."""
    db = get_db()

    # Find user
    user = db.users.find_one({"email": req.email.lower().strip()})
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Check password
    if not _check_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    user_id = str(user["_id"])
    token = _create_token(user_id, user["email"])

    return {
        "token": token,
        "user": {
            "id": user_id,
            "email": user["email"],
            "name": user["name"],
            "created_at": user["created_at"].isoformat() if user.get("created_at") else None,
        },
    }


@router.get("/me")
def me(current_user: dict = Depends(get_current_user)):
    """Return the current user's profile (validates the JWT)."""
    db = get_db()
    from bson import ObjectId

    user = db.users.find_one({"_id": ObjectId(current_user["user_id"])})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "id": str(user["_id"]),
        "email": user["email"],
        "name": user["name"],
        "created_at": user["created_at"].isoformat() if user.get("created_at") else None,
    }
