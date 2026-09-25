"""Print broker currency specs for the pairs we're adding (one-off check)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from forexbot.config import load_settings
from forexbot.broker.tickerall import TickerallBroker, _get

WANTS = ["USDSKSm", "USDDKAm", "USDNOKm", "USDSEKm", "EURCZKm", "EURSEKm",
         "EURDKKm", "AUDDKKm", "EURJPYm", "CHFJPYm", "AUDNZDm"]

settings = load_settings()
b = TickerallBroker(settings)
b.connect()
specs = b._client.accounts.symbol_specs(b.account_id)
for want in WANTS:
    sp = next((x for x in specs if str(_get(x, "name", "symbol", default="")) == want), None)
    if sp is None:
        print(f"{want:<10} -> NOT FOUND")
        continue
    print(f"{want:<10} base={_get(sp, 'base', 'base_currency')!s:<5} "
          f"quote={_get(sp, 'quote', 'quote_currency')!s:<5} "
          f"digits={_get(sp, 'digits')} point={_get(sp, 'point')} "
          f"min={_get(sp, 'volume_min')} step={_get(sp, 'volume_step')}")
b.disconnect()
