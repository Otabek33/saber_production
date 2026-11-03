"""One-off, read-only look at SmartUp's API: companies (legal persons) and yesterday's shipped orders.

Reads SMARTUP_USERNAME, SMARTUP_PASSWORD, SMARTUP_PROJECT_CODE and SMARTUP_FILIAL_ID from
.env/local.env, saves each raw response next to this script as smartup_*.json and prints a
short summary. Changes nothing in SmartUp. Delete this file when done.
"""

import base64
import datetime
import json
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
env = {}
for line in (ROOT / ".env" / "local.env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")

token = base64.b64encode(f"{env['SMARTUP_USERNAME']}:{env['SMARTUP_PASSWORD']}".encode()).decode()
headers = {
    "Authorization": f"Basic {token}",
    "project_code": env.get("SMARTUP_PROJECT_CODE", "trade"),
    "filial_id": env["SMARTUP_FILIAL_ID"],
    "Content-Type": "application/json",
}
yesterday = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%d.%m.%Y")
requests = {
    "legal_persons": ("https://smartup.online/b/anor/mxsx/mr/legal_person$export", {"state": "A,P"}),
    "orders": (
        "https://smartup.online/b/trade/txs/tdeal/order$export",
        {"statuses": ["B#S", "B#V", "A"], "begin_deal_date": yesterday, "end_deal_date": yesterday},
    ),
}

for name, (url, body) in requests.items():
    request = urllib.request.Request(url, json.dumps(body).encode(), headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        print(f"{name}: HTTP {error.code} {error.read().decode('utf-8', 'replace')[:500]}")
        continue
    (ROOT / f"smartup_{name}.json").write_text(raw, encoding="utf-8")
    data = json.loads(raw)
    items = data.items() if isinstance(data, dict) else []
    lists = {key: len(value) for key, value in items if isinstance(value, list)}
    print(f"{name}: saved smartup_{name}.json, top-level keys {list(data)[:10]}, list sizes {lists}")
