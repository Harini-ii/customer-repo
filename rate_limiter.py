
from slowapi import Limiter
from slowapi.util import get_remote_address

# Identify clients by their IP address
limiter = Limiter(
    key_func=get_remote_address
)