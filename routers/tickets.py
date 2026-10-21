import json
import textwrap
from io import BytesIO
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from reportlab.pdfgen import canvas
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import get_db
from rate_limiter import limiter

router = APIRouter(prefix='/api/tickets', tags=['Tickets'])
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


def find_ticket(db, ticket_id):
    if ticket_id is None:
        raise HTTPException(400, 'Please enter Ticket ID.')
    row = db.execute(text(TICKET_QUERY + ' WHERE t."ID"=:ticket_id'),
                     {'ticket_id': ticket_id}).mappings().first()
    if row is None:
        raise HTTPException(404, 'Ticket not found.')
    return dict(row)


@router.get('/', summary='Get All Tickets')
def get_all_tickets(booking_id: UUID | None = None, request_id: UUID | None = None,
                    db: Session = Depends(get_db)):
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
    rows = db.execute(text(query + ' ORDER BY t."TICKET_NUMBER", t."ID"'), parameters).mappings().all()
    return {'tickets': [dict(row) for row in rows], 'count': len(rows)}


@router.get('/by-id', summary='Get Ticket by ID')
def get_ticket(ticket_id: UUID | None = None, db: Session = Depends(get_db)):
    return {'ticket': find_ticket(db, ticket_id)}


@router.get('/download', summary='Download Ticket')
@limiter.limit('50/minute', per_method=True)
def download_ticket(request: Request, ticket_id: UUID | None = None,
                    db: Session = Depends(get_db)):
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

    for key in ('ticket_number', 'id', 'booking_id', 'request_id', 'pnr', 'booking_status',
                'departure', 'destination', 'travel_date', 'aircraft_model', 'aircraft_kind'):
        write_line(f"{key.replace('_', ' ').title()}: {ticket.get(key) or '-'}")
    if ticket['ticket_data']:
        write_line('Ticket Data:')
        data = ticket['ticket_data']
        if isinstance(data, dict):
            for key, value in data.items():
                write_line(f'{key}: {json.dumps(value, ensure_ascii=True, default=str)}')
        else:
            write_line(json.dumps(data, ensure_ascii=True, default=str))
    pdf.save()
    buffer.seek(0)
    return StreamingResponse(buffer, media_type='application/pdf', headers={
        'Content-Disposition': f'attachment; filename=ticket-{ticket_id}.pdf'})
