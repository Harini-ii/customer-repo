import logging
import json
from decimal import Decimal, InvalidOperation
from typing import Any
from datetime import date, datetime
from enum import Enum
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db
from rate_limiter import limiter
from routers.auth import get_current_user

router = APIRouter(prefix='/api/bookings', tags=['Bookings'])
logger = logging.getLogger(__name__)
TABLE = 'customer_hj."CUSTOMER_BOOKINGS"'
TRACKING = 'customer_hj."CUSTOMER_BOOKING_TRACKING"'
RECORDS = 'customer_hj."CUSTOMER_BOOKING_RECORDS"'
REQUESTS = 'customer_hj."CUSTOMER_REQUESTS"'
RECORD_FIELDS = '''
    "ID" AS id,
    
    "REQUEST_ID" AS request_id,
    "PNR" AS pnr,
    "BOOKING_STATUS" AS booking_status,
    "DEPARTURE" AS departure,
    "DESTINATION" AS destination,
    "TRAVEL_DATE" AS travel_date,
    "AIRCRAFT_MODEL" AS aircraft_model,
    "AIRCRAFT_KIND" AS aircraft_kind
'''


class TripType(str, Enum):
    single_trip = 'Single Trip'
    round_trip = 'Round Trip'


class BookingRequest(BaseModel):
    customer_id: UUID | None = None
    aircraft_id: UUID | None = None
    service_type: str | None = None
    trip_type: TripType | None = None
    origin: str | None = Field(default=None, validation_alias=AliasChoices('departure', 'origin'))
    destination: str | None = None
    departure_date: str | None = Field(default=None, validation_alias=AliasChoices('departure_date', 'start_date'))
    arrival_date: str | None = Field(default=None, validation_alias=AliasChoices('return_date', 'end_date', 'arrival_date'))
    passenger_count: int | None = Field(default=None, validation_alias=AliasChoices('passenger_count', 'people_count'), gt=0)


class CancelBookingRequest(BaseModel):
    booking_id: UUID | None = None


class PaymentConfirmationRequest(CancelBookingRequest):
    payment_reference: str | None = None


class BookingRecordRequest(BaseModel):
    request_id: UUID | None = None
    pnr: str | None = None
    departure: str | None = None
    destination: str | None = None
    travel_date: date | None = None
    aircraft_model: str | None = None
    aircraft_kind: str | None = None


def find_booking(db, booking_id, lock=False):
    if booking_id is None:
        raise HTTPException(400, 'Please enter Booking ID.')
    row = db.execute(text(f'SELECT * FROM {TABLE} WHERE booking_id=:id' +
                          (' FOR UPDATE' if lock else '')), {'id': booking_id}).mappings().first()
    if row is None:
        raise HTTPException(404, 'Booking draft not found.')
    return dict(row)


def get_tracking(db, booking_id):
    row = db.execute(text(f'SELECT status, location, latitude, longitude FROM {TRACKING} '
                          'WHERE booking_id=:id LIMIT 1'), {'id': booking_id}).mappings().first()
    if row:
        return dict(row)
    booking = find_booking(db, booking_id)
    return dict(status='Not Started', location=booking['origin'], latitude=None, longitude=None)


def format_booking(db, booking):
    result = dict(booking)
    result.update({
        "id": str(result["booking_id"]),
        "departure": result.get("origin"),
        "return_date": result.get("arrival_date"),
        "status": result.get("booking_status"),
    })
    result['tracking'] = get_tracking(db, result['booking_id'])
    for legacy in ('booking_id', 'origin', 'arrival_date', 'booking_status'):
        result.pop(legacy, None)
    return result


def commit(db):
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'The change conflicts with linked records or database constraints.')


DRAFTS = 'customer_hj."CUSTOMER_BOOKING_DRAFTS"'
DRAFT_COLUMNS = '''"ID" AS id, "CUSTOMER_ID" AS customer_id,
    "DRAFT_DATA" AS draft_data, "CURRENT_STEP" AS current_step,
    "REQUEST_ID" AS request_id, "CREATED_AT" AS created_at,
    "UPDATED_AT" AS updated_at'''


class BookingDraftCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    customer_id: UUID | None = None
    draft_data: dict[str, Any] = Field(default_factory=dict)
    current_step: int = Field(default=0, ge=0)


class BookingDraftUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    draft_data: dict[str, Any] | None = None
    current_step: int | None = Field(default=None, ge=0)


def draft_customer(user=Depends(get_current_user)):
    return UUID(str(user['id']))


def checked_draft_data(value):
    # Drafts intentionally allow incomplete forms and empty date strings.
    data = dict(value)
    if 'trip_type' in data and data['trip_type'] not in ('ONE_WAY', 'ROUND_TRIP'):
        raise HTTPException(422, 'draft_data.trip_type must be ONE_WAY or ROUND_TRIP.')
    if 'aircraft_type' in data and data['aircraft_type'] not in ('', 'flight', 'helicopter'):
        raise HTTPException(422, 'draft_data.aircraft_type must be flight, helicopter, or empty.')
    if 'contact' in data and not isinstance(data['contact'], dict):
        raise HTTPException(422, 'draft_data.contact must be an object.')
    if 'passengers' in data:
        if not isinstance(data['passengers'], list) or any(not isinstance(p, dict) for p in data['passengers']):
            raise HTTPException(422, 'draft_data.passengers must be an array of objects.')
        for passenger in data['passengers']:
            if 'passenger_type' in passenger and passenger['passenger_type'] not in (
                'ADULT', 'CHILD', 'INFANT', 'UNACCOMPANIED_MINOR'
            ):
                raise HTTPException(422, 'Invalid draft passenger_type.')
    try:
        return json.dumps(data, allow_nan=False)
    except (TypeError, ValueError):
        raise HTTPException(422, 'draft_data must contain valid JSON values.')


def find_draft(db, draft_id, customer_id, lock=False):
    if draft_id is None:
        raise HTTPException(400, 'Please enter the booking draft ID.')
    row = db.execute(text(f'SELECT {DRAFT_COLUMNS} FROM {DRAFTS} '
                          'WHERE "ID"=:id AND "CUSTOMER_ID"=:customer_id' +
                          (' FOR UPDATE' if lock else '')),
                     {'id': draft_id, 'customer_id': customer_id}).mappings().first()
    if row is None:
        raise HTTPException(404, 'Booking draft not found.')
    return dict(row)


@router.get('/', summary='Get All Booking Drafts')
def get_bookings(customer_id: UUID = Depends(draft_customer), db: Session = Depends(get_db)):
    rows = db.execute(text(f'SELECT {DRAFT_COLUMNS} FROM {DRAFTS} '
                           'WHERE "CUSTOMER_ID"=:customer_id AND "REQUEST_ID" IS NULL '
                           'ORDER BY "UPDATED_AT" DESC, "ID"'),
                      {'customer_id': customer_id}).mappings().all()
    return [dict(row) for row in rows]


@router.post('/', summary='Create Booking Draft')
@limiter.limit('50/minute')
def create_booking(request: Request, booking_request: BookingDraftCreate | None = None,
                   customer_id: UUID = Depends(draft_customer), db: Session = Depends(get_db)):
    if booking_request is None:
        raise HTTPException(400, 'Please enter booking draft details.')
    if booking_request.customer_id is not None and booking_request.customer_id != customer_id:
        raise HTTPException(403, 'You can only create your own booking drafts.')
    draft_json = checked_draft_data(booking_request.draft_data)
    try:
        row = db.execute(text(f'INSERT INTO {DRAFTS} '
                              '("CUSTOMER_ID", "DRAFT_DATA", "CURRENT_STEP") '
                              'VALUES (:customer_id, CAST(:draft_data AS JSONB), :current_step) '
                              f'RETURNING {DRAFT_COLUMNS}'),
                         {'customer_id': customer_id, 'draft_data': draft_json,
                          'current_step': booking_request.current_step}).mappings().one()
        result = dict(row)
        db.commit()
        return result
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'Booking draft conflicts with database constraints.')
    except SQLAlchemyError:
        db.rollback()
        logger.exception('Failed to create booking draft.')
        raise HTTPException(500, 'Unable to create booking draft.')


@router.get('/by-id', summary='Get Booking Draft by ID')
def get_booking(booking_id: UUID | None = None,
                customer_id: UUID = Depends(draft_customer), db: Session = Depends(get_db)):
    return find_draft(db, booking_id, customer_id)


@router.put('/by-id', summary='Update Booking Draft by ID')
@limiter.limit('50/minute', per_method=True)
def update_booking(request: Request, booking_id: UUID | None = None,
                   booking_request: BookingDraftUpdate | None = None,
                   customer_id: UUID = Depends(draft_customer), db: Session = Depends(get_db)):
    if booking_request is None:
        raise HTTPException(400, 'Please enter changes.')
    values = booking_request.model_dump(exclude_unset=True, exclude_none=True)
    if not values:
        raise HTTPException(400, 'Please enter at least one field to update.')
    assignments = []
    if 'draft_data' in values:
        values['draft_data'] = checked_draft_data(values['draft_data'])
        assignments.append('"DRAFT_DATA"=CAST(:draft_data AS JSONB)')
    if 'current_step' in values:
        assignments.append('"CURRENT_STEP"=:current_step')
    try:
        current = find_draft(db, booking_id, customer_id, lock=True)
        if current['request_id'] is not None:
            raise HTTPException(409, 'Submitted drafts cannot be edited.')
        row = db.execute(text(f'UPDATE {DRAFTS} SET ' + ', '.join(assignments) +
                              ', "UPDATED_AT"=CURRENT_TIMESTAMP '
                              'WHERE "ID"=:id AND "CUSTOMER_ID"=:customer_id AND "REQUEST_ID" IS NULL '
                              f'RETURNING {DRAFT_COLUMNS}'),
                         dict(values, id=booking_id, customer_id=customer_id)).mappings().one()
        result = dict(row)
        db.commit()
        return result
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'Booking draft conflicts with database constraints.')
    except SQLAlchemyError:
        db.rollback()
        logger.exception('Failed to update booking draft.')
        raise HTTPException(500, 'Unable to update booking draft.')


@router.delete('/by-id', summary='Delete Booking Draft by ID')
@limiter.limit('50/minute', per_method=True)
def delete_booking(request: Request, booking_id: UUID | None = None,
                   customer_id: UUID = Depends(draft_customer), db: Session = Depends(get_db)):
    try:
        current = find_draft(db, booking_id, customer_id, lock=True)
        if current['request_id'] is not None:
            raise HTTPException(409, 'Submitted drafts cannot be deleted.')
        db.execute(text(f'DELETE FROM {DRAFTS} WHERE "ID"=:id '
                        'AND "CUSTOMER_ID"=:customer_id AND "REQUEST_ID" IS NULL'),
                   {'id': booking_id, 'customer_id': customer_id})
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'Booking draft is referenced by another record.')
    except SQLAlchemyError:
        db.rollback()
        logger.exception('Failed to delete booking draft.')
        raise HTTPException(500, 'Unable to delete booking draft.')
    return {'message': 'Booking draft deleted successfully', 'id': booking_id}


def draft_request_values(data):
    if not isinstance(data, dict):
        raise HTTPException(422, 'Draft data must be an object.')
    checked_draft_data(data)
    def required_text(source, field, label=None):
        value = source.get(field)
        if not isinstance(value, str) or not value.strip():
            raise HTTPException(422, f'{label or field} is required before submission.')
        return value.strip()
    aircraft = required_text(data, 'aircraft_type')
    if aircraft not in ('flight', 'helicopter'):
        raise HTTPException(422, 'Select an aircraft type before submission.')
    trip = required_text(data, 'trip_type')
    count = data.get('passenger_count')
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise HTTPException(422, 'passenger_count must be a positive integer.')
    try:
        budget = Decimal(str(data.get('budget')))
        if not budget.is_finite() or budget < 0:
            raise ValueError()
    except (InvalidOperation, ValueError):
        raise HTTPException(422, 'budget must be a finite, nonnegative number.')
    try:
        start = date.fromisoformat(required_text(data, 'departure_date'))
        end = date.fromisoformat(required_text(data, 'return_date')) if trip == 'ROUND_TRIP' else None
    except ValueError:
        raise HTTPException(422, 'Travel dates must use YYYY-MM-DD.')
    if end is not None and end < start:
        raise HTTPException(422, 'return_date cannot precede departure_date.')
    for field in ('aircraft_preference', 'additional_requirements'):
        if data.get(field) is not None and not isinstance(data[field], str):
            raise HTTPException(422, f'{field} must be text.')
    contact = data.get('contact')
    if not isinstance(contact, dict):
        raise HTTPException(422, 'Contact details are required before submission.')
    customer_details = dict(contact)
    customer_details['name'] = required_text(contact, 'full_name', 'contact.full_name')
    customer_details.pop('full_name', None)
    customer_details['email'] = required_text(contact, 'email', 'contact.email')
    customer_details['mobile'] = required_text(contact, 'mobile', 'contact.mobile')
    country = contact.get('country') or None
    if country is not None and not isinstance(country, str):
        raise HTTPException(422, 'contact.country must be text.')
    # Preserve the complete draft, including passenger and journey metadata.
    # Passenger records and payment/credit processing are separate workflows.
    return {
        'aircraft_type': 'Flight' if aircraft == 'flight' else 'Helicopter',
        'aircraft_model': data.get('aircraft_preference') or None,
        'people_count': count, 'budget': budget,
        'currency': required_text(data, 'currency'),
        'departure': required_text(data, 'departure'),
        'destination': required_text(data, 'destination'),
        'start_date': start, 'end_date': end, 'trip_type': trip,
        'customer_name': customer_details['name'],
        'customer_email': customer_details['email'],
        'customer_mobile': customer_details['mobile'], 'country': country,
        'additional_requirements': data.get('additional_requirements') or None,
        'customer_details': json.dumps(customer_details, allow_nan=False),
        'service_details': json.dumps(data, allow_nan=False),
    }


@router.post('/submit', summary='Submit Booking Draft')
@limiter.limit('50/minute', per_method=True)
def submit_booking_draft(request: Request, booking_id: UUID | None = None,
                         customer_id: UUID = Depends(draft_customer),
                         db: Session = Depends(get_db)):
    try:
        draft = find_draft(db, booking_id, customer_id, lock=True)
        if draft['request_id'] is not None:
            linked = db.execute(text(f'SELECT id, request_number FROM {REQUESTS} '
                                     'WHERE id=:id AND customer_id=:customer_id'),
                                {'id': draft['request_id'], 'customer_id': customer_id}).mappings().first()
            if linked is None:
                raise HTTPException(409, 'The linked request is unavailable; the draft was not resubmitted.')
            result = {'request_id': linked['id'], 'request_number': str(linked['request_number'])}
            db.commit()
            return result
        values = draft_request_values(draft['draft_data'])
        values['customer_id'] = customer_id
        created = db.execute(text(f'''
            INSERT INTO {REQUESTS} (
                customer_id, request_type, subject, service_type, service_subtype,
                aircraft_type, aircraft_model, people_count, budget, currency,
                departure, destination, start_date, end_date, trip_type,
                customer_name, customer_email, customer_mobile, country,
                additional_requirements, customer_details, service_details,
                booking_drafts, status, created_at, updated_at
            ) VALUES (
                :customer_id, 'Charter', 'Aviation request', 'Aviation', 'Charter',
                :aircraft_type, :aircraft_model, :people_count, :budget, :currency,
                :departure, :destination, :start_date, :end_date, :trip_type,
                :customer_name, :customer_email, :customer_mobile, :country,
                :additional_requirements, CAST(:customer_details AS JSONB),
                CAST(:service_details AS JSONB), false, 'Open', CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            ) RETURNING id, request_number
        '''), values).mappings().one()
        updated = db.execute(text(f'UPDATE {DRAFTS} SET "REQUEST_ID"=:request_id, '
                                  '"UPDATED_AT"=CURRENT_TIMESTAMP '
                                  'WHERE "ID"=:id AND "CUSTOMER_ID"=:customer_id AND "REQUEST_ID" IS NULL '
                                  'RETURNING "ID"'),
                             {'request_id': created['id'], 'id': booking_id,
                              'customer_id': customer_id}).first()
        if updated is None:
            raise HTTPException(409, 'Draft changed during submission. Please retry.')
        result = {'request_id': created['id'], 'request_number': str(created['request_number'])}
        db.commit()
        return result
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        logger.exception('Booking draft submission violated a database constraint.')
        raise HTTPException(409, 'Draft submission conflicts with database constraints.')
    except SQLAlchemyError:
        db.rollback()
        logger.exception('Failed to submit booking draft.')
        raise HTTPException(500, 'Unable to submit booking draft.')


@router.post('/payment-confirmation', summary='Confirm Payment')
@limiter.limit('50/minute', per_method=True)
def confirm_payment(request: Request, data: PaymentConfirmationRequest | None = None,
                    db: Session = Depends(get_db)):
    booking = find_booking(db, data.booking_id if data else None, lock=True)
    if not data.payment_reference or not data.payment_reference.strip():
        raise HTTPException(400, 'Payment reference is required.')
    if str(booking['booking_status']).lower() == 'cancelled':
        raise HTTPException(409, 'Cannot confirm payment for a cancelled booking draft.')
    if booking['payment_status'] == 'Confirmed':
        return {'message': 'Payment is already confirmed', 'booking': format_booking(db, booking)}
    db.execute(text(f"UPDATE {TABLE} SET payment_status='Confirmed', payment_reference=:ref, "
                    "booking_status='Booked' WHERE booking_id=:id"),
               {'ref': data.payment_reference.strip(), 'id': data.booking_id})
    commit(db)
    return {'message': 'Payment confirmed successfully', 'booking': format_booking(db, find_booking(db, data.booking_id))}


@router.get('/status', summary='Get Booking Draft Status')
def get_booking_status(booking_id: UUID | None = None, db: Session = Depends(get_db)):
    booking = find_booking(db, booking_id)
    return {'id': booking['booking_id'], 'status': booking['booking_status'],
            **{key: booking[key] for key in ('ticket_number', 'trip_type',
                'payment_status', 'payment_reference')}}


@router.post('/cancel', summary='Cancel Booking Draft')
@limiter.limit('50/minute', per_method=True)
def cancel_booking(request: Request, data: CancelBookingRequest | None = None,
                   db: Session = Depends(get_db)):
    booking = find_booking(db, data.booking_id if data else None, lock=True)
    if str(booking['booking_status']).lower() == 'cancelled':
        return {'message': 'Booking draft is already cancelled'}
    db.execute(text(f"UPDATE {TABLE} SET booking_status='cancelled' WHERE booking_id=:id"), {'id': data.booking_id})
    db.execute(text(f"UPDATE {TRACKING} SET status='Cancelled' WHERE booking_id=:id"), {'id': data.booking_id})
    commit(db)
    return {'message': 'Booking draft cancelled successfully', 'booking': format_booking(db, find_booking(db, data.booking_id))}


@router.get('/location', summary='Get Booking Draft Location')
def get_booking_location(booking_id: UUID | None = None, db: Session = Depends(get_db)):
    booking = find_booking(db, booking_id)
    return {'id': booking_id, 'status': booking['booking_status'], 'tracking': get_tracking(db, booking_id)}


@router.get('/history', summary='Get Booking Draft History')
def booking_history(db: Session = Depends(get_db)):
    rows = db.execute(text(f'SELECT * FROM {TABLE} ORDER BY created_at DESC, booking_id')).mappings()
    return {'booking_history': [format_booking(db, row) for row in rows]}


@router.post('/records', summary='Create Booking Record for Ticket', status_code=201)
@limiter.limit('50/minute', per_method=True)
def create_booking_record(
    request: Request,
    data: BookingRecordRequest | None = None,
    db: Session = Depends(get_db),
):
    if data is None or data.request_id is None:
        raise HTTPException(400, 'Please enter an existing customer Request ID.')

    try:
        source = db.execute(
            text(f'''
                SELECT id, departure, destination, start_date,
                       aircraft_model, aircraft_type
                FROM {REQUESTS}
                WHERE id=:request_id
                FOR KEY SHARE
            '''),
            {'request_id': data.request_id},
        ).mappings().first()
        if source is None:
            raise HTTPException(404, 'Customer request not found.')

        values = {
            'request_id': data.request_id,
            'pnr': data.pnr,
            'departure': data.departure if data.departure is not None else source['departure'],
            'destination': data.destination if data.destination is not None else source['destination'],
            'travel_date': data.travel_date if data.travel_date is not None else source['start_date'],
            'aircraft_model': data.aircraft_model if data.aircraft_model is not None else source['aircraft_model'],
            'aircraft_kind': data.aircraft_kind if data.aircraft_kind is not None else source['aircraft_type'],
        }
        # ID and pending status are generated by the table defaults.
        # Creating this record does not confirm payment or modify a draft.
        record = dict(db.execute(
            text(f'''
                INSERT INTO {RECORDS} (
                    "REQUEST_ID", "PNR", "DEPARTURE", "DESTINATION",
                    "TRAVEL_DATE", "AIRCRAFT_MODEL", "AIRCRAFT_KIND"
                )
                VALUES (
                    :request_id, :pnr, :departure, :destination,
                    :travel_date, :aircraft_model, :aircraft_kind
                )
                RETURNING {RECORD_FIELDS}
            '''),
            values,
        ).mappings().one())
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        logger.exception('Booking record values conflict with database constraints.')
        raise HTTPException(409, 'Booking record values conflict with database constraints.')
    except SQLAlchemyError:
        db.rollback()
        logger.exception('Booking record creation failed.')
        raise HTTPException(500, 'Unable to create booking record. Check the server terminal.')

    return {'message': 'Booking record created successfully', 'booking': record}


@router.get('/records', summary='Get Booking Records for Tickets')
def get_booking_records(request_id: UUID | None = None, db: Session = Depends(get_db)):
    query = f'SELECT {RECORD_FIELDS} FROM {RECORDS}'
    parameters = {}
    if request_id is not None:
        query += ' WHERE "REQUEST_ID"=:request_id'
        parameters['request_id'] = request_id
    rows = db.execute(text(query + ' ORDER BY "ID"'), parameters).mappings().all()
    return {'booking_records': [dict(row) for row in rows], 'count': len(rows)}
