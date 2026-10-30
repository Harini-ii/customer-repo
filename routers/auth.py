from uuid import UUID
import os
import hashlib
import hmac
import secrets
import traceback
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import AliasChoices, BaseModel, Field
from jose import jwt, JWTError
from sqlalchemy import text
from database import engine
from rate_limiter import limiter

router = APIRouter(
    prefix="/api/auth",
    tags=["Authentication"],
)
security = HTTPBearer(
    auto_error=False,
    scheme_name="AccessToken",
)
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "my-super-secret-key")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7
PASSWORD_ITERATIONS = 600000
RESET_TOKEN_EXPIRE_MINUTES = 10


class RegisterRequest(BaseModel):
    name: str | None = Field(default=None, validation_alias=AliasChoices('fullName', 'full_name', 'name'))
    email: str | None = None
    phone_number: str | None = Field(default=None, validation_alias=AliasChoices('mobile', 'phone_number'))
    password: str | None = None
    confirm_password: str | None = Field(default=None, validation_alias=AliasChoices('confirmPassword', 'confirm_password'))


class LoginRequest(BaseModel):
    email: str | None = None
    password: str | None = None


class ForgotPasswordRequest(BaseModel):
    email: str | None = None


class ResetPasswordRequest(BaseModel):
    reset_token: str | None = None
    new_password: str | None = Field(default=None, validation_alias=AliasChoices('new_password', 'password'))
    confirm_password: str | None = Field(default=None, validation_alias=AliasChoices('confirmPassword', 'confirm_password'))


class RefreshRequest(BaseModel):
    refresh_token: str | None = None


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PASSWORD_ITERATIONS,
    ).hex()
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}" f"${salt}${password_hash}"


def verify_password(password: str, stored_password: str) -> bool:
    if not stored_password:
        return False
    if stored_password.startswith("pbkdf2_sha256$"):
        try:
            _, iterations, salt, expected_hash = stored_password.split("$", 3)
            actual_hash = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                salt.encode("utf-8"),
                int(iterations),
            ).hex()
            return hmac.compare_digest(
                actual_hash,
                expected_hash,
            )
        except (ValueError, TypeError):
            return False
    return hmac.compare_digest(password, stored_password)


def hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_credit_balance_from_connection(
    connection,
    customer_id: UUID,
) -> int:
    result = connection.execute(
        text("""
            SELECT COALESCE(
                SUM(
                    CASE
                        WHEN LOWER(transaction_type) IN (
                            'subscription',
                            'credit_purchase',
                            'refund'
                        )
                            THEN ABS(amount)
                        WHEN LOWER(transaction_type) = 'usage'
                            THEN -ABS(amount)
                        ELSE amount
                    END
                ),
                0
            ) AS credit_balance
            FROM customer_hj."CUSTOMER_CREDIT_TRANSACTIONS"
            WHERE customer_id = :customer_id
        """),
        {"customer_id": customer_id},
    )
    return int(result.scalar() or 0)


def get_customer_by_id(customer_id: UUID):
    with engine.connect() as connection:
        row = (
            connection.execute(
                text("""
                SELECT
                    id AS customer_id,
                    full_name AS name,
                    email,
                    country, company, profile_url, mobile AS phone_number
                FROM customer_hj."CUSTOMER_PROFILE"
                WHERE id = :customer_id
            """),
                {"customer_id": customer_id},
            )
            .mappings()
            .first()
        )
        return row


def format_customer(row, credits: int | None = None) -> dict:
    if credits is None:
        with engine.connect() as connection:
            credits = get_credit_balance_from_connection(
                connection,
                row["customer_id"],
            )
    return {
        "id": str(row["customer_id"]),
        "full_name": row["name"],
        "email": row["email"],
        "mobile": row["phone_number"],
        "country": row.get("country"),
        "company": row.get("company"),
        "avatar_url": row.get("avatar_url") or row.get("profile_url"),
        "role": "customer",
        "credits": credits,
    }


def create_access_token(user: dict) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user["id"]),
        "email": user["email"],
        "role": user.get("role", "customer"),
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(
        payload,
        SECRET_KEY,
        algorithm=ALGORITHM,
    )


def create_refresh_token(user: dict) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user["id"]),
        "email": user["email"],
        "role": user.get("role", "customer"),
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
    }
    return jwt.encode(
        payload,
        SECRET_KEY,
        algorithm=ALGORITHM,
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
):
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Access token is required",
        )
    try:
        payload = jwt.decode(
            credentials.credentials,
            SECRET_KEY,
            algorithms=[ALGORITHM],
        )
        if payload.get("type") != "access":
            raise HTTPException(
                status_code=401,
                detail="A valid access token is required",
            )
        user_id = UUID(str(payload.get("sub")))
    except (JWTError, TypeError, ValueError):
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired access token",
        )
    row = get_customer_by_id(user_id)
    if row is None:
        raise HTTPException(
            status_code=401,
            detail="Token user is no longer valid",
        )
    return format_customer(row)


@router.post("/register", summary="Register")
@limiter.limit("50/minute")
def register(request: Request, user: RegisterRequest):
    if not user.name or not user.name.strip():
        raise HTTPException(
            status_code=400,
            detail="Name is required",
        )
    if not user.email or not user.email.strip():
        raise HTTPException(
            status_code=400,
            detail="Email is required",
        )
    if not user.phone_number or not user.phone_number.strip():
        raise HTTPException(
            status_code=400,
            detail="Phone number is required",
        )
    if not user.password:
        raise HTTPException(
            status_code=400,
            detail="Password is required",
        )
    if not user.confirm_password:
        raise HTTPException(
            status_code=400,
            detail="Confirm password is required",
        )
    if user.password != user.confirm_password:
        raise HTTPException(
            status_code=400,
            detail="Password and confirm password do not match",
        )
    if len(user.password) < 6:
        raise HTTPException(
            status_code=400,
            detail="Password must contain at least 6 characters",
        )
    name = user.name.strip()
    email = user.email.strip().lower()
    phone_number = user.phone_number.strip()
    try:
        with engine.begin() as connection:
            existing_email = connection.execute(
                text("""
                    SELECT id AS customer_id
                    FROM customer_hj."CUSTOMER_PROFILE"
                    WHERE LOWER(email) = :email
                """),
                {"email": email},
            ).first()
            if existing_email:
                raise HTTPException(
                    status_code=400,
                    detail="Email already exists",
                )
            existing_phone = connection.execute(
                text("""
                    SELECT id AS customer_id
                    FROM customer_hj."CUSTOMER_PROFILE"
                    WHERE mobile = :phone_number
                """),
                {"phone_number": phone_number},
            ).first()
            if existing_phone:
                raise HTTPException(
                    status_code=400,
                    detail="Phone number already exists",
                )
            connection.execute(
                text("""
                    INSERT INTO customer_hj."CUSTOMER_PROFILE" (
                        full_name,
                        email,
                        mobile,
                        password,
                        created_at
                    )
                    VALUES (
                        :name,
                        :email,
                        :phone_number,
                        :password,
                        :created_at
                    )
                """),
                {
                    "name": name,
                    "email": email,
                    "phone_number": phone_number,
                    "password": hash_password(user.password),
                    "created_at": datetime.now(),
                },
            )
    except HTTPException:
        raise
    except Exception:
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail="Could not register customer",
        )
    return {
        "message": "Customer registration successful",
    }


@router.post("/login", summary="Login")
@limiter.limit("50/minute")
def login(request: Request, user: LoginRequest):
    if not user.email or not user.email.strip():
        raise HTTPException(
            status_code=400,
            detail="Email is required",
        )
    if not user.password:
        raise HTTPException(
            status_code=400,
            detail="Password is required",
        )
    email = user.email.strip().lower()
    try:
        with engine.begin() as connection:
            row = (
                connection.execute(
                    text("""
                    SELECT
                        id AS customer_id,
                        full_name AS name,
                        email,
                        country, company, profile_url, mobile AS phone_number,
                        password
                    FROM customer_hj."CUSTOMER_PROFILE"
                    WHERE LOWER(email) = :email
                """),
                    {"email": email},
                )
                .mappings()
                .first()
            )
            if row is None or not verify_password(
                user.password,
                row["password"] or "",
            ):
                raise HTTPException(
                    status_code=401,
                    detail="Invalid email or password",
                )
            stored_password = row["password"] or ""
            if not stored_password.startswith("pbkdf2_sha256$"):
                connection.execute(
                    text("""
                        UPDATE customer_hj."CUSTOMER_PROFILE"
                        SET password = :password
                        WHERE id = :customer_id
                    """),
                    {
                        "password": hash_password(user.password),
                        "customer_id": row["customer_id"],
                    },
                )
            credits = get_credit_balance_from_connection(
                connection,
                row["customer_id"],
            )
            customer = format_customer(row, credits)
    except HTTPException:
        raise
    except Exception:
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail="Could not log in",
        )
    return {
        "message": "Login successful",
        "logged_in": True,
        "access_token": create_access_token(customer),
        "refresh_token": create_refresh_token(customer),
        "token_type": "bearer",
        "customer": customer,
    }


@router.post("/refresh", summary="Refresh Token")
@limiter.limit("50/minute")
def refresh_token(
    request: Request,
    data: RefreshRequest,
):
    if not data.refresh_token:
        raise HTTPException(
            status_code=400,
            detail="Refresh token is required",
        )
    try:
        payload = jwt.decode(
            data.refresh_token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
        )
        if payload.get("type") != "refresh":
            raise HTTPException(
                status_code=401,
                detail="This is not a refresh token",
            )
        user_id = UUID(str(payload.get("sub")))
    except HTTPException:
        raise
    except (JWTError, TypeError, ValueError):
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired refresh token",
        )
    row = get_customer_by_id(user_id)
    if row is None:
        raise HTTPException(
            status_code=401,
            detail="Token user is no longer valid",
        )
    customer = format_customer(row)
    return {
        "access_token": create_access_token(customer),
        "token_type": "bearer",
    }


@router.post("/forgot-password", summary="Forgot Password")
@limiter.limit("50/minute")
def forgot_password(
    request: Request,
    data: ForgotPasswordRequest,
):
    if not data.email or not data.email.strip():
        raise HTTPException(
            status_code=400,
            detail="Email is required",
        )
    email = data.email.strip().lower()
    try:
        with engine.begin() as connection:
            row = connection.execute(
                text("""
                    SELECT id AS customer_id
                    FROM customer_hj."CUSTOMER_PROFILE"
                    WHERE LOWER(email) = :email
                """),
                {"email": email},
            ).first()
            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail="Email not found",
                )
            customer_id = row[0]
            connection.execute(
                text("""
                    UPDATE customer_hj.customer_password_reset_tokens
                    SET used = TRUE
                    WHERE customer_id = :customer_id
                      AND used = FALSE
                """),
                {"customer_id": customer_id},
            )
            reset_token = secrets.token_urlsafe(32)
            token_hash = hash_reset_token(reset_token)
            connection.execute(
                text("""
                    INSERT INTO customer_hj.customer_password_reset_tokens (
                        customer_id,
                        token_hash,
                        expires_at,
                        used,
                        created_at
                    )
                    VALUES (
                        :customer_id,
                        :token_hash,
                        (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
                            + (:expiry_minutes * INTERVAL '1 minute'),
                        FALSE,
                        CURRENT_TIMESTAMP AT TIME ZONE 'UTC'
                    )
                """),
                {
                    "customer_id": customer_id,
                    "token_hash": token_hash,
                    "expiry_minutes": RESET_TOKEN_EXPIRE_MINUTES,
                },
            )
    except HTTPException:
        raise
    except Exception:
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail="Could not generate password reset token",
        )
    return {
        "message": "Password reset token generated",
        "email": email,
        "reset_token": reset_token,
        "expires_in_minutes": RESET_TOKEN_EXPIRE_MINUTES,
    }


@router.post("/reset-password", summary="Reset Password")
@limiter.limit("50/minute")
def reset_password(
    request: Request,
    data: ResetPasswordRequest,
):
    if not data.reset_token:
        raise HTTPException(
            status_code=400,
            detail="Reset token is required",
        )
    if not data.new_password:
        raise HTTPException(
            status_code=400,
            detail="New password is required",
        )
    if not data.confirm_password:
        raise HTTPException(
            status_code=400,
            detail="Confirm password is required",
        )
    if data.new_password != data.confirm_password:
        raise HTTPException(
            status_code=400,
            detail="New password and confirm password do not match",
        )
    if len(data.new_password) < 6:
        raise HTTPException(
            status_code=400,
            detail="Password must contain at least 6 characters",
        )
    token_hash = hash_reset_token(data.reset_token)
    try:
        with engine.begin() as connection:
            reset_row = (
                connection.execute(
                    text("""
                    SELECT reset_token_id, customer_id
                    FROM customer_hj.customer_password_reset_tokens
                    WHERE token_hash = :token_hash
                      AND used = FALSE
                      AND expires_at > (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
                    FOR UPDATE
                """),
                    {"token_hash": token_hash},
                )
                .mappings()
                .first()
            )
            if reset_row is None:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid, expired, or already used reset token",
                )
            customer_id = reset_row["customer_id"]
            result = connection.execute(
                text("""
                    UPDATE customer_hj."CUSTOMER_PROFILE"
                    SET password = :password
                    WHERE id = :customer_id
                """),
                {
                    "password": hash_password(data.new_password),
                    "customer_id": customer_id,
                },
            )
            if result.rowcount == 0:
                raise HTTPException(
                    status_code=404,
                    detail="Customer not found",
                )
            connection.execute(
                text("""
                    UPDATE customer_hj.customer_password_reset_tokens
                    SET used = TRUE
                    WHERE reset_token_id = :reset_token_id
                """),
                {
                    "reset_token_id": reset_row["reset_token_id"],
                },
            )
    except HTTPException:
        raise
    except Exception:
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail="Could not reset password",
        )
    return {
        "message": "Password reset successful",
    }


# @router.get("/status", summary="Login Status")
# def login_status(
#     current_user: dict = Depends(get_current_user),
# ):
#     return {
#         "logged_in": True,
#         "customer": current_user,
#     }
