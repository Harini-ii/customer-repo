from fastapi import Depends, FastAPI
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from rate_limiter import limiter
from routers import auth, customer, bookings, passengers, tickets, events, requests, credits_subscription, support

app = FastAPI(
    title='Helite Jets Customer API',
    description=('Customer APIs for Authentication, Customer, '
                 'Bookings, Passenger, Tickets, Events, Requests, Credits and Subscriptions, and Support'),
    version='1.0.0',
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.include_router(auth.router)

for api_router in [customer.router, bookings.router, passengers.router, tickets.router,
                   events.router, requests.router, credits_subscription.router, support.router]:
    app.include_router(api_router, dependencies=[Depends(auth.get_current_user)])


@app.get('/')
def root():
    return {'message': 'Helite Jets Customer API is running'}
