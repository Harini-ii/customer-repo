from uuid import UUID

from datetime import datetime, timezone
from enum import Enum
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from database import get_db
from routers.auth import get_current_user

router = APIRouter(prefix="/api/search", tags=["Temporary Search"])


class SearchCategory(str, Enum):
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


class AircraftModel(str, Enum):
    b737_800_freighter = "B737-800 Freighter · Cargo"
    bd_700_1a11 = "BD-700-1A11 (Global 5500) · 13"
    beech_king_air_c90a = "Beech King Air C-90A · 6"
    beech_king_air_c90a_2 = "Beech King Air C90A · 6"
    beech_ska_b200gt = "Beech SKA B200GT · 9"
    beech_super_king_air_b200_7 = "Beech Super King Air B-200 · 7"
    beech_super_king_air_b200_9 = "Beech Super King Air B-200 · 9"
    beech_super_king_air_b200_8 = "Beech Super King Air B200 · 8"
    beechcraft_king_air_b200gt = "Beechcraft King Air B200GT · 7"
    beechcraft_premier_1a_390 = "Beechcraft Premier 1A-390 · 6"
    bombardier_challenger_605 = "Bombardier Challenger 605 · 10"
    cessna_172s = "Cessna 172S · 3"
    cessna_206h = "Cessna 206H · 4"
    cessna_citation_560_xl = "Cessna Citation 560 XL · 8"
    cessna_citation_560xl_excel_9 = "Cessna Citation 560XL Excel · 9"
    cessna_citation_560xl_excel_7 = "Cessna Citation 560XL Excel · 7"
    cessna_citation_560xls_plus = "Cessna Citation 560XLS+ · 9"
    cessna_citation_cj2 = "Cessna Citation CJ2 · 7"
    cessna_citation_ii = "Cessna Citation II · 7"
    cessna_r172k = "Cessna R172K · 3"
    cessna_t206h = "Cessna T206H · Aerial Work"
    challenger_605 = "Challenger 605 CL-600-2B16 · 9"
    challenger_650 = "Challenger 650 CL-600-2B16 · 10"
    challenger_cl604 = "Challenger CL600-2B16-CL604 · 9"
    citation_525a_cj2 = "Citation 525A CJ2+ · 7"
    cl600_cl605_12 = "CL-600-2B16 CL604 Variant (CL605) · 12"
    cl600_cl605_9 = "CL-600-2B16 CL604 Variant (CL605) · 9"
    dornier_do228 = "Dornier DO-228-201 · 7"
    emb135_legacy600 = "EMB-135 (Legacy 600) · 13"
    emb135bj_legacy650 = "EMB-135BJ (Legacy 650) · 13"
    emb135bj_legacy650_10 = "Embraer EMB135BJ (Legacy 650) · 10"
    falcon_2000_12 = "Falcon 2000 · 12"
    falcon_2000_9 = "Falcon 2000 · 9"
    falcon_2000_10 = "Falcon 2000 · 10"
    falcon_2000_8 = "Falcon 2000 · 8"
    falcon_2000ex = "Falcon 2000EX · 9"
    falcon_2000lx_10 = "Falcon 2000LX · 10"
    falcon_2000lx_8 = "Falcon 2000LX · 8"
    falcon_6x = "Falcon 6X · 14"
    falcon_7x = "Falcon 7X (M1000) · 14"
    g200 = "G-200 · 10"
    hawker_800xp = "Hawker 800XP · 8"
    hawker_850xp_9 = "Hawker 850XP · 9"
    hawker_850xp_8 = "Hawker 850XP · 8"
    hawker_900xp = "Hawker 900XP · 9"
    hawker_beechcraft_900xp = "Hawker Beechcraft 900XP · 9"
    king_air_250 = "King Air 250 (B200GT) · 7"
    pc_12_45 = "PC-12/45 · 6"
    pc_12_47e = "PC-12/47E · 8"
    stemme_s6rt = "Stemme S6RT · 2"
    super_king_air_b200 = "Super King Air B200 · 7"
    super_king_air_b300_aerial = "Super King Air B300 · 7 / Aerial Work"
    super_king_air_b300 = "Super King Air B300 · 4"
    tecnam_p2010_tdi = "Tecnam P2010TDI · 3"


class SearchRequest(BaseModel):
    category: SearchCategory | None = None
    aircraft_type: AircraftType | None = None
    aircraft_model: AircraftModel | None = None
    travellers: int | None = None
    budget: float | None = None
    currency: Currency | None = None


class UpdateSearchRequest(BaseModel):
    temporary_search_id: UUID | None = None
    category: SearchCategory | None = None
    aircraft_type: AircraftType | None = None
    aircraft_model: AircraftModel | None = None
    travellers: int | None = None
    budget: float | None = None
    currency: Currency | None = None


class SearchIdRequest(BaseModel):
    temporary_search_id: UUID | None = None


def get_customer_id(current_user):
    if isinstance(current_user, dict):
        customer_id = (
            current_user.get("customer_id")
            or current_user.get("user_id")
            or current_user.get("id")
            or current_user.get("sub")
        )
    else:
        customer_id = (
            getattr(current_user, "customer_id", None)
            or getattr(current_user, "user_id", None)
            or getattr(current_user, "id", None)
            or getattr(current_user, "sub", None)
        )
    if customer_id is None:
        raise HTTPException(
            status_code=401, detail="Customer ID not found in authenticated user"
        )
    try:
        return UUID(str(customer_id))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid customer ID")


def perform_search(request: SearchRequest, db: Session):
    if request.aircraft_type not in (AircraftType.flight, AircraftType.helicopter):
        return []
    if request.aircraft_type == AircraftType.flight:
        columns = "aviation_id AS id, flight_number, origin, destination, aircraft_type, aircraft_model AS model, capacity, price, type"
    else:
        columns = "aviation_id AS id, helicopter_number, origin, destination, aircraft_type, aircraft_model AS model, capacity, price"
    query = f'SELECT {columns} FROM customer_hj."CUSTOMER_AVIATIONS" WHERE type = :aircraft_kind' 
    params = {"aircraft_kind": request.aircraft_type.value}
    if request.aircraft_model is not None:
        query += " AND LOWER(TRIM(aircraft_model)) = LOWER(TRIM(:model))"
        params["model"] = request.aircraft_model.value.split(" · ")[0]
    if request.travellers is not None:
        query += " AND capacity >= :travellers"
        params["travellers"] = request.travellers
    if request.budget is not None:
        query += " AND price <= :budget"
        params["budget"] = request.budget
    query += " ORDER BY aviation_id"
    rows = db.execute(text(query), params).mappings().all()
    return [dict(row) for row in rows]


def find_customer_search(db: Session, search_id: UUID, customer_id: UUID):
    result = (
        db.execute(
            text("""
            SELECT *
            FROM customer_hj."CUSTOMER_TEMPORARY_SEARCHES"
            WHERE temporary_search_id = :search_id
              AND customer_id = :customer_id
        """),
            {"search_id": search_id, "customer_id": customer_id},
        )
        .mappings()
        .first()
    )
    if result is None:
        raise HTTPException(
            status_code=404, detail="Temporary search not found for this customer"
        )
    return dict(result)


def format_search(search, db: Session):
    search["travellers"] = search.pop("traveller_count", None)
    if search.get("created_at") is not None:
        search["created_at"] = search["created_at"].isoformat()
    request = SearchRequest(
        category=search.get("category"),
        aircraft_type=search.get("aircraft_type"),
        aircraft_model=search.get("aircraft_model"),
        travellers=search.get("travellers"),
        budget=search.get("budget"),
        currency=search.get("currency"),
    )
    search["results"] = perform_search(request, db)
    return search


@router.post("/temporary-search", summary="Create Temporary Search")
def create_temporary_search(
    request: SearchRequest,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    customer_id = get_customer_id(current_user)
    result = (
        db.execute(
            text("""
            INSERT INTO customer_hj."CUSTOMER_TEMPORARY_SEARCHES" (
                customer_id,
                category,
                aircraft_type,
                aircraft_model,
                traveller_count,
                budget,
                currency,
                created_at
            )
            VALUES (
                :customer_id,
                :category,
                :aircraft_type,
                :aircraft_model,
                :traveller_count,
                :budget,
                :currency,
                :created_at
            )
            RETURNING *
        """),
            {
                "customer_id": customer_id,
                "category": (request.category.value if request.category else None),
                "aircraft_type": (
                    request.aircraft_type.value if request.aircraft_type else None
                ),
                "aircraft_model": (
                    request.aircraft_model.value if request.aircraft_model else None
                ),
                "traveller_count": request.travellers,
                "budget": request.budget,
                "currency": (request.currency.value if request.currency else None),
                "created_at": datetime.now(timezone.utc).replace(tzinfo=None),
            },
        )
        .mappings()
        .first()
    )
    db.commit()
    return {
        "message": "Temporary search created successfully",
        "temporary_search": format_search(dict(result), db),
    }


@router.get("/temporary-search", summary="Get My Temporary Searches")
def get_all_temporary_searches(
    current_user=Depends(get_current_user), db: Session = Depends(get_db)
):
    customer_id = get_customer_id(current_user)
    rows = (
        db.execute(
            text("""
            SELECT *
            FROM customer_hj."CUSTOMER_TEMPORARY_SEARCHES"
            WHERE customer_id = :customer_id
            ORDER BY created_at DESC, temporary_search_id DESC
        """),
            {"customer_id": customer_id},
        )
        .mappings()
        .all()
    )
    searches = [format_search(dict(row), db) for row in rows]
    return {
        "customer_id": customer_id,
        "total_searches": len(searches),
        "temporary_searches": searches,
    }


@router.post("/temporary-search/get", summary="Get My Temporary Search by ID")
def get_temporary_search(
    request: SearchIdRequest,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if request.temporary_search_id is None:
        raise HTTPException(status_code=400, detail="Temporary search ID is required")
    customer_id = get_customer_id(current_user)
    search = find_customer_search(db, request.temporary_search_id, customer_id)
    return {"temporary_search": format_search(search, db)}


@router.get("/temporary-search/history", summary="Get My Search History")
def get_customer_search_history(
    current_user=Depends(get_current_user), db: Session = Depends(get_db)
):
    customer_id = get_customer_id(current_user)
    rows = (
        db.execute(
            text("""
            SELECT *
            FROM customer_hj."CUSTOMER_TEMPORARY_SEARCHES"
            WHERE customer_id = :customer_id
            ORDER BY created_at DESC, temporary_search_id DESC
        """),
            {"customer_id": customer_id},
        )
        .mappings()
        .all()
    )
    searches = [format_search(dict(row), db) for row in rows]
    return {
        "customer_id": customer_id,
        "total_searches": len(searches),
        "search_history": searches,
    }


@router.get(
    "/temporary-search/history/{search_id}", summary="Get One Search From My History"
)
def get_search_from_history(
    search_id: UUID,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    customer_id = get_customer_id(current_user)
    search = find_customer_search(db, search_id, customer_id)
    return {"temporary_search": format_search(search, db)}


@router.put("/temporary-search", summary="Update My Temporary Search")
def update_temporary_search(
    request: UpdateSearchRequest,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if request.temporary_search_id is None:
        raise HTTPException(status_code=400, detail="Temporary search ID is required")
    customer_id = get_customer_id(current_user)
    find_customer_search(db, request.temporary_search_id, customer_id)
    updates = {}
    if request.category is not None:
        updates["category"] = request.category.value
    if request.aircraft_type is not None:
        updates["aircraft_type"] = request.aircraft_type.value
    if request.aircraft_model is not None:
        updates["aircraft_model"] = request.aircraft_model.value
    if request.travellers is not None:
        updates["traveller_count"] = request.travellers
    if request.budget is not None:
        updates["budget"] = request.budget
    if request.currency is not None:
        updates["currency"] = request.currency.value
    if updates:
        assignments = ", ".join(f"{column} = :{column}" for column in updates)
        updates["search_id"] = request.temporary_search_id
        updates["customer_id"] = customer_id
        db.execute(
            text(f"""
                UPDATE customer_hj."CUSTOMER_TEMPORARY_SEARCHES"
                SET {assignments}
                WHERE temporary_search_id = :search_id
                  AND customer_id = :customer_id
            """),
            updates,
        )
        db.commit()
    updated_search = find_customer_search(db, request.temporary_search_id, customer_id)
    return {
        "message": "Temporary search updated successfully",
        "temporary_search": format_search(updated_search, db),
    }


@router.post("/temporary-search/delete", summary="Delete My Temporary Search")
def delete_temporary_search(
    request: SearchIdRequest,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if request.temporary_search_id is None:
        raise HTTPException(status_code=400, detail="Temporary search ID is required")
    customer_id = get_customer_id(current_user)
    find_customer_search(db, request.temporary_search_id, customer_id)
    db.execute(
        text("""
            DELETE FROM customer_hj."CUSTOMER_TEMPORARY_SEARCHES"
            WHERE temporary_search_id = :search_id
              AND customer_id = :customer_id
        """),
        {"search_id": request.temporary_search_id, "customer_id": customer_id},
    )
    db.commit()
    return {
        "message": "Temporary search deleted successfully",
        "temporary_search_id": request.temporary_search_id,
    }
