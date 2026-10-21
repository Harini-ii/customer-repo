flights = [
    {
        "id": 1,
        "flight_number": "HJ202",
        "from": "Hyderabad",
        "to": "Bangalore",
        "aircraft": "Private Jet",
        "model": "Cessna Citation",
        "capacity": 7,
        "price": 50000
    },
    {
        "id": 2,
        "flight_number": "HJ303",
        "from": "Hyderabad",
        "to": "Delhi",
        "aircraft": "Private Jet",
        "model": "Hawker 800XP",
        "capacity": 8,
        "price": 75000
    },
    {
        "id": 3,
        "flight_number": "HJ404",
        "from": "Hyderabad",
        "to": "Goa",
        "aircraft": "Private Jet",
        "model": "Citation XLS",
        "capacity": 9,
        "price": 65000
    }
]


helicopters = [
    {
        "id": 1,
        "flight_number": "HE101",
        "from": "Hyderabad",
        "to": "Mumbai",
        "aircraft": "Helicopter",
        "model": "Airbus H125",
        "capacity": 5,
        "price": 25000
    },
    {
        "id": 2,
        "flight_number": "HE202",
        "from": "Hyderabad",
        "to": "Bangalore",
        "aircraft": "Helicopter",
        "model": "Bell 407",
        "capacity": 6,
        "price": 30000
    },
    {
        "id": 3,
        "flight_number": "HE303",
        "from": "Hyderabad",
        "to": "Goa",
        "aircraft": "Helicopter",
        "model": "Leonardo AW109",
        "capacity": 7,
        "price": 35000
    }
]


bookings = []


event_requests = []


requests = []


subscription_plans = [
    {
        "id": 1,
        "name": "Basic",
        "price": 4999,
        "credits": 50,
        "validity_days": 30
    },
    {
        "id": 2,
        "name": "Premium",
        "price": 9999,
        "credits": 120,
        "validity_days": 90
    }
]


customer = {
    "id": 1,
    "name": "Demo Customer",
    "email": "customer@example.com",
    "phone_number": "9876543210",
    "password": "123456",
    "logged_in": False,
    "role": "customer",
    "credits": 100
}


customers = [
    customer
]


credit_transactions = []


support_tickets = []