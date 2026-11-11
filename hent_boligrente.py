#!/usr/bin/env python3
"""
hent_boligrente.py – henter beste boliglånsrente fra Finansportalen (Forbrukerrådet) og lagrer den
som beste_boligrente.json. Kjøres daglig av GitHub Actions (.github/workflows/boligrente.yml).

Utvalg (samme som fotnote *) i Sparefossefall):
  flytende standardlån, landsdekkende, uten krav om medlemskap, lønnsinngang eller andre produkter,
  minst 75 % maks belåningsgrad. Rangert etter effektiv rente med Finansportalens standardvalg:
  lån 3 mill. kr, boligverdi 4 mill. kr, 25 års nedbetaling, 30 år gammel låntaker.

Datakilden er tjenesten Finansportalens egen nettside bruker. Den er ikke dokumentert som et
offentlig grensesnitt og kan endres. Ved feil eller urimelige tall avsluttes programmet uten å
endre filen, slik at forrige gyldige verdi blir stående.

Bruk:
  python3 scripts/hent_boligrente.py                         # henter og skriver beste_boligrente.json
  python3 scripts/hent_boligrente.py --fra-fil a.json b.json  # test mot lagrede svar (produkter, styringsrente)
"""
import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

OSLO = ZoneInfo("Europe/Oslo")

API = "https://finans-api.forbrukerradet.no"
SIDE = "https://www.forbrukerradet.no/finansportalen/bank/lan/boliglan"
FORUTSETNINGER = dict(lan=3_000_000, boligverdi=4_000_000, aar=25, alder=30)
MIN_BELANINGSGRAD = 75
UT = Path(__file__).resolve().parent.parent / "beste_boligrente.json"


def hent_json(url):
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "Sparefossefall/1.0 (daglig henting av beste boliglånsrente; GitHub Actions)"})
    with urllib.request.urlopen(req, timeout=30) as svar:
        return json.loads(svar.read().decode("utf-8"))


def produkter_url():
    f = FORUTSETNINGER
    q = [("age", f["alder"]), ("interestType", "Flytende"), ("loanAmount", f["lan"]),
         ("loanType[0]", "standardlån"), ("membershipType[0]", "None"), ("paymentPeriod", f["aar"]),
         ("purchasePrice", f["boligverdi"]), ("query", ""), ("requiredProductTypes[0]", "None")]
    return f"{API}/bankprodukt/boliglan?" + urllib.parse.urlencode(q)


def kvalifiserer(p):
    """Samme vilkår som fotnoten: ingen medlemskap, ingen pakke/annet produkt/lønnsinngang, landsdekkende,
    flytende standardlån, minst 75 % belåningsgrad og en effektiv rente."""
    pr = p.get("product") or {}
    return (pr.get("marketArea") == "NationWide"
            and not p.get("isMembershipRequired") and not pr.get("isMembershipRequired")
            and not p.get("isPackageRequired") and not pr.get("isSalaryRequired")
            and not (pr.get("requiresProductTypes") or [])
            and pr.get("mortgageType") == "standardlån"
            and pr.get("interestType", "Flytende") == "Flytende"
            and (pr.get("maximumLoanToValueRatio") or 0) >= MIN_BELANINGSGRAD
            and isinstance(p.get("effectiveInterestRateRaw", p.get("effectiveInterestRate")), (int, float)))


def velg_beste(produkter, styring, hentet):
    if not isinstance(produkter, list) or len(produkter) < 5:
        raise ValueError(f"uventet svar: {type(produkter).__name__} med {len(produkter) if isinstance(produkter, list) else '?'} elementer")
    aktuelle = [p for p in produkter if kvalifiserer(p)]
    if not aktuelle:
        raise ValueError("ingen produkter oppfyller vilkårene")
    beste = min(aktuelle, key=lambda p: p.get("effectiveInterestRateRaw", p.get("effectiveInterestRate")))
    eff = float(beste.get("effectiveInterestRate") or beste["effectiveInterestRateRaw"])
    nom = float(beste.get("nominalInterestRate") or (beste.get("interval") or {}).get("nominalInterestRate"))
    if not (1 <= eff <= 20 and 0.5 <= nom <= eff):
        raise ValueError(f"urimelig rente: effektiv {eff}, nominell {nom}")
    gyldig = (beste.get("validFrom") or "")[:10]
    gyldig_lokal = datetime.fromisoformat(beste["validFrom"].replace("Z", "+00:00")).astimezone(OSLO).date() if beste.get("validFrom") else None
    return dict(
        effektiv=round(eff / 100, 6), nominell=round(nom / 100, 6),
        hentet=hentet.strftime("%d.%m.%Y"), hentet_iso=hentet.isoformat(timespec="seconds"),
        gyldig_fra=gyldig_lokal.strftime("%d.%m.%Y") if gyldig_lokal else gyldig,
        styringsrente=round(float(styring["rate"]) / 100, 6) if styring and "rate" in styring else None,
        styringsrente_endret=(styring or {}).get("date", "")[:10] or None,
        vilkar="flytende standardlån, landsdekkende, uten krav om medlemskap eller andre produkter, inntil 75 % belåning",
        forutsetninger="lån 3 mill. kr, boligverdi 4 mill. kr, 25 års nedbetaling, 30 år gammel låntaker",
        antall_vurdert=len(aktuelle), url=SIDE, kilde="Finansportalen (Forbrukerrådet)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--fra-fil", nargs=2, metavar=("PRODUKTER", "STYRINGSRENTE"), help="bruk lagrede svar (test)")
    ap.add_argument("--ut", default=str(UT))
    a = ap.parse_args()
    hentet = datetime.now(timezone.utc).astimezone(OSLO)
    try:
        if a.fra_fil:
            produkter = json.loads(Path(a.fra_fil[0]).read_text(encoding="utf-8"))
            styring = json.loads(Path(a.fra_fil[1]).read_text(encoding="utf-8"))
        else:
            produkter = hent_json(produkter_url())
            try:
                styring = hent_json(f"{API}/policyrate/latest")
            except Exception as e:                      # styringsrenten er bare tilleggsinformasjon
                print(f"Merk: fikk ikke hentet styringsrenten ({e}).", file=sys.stderr)
                styring = None
        resultat = velg_beste(produkter, styring, hentet)
    except Exception as e:
        print(f"Feil: {e}. beste_boligrente.json er ikke endret.", file=sys.stderr)
        return 1
    Path(a.ut).write_text(json.dumps(resultat, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Beste boliglånsrente: {resultat['effektiv'] * 100:.2f} % effektiv, {resultat['nominell'] * 100:.2f} % nominell "
          f"(av {resultat['antall_vurdert']} produkter). Lagret i {a.ut}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
