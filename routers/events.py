from uuid import UUID
import os
import uuid
import shutil
from enum import Enum
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from pydantic import AliasChoices, BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session
from database import get_db

router = APIRouter(
    prefix="/api/events",
    tags=["Events"],
)


class EventType(str, Enum):
    special_event = "Special event"
    emergency = "Emergency"


class Timing(str, Enum):
    immediate = "Immediate"
    scheduled = "Scheduled"


class AircraftType(str, Enum):
    flight = "Flight"
    helicopter = "Helicopter"


class AircraftModel(str, Enum):
    hawker_800xp_8 = "Hawker 800XP · 8"
    hawker_850xp_9 = "Hawker 850XP · 9"
    hawker_850xp_8 = "Hawker 850XP · 8"
    hawker_900xp_9 = "Hawker 900XP · 9"
    hawker_beechcraft_900xp_9 = "Hawker Beechcraft 900XP · 9"
    king_air_250 = "King Air 250 (B200GT) · 7"
    pc_12_45 = "PC-12/45 · 6"
    pc_12_47e = "PC-12/47E · 8"
    stemme_s8rt = "Stemme S8RT · 2"
    stemme_s6rt = "Stemme S6RT · 2"
    super_king_air_b200 = "Super King Air B200 · 7"
    super_king_air_b300_aerial = "Super King Air B300 · 7 / Aerial Work"
    super_king_air_b300 = "Super King Air B300 · 4"
    tecnam_p2010_tdi = "Tecnam P2010 TDI · 3"


class IdentityDocumentType(str, Enum):
    aadhaar = "Aadhaar"
    pan_card = "PAN Card"
    driving_licence = "Driving Licence"
    passport = "Passport"


class EventFields(BaseModel):
    event_type: EventType | None = Field(default=None, validation_alias=AliasChoices('subtype', 'event_type'))
    timing: Timing | None = None
    event_purpose: str | None = Field(default=None, validation_alias=AliasChoices('purpose', 'event_purpose'))
    occasion_name: str | None = Field(default=None, validation_alias=AliasChoices('eventName', 'occasion_name'))
    selected_experience: str | None = Field(default=None, validation_alias=AliasChoices('experienceName', 'selected_experience'))
    event_details: str | None = Field(default=None, validation_alias=AliasChoices('customExperience', 'event_details'))
    event_date: str | None = Field(default=None, validation_alias=AliasChoices('eventDate', 'event_date'))
    event_time: str | None = Field(default=None, validation_alias=AliasChoices('eventStartTime', 'event_time'))
    date: str | None = None
    time: str | None = None
    event_end_date: str | None = Field(default=None, validation_alias=AliasChoices('eventEndDate', 'event_end_date'))
    event_end_time: str | None = Field(default=None, validation_alias=AliasChoices('eventEndTime', 'event_end_time'))
    city_or_departure_area: str | None = Field(default=None, validation_alias=AliasChoices('eventCity', 'city_or_departure_area'))
    venue: str | None = None
    venue_address: str | None = Field(default=None, validation_alias=AliasChoices('venueAddress', 'venue_address'))
    estimated_budget: float | None = Field(default=None, validation_alias=AliasChoices('budget', 'estimated_budget'))
    budget_currency: str | None = Field(default=None, validation_alias=AliasChoices('currency', 'budget_currency'))
    date_flexibility: str | None = Field(default=None, validation_alias=AliasChoices('dateFlexibility', 'date_flexibility'))
    event_notes: str | None = Field(default=None, validation_alias=AliasChoices('eventNotes', 'event_notes'))
    number_of_people: int | None = Field(default=None, validation_alias=AliasChoices('totalAttendees', 'number_of_people'))
    aircraft_type: AircraftType | None = Field(default=None, validation_alias=AliasChoices('aircraftKind', 'aircraft_type'))
    aircraft_model: AircraftModel | None = Field(default=None, validation_alias=AliasChoices('preferredAircraft', 'aircraft_model'))
    aircraft_passengers: int | None = Field(default=None, validation_alias=AliasChoices('passengerCount', 'aircraft_passengers'))
    flight_type: str | None = Field(default=None, validation_alias=AliasChoices('tripType', 'flight_type'))
    departure_location: str | None = Field(default=None, validation_alias=AliasChoices('departure', 'departure_location'))
    event_destination: str | None = Field(default=None, validation_alias=AliasChoices('destination', 'event_destination'))
    preferred_flight_date: str | None = Field(default=None, validation_alias=AliasChoices('departureDate', 'preferred_flight_date'))
    preferred_departure_time: str | None = Field(default=None, validation_alias=AliasChoices('departureTime', 'preferred_departure_time'))
    ground_pickup: bool | None = Field(default=None, validation_alias=AliasChoices('groundPickup', 'ground_pickup'))
    vehicle_transfer: bool | None = Field(default=None, validation_alias=AliasChoices('vehicleTransfer', 'vehicle_transfer'))
    vip_reception: bool | None = Field(default=None, validation_alias=AliasChoices('vipReception', 'vip_reception'))
    baggage_coordination: bool | None = Field(default=None, validation_alias=AliasChoices('baggageCoordination', 'baggage_coordination'))
    accessibility_assistance: bool | None = Field(default=None, validation_alias=AliasChoices('accessibilityAssistance', 'accessibility_assistance'))
    special_aviation_requirements: str | None = Field(default=None, validation_alias=AliasChoices('aviationNotes', 'special_aviation_requirements'))
    food_preferences: str | None = None
    drinks: str | None = None
    champagne_service: bool | None = Field(default=None, validation_alias=AliasChoices('champagneRequested', 'champagne_service'))
    crew_preferences: str | None = None
    catering_requested: bool | None = Field(default=None, validation_alias=AliasChoices('cateringRequired', 'catering_requested'))
    dietary_preferences: str | None = Field(default=None, validation_alias=AliasChoices('dietaryPreferences', 'dietary_preferences'))
    meal_preferences: str | None = Field(default=None, validation_alias=AliasChoices('mealPreferences', 'meal_preferences'))
    catering_notes: str | None = Field(default=None, validation_alias=AliasChoices('cateringNotes', 'catering_notes'))
    beverage_service_requested: bool | None = Field(default=None, validation_alias=AliasChoices('beverageRequired', 'beverage_service_requested'))
    beverage_preferences: str | None = Field(default=None, validation_alias=AliasChoices('beveragePreferences', 'beverage_preferences'))
    decorations_requested: bool | None = Field(default=None, validation_alias=AliasChoices('decorationRequired', 'decorations_requested'))
    occasion_theme: str | None = Field(default=None, validation_alias=AliasChoices('decorationTheme', 'occasion_theme'))
    decoration_notes: str | None = Field(default=None, validation_alias=AliasChoices('decorationNotes', 'decoration_notes'))
    photography_videography_requested: bool | None = Field(default=None, validation_alias=AliasChoices('photographyRequired', 'photography_videography_requested'))
    photography_notes: str | None = Field(default=None, validation_alias=AliasChoices('photographyNotes', 'photography_notes'))
    vip_guest_arrangements: str | None = Field(default=None, validation_alias=AliasChoices('vipRequirements', 'vip_guest_arrangements'))
    privacy_requirements: str | None = Field(default=None, validation_alias=AliasChoices('privacyRequirements', 'privacy_requirements'))
    special_assistance: str | None = Field(default=None, validation_alias=AliasChoices('specialAssistance', 'special_assistance'))
    other_experience_requirements: str | None = Field(default=None, validation_alias=AliasChoices('preferenceNotes', 'other_experience_requirements'))
    full_name: str | None = Field(default=None, validation_alias=AliasChoices('contactName', 'full_name'))
    email: str | None = Field(default=None, validation_alias=AliasChoices('contactEmail', 'email'))
    mobile_number: str | None = Field(default=None, validation_alias=AliasChoices('contactMobile', 'mobile_number'))
    identity_document: str | None = None
    country: str | None = None
    company: str | None = None
    event_coordinator: str | None = Field(default=None, validation_alias=AliasChoices('coordinatorName', 'event_coordinator'))
    alternate_contact_number: str | None = Field(default=None, validation_alias=AliasChoices('alternatePhone', 'alternate_contact_number'))
    preferred_contact_method: str | None = Field(default=None, validation_alias=AliasChoices('preferredContactMethod', 'preferred_contact_method'))
    contact_notes: str | None = Field(default=None, validation_alias=AliasChoices('contactNotes', 'contact_notes'))
    


class CreateEventRequest(EventFields):
    pass


class UpdateEventRequest(EventFields):
    event_id: UUID | None = Field(default=None, validation_alias=AliasChoices('id', 'event_id'))


EVENT_COLUMNS = {field: field for field in EventFields.model_fields}
EVENT_COLUMNS["date"] = "event_date"
EVENT_COLUMNS["time"] = "event_time"
UPLOAD_FOLDER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "uploads",
)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def save_upload(upload: UploadFile) -> str:
    original_name = os.path.basename(upload.filename or "document")
    extension = os.path.splitext(original_name)[1]
    saved_name = f"{uuid.uuid4().hex}{extension}"
    file_path = os.path.join(UPLOAD_FOLDER, saved_name)
    with open(file_path, "wb") as destination:
        shutil.copyfileobj(upload.file, destination)
    return saved_name


def remove_saved_upload(filename: str):
    try:
        os.remove(os.path.join(UPLOAD_FOLDER, filename))
    except OSError:
        pass


def fetch_event(db: Session, event_id: UUID):
    event = (
        db.execute(
            text("""
            SELECT *
            FROM customer_hj."CUSTOMER_EVENTS"
            WHERE event_id = :event_id
        """),
            {"event_id": event_id},
        )
        .mappings()
        .first()
    )
    if event is None:
        return None
    event_data = dict(event)
    event_data["id"] = event_data.pop("event_id")
    event_data["date"] = event_data.pop("event_date", None)
    event_data["time"] = event_data.pop("event_time", None)
    passenger_rows = (
        db.execute(
            text("""
            SELECT
                passenger_id,
                full_name,
                date_of_birth,
                nationality,
                passport_number,
                passport_expiry,
                passenger_type,
                identity_document_type,
                identity_document,
                passport_file
            FROM customer_hj."CUSTOMER_EVENT_PASSENGERS"
            WHERE event_id = :event_id
            ORDER BY passenger_id
        """),
            {"event_id": event_id},
        )
        .mappings()
        .all()
    )
    event_data["passengers"] = [
        {
            "id": passenger["passenger_id"],
            "full_name": passenger["full_name"],
            "date_of_birth": passenger["date_of_birth"],
            "nationality": passenger["nationality"],
            "passport_number": passenger["passport_number"],
            "passport_expiry": passenger["passport_expiry"],
            "passport_file": passenger["passport_file"],
            "passenger_type": passenger["passenger_type"],
            "identity_document_type": passenger["identity_document_type"],
            "identity_document_file": passenger["identity_document"],
        }
        for passenger in passenger_rows
    ]
    # Translate database columns to the UI field names once.
    event_data['subtype'] = event_data.pop('event_type', None)
    event_data['purpose'] = event_data.pop('event_purpose', None)
    event_data['eventName'] = event_data.pop('occasion_name', None)
    event_data['experienceName'] = event_data.pop('selected_experience', None)
    event_data['customExperience'] = event_data.pop('event_details', None)
    event_data['eventDate'] = event_data.pop('date', None)
    event_data['eventStartTime'] = event_data.pop('time', None)
    event_data['eventEndDate'] = event_data.pop('event_end_date', None)
    event_data['eventEndTime'] = event_data.pop('event_end_time', None)
    event_data['eventCity'] = event_data.pop('city_or_departure_area', None)
    event_data['venueAddress'] = event_data.pop('venue_address', None)
    event_data['budget'] = event_data.pop('estimated_budget', None)
    event_data['currency'] = event_data.pop('budget_currency', None)
    event_data['dateFlexibility'] = event_data.pop('date_flexibility', None)
    event_data['eventNotes'] = event_data.pop('event_notes', None)
    event_data['totalAttendees'] = event_data.pop('number_of_people', None)
    event_data['aircraftKind'] = event_data.pop('aircraft_type', None)
    event_data['preferredAircraft'] = event_data.pop('aircraft_model', None)
    event_data['passengerCount'] = event_data.pop('aircraft_passengers', None)
    event_data['tripType'] = event_data.pop('flight_type', None)
    event_data['departure'] = event_data.pop('departure_location', None)
    event_data['destination'] = event_data.pop('event_destination', None)
    event_data['departureDate'] = event_data.pop('preferred_flight_date', None)
    event_data['departureTime'] = event_data.pop('preferred_departure_time', None)
    event_data['groundPickup'] = event_data.pop('ground_pickup', None)
    event_data['vehicleTransfer'] = event_data.pop('vehicle_transfer', None)
    event_data['vipReception'] = event_data.pop('vip_reception', None)
    event_data['baggageCoordination'] = event_data.pop('baggage_coordination', None)
    event_data['accessibilityAssistance'] = event_data.pop('accessibility_assistance', None)
    event_data['aviationNotes'] = event_data.pop('special_aviation_requirements', None)
    event_data['cateringRequired'] = event_data.pop('catering_requested', None)
    event_data['dietaryPreferences'] = event_data.pop('dietary_preferences', None)
    event_data['mealPreferences'] = event_data.pop('meal_preferences', None)
    event_data['cateringNotes'] = event_data.pop('catering_notes', None)
    event_data['beverageRequired'] = event_data.pop('beverage_service_requested', None)
    event_data['beveragePreferences'] = event_data.pop('beverage_preferences', None)
    event_data['champagneRequested'] = event_data.pop('champagne_service', None)
    event_data['decorationRequired'] = event_data.pop('decorations_requested', None)
    event_data['decorationTheme'] = event_data.pop('occasion_theme', None)
    event_data['decorationNotes'] = event_data.pop('decoration_notes', None)
    event_data['photographyRequired'] = event_data.pop('photography_videography_requested', None)
    event_data['photographyNotes'] = event_data.pop('photography_notes', None)
    event_data['vipRequirements'] = event_data.pop('vip_guest_arrangements', None)
    event_data['privacyRequirements'] = event_data.pop('privacy_requirements', None)
    event_data['specialAssistance'] = event_data.pop('special_assistance', None)
    event_data['preferenceNotes'] = event_data.pop('other_experience_requirements', None)
    event_data['contactName'] = event_data.pop('full_name', None)
    event_data['contactEmail'] = event_data.pop('email', None)
    event_data['contactMobile'] = event_data.pop('mobile_number', None)
    event_data['coordinatorName'] = event_data.pop('event_coordinator', None)
    event_data['alternatePhone'] = event_data.pop('alternate_contact_number', None)
    event_data['preferredContactMethod'] = event_data.pop('preferred_contact_method', None)
    event_data['contactNotes'] = event_data.pop('contact_notes', None)
    event_data.pop("title", None)
    event_data.pop("description", None)
    return event_data


def validate_event_fields(data: dict):
    for field in ("number_of_people", "aircraft_passengers"):
        value = data.get(field)
        if value is not None and value < 1:
            raise HTTPException(
                status_code=400,
                detail=f"{field} must be at least 1.",
            )
    budget = data.get("estimated_budget")
    if budget is not None and budget < 0:
        raise HTTPException(
            status_code=400,
            detail="Estimated budget cannot be negative.",
        )


def normalize_event_fields(data: dict):
    data = {
        key: value.value if isinstance(value, Enum) else value
        for key, value in data.items()
        if value is not None and key in EVENT_COLUMNS
    }
    for alias, column in (
        ("date", "event_date"),
        ("time", "event_time"),
    ):
        if alias in data:
            value = data.pop(alias)
            if column not in data:
                data[column] = value
    validate_event_fields(data)
    return data


def create_event_record(data: dict, db: Session):
    data = normalize_event_fields(data)
    customer_id = None
    email = data.get("email")
    try:
        if email:
            customer = (
                db.execute(
                    text("""
                    SELECT id AS customer_id
                    FROM customer_hj."CUSTOMER_PROFILE"
                    WHERE LOWER(email) = LOWER(:email)
                    LIMIT 1
                """),
                    {"email": email.strip()},
                )
                .mappings()
                .first()
            )
            if customer:
                customer_id = customer["customer_id"]
        data.update(
            {
                "customer_id": customer_id,
                "title": data.get("event_type"),
                "description": data.get("event_details"),
                "status": "pending",
            }
        )
        columns = ", ".join(data)
        placeholders = ", ".join(f":{column}" for column in data)
        event_id = db.execute(
            text(f"""
                INSERT INTO customer_hj."CUSTOMER_EVENTS" (
                    {columns},
                    created_at
                )
                VALUES (
                    {placeholders},
                    CURRENT_TIMESTAMP
                )
                RETURNING event_id
            """),
            data,
        ).scalar_one()
        db.commit()
    except Exception:
        db.rollback()
        raise
    return event_id


def event_creation_response(event_id: UUID, db: Session):
    return {
        "message": "Event created successfully",
        "event": fetch_event(db, event_id),
        "next_step": (
            f"Add extra passengers using " f"POST /api/events/{event_id}/passengers"
        ),
    }


@router.post("/", summary="Create Event Request")
def create_event(
    event_type: EventType | None = Form(None),
    timing: Timing | None = Form(None),
    event_purpose: str | None = Form(None),
    occasion_name: str | None = Form(None),
    selected_experience: str | None = Form(None),
    event_details: str | None = Form(None),
    event_date: str | None = Form(None),
    event_time: str | None = Form(None),
    date: str | None = Form(None),
    time: str | None = Form(None),
    event_end_date: str | None = Form(None),
    event_end_time: str | None = Form(None),
    city_or_departure_area: str | None = Form(None),
    venue: str | None = Form(None),
    venue_address: str | None = Form(None),
    estimated_budget: float | None = Form(None),
    budget_currency: str | None = Form(None),
    date_flexibility: str | None = Form(None),
    event_notes: str | None = Form(None),
    number_of_people: int | None = Form(None),
    aircraft_type: AircraftType | None = Form(None),
    aircraft_model: AircraftModel | None = Form(None),
    aircraft_passengers: int | None = Form(None),
    flight_type: str | None = Form(None),
    departure_location: str | None = Form(None),
    event_destination: str | None = Form(None),
    preferred_flight_date: str | None = Form(None),
    preferred_departure_time: str | None = Form(None),
    ground_pickup: bool | None = Form(None),
    vehicle_transfer: bool | None = Form(None),
    vip_reception: bool | None = Form(None),
    baggage_coordination: bool | None = Form(None),
    accessibility_assistance: bool | None = Form(None),
    special_aviation_requirements: str | None = Form(None),
    food_preferences: str | None = Form(None),
    drinks: str | None = Form(None),
    champagne_service: bool | None = Form(None),
    crew_preferences: str | None = Form(None),
    catering_requested: bool | None = Form(None),
    dietary_preferences: str | None = Form(None),
    meal_preferences: str | None = Form(None),
    catering_notes: str | None = Form(None),
    beverage_service_requested: bool | None = Form(None),
    beverage_preferences: str | None = Form(None),
    decorations_requested: bool | None = Form(None),
    occasion_theme: str | None = Form(None),
    decoration_notes: str | None = Form(None),
    photography_videography_requested: bool | None = Form(None),
    photography_notes: str | None = Form(None),
    vip_guest_arrangements: str | None = Form(None),
    privacy_requirements: str | None = Form(None),
    special_assistance: str | None = Form(None),
    other_experience_requirements: str | None = Form(None),
    full_name: str | None = Form(None),
    email: str | None = Form(None),
    mobile_number: str | None = Form(None),
    identity_document: UploadFile | None = File(None),
    country: str | None = Form(None),
    company: str | None = Form(None),
    event_coordinator: str | None = Form(None),
    alternate_contact_number: str | None = Form(None),
    preferred_contact_method: str | None = Form(None),
    contact_notes: str | None = Form(None),
    subtype: EventType | None = Form(None),
    purpose: str | None = Form(None),
    eventName: str | None = Form(None),
    experienceName: str | None = Form(None),
    customExperience: str | None = Form(None),
    eventDate: str | None = Form(None),
    eventStartTime: str | None = Form(None),
    eventEndDate: str | None = Form(None),
    eventEndTime: str | None = Form(None),
    eventCity: str | None = Form(None),
    venueAddress: str | None = Form(None),
    budget: float | None = Form(None),
    currency: str | None = Form(None),
    dateFlexibility: str | None = Form(None),
    eventNotes: str | None = Form(None),
    totalAttendees: int | None = Form(None),
    aircraftKind: AircraftType | None = Form(None),
    preferredAircraft: AircraftModel | None = Form(None),
    passengerCount: int | None = Form(None),
    tripType: str | None = Form(None),
    departure: str | None = Form(None),
    destination: str | None = Form(None),
    departureDate: str | None = Form(None),
    departureTime: str | None = Form(None),
    groundPickup: bool | None = Form(None),
    vehicleTransfer: bool | None = Form(None),
    vipReception: bool | None = Form(None),
    baggageCoordination: bool | None = Form(None),
    accessibilityAssistance: bool | None = Form(None),
    aviationNotes: str | None = Form(None),
    cateringRequired: bool | None = Form(None),
    dietaryPreferences: str | None = Form(None),
    mealPreferences: str | None = Form(None),
    cateringNotes: str | None = Form(None),
    beverageRequired: bool | None = Form(None),
    beveragePreferences: str | None = Form(None),
    champagneRequested: bool | None = Form(None),
    decorationRequired: bool | None = Form(None),
    decorationTheme: str | None = Form(None),
    decorationNotes: str | None = Form(None),
    photographyRequired: bool | None = Form(None),
    photographyNotes: str | None = Form(None),
    vipRequirements: str | None = Form(None),
    privacyRequirements: str | None = Form(None),
    specialAssistance: str | None = Form(None),
    preferenceNotes: str | None = Form(None),
    contactName: str | None = Form(None),
    contactEmail: str | None = Form(None),
    contactMobile: str | None = Form(None),
    coordinatorName: str | None = Form(None),
    alternatePhone: str | None = Form(None),
    preferredContactMethod: str | None = Form(None),
    contactNotes: str | None = Form(None),
    db: Session = Depends(get_db),
):
    if subtype is not None:
        event_type = subtype
    if purpose is not None:
        event_purpose = purpose
    if eventName is not None:
        occasion_name = eventName
    if experienceName is not None:
        selected_experience = experienceName
    if customExperience is not None:
        event_details = customExperience
    if eventDate is not None:
        event_date = eventDate
    if eventStartTime is not None:
        event_time = eventStartTime
    if eventEndDate is not None:
        event_end_date = eventEndDate
    if eventEndTime is not None:
        event_end_time = eventEndTime
    if eventCity is not None:
        city_or_departure_area = eventCity
    if venueAddress is not None:
        venue_address = venueAddress
    if budget is not None:
        estimated_budget = budget
    if currency is not None:
        budget_currency = currency
    if dateFlexibility is not None:
        date_flexibility = dateFlexibility
    if eventNotes is not None:
        event_notes = eventNotes
    if totalAttendees is not None:
        number_of_people = totalAttendees
    if aircraftKind is not None:
        aircraft_type = aircraftKind
    if preferredAircraft is not None:
        aircraft_model = preferredAircraft
    if passengerCount is not None:
        aircraft_passengers = passengerCount
    if tripType is not None:
        flight_type = tripType
    if departure is not None:
        departure_location = departure
    if destination is not None:
        event_destination = destination
    if departureDate is not None:
        preferred_flight_date = departureDate
    if departureTime is not None:
        preferred_departure_time = departureTime
    if groundPickup is not None:
        ground_pickup = groundPickup
    if vehicleTransfer is not None:
        vehicle_transfer = vehicleTransfer
    if vipReception is not None:
        vip_reception = vipReception
    if baggageCoordination is not None:
        baggage_coordination = baggageCoordination
    if accessibilityAssistance is not None:
        accessibility_assistance = accessibilityAssistance
    if aviationNotes is not None:
        special_aviation_requirements = aviationNotes
    if cateringRequired is not None:
        catering_requested = cateringRequired
    if dietaryPreferences is not None:
        dietary_preferences = dietaryPreferences
    if mealPreferences is not None:
        meal_preferences = mealPreferences
    if cateringNotes is not None:
        catering_notes = cateringNotes
    if beverageRequired is not None:
        beverage_service_requested = beverageRequired
    if beveragePreferences is not None:
        beverage_preferences = beveragePreferences
    if champagneRequested is not None:
        champagne_service = champagneRequested
    if decorationRequired is not None:
        decorations_requested = decorationRequired
    if decorationTheme is not None:
        occasion_theme = decorationTheme
    if decorationNotes is not None:
        decoration_notes = decorationNotes
    if photographyRequired is not None:
        photography_videography_requested = photographyRequired
    if photographyNotes is not None:
        photography_notes = photographyNotes
    if vipRequirements is not None:
        vip_guest_arrangements = vipRequirements
    if privacyRequirements is not None:
        privacy_requirements = privacyRequirements
    if specialAssistance is not None:
        special_assistance = specialAssistance
    if preferenceNotes is not None:
        other_experience_requirements = preferenceNotes
    if contactName is not None:
        full_name = contactName
    if contactEmail is not None:
        email = contactEmail
    if contactMobile is not None:
        mobile_number = contactMobile
    if coordinatorName is not None:
        event_coordinator = coordinatorName
    if alternatePhone is not None:
        alternate_contact_number = alternatePhone
    if preferredContactMethod is not None:
        preferred_contact_method = preferredContactMethod
    if contactNotes is not None:
        contact_notes = contactNotes
    data = {
        "event_type": event_type,
        "timing": timing,
        "event_purpose": event_purpose,
        "occasion_name": occasion_name,
        "selected_experience": selected_experience,
        "event_details": event_details,
        "event_date": event_date,
        "event_time": event_time,
        "date": date,
        "time": time,
        "event_end_date": event_end_date,
        "event_end_time": event_end_time,
        "city_or_departure_area": city_or_departure_area,
        "venue": venue,
        "venue_address": venue_address,
        "estimated_budget": estimated_budget,
        "budget_currency": budget_currency,
        "date_flexibility": date_flexibility,
        "event_notes": event_notes,
        "number_of_people": number_of_people,
        "aircraft_type": aircraft_type,
        "aircraft_model": aircraft_model,
        "aircraft_passengers": aircraft_passengers,
        "flight_type": flight_type,
        "departure_location": departure_location,
        "event_destination": event_destination,
        "preferred_flight_date": preferred_flight_date,
        "preferred_departure_time": preferred_departure_time,
        "ground_pickup": ground_pickup,
        "vehicle_transfer": vehicle_transfer,
        "vip_reception": vip_reception,
        "baggage_coordination": baggage_coordination,
        "accessibility_assistance": accessibility_assistance,
        "special_aviation_requirements": special_aviation_requirements,
        "food_preferences": food_preferences,
        "drinks": drinks,
        "champagne_service": champagne_service,
        "crew_preferences": crew_preferences,
        "catering_requested": catering_requested,
        "dietary_preferences": dietary_preferences,
        "meal_preferences": meal_preferences,
        "catering_notes": catering_notes,
        "beverage_service_requested": beverage_service_requested,
        "beverage_preferences": beverage_preferences,
        "decorations_requested": decorations_requested,
        "occasion_theme": occasion_theme,
        "decoration_notes": decoration_notes,
        "photography_videography_requested": photography_videography_requested,
        "photography_notes": photography_notes,
        "vip_guest_arrangements": vip_guest_arrangements,
        "privacy_requirements": privacy_requirements,
        "special_assistance": special_assistance,
        "other_experience_requirements": other_experience_requirements,
        "full_name": full_name,
        "email": email,
        "mobile_number": mobile_number,
        "country": country,
        "company": company,
        "event_coordinator": event_coordinator,
        "alternate_contact_number": alternate_contact_number,
        "preferred_contact_method": preferred_contact_method,
        "contact_notes": contact_notes,
    }
    saved_name = None
    try:
        if identity_document is not None and identity_document.filename:
            saved_name = save_upload(identity_document)
            data["identity_document"] = saved_name
        event_id = create_event_record(data, db)
    except Exception:
        if saved_name:
            remove_saved_upload(saved_name)
        raise
    finally:
        if identity_document is not None:
            identity_document.file.close()
    return event_creation_response(event_id, db)


@router.post("/{event_id}/passengers", summary="Add Extra Passenger")
def add_passengers(
    event_id: UUID,
    full_name: str | None = Form(None),
    date_of_birth: str | None = Form(None),
    nationality: str | None = Form(None),
    passport_number: str | None = Form(None),
    passport_expiry: str | None = Form(None),
    passport_file: UploadFile | None = File(None),
    passenger_type: str | None = Form(None),
    identity_document_type: IdentityDocumentType | None = Form(None),
    identity_document_file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    required_fields = {
        "full_name": full_name,
        "date_of_birth": date_of_birth,
        "nationality": nationality,
        "passport_number": passport_number,
        "passport_expiry": passport_expiry,
        "passenger_type": passenger_type,
        "identity_document_type": identity_document_type,
    }
    missing_fields = [
        field
        for field, value in required_fields.items()
        if value is None or (isinstance(value, str) and not value.strip())
    ]
    if missing_fields:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Please provide all required passenger details.",
                "missing_fields": missing_fields,
            },
        )
    event = (
        db.execute(
            text("""
            SELECT number_of_people
            FROM customer_hj."CUSTOMER_EVENTS"
            WHERE event_id = :event_id
        """),
            {"event_id": event_id},
        )
        .mappings()
        .first()
    )
    if event is None:
        raise HTTPException(
            status_code=404,
            detail="Event not found.",
        )
    total_people = event["number_of_people"]
    if total_people is None:
        raise HTTPException(
            status_code=400,
            detail="Set number_of_people when creating the event.",
        )
    existing_count = db.execute(
        text("""
            SELECT COUNT(*)
            FROM customer_hj."CUSTOMER_EVENT_PASSENGERS"
            WHERE event_id = :event_id
        """),
        {"event_id": event_id},
    ).scalar_one()
    if existing_count >= total_people - 1:
        raise HTTPException(
            status_code=400,
            detail="The maximum number of passengers has been reached.",
        )
    passport_filename = None
    identity_filename = None
    try:
        if passport_file and passport_file.filename:
            passport_filename = save_upload(passport_file)
        if identity_document_file and identity_document_file.filename:
            identity_filename = save_upload(identity_document_file)
        passenger_id = db.execute(
            text("""
                INSERT INTO customer_hj."CUSTOMER_EVENT_PASSENGERS" (
                    event_id,
                    full_name,
                    date_of_birth,
                    nationality,
                    passport_number,
                    passport_expiry,
                    passenger_type,
                    identity_document_type,
                    identity_document,
                    passport_file
                )
                VALUES (
                    :event_id,
                    :full_name,
                    :date_of_birth,
                    :nationality,
                    :passport_number,
                    :passport_expiry,
                    :passenger_type,
                    :identity_document_type,
                    :identity_document,
                    :passport_file
                )
                RETURNING passenger_id
            """),
            {
                "event_id": event_id,
                "full_name": full_name.strip(),
                "date_of_birth": date_of_birth,
                "nationality": nationality.strip(),
                "passport_number": passport_number.strip(),
                "passport_expiry": passport_expiry,
                "passenger_type": passenger_type.strip(),
                "identity_document_type": identity_document_type.value,
                "identity_document": identity_filename,
                "passport_file": passport_filename,
            },
        ).scalar_one()
        db.commit()
    except Exception as exc:
        db.rollback()
        for filename in (passport_filename, identity_filename):
            if filename:
                remove_saved_upload(filename)
        print("Passenger save error:", repr(exc))
        raise HTTPException(
            status_code=500,
            detail=(
                "Failed to save passenger details. "
                "Check the terminal for the actual error."
            ),
        ) from exc
    return {
        "message": "Extra passenger added successfully",
        "event_id": event_id,
        "passenger": {
            "passenger_id": passenger_id,
            "full_name": full_name.strip(),
            "date_of_birth": date_of_birth,
            "nationality": nationality.strip(),
            "passport_number": passport_number.strip(),
            "passport_expiry": passport_expiry,
            "passport_file": passport_filename,
            "passenger_type": passenger_type.strip(),
            "identity_document_type": identity_document_type.value,
            "identity_document_file": identity_filename,
        },
    }


@router.get("/", summary="Get All Events")
def get_all_events(
    db: Session = Depends(get_db),
):
    rows = db.execute(text("""
            SELECT event_id
            FROM customer_hj."CUSTOMER_EVENTS"
            ORDER BY created_at ASC, event_id ASC
        """)).mappings().all()
    events = [fetch_event(db, row["event_id"]) for row in rows]
    return {"events": events}


@router.get("/event", summary="Get Event by ID")
def get_event_by_id(
    event_id: UUID | None = None,
    db: Session = Depends(get_db),
):
    if event_id is None:
        raise HTTPException(
            status_code=400,
            detail="Event ID is required.",
        )
    event = fetch_event(db, event_id)
    if event is None:
        raise HTTPException(
            status_code=404,
            detail="Event not found.",
        )
    return {"event": event}


@router.put("/", summary="Update Event")
def update_event(
    request: UpdateEventRequest | None = None,
    db: Session = Depends(get_db),
):
    if request is None or request.event_id is None:
        raise HTTPException(
            status_code=400,
            detail="Event ID is required.",
        )
    event_id = request.event_id
    current = db.execute(
        text("""
            SELECT number_of_people
            FROM customer_hj."CUSTOMER_EVENTS"
            WHERE event_id = :event_id
        """),
        {"event_id": event_id},
    ).first()
    if current is None:
        raise HTTPException(
            status_code=404,
            detail="Event not found.",
        )
    update_data = request.model_dump(
        exclude={"event_id"},
        exclude_unset=True,
        exclude_none=True,
        mode="json",
    )
    update_data = normalize_event_fields(update_data)
    new_count = update_data.get(
        "number_of_people",
        current[0],
    )
    if new_count is not None:
        if new_count < 1:
            raise HTTPException(
                status_code=400,
                detail="Number of people must be at least 1.",
            )
        passenger_count = db.execute(
            text("""
                SELECT COUNT(*)
                FROM customer_hj."CUSTOMER_EVENT_PASSENGERS"
                WHERE event_id = :event_id
            """),
            {"event_id": event_id},
        ).scalar_one()
        if passenger_count > new_count - 1:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Cannot reduce the total below the main customer "
                    "plus the existing extra passengers."
                ),
            )
    allowed_columns = set(EVENT_COLUMNS.values())
    values = {
        field: value for field, value in update_data.items() if field in allowed_columns
    }
    if "event_type" in values:
        values["title"] = values["event_type"]
    if "event_details" in values:
        values["description"] = values["event_details"]
    if values:
        assignments = ", ".join(f"{column} = :{column}" for column in values)
        try:
            db.execute(
                text(f"""
                    UPDATE customer_hj."CUSTOMER_EVENTS"
                    SET {assignments}
                    WHERE event_id = :event_id
                """),
                {
                    "event_id": event_id,
                    **values,
                },
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
    return {
        "message": "Event updated successfully",
        "event": fetch_event(db, event_id),
    }


@router.delete("/", summary="Delete Event")
def delete_event(
    event_id: UUID | None = None,
    db: Session = Depends(get_db),
):
    if event_id is None:
        raise HTTPException(
            status_code=400,
            detail="Event ID is required.",
        )
    existing = db.execute(
        text("""
            SELECT event_id
            FROM customer_hj."CUSTOMER_EVENTS"
            WHERE event_id = :event_id
        """),
        {"event_id": event_id},
    ).first()
    if existing is None:
        raise HTTPException(
            status_code=404,
            detail="Event not found.",
        )
    try:
        db.execute(
            text("""
                DELETE FROM customer_hj."CUSTOMER_EVENTS"
                WHERE event_id = :event_id
            """),
            {"event_id": event_id},
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {
        "message": "Event deleted successfully",
        "event_id": event_id,
    }
