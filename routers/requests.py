import json
import logging
import shutil
from datetime import date
from decimal import Decimal
from enum import Enum
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    Request,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db


router = APIRouter(
    prefix="/api/requests",
    tags=["Requests"],
)

logger = logging.getLogger(__name__)

UPLOAD_DIR = (
    Path(__file__).resolve().parent.parent
    / "uploads"
    / "requests"
)


class CaseInsensitiveEnum(str, Enum):
    @classmethod
    def _missing_(cls, value):
        if isinstance(value, str):
            return next((item for item in cls
                         if item.value.casefold() == value.strip().casefold()), None)
        return None


class RequestCategory(CaseInsensitiveEnum):
    aviation = "Aviation"
    mobility = "Mobility"
    yachts = "Yachts"
    residences = "Residences"
    concierge = "Concierge"
    experiences = "Experiences"


class AircraftType(CaseInsensitiveEnum):
    flight = "Flight"
    helicopter = "Helicopter"


class Currency(CaseInsensitiveEnum):
    inr = "INR"
    usd = "USD"
    eur = "EUR"
    gbp = "GBP"
    aed = "AED"
    sgd = "SGD"


REQUEST_COLUMNS = """
    id,
    id AS request_id,
    request_number,
    customer_id,
    request_type,
    subject,
    message,
    service_type,
    aircraft_type,
    aircraft_model,
    people_count,
    budget,
    currency,
    departure,
    destination,
    start_date,
    customer_name,
    customer_email,
    customer_mobile,
    country,
    status,
    created_at,
    service_subtype,
    end_date,
    trip_type,
    additional_requirements,
    customer_details,
    service_details,
    booking_drafts,
    updated_at,
    identity_document,
    mode,
    access,
    department,
    request_mode
"""


FORM_FIELDS = {
    "customer_id", "service_type", "service_subtype", "aircraft_type",
    "aircraft_model", "people_count", "budget", "currency", "departure",
    "destination", "start_date", "end_date", "trip_type",
    "additional_requirements", "customer_details", "service_details",
    "request_type", "subject", "message", "request_mode", "booking_drafts",
    "attachments",
}


async def validate_form_fields(request: Request):
    submitted = await request.form()
    unknown = set(submitted.keys()) - FORM_FIELDS
    if unknown:
        raise HTTPException(
            400,
            "Unsupported form fields: " + ", ".join(sorted(unknown))
            + ". Refresh Swagger and use only the displayed fields.",
        )


def normalize_request_fields(value):
    if not isinstance(value, dict):
        return value
    result = dict(value)
    details = result.get("customer_details")
    if isinstance(details, dict):
        for target, source in {
            "customer_name": "name",
            "customer_email": "email",
            "customer_mobile": "mobile",
            "country": "country",
        }.items():
            if source in details:
                result[target] = details[source]
    return result


def cleanup_files(paths):
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.exception("Failed to remove request attachment.")


def parse_json(value, field_name):
    if value is None or not value.strip():
        return None

    try:
        result = json.loads(value)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} must contain valid JSON.",
        )

    if not isinstance(result, (dict, list)):
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} must be a JSON object or array.",
        )

    return result


class RequestTripType(str, Enum):
    one_way = "ONE_WAY"
    round_trip = "ROUND_TRIP"

    @classmethod
    def _missing_(cls, value):
        if isinstance(value, str):
            key = "_".join(value.strip().upper().replace("-", " ").replace("_", " ").split())
            if key in {"ONE_WAY", "ONEWAY", "SINGLE", "SINGLE_TRIP"}:
                return cls.one_way
            if key in {"ROUND_TRIP", "ROUNDTRIP", "ROUND", "RETURN", "RETURN_TRIP"}:
                return cls.round_trip
        return None


def public_trip_type(value):
    if value is None or value == "":
        return None
    try:
        return RequestTripType(value).value
    except ValueError:
        # Preserve unknown historical values rather than guess their meaning.
        return value


class RequestStatus(str, Enum):
    submitted = "SUBMITTED"
    under_review = "UNDER_REVIEW"
    quoted = "QUOTED"
    confirmed = "CONFIRMED"
    completed = "COMPLETED"
    cancelled = "CANCELLED"


def public_request_status(value):
    """Normalize known statuses without mislabelling unknown database values."""
    if value is None:
        return None
    normalized = str(value).strip().upper().replace(" ", "_").replace("-", "_")
    if normalized == "OPEN":
        return RequestStatus.submitted.value
    if normalized == "CANCELED":
        return RequestStatus.cancelled.value
    if normalized in {status.value for status in RequestStatus}:
        return normalized
    return value


def database_request_status(value):
    """Keep the existing Open value for newly submitted requests."""
    status = RequestStatus(value)
    return "Open" if status == RequestStatus.submitted else status.value


def with_attachments(db, row):
    result = dict(row)
    result["status"] = public_request_status(result.get("status"))
    result["trip_type"] = public_trip_type(result.get("trip_type"))

    documents = db.execute(
        text("""
            SELECT
                "ID" AS id,
                "ORIGINAL_FILE_NAME" AS original_filename,
                "MIME_TYPE" AS mime_type,
                "STORAGE_PATH" AS storage_path,
                "FILE_SIZE" AS file_size,
                "DOCUMENT_TYPE" AS document_type
            FROM customer_hj."CUSTOMER_REQUEST_DOCUMENTS"
            WHERE "REQUEST_ID" = :request_id
            ORDER BY "CREATED_AT", "ID"
        """),
        {"request_id": result["request_id"]},
    ).mappings().all()

    result.pop("request_id", None)
    result["request_documents"] = [dict(document) for document in documents]
    details = result.get("customer_details")
    if details is None or isinstance(details, dict):
        details = dict(details or {})
        for field, legacy in (("name", "customer_name"), ("email", "customer_email"),
                              ("mobile", "customer_mobile"), ("country", "country")):
            value = result.pop(legacy, None)
            if details.get(field) is None:
                details[field] = value
        result["customer_details"] = details
    return result


@router.post(
    "/",
    summary="Create New Request",
    status_code=200,
    dependencies=[Depends(validate_form_fields)],
)
def create_request(
    customer_id: UUID | None = Form(None),
    service_type: RequestCategory = Form(RequestCategory.aviation),
    service_subtype: str | None = Form(None),
    aircraft_type: AircraftType | None = Form(None),
    aircraft_model: str | None = Form(None),
    people_count: int | None = Form(None, gt=0),
    budget: Decimal | None = Form(None, ge=0),
    currency: Currency = Form(Currency.inr),
    departure: str | None = Form(None),
    destination: str | None = Form(None),
    start_date: date | None = Form(None),
    end_date: date | None = Form(None),
    trip_type: RequestTripType | None = Form(None),
    additional_requirements: str | None = Form(None),
    customer_details: str | None = Form(
        None,
        description='JSON object with name, email, mobile and country.',
    ),
    service_details: str | None = Form(
        None, description="Optional JSON object containing extra service details.",
    ),
    request_type: str = Form("Charter"),
    subject: str | None = Form(None),
    message: str | None = Form(None),
    request_mode: str | None = Form(None),
    booking_drafts: bool = Form(False),
    attachments: list[UploadFile] = File(
        default=None,
        json_schema_extra={"items": {"type": "string", "format": "binary"}},
    ),
    db: Session = Depends(get_db),
):
    customer_details = parse_json(customer_details, "customer_details")
    service_details = parse_json(service_details, "service_details")
    if customer_details is not None and not isinstance(customer_details, dict):
        raise HTTPException(400, "customer_details must be a JSON object.")
    nested = normalize_request_fields({"customer_details": customer_details})
    customer_name = nested.get("customer_name")
    customer_email = nested.get("customer_email")
    customer_mobile = nested.get("customer_mobile")
    country = nested.get("country")
    for value in (customer_name, customer_email, customer_mobile, country):
        if value is not None and not isinstance(value, str):
            raise HTTPException(400, "Customer contact fields must be strings.")
    # Retain database compatibility without exposing administrative inputs.
    identity_document = mode = access = department = None
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
        "customer_name": (customer_name or "").strip(),
        "customer_email": (customer_email or "").strip(),
        "customer_mobile": (customer_mobile or "").strip(),
        "country": (country or "").strip(),
    }

    if any(not value for value in contact_details.values()):
        raise HTTPException(
            status_code=400,
            detail=(
                "Please enter customer name, email, "
                "mobile number, and country."
            ),
        )

    if service_type == RequestCategory.aviation:
        if aircraft_type is None:
            raise HTTPException(
                status_code=400,
                detail="Please select aircraft type.",
            )

        if people_count is None:
            raise HTTPException(
                status_code=400,
                detail="Please enter people count.",
            )

        if not departure or not departure.strip():
            raise HTTPException(
                status_code=400,
                detail="Please enter departure.",
            )

        if not destination or not destination.strip():
            raise HTTPException(
                status_code=400,
                detail="Please enter destination.",
            )

        if start_date is None:
            raise HTTPException(
                status_code=400,
                detail="Please enter start date.",
            )

        if departure.strip().casefold() == destination.strip().casefold():
            raise HTTPException(
                status_code=400,
                detail="Departure and destination must be different.",
            )

    if start_date and end_date and end_date < start_date:
        raise HTTPException(
            status_code=400,
            detail="End date cannot be before start date.",
        )

    request_subject = (
        subject.strip()
        if subject and subject.strip()
        else f"{service_type.value} request"
    )

    saved_files = []
    saved_paths = []

    # id, request_number, and updated_at use database defaults.
    query = text(f"""
        INSERT INTO customer_hj."CUSTOMER_REQUESTS" (
            customer_id,
            request_type,
            subject,
            message,
            service_type,
            aircraft_type,
            aircraft_model,
            people_count,
            budget,
            currency,
            departure,
            destination,
            start_date,
            customer_name,
            customer_email,
            customer_mobile,
            country,
            service_subtype,
            end_date,
            trip_type,
            additional_requirements,
            customer_details,
            service_details,
            identity_document,
            mode,
            access,
            department,
            request_mode,
            booking_drafts,
            status,
            created_at
        )
        VALUES (
            :customer_id,
            :request_type,
            :subject,
            :message,
            :service_type,
            :aircraft_type,
            :aircraft_model,
            :people_count,
            :budget,
            :currency,
            :departure,
            :destination,
            :start_date,
            :customer_name,
            :customer_email,
            :customer_mobile,
            :country,
            :service_subtype,
            :end_date,
            :trip_type,
            :additional_requirements,
            CAST(:customer_details AS JSONB),
            CAST(:service_details AS JSONB),
            :identity_document,
            :mode,
            :access,
            :department,
            :request_mode,
            :booking_drafts,
            'Open',
            CURRENT_TIMESTAMP
        )
        RETURNING {REQUEST_COLUMNS}
    """)

    try:
        customer_exists = db.execute(
            text('SELECT id FROM customer_hj."CUSTOMER_PROFILE" '
                 'WHERE id = :customer_id FOR KEY SHARE'),
            {"customer_id": customer_id},
        ).first()
        if customer_exists is None:
            raise HTTPException(404, "Customer not found. Use the ID returned by login.")
        for uploaded_file in uploaded_attachments:
            if not uploaded_file.filename:
                continue

            UPLOAD_DIR.mkdir(
                parents=True,
                exist_ok=True,
            )

            stored_filename = uuid4().hex
            file_path = UPLOAD_DIR / stored_filename
            saved_paths.append(file_path)

            with file_path.open("wb") as destination_file:
                shutil.copyfileobj(
                    uploaded_file.file,
                    destination_file,
                )

            saved_files.append({
                "filename": uploaded_file.filename,
                "stored_filename": stored_filename,
                "content_type": uploaded_file.content_type,
                "path": f"uploads/requests/{stored_filename}",
            })

        parameters = {
            "customer_id": customer_id,
            "request_type": request_type.strip() or "Charter",
            "subject": request_subject,
            "message": message,
            "service_type": service_type.value,
            "aircraft_type": (
                aircraft_type.value
                if aircraft_type is not None
                else None
            ),
            "aircraft_model": aircraft_model,
            "people_count": people_count,
            "budget": budget,
            "currency": currency.value,
            "departure": departure.strip() if departure else None,
            "destination": (
                destination.strip() if destination else None
            ),
            "start_date": start_date,
            **contact_details,
            "service_subtype": service_subtype,
            "end_date": end_date,
            "trip_type": trip_type.value if trip_type is not None else None,
            "additional_requirements": additional_requirements,
            "customer_details": (
                json.dumps(customer_details)
                if customer_details is not None
                else None
            ),
            "service_details": (
                json.dumps(service_details)
                if service_details is not None
                else None
            ),
            "identity_document": identity_document,
            "mode": mode,
            "access": access,
            "department": department,
            "request_mode": request_mode,
            "booking_drafts": booking_drafts,
        }

        created_request = dict(
            db.execute(
                query,
                parameters,
            ).mappings().one()
        )

        files_with_names = [
            uploaded_file
            for uploaded_file in uploaded_attachments
            if uploaded_file.filename
        ]

        for uploaded, saved_file, saved_path in zip(
            files_with_names,
            saved_files,
            saved_paths,
        ):
            db.execute(
                text("""
                    INSERT INTO customer_hj."CUSTOMER_REQUEST_DOCUMENTS" (
                        "REQUEST_ID",
                        "CUSTOMER_ID",
                        "STORAGE_PATH",
                        "ORIGINAL_FILE_NAME",
                        "MIME_TYPE",
                        "FILE_SIZE",
                        "DOCUMENT_TYPE"
                    )
                    VALUES (
                        :request_id,
                        :customer_id,
                        :path,
                        :filename,
                        :mime_type,
                        :file_size,
                        :document_type
                    )
                """),
                {
                    "request_id": created_request["request_id"],
                    "customer_id": customer_id,
                    "path": saved_file["path"],
                    "filename": saved_file["filename"],
                    "mime_type": (
                        uploaded.content_type
                        or "application/octet-stream"
                    ),
                    "file_size": saved_path.stat().st_size,
                    "document_type": "Attachment",
                },
            )

        created_request = with_attachments(
            db,
            created_request,
        )
        db.commit()

    except HTTPException:
        db.rollback()
        cleanup_files(saved_paths)
        raise

    except IntegrityError:
        db.rollback()
        cleanup_files(saved_paths)
        logger.exception("Request conflicts with database constraints.")
        raise HTTPException(409, "Request conflicts with a linked record or database constraint.")

    except (SQLAlchemyError, OSError):
        db.rollback()

        for file_path in saved_paths:
            try:
                file_path.unlink(missing_ok=True)
            except OSError:
                logger.exception(
                    "Failed to remove request attachment."
                )

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

    rows = db.execute(
        query,
        {"customer_id": customer_id},
    ).mappings().all()

    return {
        "requests": [
            with_attachments(db, row)
            for row in rows
        ]
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

    request = db.execute(
        query,
        {"request_id": request_id},
    ).mappings().first()

    if request is None:
        return {
            "message": "Request not found",
        }

    return {
        "request": with_attachments(db, request),
    }


class UpdateRequestDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_type: str | None = None
    subject: str | None = None
    message: str | None = None
    service_type: RequestCategory | None = None
    aircraft_type: AircraftType | None = None
    aircraft_model: str | None = None
    people_count: int | None = Field(default=None, gt=0)
    budget: Decimal | None = Field(default=None, ge=0)
    currency: Currency | None = None
    departure: str | None = None
    destination: str | None = None
    start_date: date | None = None
    status: RequestStatus | None = None
    service_subtype: str | None = None
    end_date: date | None = None
    trip_type: RequestTripType | None = None
    additional_requirements: str | None = None
    customer_details: dict | None = None
    service_details: dict | list | None = None
    request_mode: str | None = None
    booking_drafts: bool | None = None


@router.put("/", summary="Update Request")
def update_request(
    request_id: UUID | None = None,
    data: UpdateRequestDetails | None = None,
    db: Session = Depends(get_db),
):
    if request_id is None:
        raise HTTPException(
            status_code=400,
            detail="Request ID is required.",
        )

    if data is None:
        raise HTTPException(
            status_code=400,
            detail="Please enter details to update.",
        )

    updates = data.model_dump(
        exclude_unset=True,
        exclude_none=True,
    )

    if not updates:
        raise HTTPException(
            status_code=400,
            detail="Please provide at least one field to update.",
        )

    updates = {
        key: value.value if isinstance(value, Enum) else value
        for key, value in updates.items()
    }
    if "status" in updates:
        updates["status"] = database_request_status(updates["status"])
    updates = normalize_request_fields(updates)
    for field in ("customer_name", "customer_email", "customer_mobile", "country"):
        if field in updates and not isinstance(updates[field], str):
            raise HTTPException(400, f"{field} must be a string.")

    for field in (
        "request_type",
        "customer_name",
        "customer_email",
        "customer_mobile",
        "country",
        "departure",
        "destination",
        "status",
    ):
        if field in updates:
            updates[field] = updates[field].strip()

            if not updates[field]:
                raise HTTPException(
                    status_code=400,
                    detail=f"{field} cannot be blank.",
                )

    try:
        existing = db.execute(
            text(f"""
                SELECT {REQUEST_COLUMNS}
                FROM customer_hj."CUSTOMER_REQUESTS"
                WHERE id = :request_id
                FOR UPDATE
            """),
            {"request_id": request_id},
        ).mappings().first()

        if existing is None:
            raise HTTPException(
                status_code=404,
                detail="Request not found.",
            )

        merged = {
            **dict(existing),
            **updates,
        }

        if "departure" in updates or "destination" in updates:
            departure = merged.get("departure")
            destination = merged.get("destination")

            if (
                departure
                and destination
                and departure.strip().casefold()
                == destination.strip().casefold()
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Departure and destination must be different.",
                )

        if merged.get("start_date") and merged.get("end_date"):
            if merged["end_date"] < merged["start_date"]:
                raise HTTPException(
                    status_code=400,
                    detail="End date cannot be before start date.",
                )

        assignments = ", ".join(
            f"{column} = CAST(:{column} AS JSONB)"
            if column in {"customer_details", "service_details"}
            else f"{column} = :{column}"
            for column in updates
        )

        assignments += ", updated_at = CURRENT_TIMESTAMP"

        for column in ("customer_details", "service_details"):
            if column in updates:
                updates[column] = json.dumps(updates[column])

        updated = db.execute(
            text(f"""
                UPDATE customer_hj."CUSTOMER_REQUESTS"
                SET {assignments}
                WHERE id = :request_id
                RETURNING {REQUEST_COLUMNS}
            """),
            {
                **updates,
                "request_id": request_id,
            },
        ).mappings().one()

        updated_request = with_attachments(db, updated)
        db.commit()

        return {
            "message": "Request updated successfully",
            "request": updated_request,
        }

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to update customer request.")

        raise HTTPException(
            status_code=500,
            detail="Unable to update request.",
        )


@router.delete("/", summary="Delete Request")
def delete_request(
    request_id: UUID | None = None,
    db: Session = Depends(get_db),
):
    if request_id is None:
        raise HTTPException(
            status_code=400,
            detail="Request ID is required.",
        )

    try:
        saved_attachments = [
            dict(row)
            for row in db.execute(
                text("""
                    DELETE FROM customer_hj."CUSTOMER_REQUEST_DOCUMENTS"
                    WHERE "REQUEST_ID" = :request_id
                    RETURNING "STORAGE_PATH" AS path
                """),
                {"request_id": request_id},
            ).mappings().all()
        ]

        deleted = db.execute(
            text("""
                DELETE FROM customer_hj."CUSTOMER_REQUESTS"
                WHERE id = :request_id
                RETURNING id AS request_id
            """),
            {"request_id": request_id},
        ).mappings().first()

        if deleted is None:
            raise HTTPException(
                status_code=404,
                detail="Request not found.",
            )

        db.commit()

    except HTTPException:
        db.rollback()
        raise

    except IntegrityError:
        db.rollback()

        raise HTTPException(
            status_code=409,
            detail=(
                "Request has linked passengers, bookings, "
                "drafts, or timeline records."
            ),
        )

    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to delete customer request.")

        raise HTTPException(
            status_code=500,
            detail="Unable to delete request.",
        )

    for attachment in saved_attachments:
        stored_filename = Path(
            attachment.get("path") or ""
        ).name

        if (
            len(stored_filename) != 32
            or any(
                character not in "0123456789abcdef"
                for character in stored_filename
            )
        ):
            continue

        try:
            (UPLOAD_DIR / stored_filename).unlink(
                missing_ok=True
            )
        except OSError:
            logger.exception(
                "Request deleted, but attachment cleanup failed."
            )

    return {
        "message": "Request deleted successfully",
        "request_id": str(request_id),
    }
