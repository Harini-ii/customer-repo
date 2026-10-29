import logging
from datetime import date
from enum import Enum
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db
from rate_limiter import limiter

router = APIRouter(prefix='/api/passengers', tags=['Passenger'])
logger = logging.getLogger(__name__)
PASSENGERS = 'customer_hj."CUSTOMER_PASSENGERS"'
DOCUMENTS = 'customer_hj."CUSTOMER_PASSENGER_DOCUMENTS"'
REQUESTS = 'customer_hj."CUSTOMER_REQUESTS"'
DOCUMENT_DIR = Path(__file__).resolve().parent.parent / 'uploads' / 'passenger_documents'


class DocumentType(str, Enum):
    government_photo_id = 'Government Photo ID'
    passport = 'Passport'
    visa = 'Visa'
    medical_fitness = 'Medical Fitness Certificate'


class PassengerType(str, Enum):
    oldage = 'Oldage'
    adult = 'Adult'
    teenager = 'Teenager'
    child = 'Child'


class PassengerRequest(BaseModel):
    request_id: UUID | None = None
    full_name: str | None = None
    date_of_birth: str | None = None
    nationality: str | None = None
    passenger_type: PassengerType | None = None
    identity_document: str | None = None
    foreign_national: bool | None = None
    visa_required: bool | None = None
    medical_fitness_documentation_required: bool | None = None
    emergency_contact_name: str | None = None
    emergency_contact_relationship: str | None = None
    emergency_contact_phone: str | None = None
    next_of_kin_name: str | None = None
    next_of_kin_relationship: str | None = None
    next_of_kin_phone: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None


def fetch(db, table, key, value, label, lock=False):
    if value is None:
        raise HTTPException(400, f'Please enter {label} ID.')
    row = db.execute(text(f'SELECT * FROM {table} WHERE {key}=:id' +
                          (' FOR UPDATE' if lock else '')), {'id': value}).mappings().first()
    if row is None:
        raise HTTPException(404, f'{label} not found.')
    return dict(row)


def passenger(db, passenger_id, lock=False):
    return fetch(db, PASSENGERS, 'passenger_id', passenger_id, 'Passenger', lock)


def customer_request(db, request_id):
    return fetch(db, REQUESTS, 'id', request_id, 'Request')


def document(db, document_id, lock=False):
    return fetch(db, DOCUMENTS, 'id', document_id, 'Document', lock)


def public_document(row):
    return {key: value for key, value in dict(row).items() if key != 'storage_path'}


def update_row(db, table, key, record_id, values):
    assignments = ', '.join(f'{column}=:{column}' for column in values)
    return dict(db.execute(text(f'UPDATE {table} SET {assignments}, updated_at=CURRENT_TIMESTAMP '
                                f'WHERE {key}=:record_id RETURNING *'),
                           dict(values, record_id=record_id)).mappings().one())


@router.post('/', summary='Add Passenger Details')
@limiter.limit('50/minute', per_method=True)
def add_passenger_details(request: Request, data: PassengerRequest | None = None,
                          db: Session = Depends(get_db)):
    if data is None or not data.full_name or not data.full_name.strip():
        raise HTTPException(400, 'Please enter full name and passenger details.')
    customer_request(db, data.request_id)
    values = data.model_dump(mode='json', exclude_none=True)
    values['full_name'] = data.full_name.strip()
    columns = ', '.join(values)
    params = ', '.join(':' + key for key in values)
    try:
        row = dict(db.execute(text(f'INSERT INTO {PASSENGERS} ({columns}) VALUES ({params}) RETURNING *'),
                              values).mappings().one())
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'Passenger values conflict with database constraints.')
    return {'message': 'Passenger details added successfully', 'passenger': row}


@router.get('/', summary='Get All Passenger Details')
def get_all_passengers(request_id: UUID | None = None, db: Session = Depends(get_db)):
    query = f'SELECT * FROM {PASSENGERS}'
    if request_id is not None:
        query += ' WHERE request_id=:request_id'
    rows = db.execute(text(query + ' ORDER BY created_at DESC, passenger_id'), {'request_id': request_id}).mappings()
    return {'passengers': [dict(row) for row in rows]}


@router.get('/by-id', summary='Get Passenger Details by ID')
def get_passenger(passenger_id: UUID | None = None, db: Session = Depends(get_db)):
    return {'passenger': passenger(db, passenger_id)}


@router.put('/by-id', summary='Update Passenger Details')
@limiter.limit('50/minute', per_method=True)
def update_passenger_details(request: Request, passenger_id: UUID | None = None,
                             data: PassengerRequest | None = None, db: Session = Depends(get_db)):
    existing = passenger(db, passenger_id, lock=True)
    if data is None:
        raise HTTPException(400, 'Please enter changes.')
    values = data.model_dump(mode='json', exclude_unset=True)
    if not values:
        raise HTTPException(400, 'Please enter at least one field to update.')
    if 'full_name' in values and (not values['full_name'] or not values['full_name'].strip()):
        raise HTTPException(400, 'Full name cannot be empty.')
    if 'request_id' in values:
        customer_request(db, data.request_id)
        if str(data.request_id) != str(existing['request_id']):
            linked = db.execute(text(f'SELECT 1 FROM {DOCUMENTS} WHERE passenger_id=:id LIMIT 1'),
                                {'id': passenger_id}).first()
            if linked:
                raise HTTPException(409, 'Cannot change the request while passenger documents are linked.')
    try:
        row = update_row(db, PASSENGERS, 'passenger_id', passenger_id, values)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'Passenger values conflict with database constraints.')
    return {'message': 'Passenger details updated successfully', 'passenger': row}


@router.delete('/by-id', summary='Delete Passenger Details')
@limiter.limit('50/minute', per_method=True)
def delete_passenger_details(request: Request, passenger_id: UUID | None = None,
                             db: Session = Depends(get_db)):
    passenger(db, passenger_id, lock=True)
    try:
        db.execute(text(f'DELETE FROM {PASSENGERS} WHERE passenger_id=:id'), {'id': passenger_id})
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'Passenger has linked documents or timeline records. Remove those links before deleting.')
    return {'message': 'Passenger details deleted successfully', 'passenger_id': passenger_id}


def validate_dates(issue_date, expiry_date):
    if issue_date is not None and expiry_date is not None and expiry_date < issue_date:
        raise HTTPException(400, 'Expiry date cannot be earlier than issue date.')


def save_file(upload, saved_paths):
    filename = (upload.filename or '').replace('\\', '/').rsplit('/', 1)[-1]
    if not filename:
        raise HTTPException(400, 'Please select a document file.')
    DOCUMENT_DIR.mkdir(parents=True, exist_ok=True)
    path = DOCUMENT_DIR / uuid4().hex
    saved_paths.append(path)
    size = 0
    upload.file.seek(0)
    with path.open('wb') as destination:
        while chunk := upload.file.read(1024 * 1024):
            destination.write(chunk)
            size += len(chunk)
    if not size:
        raise HTTPException(400, f'{filename} is empty.')
    return dict(documents=filename, storage_path=f'uploads/passenger_documents/{path.name}',
                mime_type=upload.content_type or 'application/octet-stream', file_size=size)


def cleanup(paths):
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.exception('Unable to clean up a document file.')


def remove_stored_file(storage_path):
    if not storage_path:
        return
    project = DOCUMENT_DIR.parent.parent.resolve()
    path = (project / storage_path).resolve()
    if path.parent == DOCUMENT_DIR.resolve():
        cleanup([path])
    else:
        logger.warning('Skipped document cleanup outside the managed document directory.')


@router.post('/documents', summary='Upload Passenger Documents')
@limiter.limit('50/minute', per_method=True)
def upload_passenger_documents(
    request: Request,
    passenger_id: UUID | None = Form(None),
    request_id: UUID | None = Form(None),
    # A plain array of binary files lets Swagger show a multiple-file picker.
    # The empty default keeps the field optional in the API schema; the
    # endpoint returns a clear 400 response when no files are selected.
    documents: list[UploadFile] = File(
        default=[],
        description='Select one or more document files using Choose Files.',
        json_schema_extra={'items': {'type': 'string', 'format': 'binary'}},
    ),
    document_type: DocumentType | None = Form(None),
    document_reference: str | None = Form(None),
    issuing_country: str | None = Form(None),
    issue_date: date | None = Form(None),
    expiry_date: date | None = Form(None),
    uploaded_by: int | None = Form(None, gt=0),
    db: Session = Depends(get_db),
):
    saved = []
    records = []
    try:
        person = passenger(db, passenger_id, lock=True)
        customer_request(db, request_id)
        if str(person['request_id']) != str(request_id):
            raise HTTPException(400, 'Request ID must match the passenger request.')
        validate_dates(issue_date, expiry_date)
        if not documents:
            raise HTTPException(400, 'Please select at least one document.')
        for upload in documents:
            values = save_file(upload, saved)
            values.update(passenger_id=passenger_id, request_id=request_id, document_type=document_type,
                          document_reference=document_reference, issuing_country=issuing_country,
                          issue_date=issue_date, expiry_date=expiry_date, uploaded_by=uploaded_by)
            columns = ', '.join(values)
            params = ', '.join(':' + key for key in values)
            records.append(public_document(db.execute(text(
                f'INSERT INTO {DOCUMENTS} ({columns}) VALUES ({params}) RETURNING *'), values).mappings().one()))
        db.commit()
    except HTTPException:
        db.rollback()
        cleanup(saved)
        raise
    except (SQLAlchemyError, OSError):
        db.rollback()
        cleanup(saved)
        logger.exception('Document upload failed.')
        raise HTTPException(500, 'Unable to upload documents. Check the server terminal.')
    finally:
        for upload in documents or []:
            upload.file.close()
    return {'message': 'Passenger documents uploaded successfully', 'documents': records, 'total_documents': len(records)}


@router.get('/documents', summary='Get All Passenger Documents')
def get_all_documents(passenger_id: UUID | None = None, request_id: UUID | None = None,
                      db: Session = Depends(get_db)):
    filters = []
    values = {}
    if passenger_id is not None:
        filters.append('passenger_id=:passenger_id')
        values['passenger_id'] = passenger_id
    if request_id is not None:
        filters.append('request_id=:request_id')
        values['request_id'] = request_id
    query = f'SELECT * FROM {DOCUMENTS}'
    if filters:
        query += ' WHERE ' + ' AND '.join(filters)
    rows = db.execute(text(query + ' ORDER BY created_at DESC, id'), values).mappings()
    return {'documents': [public_document(row) for row in rows]}


@router.get('/documents/by-id', summary='Get Passenger Document by ID')
def get_document(document_id: UUID | None = None, db: Session = Depends(get_db)):
    return {'document': public_document(document(db, document_id))}


@router.put('/documents/by-id', summary='Update Uploaded Passenger Document')
@limiter.limit('50/minute', per_method=True)
def update_document(
    request: Request,
    document_id: UUID | None = Form(None),
    # Keep the OpenAPI schema binary rather than binary-or-null so Swagger
    # shows a file picker. Omission still defaults to None for metadata edits.
    uploaded_document: UploadFile = File(
        default=None,
        description='Choose a replacement file, or omit to update metadata only.',
        json_schema_extra={'type': 'string', 'format': 'binary'},
    ),
    document_type: DocumentType | None = Form(None),
    document_reference: str | None = Form(None),
    issuing_country: str | None = Form(None),
    issue_date: date | None = Form(None),
    expiry_date: date | None = Form(None),
    uploaded_by: int | None = Form(None, gt=0),
    db: Session = Depends(get_db),
):
    saved = []
    old_path = None
    try:
        existing = document(db, document_id, lock=True)
        values = {key: value for key, value in dict(document_type=document_type,
            document_reference=document_reference, issuing_country=issuing_country,
            issue_date=issue_date, expiry_date=expiry_date, uploaded_by=uploaded_by).items() if value is not None}
        validate_dates(values.get('issue_date', existing['issue_date']),
                       values.get('expiry_date', existing['expiry_date']))
        if uploaded_document is not None:
            values.update(save_file(uploaded_document, saved))
            old_path = existing['storage_path']
        if not values:
            raise HTTPException(400, 'Please select a replacement file or enter changes.')
        values.update(status='pending', rejection_reason=None, verified_by=None,
                      verified_at=None, verification_notes=None)
        assignments = ', '.join(f'{key}=:{key}' for key in values)
        row = db.execute(text(f'UPDATE {DOCUMENTS} SET {assignments}, '
            'uploaded_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE id=:id RETURNING *'),
            dict(values, id=document_id)).mappings().one()
        result = public_document(row)
        db.commit()
    except HTTPException:
        db.rollback()
        cleanup(saved)
        raise
    except (SQLAlchemyError, OSError):
        db.rollback()
        cleanup(saved)
        logger.exception('Document update failed.')
        raise HTTPException(500, 'Unable to update document. Check the server terminal.')
    finally:
        if uploaded_document is not None:
            uploaded_document.file.close()
    remove_stored_file(old_path)
    return {'message': 'Passenger document updated successfully', 'document': result}


@router.delete('/documents/by-id', summary='Delete Passenger Document')
@limiter.limit('50/minute', per_method=True)
def delete_document(request: Request, document_id: UUID | None = None,
                    db: Session = Depends(get_db)):
    existing = document(db, document_id, lock=True)
    try:
        db.execute(text(f'DELETE FROM {DOCUMENTS} WHERE id=:id'), {'id': document_id})
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'Document has linked timeline records. Remove those links before deleting.')
    remove_stored_file(existing['storage_path'])
    return {'message': 'Passenger document deleted successfully', 'document_id': document_id}
