from kyc.tools.registry import get_company_record
from kyc.tools.sanctions import screen_sanctions

TOOLS = {t.name: t for t in (get_company_record, screen_sanctions)}

__all__ = ["TOOLS", "get_company_record", "screen_sanctions"]
