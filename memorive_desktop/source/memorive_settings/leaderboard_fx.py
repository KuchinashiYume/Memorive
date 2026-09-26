"""ECB daily reference rates. Pure parser; network and receipts belong to ExternalDataSources."""
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import re
import xml.etree.ElementTree as ET

ECB_ENDPOINT = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
ECB_PAGE = "https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html"


def parse_ecb(payload, *, today=None):
    if not isinstance(payload, bytes) or len(payload) > 256 * 1024:
        raise ValueError("FX_PAYLOAD_INVALID")
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)", payload, re.I):
        raise ValueError("FX_XML_DECLARATION_FORBIDDEN")
    try:
        root = ET.fromstring(payload)
        cubes = [x for x in root.iter() if x.tag.rsplit("}", 1)[-1] == "Cube" and "time" in x.attrib]
        if len(cubes) != 1:
            raise ValueError("FX_SINGLE_DAY_REQUIRED")
        stamp = cubes[0].attrib["time"]
        day = date.fromisoformat(stamp)
        now = today or datetime.now(timezone.utc).date()
        if stamp != day.isoformat() or not 0 <= (now - day).days <= 7:
            raise ValueError("FX_DATE_UNUSABLE")
        rates = {}
        for item in cubes[0]:
            code = item.attrib.get("currency")
            if code not in {"USD", "CNY", "JPY"}:
                continue
            if code in rates:
                raise ValueError("FX_DUPLICATE_CURRENCY")
            value = Decimal(item.attrib["rate"])
            if not value.is_finite() or value <= 0:
                raise ValueError("FX_RATE_INVALID")
            rates[code] = value
        if set(rates) != {"USD", "CNY", "JPY"}:
            raise ValueError("FX_REQUIRED_CURRENCY_MISSING")
        converted = {k: float(v / rates["USD"]) for k, v in rates.items()}
        if any(not 0 < v < float("inf") for v in converted.values()):
            raise ValueError("FX_RATE_INVALID")
        return {"rateDate": stamp, "unitsPerUsd": converted, "originalBase": "EUR",
                "originalRates": {k: str(v) for k, v in rates.items()}}
    except (ET.ParseError, KeyError, InvalidOperation, OverflowError, TypeError) as exc:
        raise ValueError("FX_PAYLOAD_INVALID") from exc
