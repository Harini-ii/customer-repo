import logging
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from pydantic import AliasChoices, BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from database import get_db

router = APIRouter(prefix="/api/support", tags=["Support"])
logger = logging.getLogger(__name__)
TICKET_COLUMNS = "ticket_id, ticket_number, customer_id, subject, message, status, created_at, updated_at"


class SupportTicketRequest(BaseModel):
    ticket_id: UUID | None = Field(default=None, validation_alias=AliasChoices('id', 'ticket_id'))
    ticket_number: str | None = None
    customer_id: UUID | None = Field(default=None, validation_alias=AliasChoices('requester_id', 'customer_id'))
    subject: str | None = None
    message: str | None = None


class UpdateSupportTicketRequest(BaseModel):
    ticket_id: UUID = Field(default=..., validation_alias=AliasChoices('id', 'ticket_id'))
    ticket_number: str | None = None
    subject: str | None = None
    message: str | None = None
    status: str | None = None


def format_ticket(ticket):
    if ticket is None:
        return None
    result = dict(ticket)
    result["ticket_id"] = str(result["ticket_id"])
    result["id"] = result["ticket_id"]
    result["requester_id"] = str(result["customer_id"]) if result["customer_id"] is not None else None
    result["requester_role"] = "CUSTOMER"
    result["admin_response"] = result.get("admin_response")
    if result["customer_id"] is not None:
        result["customer_id"] = str(result["customer_id"])
    for field in ("created_at", "updated_at"):
        result[field] = result[field].isoformat() if result[field] else None
    result.pop("ticket_id", None)
    result.pop("customer_id", None)
    return result


@router.post("/", summary="Create Support Ticket")
def create_support_ticket(request: SupportTicketRequest, db: Session = Depends(get_db)):
    for value, message in [
        (request.ticket_number, "Ticket number is required"),
        (request.subject, "Subject is required"),
        (request.message, "Message is required"),
    ]:
        if not value:
            raise HTTPException(400, message)
    if request.customer_id is None:
        raise HTTPException(400, "Customer ID is required")
    try:
        customer = db.execute(
            text("""SELECT id AS customer_id FROM customer_hj."CUSTOMER_PROFILE"
            WHERE id = :customer_id"""),
            {"customer_id": request.customer_id},
        ).first()
        if customer is None:
            raise HTTPException(404, "Customer not found")
        ticket = (
            db.execute(
                text(f"""INSERT INTO customer_hj."CUSTOMER_SUPPORT_TICKETS"
            (ticket_number, customer_id, subject, message, status, created_at, updated_at)
            VALUES (:ticket_number, :customer_id, :subject, :message, 'Open', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            RETURNING {TICKET_COLUMNS}"""),
                {
                    "ticket_number": request.ticket_number,
                    "customer_id": request.customer_id,
                    "subject": request.subject,
                    "message": request.message,
                },
            )
            .mappings()
            .one()
        )
        ticket_data = format_ticket(ticket)
        db.commit()
        return {"message": "Support ticket created successfully", "ticket": ticket_data}
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Could not create support ticket.")
        raise HTTPException(
            500, "Could not create support ticket. Check the server terminal."
        )


@router.get("/", summary="Get All Support Tickets")
def get_all_support_tickets(db: Session = Depends(get_db)):
    rows = (
        db.execute(
            text(f"""SELECT {TICKET_COLUMNS} FROM customer_hj."CUSTOMER_SUPPORT_TICKETS"
        ORDER BY created_at DESC, ticket_id DESC""")
        )
        .mappings()
        .all()
    )
    return {"tickets": [format_ticket(row) for row in rows]}


@router.get("/ticket", summary="Get Support Ticket by ID")
def get_support_ticket(ticket_id: UUID, db: Session = Depends(get_db)):
    ticket = (
        db.execute(
            text(f"""SELECT {TICKET_COLUMNS} FROM customer_hj."CUSTOMER_SUPPORT_TICKETS"
        WHERE ticket_id = :ticket_id"""),
            {"ticket_id": ticket_id},
        )
        .mappings()
        .first()
    )
    if ticket is None:
        raise HTTPException(404, "Support ticket not found")
    return {"ticket": format_ticket(ticket)}


@router.put("/", summary="Update Support Ticket")
def update_support_ticket(
    request: UpdateSupportTicketRequest, db: Session = Depends(get_db)
):
    updates = request.model_dump(
        exclude={"ticket_id"}, exclude_unset=True, exclude_none=True
    )
    if not updates:
        raise HTTPException(400, "Provide at least one field to update")
    assignments = ", ".join(f"{column} = :{column}" for column in updates)
    try:
        ticket = (
            db.execute(
                text(f"""UPDATE customer_hj."CUSTOMER_SUPPORT_TICKETS"
            SET {assignments}, updated_at = CURRENT_TIMESTAMP
            WHERE ticket_id = :ticket_id RETURNING {TICKET_COLUMNS}"""),
                {**updates, "ticket_id": request.ticket_id},
            )
            .mappings()
            .first()
        )
        if ticket is None:
            raise HTTPException(404, "Support ticket not found")
        ticket_data = format_ticket(ticket)
        db.commit()
        return {"message": "Support ticket updated successfully", "ticket": ticket_data}
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Could not update support ticket.")
        raise HTTPException(
            500, "Could not update support ticket. Check the server terminal."
        )


@router.delete("/", summary="Delete Support Ticket")
def delete_support_ticket(ticket_id: UUID, db: Session = Depends(get_db)):
    try:
        deleted = db.execute(
            text("""DELETE FROM customer_hj."CUSTOMER_SUPPORT_TICKETS"
            WHERE ticket_id = :ticket_id RETURNING ticket_id"""),
            {"ticket_id": ticket_id},
        ).first()
        if deleted is None:
            raise HTTPException(404, "Support ticket not found")
        deleted_id = str(deleted[0])
        db.commit()
        return {
            "message": "Support ticket deleted successfully",
            "ticket_id": deleted_id,
        }
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Could not delete support ticket.")
        raise HTTPException(
            500, "Could not delete support ticket. Check the server terminal."
        )


@router.get("/count", summary="Get Ticket Count")
def get_ticket_count(db: Session = Depends(get_db)):
    return {
        "ticket_count": db.execute(
            text('SELECT COUNT(*) FROM customer_hj."CUSTOMER_SUPPORT_TICKETS"')
        ).scalar_one()
    }
