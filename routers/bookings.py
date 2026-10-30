import logging
from datetime import date, datetime
from enum import Enum
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import AliasChoices, BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db
from rate_limiter import limiter

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


@router.get('/', summary='Get All Booking Drafts')
def get_bookings(db: Session = Depends(get_db)):
    rows = db.execute(text(f'SELECT * FROM {TABLE} ORDER BY created_at DESC, booking_id')).mappings()
    return {'bookings': [format_booking(db, row) for row in rows]}


@router.post('/', summary='Create Booking Draft')
@limiter.limit('50/minute')
def create_booking(request: Request, booking_request: BookingRequest | None = None,
                   db: Session = Depends(get_db)):
    if booking_request is None:
        raise HTTPException(400, 'Please enter booking details.')
    values = booking_request.model_dump(mode='json')
    values['trip_type'] = values['trip_type'] or 'Single Trip'
    values.update(booking_id=uuid4(), ticket_number='HJ-TKT-' + uuid4().hex.upper(),
                  booking_status='Submitted', payment_status='Pending',
                  payment_reference=None, created_at=datetime.now())
    columns = ', '.join(values)
    params = ', '.join(':' + key for key in values)
    try:
        booking = dict(db.execute(text(f'INSERT INTO {TABLE} ({columns}) VALUES ({params}) RETURNING *'),
                                  values).mappings().one())
        db.execute(text(f'INSERT INTO {TRACKING} (booking_id,status,location) '
                        "VALUES (:id,'Not Started',:origin)"),
                   {'id': booking['booking_id'], 'origin': booking['origin']})
        commit(db)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'Check customer ID and booking values against the database constraints.')
    return {'message': 'Booking draft created successfully', 'booking': format_booking(db, booking)}


@router.get('/by-id', summary='Get Booking Draft by ID')
def get_booking(booking_id: UUID | None = None, db: Session = Depends(get_db)):
    return format_booking(db, find_booking(db, booking_id))


@router.put('/by-id', summary='Update Booking Draft by ID')
@limiter.limit('50/minute', per_method=True)
def update_booking(request: Request, booking_id: UUID | None = None,
                   booking_request: BookingRequest | None = None, db: Session = Depends(get_db)):
    find_booking(db, booking_id, lock=True)
    if booking_request is None:
        raise HTTPException(400, 'Please enter changes.')
    values = booking_request.model_dump(mode='json', exclude_unset=True)
    if not values:
        raise HTTPException(400, 'Please enter at least one field to update.')
    assignments = ', '.join(f'{key}=:{key}' for key in values)
    try:
        db.execute(text(f'UPDATE {TABLE} SET {assignments} WHERE booking_id=:id'),
                   dict(values, id=booking_id))
        commit(db)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'Check customer ID and booking values against the database constraints.')
    return {'message': 'Booking draft updated successfully', 'booking': format_booking(db, find_booking(db, booking_id))}


@router.delete('/by-id', summary='Delete Booking Draft by ID')
@limiter.limit('50/minute', per_method=True)
def delete_booking(request: Request, booking_id: UUID | None = None, db: Session = Depends(get_db)):
    find_booking(db, booking_id, lock=True)
    try:
        db.execute(text(f'DELETE FROM {TRACKING} WHERE booking_id=:id'), {'id': booking_id})
        db.execute(text(f'DELETE FROM {TABLE} WHERE booking_id=:id'), {'id': booking_id})
        commit(db)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'Booking draft is referenced by another record.')
    return {'message': 'Booking draft deleted successfully', 'booking_id': booking_id}


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
