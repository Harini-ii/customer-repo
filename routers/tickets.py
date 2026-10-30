import json
import logging
import textwrap
from io import BytesIO
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from reportlab.pdfgen import canvas
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db
from rate_limiter import limiter

router = APIRouter(prefix='/api/tickets', tags=['Tickets'])
logger = logging.getLogger(__name__)

TICKET_QUERY = '''
    SELECT t."ID" AS id, t."BOOKING_ID" AS booking_id,
           t."TICKET_NUMBER" AS ticket_number, t."TICKET_DATA" AS ticket_data,
           b."REQUEST_ID" AS request_id, b."PNR" AS pnr,
           b."BOOKING_STATUS" AS booking_status, b."DEPARTURE" AS departure,
           b."DESTINATION" AS destination, b."TRAVEL_DATE" AS travel_date,
           b."AIRCRAFT_MODEL" AS aircraft_model, b."AIRCRAFT_KIND" AS aircraft_kind
    FROM customer_hj."CUSTOMER_BOOKING_TICKETS" AS t
    JOIN customer_hj."CUSTOMER_BOOKING_RECORDS" AS b ON b."ID" = t."BOOKING_ID"
'''


class CreateTicketRequest(BaseModel):
    booking_id: UUID | None = None
    ticket_data: dict = Field(default_factory=dict)


def format_ticket(row):
    result = dict(row)
    metadata = result.get("ticket_data")
    metadata = metadata if isinstance(metadata, dict) else {}
    for field in ("passenger_id", "passenger_name", "sector", "generated_at"):
        if field not in result:
            result[field] = metadata.get(field)
    return result


def find_ticket(db, ticket_id):
    if ticket_id is None:
        raise HTTPException(400, 'Please enter Ticket ID.')
    row = db.execute(
        text(TICKET_QUERY + ' WHERE t."ID"=:ticket_id'),
        {'ticket_id': ticket_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(404, 'Ticket not found.')
    return format_ticket(row)


@router.post('/', summary='Create Ticket', status_code=201)
@limiter.limit('50/minute', per_method=True)
def create_ticket(
    request: Request,
    data: CreateTicketRequest | None = None,
    db: Session = Depends(get_db),
):
    if data is None or data.booking_id is None:
        raise HTTPException(400, 'Please enter Booking ID.')

    try:
        # Lock the booking so simultaneous calls to this endpoint cannot
        # create two tickets for the same booking.
        booking = db.execute(
            text('''
                SELECT "ID" AS id, "BOOKING_STATUS" AS booking_status
                FROM customer_hj."CUSTOMER_BOOKING_RECORDS"
                WHERE "ID"=:booking_id
                FOR UPDATE
            '''),
            {'booking_id': data.booking_id},
        ).mappings().first()
        if booking is None:
            raise HTTPException(404, 'Booking record not found.')
        if str(booking['booking_status']).strip().lower() in ('cancelled', 'canceled'):
            raise HTTPException(409, 'Cannot create a ticket for a cancelled booking.')

        existing = db.execute(
            text('''
                SELECT "ID" AS id
                FROM customer_hj."CUSTOMER_BOOKING_TICKETS"
                WHERE "BOOKING_ID"=:booking_id
                LIMIT 1
            '''),
            {'booking_id': data.booking_id},
        ).mappings().first()
        if existing is not None:
            raise HTTPException(
                409,
                detail={
                    'message': 'A ticket already exists for this booking.',
                    'ticket_id': str(existing['id']),
                },
            )

        # UUID-based numbering avoids duplicate numbers from COUNT(*) + 1.
        ticket_number = 'HJ-TKT-' + uuid4().hex.upper()
        inserted = db.execute(
            text('''
                INSERT INTO customer_hj."CUSTOMER_BOOKING_TICKETS" (
                    "BOOKING_ID", "TICKET_NUMBER", "TICKET_DATA"
                )
                VALUES (
                    :booking_id, :ticket_number, CAST(:ticket_data AS JSONB)
                )
                RETURNING "ID" AS id
            '''),
            {
                'booking_id': data.booking_id,
                'ticket_number': ticket_number,
                'ticket_data': json.dumps(data.ticket_data),
            },
        ).mappings().one()
        ticket = find_ticket(db, inserted['id'])
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        logger.exception('Ticket values conflict with database constraints.')
        raise HTTPException(409, 'Ticket values conflict with database constraints.')
    except SQLAlchemyError:
        db.rollback()
        logger.exception('Ticket creation failed.')
        raise HTTPException(500, 'Unable to create ticket. Check the server terminal.')

    return {
        'message': 'Ticket created successfully',
        'ticket': ticket,
    }


@router.get('/', summary='Get All Tickets')
def get_all_tickets(
    booking_id: UUID | None = None,
    request_id: UUID | None = None,
    db: Session = Depends(get_db),
):
    conditions = []
    parameters = {}
    if booking_id is not None:
        conditions.append('t."BOOKING_ID"=:booking_id')
        parameters['booking_id'] = booking_id
    if request_id is not None:
        conditions.append('b."REQUEST_ID"=:request_id')
        parameters['request_id'] = request_id
    query = TICKET_QUERY
    if conditions:
        query += ' WHERE ' + ' AND '.join(conditions)
    rows = db.execute(
        text(query + ' ORDER BY t."TICKET_NUMBER", t."ID"'), parameters,
    ).mappings().all()
    return {'tickets': [format_ticket(row) for row in rows], 'count': len(rows)}


@router.get('/by-id', summary='Get Ticket by ID')
def get_ticket(ticket_id: UUID | None = None, db: Session = Depends(get_db)):
    return {'ticket': find_ticket(db, ticket_id)}


@router.get('/download', summary='Download Ticket')
@limiter.limit('50/minute', per_method=True)
def download_ticket(
    request: Request,
    ticket_id: UUID | None = None,
    db: Session = Depends(get_db),
):
    ticket = find_ticket(db, ticket_id)
    if str(ticket['booking_status']).strip().lower() in ('cancelled', 'canceled'):
        raise HTTPException(409, 'Ticket download is unavailable for a cancelled booking.')
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.setTitle(f"Ticket-{ticket['ticket_number']}")
    pdf.setFont('Helvetica-Bold', 18)
    pdf.drawString(55, 795, 'HELITE JETS - Flight Ticket')
    y = 755

    def write_line(value):
        nonlocal y
        for line in textwrap.wrap(str(value), width=88) or ['']:
            if y < 65:
                pdf.showPage()
                y = 790
            pdf.setFont('Helvetica', 10)
            pdf.drawString(55, y, line)
            y -= 16

    for key in (
        'ticket_number', 'id', 'booking_id', 'request_id', 'pnr', 'booking_status',
        'departure', 'destination', 'travel_date', 'aircraft_model', 'aircraft_kind',
    ):
        write_line(f"{key.replace('_', ' ').title()}: {ticket.get(key) or '-'}")
    if ticket['ticket_data']:
        write_line('Ticket Data:')
        ticket_data = ticket['ticket_data']
        if isinstance(ticket_data, dict):
            for key, value in ticket_data.items():
                write_line(f'{key}: {json.dumps(value, ensure_ascii=True, default=str)}')
        else:
            write_line(json.dumps(ticket_data, ensure_ascii=True, default=str))
    pdf.save()
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type='application/pdf',
        headers={'Content-Disposition': f'attachment; filename=ticket-{ticket_id}.pdf'},
    )
