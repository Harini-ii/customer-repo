from fastapi import APIRouter
from data.dummy_data import flights, helicopters

router = APIRouter(
    prefix="/api/flights",
    tags=["Aviation"]
)


@router.get("/", summary="Get All Flights")
def get_flights():
    return {
        "flights": flights
    }


@router.get("/flight", summary="Get Flight by ID")
def get_flight(flight_id: int | None = None):

    if flight_id is None:
        return {
            "message": "Flight ID is required"
        }

    for flight in flights:

        if flight["id"] == flight_id:
            return {
                "flight": flight
            }

    return {
        "message": "Flight not found"
    }


@router.get("/helicopters", summary="Get All Helicopters")
def get_helicopters():
    return {
        "helicopters": helicopters
    }


@router.get("/helicopter", summary="Get Helicopter by ID")
def get_helicopter(helicopter_id: int | None = None):

    if helicopter_id is None:
        return {
            "message": "Helicopter ID is required"
        }

    for helicopter in helicopters:

        if helicopter["id"] == helicopter_id:
            return {
                "helicopter": helicopter
            }

    return {
        "message": "Helicopter not found"
    }