
from uuid import UUID
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import get_db


router = APIRouter(
    prefix="/api/customers",
    tags=["Customer"],
)


# --------------------------------------------------
# ENUMS
# --------------------------------------------------

class AircraftType(str, Enum):
    flight = "Flight"
    helicopter = "Helicopter"


class AircraftModel(str, Enum):
    airbus_h125 = "Airbus H125"
    bell_407 = "Bell 407"
    leonardo_aw109 = "Leonardo AW109"
    cessna_citation = "Cessna Citation"
    hawker_800xp = "Hawker 800XP"
    citation_xls = "Citation XLS"


class Currency(str, Enum):
    inr = "INR"
    usd = "USD"
    eur = "EUR"
    gbp = "GBP"
    aed = "AED"
    sgd = "SGD"


# --------------------------------------------------
# CUSTOMER PREFERENCES MODEL
# --------------------------------------------------

class CustomerPreferences(BaseModel):
    customer_id: UUID | None = None
    aircraft_type: AircraftType | None = None
    aircraft_model: AircraftModel | None = None
    travellers: int | None = Field(default=None, gt=0)
    budget: float | None = Field(default=None, gt=0)
    currency: Currency | None = None
    origin: str | None = None
    destination: str | None = None
    travel_date: str | None = None


# --------------------------------------------------
# HELPER: GET PREFERENCES FOR A CUSTOMER
# --------------------------------------------------

def get_preferences(db: Session, customer_id: UUID):
    row = (
        db.execute(
            text("""
                SELECT
                    aircraft_type,
                    aircraft_model,
                    travellers,
                    budget,
                    currency,
                    origin,
                    destination,
                    travel_date
                FROM customer_hj."CUSTOMER_PREFERENCES"
                WHERE customer_id = :customer_id
            """),
            {"customer_id": customer_id},
        )
        .mappings()
        .first()
    )

    if row is None:
        return {}

    result = dict(row)

    if result["budget"] is not None:
        result["budget"] = float(result["budget"])

    return result


# --------------------------------------------------
# HELPER: FORMAT CUSTOMER RESPONSE
# --------------------------------------------------

def format_customer(db: Session, customer):
    return {
        "id": str(customer["customer_id"]),
        "full_name": customer["name"],
        "email": customer["email"],
        "mobile": customer["phone_number"],
        "country": customer.get("country"),
        "company": customer.get("company"),
        "avatar_url": customer.get("avatar_url") or customer.get("profile_url"),
        "role": "customer",
        "credits": None,
        "preferences": get_preferences(
            db,
            customer["customer_id"],
        ),
    }


# --------------------------------------------------
# GET ALL CUSTOMERS
# GET /api/customers/
# --------------------------------------------------

@router.get(
    "/",
    summary="Get All Customers",
)
def get_all_customers(
    db: Session = Depends(get_db),
):
    rows = (
        db.execute(
            text("""
                SELECT
                    id AS customer_id,
                    full_name AS name,
                    email,
                    country, company, profile_url, mobile AS phone_number
                FROM customer_hj."CUSTOMER_PROFILE"
                ORDER BY id
            """)
        )
        .mappings()
        .all()
    )

    return {
        "total": len(rows),
        "customers": [
            format_customer(db, row)
            for row in rows
        ],
    }


# --------------------------------------------------
# GET CUSTOMER BY CUSTOMER ID
# GET /api/customers/get?customer_id=<UUID>
# --------------------------------------------------

@router.get(
    "/get",
    summary="Get Customer by ID",
)
def get_customer(
    customer_id: UUID,
    db: Session = Depends(get_db),
):
    row = (
        db.execute(
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

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Customer not found",
        )

    return {
        "customer": format_customer(db, row),
    }


# --------------------------------------------------
# CREATE OR UPDATE CUSTOMER PREFERENCES
# PUT /api/customers/preferences
# --------------------------------------------------

@router.put(
    "/preferences",
    summary="Create or Update Customer Preferences",
)
def update_customer_preferences(
    preferences: CustomerPreferences,
    db: Session = Depends(get_db),
):
    customer_id = preferences.customer_id

    if customer_id is None:
        raise HTTPException(
            status_code=400,
            detail="Customer ID is required",
        )

    # Check whether the customer exists.
    customer = db.execute(
        text("""
            SELECT id AS customer_id
            FROM customer_hj."CUSTOMER_PROFILE"
            WHERE id = :customer_id
        """),
        {"customer_id": customer_id},
    ).first()

    if customer is None:
        raise HTTPException(
            status_code=404,
            detail="Customer not found",
        )

    # Get preference fields supplied in the request.
    values = preferences.model_dump(
        exclude={"customer_id"},
        exclude_none=True,
    )

    # Convert Enum values into strings.
    values = {
        key: value.value if isinstance(value, Enum) else value
        for key, value in values.items()
    }

    # If no preference fields were supplied, return current preferences.
    if not values:
        return {
            "message": "No preference fields provided",
            "customer_id": str(customer_id),
            "preferences": get_preferences(db, customer_id),
        }

    # Check whether this customer already has a preferences record.
    existing = db.execute(
        text("""
            SELECT preference_id
            FROM customer_hj."CUSTOMER_PREFERENCES"
            WHERE customer_id = :customer_id
        """),
        {"customer_id": customer_id},
    ).first()

    try:
        if existing:
            # Update existing preferences.
            assignments = ", ".join(
                f"{field} = :{field}"
                for field in values
            )

            query = text(f"""
                UPDATE customer_hj."CUSTOMER_PREFERENCES"
                SET {assignments}
                WHERE customer_id = :customer_id
            """)

        else:
            # Insert a new preferences record.
            columns = ", ".join(
                ["customer_id", *values.keys()]
            )

            placeholders = ", ".join(
                [
                    ":customer_id",
                    *[f":{field}" for field in values],
                ]
            )

            query = text(f"""
                INSERT INTO customer_hj."CUSTOMER_PREFERENCES" (
                    {columns}
                )
                VALUES (
                    {placeholders}
                )
            """)

        db.execute(
            query,
            {
                "customer_id": customer_id,
                **values,
            },
        )

        db.commit()

    except Exception:
        db.rollback()
        raise

    return {
        "message": "Customer preferences saved successfully",
        "customer_id": str(customer_id),
        "preferences": get_preferences(db, customer_id),
    }


# --------------------------------------------------
# GET ALL CUSTOMER PREFERENCES
# GET /api/customers/preferences
# --------------------------------------------------

@router.get(
    "/preferences",
    summary="Get All Customer Preferences",
)
def get_all_customer_preferences(
    db: Session = Depends(get_db),
):
    rows = (
        db.execute(
            text("""
                SELECT
                    preference_id,
                    customer_id,
                    aircraft_type,
                    aircraft_model,
                    travellers,
                    budget,
                    currency,
                    origin,
                    destination,
                    travel_date
                FROM customer_hj."CUSTOMER_PREFERENCES"
                ORDER BY preference_id
            """)
        )
        .mappings()
        .all()
    )

    preferences_list = []

    for row in rows:
        result = dict(row)

        # Return IDs as strings.
        if result["preference_id"] is not None:
            result["preference_id"] = str(
                result["preference_id"]
            )

        if result["customer_id"] is not None:
            result["customer_id"] = str(
                result["customer_id"]
            )

        # Convert Decimal budget values into JSON-friendly numbers.
        if result["budget"] is not None:
            result["budget"] = float(result["budget"])

        preferences_list.append(result)

    return {
        "total": len(preferences_list),
        "preferences": preferences_list,
    }

# --------------------------------------------------
# GET CUSTOMER PREFERENCES BY CUSTOMER ID
# GET /api/customers/preferences/get?customer_id=<UUID>
# --------------------------------------------------

@router.get(
    "/preferences/get",
    summary="Get Customer Preferences by Customer ID",
)
def get_customer_preferences(
    customer_id: UUID,
    db: Session = Depends(get_db),
):
    # Check whether the customer exists.
    customer = db.execute(
        text("""
            SELECT id AS customer_id
            FROM customer_hj."CUSTOMER_PROFILE"
            WHERE id = :customer_id
        """),
        {"customer_id": customer_id},
    ).first()

    if customer is None:
        raise HTTPException(
            status_code=404,
            detail="Customer not found",
        )

    # Retrieve preferences using the customer ID.
    preferences = get_preferences(db, customer_id)

    return {
        "customer_id": str(customer_id),
        "preferences": preferences,
    }
