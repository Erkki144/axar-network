from datetime import date
import pytest
from axar_connect import SaleLine, CashSale

@pytest.fixture
def sale():
    return CashSale(
        tenant_id="t-a",event_id="evt-1",sale_id="sale-1",register_id="r-1",
        business_date=date(2026,11,22),receipt_no=1,
        lines=(SaleLine(500,403,97,2400),SaleLine(199,160,39,2400)),
        gross_cents=699,net_cents=563,vat_cents=136,
        rounding_cents=1,cash_due_cents=700)

@pytest.fixture
def map_accounts():
    return dict(cash_account="CASH",revenue_account="SALES",
                vat_accounts={0:"VAT0",900:"VAT9",1300:"VAT13",2400:"VAT24"},
                rounding_gain_account="GAIN",rounding_loss_account="LOSS")
