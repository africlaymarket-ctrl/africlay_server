from typing import Protocol
from decimal import Decimal


class PaymentGateway(Protocol):
    def initiate_payment(self, *, phone_number: str, amount: Decimal, account_reference: str) -> dict:
        ...
