import logging
import re
from decimal import Decimal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from database import get_db
from rate_limiter import limiter

router = APIRouter(prefix="/api/credits", tags=["Credits & Subscriptions"])
logger = logging.getLogger(__name__)
SCHEMA = "customer_hj"
CREDIT_PACKAGES = {
    1000: Decimal("25.00"),
    2500: Decimal("60.00"),
    5000: Decimal("110.00"),
}
TRANSACTION_COLUMNS = """transaction_id, customer_id, transaction_type, amount,
    description, created_at, price_paid, purchase_status"""


class UseCreditsRequest(BaseModel):
    amount: int | None = None
    reason: str | None = None


class PurchaseSubscriptionRequest(BaseModel):
    plan_name: str | None = None


class PurchaseCreditsRequest(BaseModel):
    credits: int | None = None


def require_customer_id(customer_id: UUID | None = None) -> UUID:
    if customer_id is None:
        raise HTTPException(400, "Customer ID is required")
    return customer_id


def get_customer(db: Session, customer_id: UUID, lock: bool = False):
    lock_clause = "FOR UPDATE" if lock else ""
    row = (
        db.execute(
            text(
                f"""SELECT *
        FROM {SCHEMA}."CUSTOMER_PROFILE" WHERE customer_id = :customer_id {lock_clause}"""
            ),
            {"customer_id": customer_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(404, "Customer not found")
    return row


def get_credit_balance(db: Session, customer_id: UUID) -> int:
    balance = db.execute(
        text(f"""SELECT COALESCE(SUM(CASE
        WHEN LOWER(transaction_type) IN ('subscription', 'credit_purchase', 'refund') THEN ABS(amount)
        WHEN LOWER(transaction_type) = 'usage' THEN -ABS(amount)
        ELSE amount END), 0)
        FROM {SCHEMA}."CUSTOMER_CREDIT_TRANSACTIONS" WHERE customer_id = :customer_id"""),
        {"customer_id": customer_id},
    ).scalar_one()
    return int(balance or 0)


def get_validity_days(value) -> int:
    match = re.fullmatch(
        r"(\d+)(?:\s*days?)?", str(value or "").strip(), flags=re.IGNORECASE
    )
    if match is None:
        raise HTTPException(
            400,
            "Plan validity_period must contain a number of days, for example '30', '365', or '30 days'.",
        )
    days = int(match.group(1))
    if days <= 0:
        raise HTTPException(400, "Subscription validity must be greater than zero.")
    return days


def transaction_to_dict(transaction):
    result = dict(transaction)
    result.update(
        {
            "id": str(transaction["transaction_id"]),
            "transaction_id": str(transaction["transaction_id"]),
            "customer_id": str(transaction["customer_id"]),
            "type": transaction["transaction_type"],
            "reason": transaction["description"],
            "price_paid": (
                float(transaction["price_paid"])
                if transaction["price_paid"] is not None
                else None
            ),
        }
    )
    return result


def subscription_plan_to_dict(plan):
    return {
        "id": str(plan["plan_id"]),
        "name": plan["name"],
        "price": float(plan["price"]),
        "credits": plan["credit_amount"],
        "validity_period": plan["validity_period"],
        "validity_days": get_validity_days(plan["validity_period"]),
        "validity_unit": "days",
        "active": plan["active"],
    }


def insert_transaction(
    db: Session,
    customer_id: UUID,
    transaction_type: str,
    amount: int,
    description: str | None,
    price_paid: Decimal | None = None,
    purchase_status: str | None = None,
):
    return (
        db.execute(
            text(f"""INSERT INTO {SCHEMA}."CUSTOMER_CREDIT_TRANSACTIONS"
        (customer_id, transaction_type, amount, description, created_at, price_paid, purchase_status)
        VALUES (:customer_id, :transaction_type, :amount, :description, CURRENT_TIMESTAMP, :price_paid, :purchase_status)
        RETURNING {TRANSACTION_COLUMNS}"""),
            {
                "customer_id": customer_id,
                "transaction_type": transaction_type,
                "amount": amount,
                "description": description,
                "price_paid": price_paid,
                "purchase_status": purchase_status,
            },
        )
        .mappings()
        .one()
    )


@router.get("/", summary="Get Credit Balance")
def get_credits(
    customer_id: UUID = Depends(require_customer_id), db: Session = Depends(get_db)
):
    customer = get_customer(db, customer_id)
    return {
        "customer": customer.get("name"),
        "customer_id": str(customer_id),
        "credit_balance": get_credit_balance(db, customer_id),
    }


@router.get("/transactions", summary="Get Credit and Purchase History")
def get_credit_transactions(
    customer_id: UUID = Depends(require_customer_id), db: Session = Depends(get_db)
):
    get_customer(db, customer_id)
    rows = (
        db.execute(
            text(
                f"""SELECT {TRANSACTION_COLUMNS} FROM {SCHEMA}."CUSTOMER_CREDIT_TRANSACTIONS"
        WHERE customer_id = :customer_id ORDER BY created_at DESC, transaction_id DESC"""
            ),
            {"customer_id": customer_id},
        )
        .mappings()
        .all()
    )
    transactions = [transaction_to_dict(row) for row in rows]
    return {
        "credit_transactions": transactions,
        "transactions": transactions,
        "count": len(transactions),
    }


@router.post("/use", summary="Use Credits")
@limiter.limit("50/minute")
def use_credits(
    request: Request,
    data: UseCreditsRequest | None = None,
    customer_id: UUID = Depends(require_customer_id),
    db: Session = Depends(get_db),
):
    if data is None or data.amount is None:
        raise HTTPException(400, "Credit amount is required")
    if data.amount <= 0:
        raise HTTPException(400, "Credit amount must be greater than 0")
    try:
        get_customer(db, customer_id, lock=True)
        balance = get_credit_balance(db, customer_id)
        if data.amount > balance:
            raise HTTPException(400, "Insufficient credits")
        transaction = insert_transaction(
            db, customer_id, "usage", data.amount, data.reason or "Credit usage"
        )
        remaining_balance = balance - data.amount
        transaction_data = transaction_to_dict(transaction)
        transaction_data["remaining_balance"] = remaining_balance
        db.commit()
        return {
            "message": "Credits used successfully",
            "transaction": transaction_data,
            "remaining_balance": remaining_balance,
        }
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Credit usage failed.")
        raise HTTPException(500, "Unable to use credits")


@router.get("/subscriptions", summary="Get Subscription Plans")
def get_subscription_plans(db: Session = Depends(get_db)):
    plans = (
        db.execute(
            text(
                f"""SELECT plan_id, name, price, credit_amount, validity_period, active
        FROM {SCHEMA}."CUSTOMER_SUBSCRIPTION_PLANS" WHERE active = TRUE ORDER BY plan_id"""
            )
        )
        .mappings()
        .all()
    )
    return {"subscription_plans": [subscription_plan_to_dict(plan) for plan in plans]}


@router.get("/subscriptions/current", summary="Get Current Subscription")
def get_current_subscription(
    customer_id: UUID = Depends(require_customer_id), db: Session = Depends(get_db)
):
    get_customer(db, customer_id)
    subscription = (
        db.execute(
            text(
                f"""SELECT subscriptions.customer_subscription_id,
        subscriptions.customer_id, subscriptions.plan_id, subscriptions.start_date,
        subscriptions.end_date, subscriptions.status, subscriptions.price_paid, plans.name AS plan_name
        FROM {SCHEMA}."CUSTOMER_SUBSCRIPTIONS" AS subscriptions
        LEFT JOIN {SCHEMA}."CUSTOMER_SUBSCRIPTION_PLANS" AS plans ON plans.plan_id = subscriptions.plan_id
        WHERE subscriptions.customer_id = :customer_id AND subscriptions.status = 'active'
        AND subscriptions.start_date <= CURRENT_TIMESTAMP AND subscriptions.end_date > CURRENT_TIMESTAMP
        ORDER BY subscriptions.end_date DESC, subscriptions.customer_subscription_id DESC LIMIT 1"""
            ),
            {"customer_id": customer_id},
        )
        .mappings()
        .first()
    )
    if subscription is None:
        return {"active": False, "booking_access": False, "subscription": None}
    return {
        "active": True,
        "booking_access": True,
        "subscription": {
            "id": str(subscription["customer_subscription_id"]),
            "customer_id": str(subscription["customer_id"]),
            "plan_id": str(subscription["plan_id"]),
            "plan_name": subscription["plan_name"],
            "start_date": subscription["start_date"],
            "end_date": subscription["end_date"],
            "price_paid": float(subscription["price_paid"]),
            "status": subscription["status"],
        },
    }


@router.post("/subscriptions/purchase", summary="Purchase Subscription (Test Mode)")
@limiter.limit("50/minute")
def purchase_subscription(
    request: Request,
    data: PurchaseSubscriptionRequest | None = None,
    customer_id: UUID = Depends(require_customer_id),
    db: Session = Depends(get_db),
):
    if data is None or not data.plan_name or not data.plan_name.strip():
        raise HTTPException(400, "Subscription plan name is required")
    try:
        get_customer(db, customer_id, lock=True)
        plan = (
            db.execute(
                text(
                    f"""SELECT plan_id, name, price, credit_amount, validity_period, active
            FROM {SCHEMA}."CUSTOMER_SUBSCRIPTION_PLANS" WHERE LOWER(name) = LOWER(:plan_name)
            AND active = TRUE ORDER BY plan_id LIMIT 1"""
                ),
                {"plan_name": data.plan_name.strip()},
            )
            .mappings()
            .first()
        )
        if plan is None:
            raise HTTPException(404, "Active subscription plan not found")
        validity_days = get_validity_days(plan["validity_period"])
        if plan["credit_amount"] < 0 or plan["price"] < 0:
            raise HTTPException(400, "Plan credits and price cannot be negative")
        db.execute(
            text(
                f"""UPDATE {SCHEMA}."CUSTOMER_SUBSCRIPTIONS" SET status = 'expired'
            WHERE customer_id = :customer_id AND status = 'active' AND end_date <= CURRENT_TIMESTAMP"""
            ),
            {"customer_id": customer_id},
        )
        subscription = (
            db.execute(
                text(
                    f"""INSERT INTO {SCHEMA}."CUSTOMER_SUBSCRIPTIONS"
            (customer_id, plan_id, start_date, end_date, status, price_paid, created_at)
            VALUES (:customer_id, :plan_id, CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP + (:validity_days * INTERVAL '1 day'), 'active', :price_paid, CURRENT_TIMESTAMP)
            RETURNING customer_subscription_id, customer_id, plan_id, start_date, end_date, status, price_paid"""
                ),
                {
                    "customer_id": customer_id,
                    "plan_id": plan["plan_id"],
                    "validity_days": validity_days,
                    "price_paid": plan["price"],
                },
            )
            .mappings()
            .one()
        )
        transaction = insert_transaction(
            db,
            customer_id,
            "subscription",
            plan["credit_amount"],
            f"Subscription purchase: {plan['name']} (Plan ID: {plan['plan_id']})",
            plan["price"],
            "completed_test",
        )
        new_balance = get_credit_balance(db, customer_id)
        plan_data = subscription_plan_to_dict(plan)
        subscription_data = {
            **plan_data,
            "customer_subscription_id": str(subscription["customer_subscription_id"]),
            "plan_id": str(subscription["plan_id"]),
            "plan": plan_data,
            "start_date": subscription["start_date"],
            "end_date": subscription["end_date"],
            "status": subscription["status"],
            "price_paid": float(subscription["price_paid"]),
        }
        transaction_id = str(transaction["transaction_id"])
        db.commit()
        return {
            "message": "Subscription purchased successfully (test mode)",
            "payment_mode": "test",
            "subscription": subscription_data,
            "credits_added": plan["credit_amount"],
            "credit_balance": new_balance,
            "transaction_id": transaction_id,
        }
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Subscription purchase failed.")
        raise HTTPException(500, "Unable to purchase subscription")


@router.get("/packages", summary="Get Enquiry Credit Packages")
def get_credit_packages():
    return {
        "credit_packages": [
            {
                "credits": credits,
                "price": float(price),
                "currency": "USD",
                "cost_per_100_credits": round(float(price) / credits * 100, 2),
            }
            for credits, price in CREDIT_PACKAGES.items()
        ]
    }


@router.post("/purchase", summary="Purchase Enquiry Credits (Test Mode)")
@limiter.limit("50/minute")
def purchase_credits(
    request: Request,
    data: PurchaseCreditsRequest | None = None,
    customer_id: UUID = Depends(require_customer_id),
    db: Session = Depends(get_db),
):
    if data is None or data.credits is None:
        raise HTTPException(400, "Please select a credit package")
    price = CREDIT_PACKAGES.get(data.credits)
    if price is None:
        raise HTTPException(
            400,
            {
                "message": "Invalid credit package",
                "available_packages": list(CREDIT_PACKAGES),
            },
        )
    try:
        get_customer(db, customer_id, lock=True)
        transaction = insert_transaction(
            db,
            customer_id,
            "credit_purchase",
            data.credits,
            f"{data.credits:,} enquiry credits purchased",
            price,
            "completed_test",
        )
        new_balance = get_credit_balance(db, customer_id)
        transaction_data = transaction_to_dict(transaction)
        db.commit()
        return {
            "message": "Credit purchase recorded successfully (test mode)",
            "payment_mode": "test",
            "credits_purchased": data.credits,
            "price_paid": float(price),
            "currency": "USD",
            "credit_balance": new_balance,
            "transaction": transaction_data,
        }
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Credit purchase failed.")
        raise HTTPException(500, "Credit purchase failed")


@router.get('/entitlements', summary='Get Entitlements')
def get_entitlements(
    customer_id: UUID = Depends(require_customer_id),
    db: Session = Depends(get_db),
):
    row = db.execute(
        text('''SELECT e."CUSTOMER_ID" AS customer_id,
                       e."SUBSCRIPTION_STATUS" AS subscription_status,
                       e."SUB_PLAN" AS sub_plan,
                       e."CREDITS_REMAINING" AS credits_remaining,
                       e."UPDATED_AT" AS updated_at,
                       p.name AS plan_name, p.price AS plan_price,
                       p.credit_amount AS plan_credits,
                       p.validity_period AS plan_validity_period,
                       p.active AS plan_active
                FROM customer_hj."CUSTOMER_ENTITLEMENTS" AS e
                LEFT JOIN customer_hj."CUSTOMER_SUBSCRIPTION_PLANS" AS p
                    ON p.plan_id = e."SUB_PLAN"
                WHERE e."CUSTOMER_ID"=:customer_id'''),
        {'customer_id': customer_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(404, 'No entitlements found for this customer.')
    subscriptions = db.execute(
        text('''SELECT s.customer_subscription_id, s.customer_id, s.plan_id,
                       s.start_date, s.end_date, s.status, s.price_paid,
                       p.name AS plan_name
                FROM customer_hj."CUSTOMER_SUBSCRIPTIONS" AS s
                LEFT JOIN customer_hj."CUSTOMER_SUBSCRIPTION_PLANS" AS p
                    ON p.plan_id=s.plan_id
                WHERE s.customer_id=:customer_id
                ORDER BY s.start_date DESC, s.customer_subscription_id DESC'''),
        {'customer_id': customer_id},
    ).mappings().all()
    plan = None
    if row['sub_plan'] is not None:
        plan = {
            'plan_id': row['sub_plan'], 'name': row['plan_name'],
            'price': row['plan_price'], 'credit_amount': row['plan_credits'],
            'validity_period': row['plan_validity_period'], 'active': row['plan_active'],
        }
    return {
        'customer_id': row['customer_id'],
        'subscription_status': row['subscription_status'],
        'sub_plan': row['sub_plan'],
        'credits_remaining': row['credits_remaining'],
        'updated_at': row['updated_at'],
        'subscriptions': [dict(subscription) for subscription in subscriptions],
        'plan': plan,
    }


PURCHASE_COLUMNS = '''"ID" AS id, "CUSTOMER_ID" AS customer_id,
    "PURCHASE_TYPE" AS purchase_type, "PRODUCT_CODE" AS product_code,
    "CREDITS" AS credits, "AMOUNT" AS amount, "CURRENCY" AS currency,
    "STATUS" AS status, "CREATED_AT" AS created_at'''


@router.get('/purchases', summary='Get Purchases')
def get_purchases(
    customer_id: UUID = Depends(require_customer_id),
    db: Session = Depends(get_db),
):
    get_customer(db, customer_id)
    rows = db.execute(
        text(f'''SELECT {PURCHASE_COLUMNS}
                 FROM customer_hj."CUSTOMER_ENTITLEMENT_PURCHASES"
                 WHERE "CUSTOMER_ID"=:customer_id
                 ORDER BY "CREATED_AT" DESC, "ID" DESC'''),
        {'customer_id': customer_id},
    ).mappings().all()
    return {'customer_id': customer_id, 'purchases': [dict(row) for row in rows], 'count': len(rows)}


@router.get('/purchases/by-id', summary='Get Purchase by ID')
def get_purchase(
    purchase_id: UUID | None = None,
    customer_id: UUID = Depends(require_customer_id),
    db: Session = Depends(get_db),
):
    if purchase_id is None:
        raise HTTPException(400, 'Please enter Purchase ID.')
    row = db.execute(
        text(f'''SELECT {PURCHASE_COLUMNS}
                 FROM customer_hj."CUSTOMER_ENTITLEMENT_PURCHASES"
                 WHERE "ID"=:purchase_id AND "CUSTOMER_ID"=:customer_id'''),
        {'purchase_id': purchase_id, 'customer_id': customer_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(404, 'Purchase not found for this customer.')
    return {'purchase': dict(row)}
