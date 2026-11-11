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

I tillegg lagres en rentetabell: beste lån for hvert punkt i et rutenett av lånebeløp × belåningsgrad
(uten banknavn). Nettappen velger punktet nærmest personens egne tall lokalt, slik at ingen personlige
tall sendes ut. Rutenettet og utvalgsreglene er de samme som i sparefossefall.py.

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
# Samme rutenett som TABELL_* i sparefossefall.py
TABELL_LAN = (1_500_000, 3_000_000, 5_000_000, 8_000_000, 12_000_000)
TABELL_BELANING = (0.40, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90)
TABELL_ALDER = 25
PAUSE_SEK = 1.0              # pause mellom spørringene, for å skåne tjenesten
MAKS_FEILANDEL = 0.2         # flere feil enn dette: ingen ny fil


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


def finansportalen_url(lan, belaning, alder=TABELL_ALDER):
    q = [("age", alder), ("interestType", "Flytende"), ("loanAmount", int(lan)), ("loanType[0]", "standardlån"),
         ("membershipType[0]", "None"), ("paymentPeriod", 25), ("purchasePrice", int(round(lan / belaning))),
         ("query", ""), ("requiredProductTypes[0]", "None")]
    return f"{API}/bankprodukt/boliglan?" + urllib.parse.urlencode(q)


def kvalifiserende_produkter(produkter, belaning):
    """Kompakt liste (uten banknavn) – identisk med kvalifiserende_produkter() i sparefossefall.py."""
    ut = []
    for p_ in produkter if isinstance(produkter, list) else []:
        pr = p_.get("product") or {}
        eff = p_.get("effectiveInterestRateRaw", p_.get("effectiveInterestRate"))
        if not (pr.get("marketArea") == "NationWide" and not p_.get("isMembershipRequired")
                and not pr.get("isMembershipRequired") and not p_.get("isPackageRequired")
                and not pr.get("isSalaryRequired") and not (pr.get("requiresProductTypes") or [])
                and pr.get("mortgageType") == "standardlån" and pr.get("interestType", "Flytende") == "Flytende"
                and (pr.get("maximumLoanToValueRatio") or 0) >= round(belaning * 100) and isinstance(eff, (int, float))):
            continue
        ut.append(dict(e=round(float(p_.get("effectiveInterestRate") or eff) / 100, 6),
                       n=round(float(p_.get("nominalInterestRate") or 0) / 100, 6),
                       alder_fra=pr.get("ageFrom"), alder_til=pr.get("ageTo"),
                       maks_belaning=pr.get("maximumLoanToValueRatio"), gyldig_fra=(p_.get("validFrom") or "")[:10]))
    return sorted(ut, key=lambda x: x["e"])[:20]       # de 20 billigste er nok, også etter aldersfilter


def hent_tabell(hent):
    """Henter alle punktene i rutenettet. `hent(url)` returnerer API-svaret. Kaster feil ved for mange feil."""
    import time
    celler, feil = {}, 0
    punkter = [(lan, bel) for lan in TABELL_LAN for bel in TABELL_BELANING]
    for i, (lan, bel) in enumerate(punkter):
        try:
            produkter = kvalifiserende_produkter(hent(finansportalen_url(lan, bel)), bel)
            if produkter:
                celler[f"{lan}|{bel:.2f}"] = produkter
        except Exception as e:
            feil += 1
            print(f"Merk: punkt {lan} kr / {bel:.0%} feilet ({e}).", file=sys.stderr)
        if i < len(punkter) - 1 and PAUSE_SEK:
            time.sleep(PAUSE_SEK)
    if feil > MAKS_FEILANDEL * len(punkter):
        raise ValueError(f"{feil} av {len(punkter)} punkter i rentetabellen feilet")
    return dict(lan=list(TABELL_LAN), belaning=list(TABELL_BELANING), sporringsalder=TABELL_ALDER, celler=celler)


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
    ap.add_argument("--uten-tabell", action="store_true", help="hent bare én beste rente, ikke rentetabellen")
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
        if not a.uten_tabell:
            if a.fra_fil:      # test: samme lagrede svar for alle punkter
                global PAUSE_SEK
                PAUSE_SEK = 0
                resultat["tabell"] = hent_tabell(lambda url: produkter)
            else:
                resultat["tabell"] = hent_tabell(hent_json)
    except Exception as e:
        print(f"Feil: {e}. beste_boligrente.json er ikke endret.", file=sys.stderr)
        return 1
    Path(a.ut).write_text(json.dumps(resultat, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Beste boliglånsrente: {resultat['effektiv'] * 100:.2f} % effektiv, {resultat['nominell'] * 100:.2f} % nominell "
          f"(av {resultat['antall_vurdert']} produkter). Rentetabell: {len(resultat.get('tabell', {}).get('celler', {}))} punkter. "
          f"Lagret i {a.ut}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
