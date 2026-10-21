from pydantic import BaseModel, Field
from uuid import UUID
import json
import logging
import shutil
from datetime import date
from decimal import Decimal
from enum import Enum
from pathlib import Path
from uuid import uuid4
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from database import get_db

router = APIRouter(
    prefix="/api/requests",
    tags=["Requests"],
)
logger = logging.getLogger(__name__)
UPLOAD_DIR = Path(__file__).resolve().parent.parent / "uploads" / "requests"


class RequestCategory(str, Enum):
    aviation = "Aviation"
    mobility = "Mobility"
    yachts = "Yachts"
    residences = "Residences"
    concierge = "Concierge"
    experiences = "Experiences"


class AircraftType(str, Enum):
    flight = "Flight"
    helicopter = "Helicopter"


class Currency(str, Enum):
    inr = "INR"
    usd = "USD"
    eur = "EUR"
    gbp = "GBP"
    aed = "AED"
    sgd = "SGD"


REQUEST_COLUMNS = """
    id AS request_id, request_number, customer_id,
    service_type AS request_type, subject, message, category,
    aircraft_type, aircraft_model, passenger_count, budget, currency,
    departure AS origin, destination, start_date AS travel_date,
    full_name, email, mobile_number, country, status, created_at,
    service_sub_type, end_date, trip_type, additional_requirements,
    customer_details, service_details, booking_drafts, updated_at
"""
FIELD_COLUMNS = {"request_type": "service_type", "origin": "departure", "travel_date": "start_date"}


def with_attachments(db, row):
    result = dict(row)
    documents = db.execute(text('''SELECT "ID" AS id,
        "ORIGINAL_FILE_NAME" AS filename, "MIME_TYPE" AS content_type,
        "STORAGE_PATH" AS path, "FILE_SIZE" AS file_size,
        "DOCUMENT_TYPE" AS document_type
        FROM customer_hj."CUSTOMER_REQUEST_DOCUMENTS"
        WHERE "REQUEST_ID"=:request_id ORDER BY "CREATED_AT", "ID"'''),
        {"request_id": result["request_id"]}).mappings().all()
    result["attachments"] = [dict(document) for document in documents]
    return result


@router.post("/", summary="Create New Request", status_code=200)
def create_request(
    customer_id: UUID | None = Form(None),
    category: RequestCategory = Form(RequestCategory.aviation),
    aircraft_type: AircraftType | None = Form(None),
    aircraft_model: str | None = Form(None),
    passenger_count: int | None = Form(None, gt=0),
    budget: Decimal | None = Form(None, ge=0),
    currency: Currency = Form(Currency.inr),
    origin: str | None = Form(None),
    destination: str | None = Form(None),
    travel_date: date | None = Form(None),
    full_name: str | None = Form(None),
    email: str | None = Form(None),
    mobile_number: str | None = Form(None),
    country: str | None = Form(None),
    request_type: str = Form("Charter"),
    subject: str | None = Form(None),
    message: str | None = Form(None),
    attachments: list[UploadFile | str] = File(
        default=None,
        description="Select one or more documents or images.",
        json_schema_extra={
            "items": {
                "type": "string",
                "format": "binary",
            },
        },
    ),
    db: Session = Depends(get_db),
):
    uploaded_attachments = []
    for attachment in attachments or []:
        if isinstance(attachment, str):
            if not attachment.strip():
                continue
            raise HTTPException(
                status_code=400,
                detail="Please select a file instead of entering text.",
            )
        uploaded_attachments.append(attachment)
    if customer_id is None:
        raise HTTPException(
            status_code=400,
            detail="Please enter Customer ID.",
        )
    contact_details = {
        "full_name": (full_name or "").strip(),
        "email": (email or "").strip(),
        "mobile_number": (mobile_number or "").strip(),
        "country": (country or "").strip(),
    }
    if any(not value for value in contact_details.values()):
        raise HTTPException(
            status_code=400,
            detail=("Please enter full name, email, " "mobile number, and country."),
        )
    if category == RequestCategory.aviation:
        if aircraft_type is None:
            raise HTTPException(
                status_code=400,
                detail="Please select aircraft type.",
            )
        if passenger_count is None:
            raise HTTPException(
                status_code=400,
                detail="Please enter passenger count.",
            )
        if not origin or not origin.strip():
            raise HTTPException(
                status_code=400,
                detail="Please enter origin.",
            )
        if not destination or not destination.strip():
            raise HTTPException(
                status_code=400,
                detail="Please enter destination.",
            )
        if travel_date is None:
            raise HTTPException(
                status_code=400,
                detail="Please enter travel date.",
            )
        if origin.strip().casefold() == destination.strip().casefold():
            raise HTTPException(
                status_code=400,
                detail="Origin and destination must be different.",
            )
    request_subject = (
        subject.strip() if subject and subject.strip() else f"{category.value} request"
    )
    saved_files = []
    saved_paths = []
    query = text(f"""
        INSERT INTO customer_hj."CUSTOMER_REQUESTS" (
            customer_id,
            service_type,
            subject,
            message,
            category,
            aircraft_type,
            aircraft_model,
            passenger_count,
            budget,
            currency,
            departure,
            destination,
            start_date,
            full_name,
            email,
            mobile_number,
            country,
            status,
            created_at
        )
        VALUES (
            :customer_id,
            :request_type,
            :subject,
            :message,
            :category,
            :aircraft_type,
            :aircraft_model,
            :passenger_count,
            :budget,
            :currency,
            :origin,
            :destination,
            :travel_date,
            :full_name,
            :email,
            :mobile_number,
            :country,
            'pending',
            CURRENT_TIMESTAMP
        )
        RETURNING {REQUEST_COLUMNS}
    """)
    try:
        for uploaded_file in uploaded_attachments:
            if not uploaded_file.filename:
                continue
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            stored_filename = uuid4().hex
            file_path = UPLOAD_DIR / stored_filename
            saved_paths.append(file_path)
            with file_path.open("wb") as destination_file:
                shutil.copyfileobj(
                    uploaded_file.file,
                    destination_file,
                )
            saved_files.append(
                {
                    "filename": uploaded_file.filename,
                    "stored_filename": stored_filename,
                    "content_type": uploaded_file.content_type,
                    "path": f"uploads/requests/{stored_filename}",
                }
            )
        parameters = {
            "customer_id": customer_id,
            "request_type": request_type.strip() or "Charter",
            "subject": request_subject,
            "message": message,
            "category": category.value,
            "aircraft_type": (
                aircraft_type.value if aircraft_type is not None else None
            ),
            "aircraft_model": aircraft_model,
            "passenger_count": passenger_count,
            "budget": budget,
            "currency": currency.value,
            "origin": origin.strip() if origin else None,
            "destination": (destination.strip() if destination else None),
            "travel_date": travel_date,
            **contact_details,
            "attachments": json.dumps(saved_files),
        }
        result = db.execute(query, parameters)
        created_request = dict(result.mappings().one())
        for uploaded, saved_file, saved_path in zip(
            [f for f in uploaded_attachments if f.filename], saved_files, saved_paths
        ):
            db.execute(text('''INSERT INTO customer_hj."CUSTOMER_REQUEST_DOCUMENTS"
                ("REQUEST_ID", "CUSTOMER_ID", "STORAGE_PATH", "ORIGINAL_FILE_NAME",
                 "MIME_TYPE", "FILE_SIZE", "DOCUMENT_TYPE")
                VALUES (:request_id, :customer_id, :path, :filename, :mime_type,
                        :file_size, :document_type)'''), {
                "request_id": created_request["request_id"], "customer_id": customer_id,
                "path": saved_file["path"], "filename": saved_file["filename"],
                "mime_type": uploaded.content_type or "application/octet-stream",
                "file_size": saved_path.stat().st_size, "document_type": "Attachment",
            })
        created_request = with_attachments(db, created_request)
        db.commit()
    except (SQLAlchemyError, OSError):
        db.rollback()
        for file_path in saved_paths:
            try:
                file_path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Failed to remove request attachment.")
        logger.exception("Failed to create customer request.")
        raise HTTPException(
            status_code=500,
            detail="Unable to create request.",
        )
    finally:
        for uploaded_file in uploaded_attachments:
            uploaded_file.file.close()
    return {
        "message": "Request created successfully",
        "request": created_request,
    }


@router.get("/", summary="Get All Requests")
def get_all_requests(
    customer_id: UUID | None = None,
    db: Session = Depends(get_db),
):
    query = text(f"""
        SELECT {REQUEST_COLUMNS}
        FROM customer_hj."CUSTOMER_REQUESTS"
        WHERE (
            CAST(:customer_id AS UUID) IS NULL
            OR customer_id = :customer_id
        )
        ORDER BY created_at DESC, id DESC
    """)
    result = db.execute(
        query,
        {"customer_id": customer_id},
    )
    requests = [with_attachments(db, row) for row in result.mappings().all()]
    return {
        "requests": requests,
    }


@router.get("/request", summary="Get Request by ID")
def get_request(
    request_id: UUID | None = None,
    db: Session = Depends(get_db),
):
    if request_id is None:
        return {
            "message": "Request ID is required",
        }
    query = text(f"""
        SELECT {REQUEST_COLUMNS}
        FROM customer_hj."CUSTOMER_REQUESTS"
        WHERE id = :request_id
    """)
    result = db.execute(
        query,
        {"request_id": request_id},
    )
    request = result.mappings().first()
    if request is None:
        return {
            "message": "Request not found",
        }
    return {
        "request": with_attachments(db, request),
    }


class UpdateRequestDetails(BaseModel):
    request_type: str | None = None
    subject: str | None = None
    message: str | None = None
    category: RequestCategory | None = None
    aircraft_type: AircraftType | None = None
    aircraft_model: str | None = None
    passenger_count: int | None = Field(default=None, gt=0)
    budget: Decimal | None = Field(default=None, ge=0)
    currency: Currency | None = None
    origin: str | None = None
    destination: str | None = None
    travel_date: date | None = None
    full_name: str | None = None
    email: str | None = None
    mobile_number: str | None = None
    country: str | None = None
    status: str | None = None


@router.put("/", summary="Update Request")
def update_request(
    request_id: UUID | None = None,
    data: UpdateRequestDetails | None = None,
    db: Session = Depends(get_db),
):
    if request_id is None:
        raise HTTPException(400, "Request ID is required.")
    if data is None:
        raise HTTPException(400, "Please enter details to update.")
    updates = data.model_dump(exclude_unset=True, exclude_none=True)
    if not updates:
        raise HTTPException(400, "Please provide at least one field to update.")
    updates = {k: v.value if isinstance(v, Enum) else v for k, v in updates.items()}
    for field in (
        "request_type",
        "full_name",
        "email",
        "mobile_number",
        "country",
        "origin",
        "destination",
        "status",
    ):
        if field in updates:
            updates[field] = updates[field].strip()
            if not updates[field]:
                raise HTTPException(400, f"{field} cannot be blank.")
    try:
        existing = (
            db.execute(
                text(f"""SELECT {REQUEST_COLUMNS} FROM customer_hj."CUSTOMER_REQUESTS"
            WHERE id = :request_id FOR UPDATE"""),
                {"request_id": request_id},
            )
            .mappings()
            .first()
        )
        if existing is None:
            raise HTTPException(404, "Request not found.")
        merged = {**dict(existing), **updates}
        if "origin" in updates or "destination" in updates:
            origin, destination = merged.get("origin"), merged.get("destination")
            if (
                origin
                and destination
                and origin.strip().casefold() == destination.strip().casefold()
            ):
                raise HTTPException(400, "Origin and destination must be different.")
        assignments = ", ".join(f"{FIELD_COLUMNS.get(column, column)} = :{column}" for column in updates)
        assignments += ", updated_at = CURRENT_TIMESTAMP"
        updated = (
            db.execute(
                text(f"""UPDATE customer_hj."CUSTOMER_REQUESTS" SET {assignments}
            WHERE id = :request_id RETURNING {REQUEST_COLUMNS}"""),
                {**updates, "request_id": request_id},
            )
            .mappings()
            .one()
        )
        updated_request = with_attachments(db, updated)
        db.commit()
        return {"message": "Request updated successfully", "request": updated_request}
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to update customer request.")
        raise HTTPException(500, "Unable to update request.")


@router.delete("/", summary="Delete Request")
def delete_request(request_id: UUID | None = None, db: Session = Depends(get_db)):
    if request_id is None:
        raise HTTPException(400, "Request ID is required.")
    try:
        saved_attachments = [dict(row) for row in db.execute(
            text('''DELETE FROM customer_hj."CUSTOMER_REQUEST_DOCUMENTS"
                WHERE "REQUEST_ID"=:request_id RETURNING "STORAGE_PATH" AS path'''),
            {"request_id": request_id}).mappings().all()]
        deleted = (
            db.execute(
                text("""DELETE FROM customer_hj."CUSTOMER_REQUESTS"
            WHERE id = :request_id RETURNING id AS request_id"""),
                {"request_id": request_id},
            )
            .mappings()
            .first()
        )
        if deleted is None:
            raise HTTPException(404, "Request not found.")
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Request has linked passengers, bookings, drafts, or timeline records.")
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to delete customer request.")
        raise HTTPException(500, "Unable to delete request.")
    for attachment in saved_attachments:
        if not isinstance(attachment, dict):
            continue
        stored_filename = Path(attachment.get("path") or "").name
        if (
            not isinstance(stored_filename, str)
            or len(stored_filename) != 32
            or any(c not in "0123456789abcdef" for c in stored_filename)
        ):
            continue
        try:
            (UPLOAD_DIR / stored_filename).unlink(missing_ok=True)
        except OSError:
            logger.exception("Request deleted, but attachment cleanup failed.")
    return {"message": "Request deleted successfully", "request_id": str(request_id)}
