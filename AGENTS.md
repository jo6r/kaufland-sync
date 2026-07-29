# AGENTS instrukce: kaufland-sync

Tyto instrukce jsou urcene pro AI coding agenty pracujici v tomto repozitari.

## Rozsah a priority
- Zameruj se primarne na `jirimodels-offer-stock-updater/`, `shoptet-offer-stock-updater/` a `deployment/`.
- Zachovej soucasnou architekturu: oba updatery jsou samostatne one-shot joby bez zavislosti na databazi.
- Preferuj minimalni, cilene zmeny. Nerefactoruj nesouvisejici kod.

## Mapa repozitare
- `jirimodels-offer-stock-updater/`: XML feed -> dohledani Kaufland unit podle EAN -> hromadna aktualizace stocku.
- `shoptet-offer-stock-updater/`: CSV feed -> filtrovani/normalizace radku -> dohledani Kaufland unit podle EAN -> hromadna aktualizace stocku.
- `deployment/`: ConfigMapy, CronJoby a sdilena API konfigurace pro Kubernetes/Fleet.
- `scripts/`: operacni pomocne skripty; upravuj pouze na explicitni pozadani.

## Chovani behem behu, ktere je nutne zachovat
- Obe sluzby pouzivaji Kaufland REST API HMAC podpis z lokalniho `kaufland_api_client.py`.
- `MAX_UNITS_PER_REQUEST` je `150` a payloady jsou pred `/v2/units/bulk` deduplikovane podle `id_unit`.
- Pri HTTP 401 behem dohledavani offeru ukonci beh okamzite (re-raise), ne jen tiche pokracovani.
- Udrz logovani dostatecne detailni pro troubleshooting cronjobu.

## Kaufland API reference a lookup postup
- Dokumentace endpointu: https://sellerapi.kaufland.com/?page=endpoints#/
- Pro zjisteni ID nabidky (`id_unit`) pouzij tento postup:
  - 1) podle EAN ziskej produkt (a `id_product`):
    - `https://sellerapi.kaufland.com/v2/products/ean/<EAN>?storefront=cz&embedded=units`
  - 2) podle `id_product` ziskej jednotky prodavce:
    - `https://sellerapi.kaufland.com/v2/units?limit=30&offset=0&storefront=cz&id_product=<ID_PRODUCT>&fulfillment_type=fulfilled_by_merchant`
  - 3) detail konkretni nabidky over pres `id_unit`:
    - `https://sellerapi.kaufland.com/v2/units/<ID_UNIT>?storefront=cz`

## Pravidla specificka pro zdroj

### jirimodels-offer-stock-updater
- Povinne env: `FEED_XML_URL`, `KAUFLAND_CLIENT_KEY`, `KAUFLAND_SECRET_KEY`, `KAUFLAND_BASE_URL`.
- Vstupni feed je XML s uzly `product` a ocekavanymi poli `EAN`, `CODE`, `STOCK`.

### shoptet-offer-stock-updater
- Povinne env: `FEED_CSV_URL`, `KAUFLAND_CLIENT_KEY`, `KAUFLAND_SECRET_KEY`, `KAUFLAND_BASE_URL`.
- Volitelne env: `IGNORE_CODES` (hodnoty oddelene carkou, case-insensitive).
- CSV je oddelene strednikem a musi byt dekodovano jako `windows-1250`, pokud se format feedu explicitne nezmeni.
- Zachovej semantiku filtrovani:
  - preskoc skryte produkty (`productvisibility=hidden`)
  - preskoc radky bez EAN nebo stock
  - preskoc ignorovane kody
  - osetreni nevalidniho/zaporneho stocku musi zustat defenzivni

## Pravidla pro deployment
- Sdilena Kaufland konfigurace pochazi z:
  - `deployment/kaufland-api-configmap.yaml`
  - `deployment/kaufland-api-secret.yaml` (secret, v repozitari pouze template/priklad)
- URL feedu sluzeb a service-specific volby patri do per-service ConfigMap.
- Pri aktualizaci verzi image zachovej, aby CronJob image names odpovidaly `build-and-push.sh`:
  - `zot.jo6r.xyz/kaufland/jirimodels-offer-stock-updater:<version>`
  - `zot.jo6r.xyz/kaufland/shoptet-offer-stock-updater:<version>`
- Zachovej `defaultNamespace: kaufland-sync` ve Fleet, pokud neni explicitni pozadavek na zmenu.

## Lokalni spusteni a overeni
- Typicke lokalni spusteni pro kazdou sluzbu:
  - `pip install -r requirements.txt`
  - nastav povinne env promenne (nebo `.env` vedle `main.py`)
  - `python main.py`
- Over zmenene code paths spustenim relevantniho service entrypointu.
- Pro zmeny parseru/filtrovani pridej nebo uprav cilene testy, pokud je zaveden test harness.

## Ochranna pravidla pri editaci
- Nezavadej cross-service coupling, pokud to neni vyzadano.
- Nepresouvej Kaufland signing logiku do externich knihoven bez explicitniho pozadavku.
- Zachovej defaulty kompatibilni se storefront `cz` a soucasnym cron pouzitim.
- Pokud je to prakticke, zachovej obsah pouze v ASCII.
