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
    return get_credit_totals(db, customer_id)['credit_balance']


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



# Only posted transactions affect available credits. NULL/blank statuses retain
# compatibility with older usage/refund/adjustment records.
POSTED_TRANSACTION_SQL = """(
    purchase_status IS NULL OR LOWER(TRIM(purchase_status)) IN (
        '', 'completed_test', 'completed', 'confirmed', 'success',
        'successful', 'succeeded', 'paid'
    )
)"""


def get_credit_totals(db: Session, customer_id: UUID) -> dict:
    row = db.execute(
        text(f"""SELECT
            COALESCE(SUM(CASE WHEN LOWER(transaction_type) = 'credit_purchase'
                THEN ABS(amount) ELSE 0 END), 0) AS credits_purchased,
            COALESCE(SUM(CASE WHEN LOWER(transaction_type) = 'subscription'
                THEN ABS(amount) ELSE 0 END), 0) AS subscription_credits,
            COALESCE(SUM(CASE WHEN LOWER(transaction_type) = 'refund'
                THEN ABS(amount) ELSE 0 END), 0) AS credits_refunded,
            COALESCE(SUM(CASE WHEN LOWER(transaction_type) = 'usage'
                THEN ABS(amount) ELSE 0 END), 0) AS credits_used,
            COALESCE(SUM(CASE
                WHEN LOWER(transaction_type) IN ('subscription', 'credit_purchase', 'refund') THEN ABS(amount)
                WHEN LOWER(transaction_type) = 'usage' THEN -ABS(amount)
                ELSE amount END), 0) AS credit_balance
            FROM {SCHEMA}."CUSTOMER_CREDIT_TRANSACTIONS"
            WHERE customer_id=:customer_id AND {POSTED_TRANSACTION_SQL}"""),
        {'customer_id': customer_id},
    ).mappings().one()
    return {key: int(value or 0) for key, value in row.items()}


def fetch_subscriptions(db: Session, customer_id: UUID) -> list[dict]:
    rows = db.execute(
        text(f"""SELECT s.customer_subscription_id, s.customer_id, s.plan_id,
                   s.start_date, s.end_date, s.status, s.price_paid,
                   p.name AS plan_name, p.credit_amount AS credits,
                   p.validity_period, p.price AS plan_price, p.active AS plan_active,
                   (LOWER(TRIM(s.status)) = 'active'
                       AND s.start_date <= CURRENT_TIMESTAMP
                       AND s.end_date > CURRENT_TIMESTAMP) AS is_current
            FROM {SCHEMA}."CUSTOMER_SUBSCRIPTIONS" AS s
            LEFT JOIN {SCHEMA}."CUSTOMER_SUBSCRIPTION_PLANS" AS p
                ON p.plan_id=s.plan_id
            WHERE s.customer_id=:customer_id
            ORDER BY s.end_date DESC, s.customer_subscription_id DESC"""),
        {'customer_id': customer_id},
    ).mappings().all()
    return [dict(row) for row in rows]


def format_subscription(row: dict) -> dict:
    return {
        'id': str(row['customer_subscription_id']),
        'customer_subscription_id': str(row['customer_subscription_id']),
        'customer_id': str(row['customer_id']),
        'plan_id': str(row['plan_id']) if row['plan_id'] is not None else None,
        'plan_name': row['plan_name'],
        'start_date': row['start_date'],
        'end_date': row['end_date'],
        'status': row['status'],
        'price_paid': float(row['price_paid']) if row['price_paid'] is not None else None,
        'credits': row['credits'],
        'active': bool(row['is_current']),
    }


def sync_entitlements(db: Session, customer_id: UUID, balance: int):
    # All callers hold the customer profile's row lock. This snapshot is written
    # in the same transaction as the ledger and purchase; it never adds credits.
    if balance < 0:
        raise HTTPException(409, 'Recorded credit balance is negative. Review existing transactions.')
    current = next((s for s in fetch_subscriptions(db, customer_id) if s['is_current']), None)
    db.execute(
        text(f"""INSERT INTO {SCHEMA}."CUSTOMER_ENTITLEMENTS" (
                "CUSTOMER_ID", "SUBSCRIPTION_STATUS", "SUB_PLAN", "CREDITS_REMAINING", "UPDATED_AT"
            ) VALUES (:customer_id, :status, :plan_id, :balance, CURRENT_TIMESTAMP)
            ON CONFLICT ("CUSTOMER_ID") DO UPDATE SET
                "SUBSCRIPTION_STATUS"=EXCLUDED."SUBSCRIPTION_STATUS",
                "SUB_PLAN"=EXCLUDED."SUB_PLAN",
                "CREDITS_REMAINING"=EXCLUDED."CREDITS_REMAINING",
                "UPDATED_AT"=CURRENT_TIMESTAMP"""),
        {'customer_id': customer_id, 'status': 'active' if current else 'inactive',
         'plan_id': current['plan_id'] if current else None, 'balance': balance},
    )


def record_purchase(db, transaction, purchase_type, product_code, credits, amount):
    # Sharing the UUID links a purchase to its ledger transaction without adding
    # a column or generating a second, confusing identifier.
    return dict(db.execute(
        text(f"""INSERT INTO {SCHEMA}."CUSTOMER_ENTITLEMENT_PURCHASES" (
                "ID", "CUSTOMER_ID", "PURCHASE_TYPE", "PRODUCT_CODE",
                "CREDITS", "AMOUNT", "CURRENCY", "STATUS", "CREATED_AT"
            ) VALUES (:id, :customer_id, :purchase_type, :product_code,
                :credits, :amount, 'USD', 'completed_test', CURRENT_TIMESTAMP)
            RETURNING {PURCHASE_COLUMNS}"""),
        {'id': transaction['transaction_id'], 'customer_id': transaction['customer_id'],
         'purchase_type': purchase_type, 'product_code': product_code,
         'credits': credits, 'amount': amount},
    ).mappings().one())


def purchase_history_query() -> str:
    # Earlier code wrote only ledger transactions. Expose those historical
    # purchases without replaying them, granting credits again or writing on GET.
    # Newly recorded purchases share their transaction ID, so appear only once.
    return f"""
        SELECT {PURCHASE_COLUMNS},
               'entitlement_purchase'::text AS record_source,
               NULL::text AS description
        FROM {SCHEMA}."CUSTOMER_ENTITLEMENT_PURCHASES"
        WHERE "CUSTOMER_ID"=:customer_id
        UNION ALL
        SELECT t.transaction_id AS id, t.customer_id,
               LOWER(t.transaction_type) AS purchase_type,
               NULL::text AS product_code, ABS(t.amount) AS credits,
               t.price_paid AS amount, NULL::text AS currency,
               t.purchase_status AS status, t.created_at,
               'credit_transaction'::text AS record_source, t.description
        FROM {SCHEMA}."CUSTOMER_CREDIT_TRANSACTIONS" AS t
        WHERE t.customer_id=:customer_id
          AND LOWER(t.transaction_type) IN ('subscription', 'credit_purchase')
          AND NOT EXISTS (
              SELECT 1 FROM {SCHEMA}."CUSTOMER_ENTITLEMENT_PURCHASES" AS p
              WHERE p."ID"=t.transaction_id AND p."CUSTOMER_ID"=t.customer_id
          )
    """


@router.get('/', summary='Get Credit Balance')
def get_credits(customer_id: UUID = Depends(require_customer_id), db: Session = Depends(get_db)):
    customer = get_customer(db, customer_id)
    return {'customer': customer.get('name'), 'customer_id': str(customer_id),
            **get_credit_totals(db, customer_id)}


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
        sync_entitlements(db, customer_id, remaining_balance)
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


@router.get('/subscriptions/current', summary='Get Current Subscription')
def get_current_subscription(customer_id: UUID = Depends(require_customer_id), db: Session = Depends(get_db)):
    get_customer(db, customer_id)
    current = next((s for s in fetch_subscriptions(db, customer_id) if s['is_current']), None)
    return {'active': current is not None, 'booking_access': current is not None,
            'subscription': format_subscription(current) if current else None,
            'credit_balance': get_credit_balance(db, customer_id)}


@router.post("/subscriptions/purchase", summary="Purchase Subscription")
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
        purchase = record_purchase(db, transaction, "subscription", str(plan["plan_id"]),
                                   plan["credit_amount"], plan["price"])
        new_balance = get_credit_balance(db, customer_id)
        sync_entitlements(db, customer_id, new_balance)
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
            "message": "Subscription purchased successfully",
            "payment_mode": "test",
            "subscription": subscription_data,
            "credits_added": plan["credit_amount"],
            "credit_balance": new_balance,
            "transaction_id": transaction_id,
            "purchase_id": str(purchase["id"]),
            "purchase": purchase,
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


@router.post("/purchase", summary="Purchase Enquiry Credits")
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
        purchase = record_purchase(db, transaction, "credit_purchase",
                                   f"CREDITS_{data.credits}", data.credits, price)
        new_balance = get_credit_balance(db, customer_id)
        sync_entitlements(db, customer_id, new_balance)
        transaction_data = transaction_to_dict(transaction)
        db.commit()
        return {
            "message": "Credit purchase recorded successfully",
            "payment_mode": "test",
            "credits_purchased": data.credits,
            "purchase_id": str(purchase["id"]),
            "transaction_id": str(transaction["transaction_id"]),
            "purchase": purchase,
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


@router.get('/entitlements', summary='Get Entitlements and Subscription History')
def get_entitlements(customer_id: UUID = Depends(require_customer_id), db: Session = Depends(get_db)):
    get_customer(db, customer_id)
    totals = get_credit_totals(db, customer_id)
    subscriptions = fetch_subscriptions(db, customer_id)
    active = [s for s in subscriptions if s['is_current']]
    current = active[0] if active else None
    # Read live sources so existing customers do not get 404, and expired plans
    # or subsequent transactions do not leave this response stale.
    snapshot = db.execute(
        text(f'SELECT "UPDATED_AT" AS updated_at FROM {SCHEMA}."CUSTOMER_ENTITLEMENTS" '
             'WHERE "CUSTOMER_ID"=:customer_id'), {'customer_id': customer_id},
    ).mappings().first()
    plan = None
    if current is not None:
        plan = {'plan_id': str(current['plan_id']), 'name': current['plan_name'],
                'price': current['plan_price'], 'credit_amount': current['credits'],
                'validity_period': current['validity_period'], 'active': current['plan_active']}
    return {
        'customer_id': str(customer_id),
        'subscription_status': 'active' if current else 'inactive',
        'sub_plan': str(current['plan_id']) if current else None,
        'credits_remaining': totals['credit_balance'],
        **totals,
        'updated_at': snapshot['updated_at'] if snapshot else None,
        'active_subscription_count': len(active),
        'subscription_count': len(subscriptions),
        'subscriptions': [format_subscription(s) for s in subscriptions],
        'plan': plan,
    }


PURCHASE_COLUMNS = '''"ID" AS id, "CUSTOMER_ID" AS customer_id,
    "PURCHASE_TYPE" AS purchase_type, "PRODUCT_CODE" AS product_code,
    "CREDITS" AS credits, "AMOUNT" AS amount, "CURRENCY" AS currency,
    "STATUS" AS status, "CREATED_AT" AS created_at'''


@router.get('/purchases', summary='Get Purchases')
def get_purchases(customer_id: UUID = Depends(require_customer_id), db: Session = Depends(get_db)):
    get_customer(db, customer_id)
    rows = db.execute(
        text('SELECT * FROM (' + purchase_history_query() + ') AS history '
             'ORDER BY created_at DESC, id DESC'), {'customer_id': customer_id},
    ).mappings().all()
    return {'customer_id': str(customer_id), 'purchases': [dict(row) for row in rows], 'count': len(rows)}


@router.get('/purchases/by-id', summary='Get Purchase by ID')
def get_purchase(purchase_id: UUID | None = None,
                customer_id: UUID = Depends(require_customer_id), db: Session = Depends(get_db)):
    if purchase_id is None:
        raise HTTPException(400, 'Please enter Purchase ID.')
    get_customer(db, customer_id)
    row = db.execute(
        text('SELECT * FROM (' + purchase_history_query() + ') AS history WHERE id=:purchase_id'),
        {'customer_id': customer_id, 'purchase_id': purchase_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(404, 'Purchase not found for this customer.')
    return {'purchase': dict(row)}



@router.get('/subscriptions/history', summary='Get Purchased Subscription History')
def get_subscription_history(customer_id: UUID = Depends(require_customer_id), db: Session = Depends(get_db)):
    get_customer(db, customer_id)
    subscriptions = fetch_subscriptions(db, customer_id)
    return {'customer_id': str(customer_id),
            'subscriptions': [format_subscription(s) for s in subscriptions],
            'count': len(subscriptions),
            'active_count': sum(bool(s['is_current']) for s in subscriptions)}
