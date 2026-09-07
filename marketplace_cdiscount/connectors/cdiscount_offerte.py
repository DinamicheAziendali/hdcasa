# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Com'e' fatta un'OFFERTA Cdiscount (Octopia), e come si legge il suo esito.

Solo forma: qui non si parla con nessuno e non si importa Odoo. E' la
Consegna 2 (`docs/cdiscount-offerte-consegna-2.md`); il contratto e'
`docs/cdiscount-offerte-contratto.md`.

Cosa e' MISURATO (2026-09-02) e cosa e' LETTO (documentazione ufficiale):

- MISURATO: l'involucro `{"itemsPerPage", "items"}`, i modi di consegna
  dell'account (`TRK`, `REG`, nessuno oltre i 30 kg), il canale `CDISFR`;
- LETTO: il corpo di un'offerta (`sellerExternalReference`, `product.gtin`,
  `condition`, `price.taxes[]` con `VAT` ed `Ecotax`, `deliveryModes[]`,
  `preparationTime`, `quantity`), il numero del pacchetto nel
  `Content-Location`, il cursore nel `Link`, gli esiti
  (`integrationStatus` Integrated/Rejected/Duplicated, `results[]`).

⚠️ **Le maiuscole contano.** `VAT` e `Ecotax` sono i nomi della
documentazione JSON; la ricognizione del 25 agosto scriveva `EcoTax`, che e'
lo schema XML — e un nome sbagliato qui e' un pacchetto rifiutato tre giorni
dopo.

⚠️ **Un campo che non va NON si aggiusta: si rifiuta, nominando il codice.**
Stessa regola di `cdiscount_schede`: un pacchetto porta fino a 50.000
offerte, e un ValueError che non dice QUALE riga e' inutile. Il tipo del
rifiuto e' un contratto — solo `ValueError` — perche' chi compone il giro lo
cattura riga per riga.

Questo file NON importa Odoo: si prova con tools/test_cdiscount_offerte.py.
"""
import re
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import parse_qs, unquote, urlparse

try:
    from .cdiscount_rapporto import (
        IN_LAVORAZIONE, PRONTO, RIFIUTATO, RIUSCITO, SCONOSCIUTO, _tetto)
    from .cdiscount_schede import _testo
except ImportError:  # eseguito fuori da Odoo, dai test di tools/
    from cdiscount_rapporto import (
        IN_LAVORAZIONE, PRONTO, RIFIUTATO, RIUSCITO, SCONOSCIUTO, _tetto)
    from cdiscount_schede import _testo

# I tetti dichiarati dalla documentazione.
MAX_PER_LOTTO = 100               # offerte per `POST .../offer-requests`
MAX_PER_PACCHETTO_OFFERTE = 50000  # offerte per pacchetto
MAX_CODICE = 50
QUANTITA_MASSIMA = 10000000000
CONDIZIONE = "New"
CODICE_IVA = "VAT"
CODICE_ECOTAX = "Ecotax"
# ⚠️ MISURATO: nessuno dei due modi dell'account regge oltre i 30 kg.
SOGLIA_KG = 30

# Lo stato di un PACCHETTO di offerte (`GET /offer-packages/{id}`), LETTO.
# Il quarto valore non esiste per le schede: un pacchetto di offerte puo'
# essere rifiutato IN BLOCCO, e allora ogni sua riga e' rifiutata.
RIFIUTATO_IN_BLOCCO = "rifiutato_in_blocco"
STATI_PACCHETTO_IN_LAVORAZIONE = ("waitingforcompletion", "ready",
                                  "integrationpending")
STATI_PACCHETTO_PRONTO = ("integrated",)
STATI_PACCHETTO_RIFIUTATO = ("rejected",)

# Gli esiti di UNA riga (`integrationStatus`), LETTI.
ESITI_RIUSCITI = ("integrated",)
ESITI_RIFIUTATI = ("rejected", "duplicated")
CHIAVI_CODICE = ("sellerExternalReference",)
CHIAVE_STATO_VOCE = "integrationStatus"
CHIAVE_RISULTATI = "results"
MAX_MESSAGGIO = 300

_FORMA_NUMERO = re.compile(r"^[^\s/]*[0-9][^\s/]*$")


def _euro(valore, chi, nome, minimo=None, obbligatorio=True):
    """Un importo in euro arrotondato ai centesimi, o un ValueError.

    ⚠️ Si arrotonda ai centesimi, e in modo commerciale (2.005 → 2.01): un
    JSON pubblico con 8.700000000000001 e' brutto, e un prezzo troncato e'
    un centesimo regalato su migliaia di offerte.
    """
    if valore is None or valore is False:
        if obbligatorio:
            raise ValueError("%s: manca %s." % (chi, nome))
        valore = 0
    if isinstance(valore, bool):
        raise ValueError("%s: %s e' %r, che non e' un importo."
                         % (chi, nome, valore))
    try:
        numero = Decimal(str(valore)).quantize(Decimal("0.01"),
                                               rounding=ROUND_HALF_UP)
    except Exception:
        raise ValueError("%s: %s e' %r, che non e' un importo."
                         % (chi, nome, valore))
    if minimo is not None and numero < minimo:
        raise ValueError("%s: %s e' %s, e non puo' essere sotto %s."
                         % (chi, nome, numero, minimo))
    return float(numero)


def _pezzi(quantita):
    """La quantita' come intero, dentro i limiti. Zero e' legittimo: e'
    cosi' che si ritira un'offerta senza cancellarla."""
    if quantita is None or quantita is False:
        return 0
    try:
        interi = int(quantita)
    except (TypeError, ValueError):
        return 0
    return max(0, min(interi, QUANTITA_MASSIMA))


def corpo_offerta(codice, gtin, prezzo, iva, ecotax, quantita, modo, costo,
                  costo_aggiuntivo, giorni_preparazione):
    """Il corpo di UNA offerta dentro `POST /offer-packages/{id}/offer-requests`.

    `prezzo` e' in euro, IVA inclusa. `iva` e' una percentuale. `ecotax` e'
    l'éco-participation in euro, e a ZERO si dichiara comunque: e' il canale
    a decidere se pretenderla (guardia del connettore), non questa funzione.
    """
    codice_pulito = _testo(codice)
    chi = "Offerta %s" % (codice_pulito or "<senza codice>")
    if not codice_pulito:
        raise ValueError("%s: manca il riferimento venditore (lo SKU): senza, "
                         "l'offerta non e' ricollegabile a niente." % chi)
    if len(codice_pulito) > MAX_CODICE:
        raise ValueError("%s: il riferimento venditore e' lungo %d caratteri "
                         "e Cdiscount ne accetta %d."
                         % (chi, len(codice_pulito), MAX_CODICE))
    gtin_pulito = _testo(gtin)
    if not gtin_pulito:
        raise ValueError("%s: manca il GTIN. L'offerta nomina la scheda per "
                         "codice a barre." % chi)
    if not gtin_pulito.isdigit():
        raise ValueError("%s: il GTIN «%s» non e' fatto di sole cifre."
                         % (chi, gtin_pulito))
    prezzo_pulito = _euro(prezzo, chi, "il prezzo")
    if prezzo_pulito <= 0:
        raise ValueError("%s: il prezzo e' %s. Un prezzo a zero o negativo e' "
                         "un dato mancante, non un'offerta. (La quantita' a "
                         "zero invece e' legittima: e' cosi' che si ritira.)"
                         % (chi, prezzo_pulito))
    iva_pulita = _euro(iva, chi, "l'IVA (%)", minimo=0)
    ecotax_pulita = _euro(ecotax, chi, "l'éco-participation", minimo=0,
                          obbligatorio=False)
    modo_pulito = _testo(modo)
    if not modo_pulito:
        raise ValueError("%s: manca il modo di consegna: Cdiscount rifiuta le "
                         "offerte senza." % chi)
    costo_pulito = _euro(costo, chi, "il costo di spedizione", minimo=0,
                         obbligatorio=False)
    aggiuntivo_pulito = _euro(costo_aggiuntivo, chi,
                              "il costo di spedizione aggiuntivo", minimo=0,
                              obbligatorio=False)
    if giorni_preparazione is None or giorni_preparazione is False:
        giorni_preparazione = 0
    try:
        giorni = int(giorni_preparazione)
    except (TypeError, ValueError):
        raise ValueError("%s: i giorni di preparazione (%r) non sono un "
                         "numero." % (chi, giorni_preparazione))
    if giorni < 0:
        raise ValueError("%s: i giorni di preparazione sono %d, e non "
                         "possono essere negativi." % (chi, giorni))
    return {
        "sellerExternalReference": codice_pulito,
        "product": {"gtin": gtin_pulito},
        "condition": CONDIZIONE,
        "price": {
            "price": prezzo_pulito,
            "taxes": [{"code": CODICE_IVA, "value": _numero_secco(iva_pulita)},
                      {"code": CODICE_ECOTAX,
                       "value": _numero_secco(ecotax_pulita)}],
        },
        "deliveryModes": [{"code": modo_pulito, "cost": costo_pulito,
                           "additionalCost": aggiuntivo_pulito}],
        "preparationTime": giorni,
        "quantity": _pezzi(quantita),
    }


def _numero_secco(valore):
    """20.0 → 20, 0.35 → 0.35: un intero si scrive da intero."""
    return int(valore) if float(valore).is_integer() else valore


def lotti(corpi):
    """I corpi divisi in lotti da `MAX_PER_LOTTO`, senza doppioni.

    ⚠️ Un codice ripetuto dentro lo stesso pacchetto fa rifiutare TUTTE E DUE
    le offerte, non una (LETTO): ci si ferma prima, nominando il codice.
    """
    elenco = list(corpi or [])
    if len(elenco) > MAX_PER_PACCHETTO_OFFERTE:
        raise ValueError("Un pacchetto porta al massimo %d offerte, qui ce ne "
                         "sono %d." % (MAX_PER_PACCHETTO_OFFERTE, len(elenco)))
    visti = set()
    for corpo in elenco:
        codice = _testo(corpo.get("sellerExternalReference"))
        if codice in visti:
            raise ValueError("Il riferimento «%s» compare due volte nello "
                             "stesso pacchetto: Cdiscount rifiuterebbe tutte "
                             "e due le offerte." % codice)
        visti.add(codice)
    return [elenco[i:i + MAX_PER_LOTTO]
            for i in range(0, len(elenco), MAX_PER_LOTTO)]


def _testa(teste, nome):
    """Un'intestazione, senza badare alle maiuscole. "" se manca."""
    if not isinstance(teste, dict):
        return ""
    nome = nome.lower()
    for chiave, valore in teste.items():
        if _testo(chiave).lower() == nome:
            return _testo(valore)
    return ""


def numero_pacchetto_offerte(teste, corpo):
    """Il numero del pacchetto reso da `POST /offer-packages`, o "".

    LETTO: 201 senza corpo, numero nel `Content-Location`. Si prende
    l'ultimo segmento del percorso; se l'intestazione manca si guarda un
    `packageId` nel corpo. ⚠️ Un valore senza nemmeno una cifra non e' un
    numero: e' la lezione del pacchetto «Accepted» delle schede.
    """
    posizione = _testa(teste, "Content-Location")
    if posizione:
        percorso = urlparse(posizione).path.rstrip("/")
        candidato = percorso.rsplit("/", 1)[-1]
        if candidato and _FORMA_NUMERO.match(candidato):
            return candidato
    if isinstance(corpo, dict):
        grezzo = corpo.get("packageId")
        if grezzo is not None and not isinstance(grezzo, (bool, dict, list)):
            candidato = _testo(grezzo)
            if candidato and _FORMA_NUMERO.match(candidato):
                return candidato
    return ""


def cursore_da_link(teste):
    """Il cursore della pagina successiva, dall'intestazione `Link`, o "".

    LETTO: `</x?cursor=abc&limit=10>; rel="next"`. Nessun `rel="next"`,
    nessuna pagina dopo.
    """
    link = _testa(teste, "Link")
    if not link:
        return ""
    for pezzo in link.split(","):
        if 'rel="next"' not in pezzo.replace(" ", "").replace("'", '"'):
            continue
        inizio, fine = pezzo.find("<"), pezzo.find(">")
        if inizio < 0 or fine < 0 or fine <= inizio:
            continue
        indirizzo = pezzo[inizio + 1:fine]
        try:
            parametri = parse_qs(urlparse(indirizzo).query)
        except Exception:
            return ""
        for nome, valori in parametri.items():
            if nome.lower() == "cursor" and valori:
                return unquote(valori[0])
    return ""


def stato_pacchetto(corpo):
    """Lo stato di `GET /offer-packages/{id}`, nei termini del raccoglitore."""
    if not isinstance(corpo, dict):
        return SCONOSCIUTO
    stato = _testo(corpo.get("state")).lower()
    if stato in STATI_PACCHETTO_IN_LAVORAZIONE:
        return IN_LAVORAZIONE
    if stato in STATI_PACCHETTO_PRONTO:
        return PRONTO
    if stato in STATI_PACCHETTO_RIFIUTATO:
        return RIFIUTATO_IN_BLOCCO
    return SCONOSCIUTO


def _risultati_in_chiaro(risultati):
    """`[{resultCode, message}]` → «message [code]; …», col tetto."""
    pezzi = []
    if isinstance(risultati, (list, tuple)):
        for uno in risultati:
            if not isinstance(uno, dict):
                continue
            messaggio = _testo(uno.get("message"))[:MAX_MESSAGGIO]
            codice = _testo(uno.get("resultCode"))[:40]
            testo = messaggio
            if codice:
                testo = ("%s [%s]" % (messaggio, codice)) if messaggio else codice
            if testo:
                pezzi.append(testo)
    return _tetto("; ".join(pezzi))


def motivo_pacchetto(corpo):
    """Il perche' di un pacchetto rifiutato in blocco, o una frase che dice
    che Cdiscount non l'ha detto."""
    risultato = corpo.get("result") if isinstance(corpo, dict) else None
    testo = _risultati_in_chiaro([risultato]) if risultato else ""
    return testo or ("pacchetto rifiutato in blocco da Cdiscount, senza un "
                     "motivo nella risposta")


def _codice_riga(voce):
    grezzo = voce.get(CHIAVI_CODICE[0])
    if grezzo is None or isinstance(grezzo, (bool, dict, list)):
        return ""
    return _testo(grezzo)


def leggi_esiti_offerte(corpo):
    """Gli esiti di `GET /offer-packages/{id}/offer-requests-results`.

    Rende `(stato, {codice: {"esito", "motivo", "operazione"}})` nella STESSA
    forma di `cdiscount_rapporto.leggi_rapporto`, cosi' `riconcilia` e il
    raccoglitore restano quelli delle schede. `corpo` e' l'elenco delle righe
    (tutte le pagine unite) o un dizionario con `items`.
    """
    if isinstance(corpo, dict):
        righe = corpo.get("items")
    elif isinstance(corpo, (list, tuple)):
        righe = corpo
    else:
        return SCONOSCIUTO, {}
    if not isinstance(righe, (list, tuple)):
        return SCONOSCIUTO, {}
    if not righe:
        return IN_LAVORAZIONE, {}
    esiti = {}
    for voce in righe:
        if not isinstance(voce, dict):
            continue
        codice = _codice_riga(voce)
        if not codice:
            continue
        grezzo = _testo(voce.get(CHIAVE_STATO_VOCE))
        stato = grezzo.lower()
        if stato in ESITI_RIUSCITI:
            nuovo = {"esito": RIUSCITO, "motivo": "", "operazione": ""}
        else:
            dettaglio = _risultati_in_chiaro(voce.get(CHIAVE_RISULTATI))
            if stato in ESITI_RIFIUTATI:
                premessa = grezzo if stato == "duplicated" else ""
            elif not stato:
                premessa = "il rapporto non dice lo stato di questa riga"
            else:
                premessa = "stato «%s» non riconosciuto: si conta come rifiutato" % grezzo[:60]
            motivo = " — ".join(p for p in (premessa, dettaglio) if p)
            nuovo = {"esito": RIFIUTATO,
                     "motivo": _tetto(motivo) or "rifiutata senza motivo",
                     "operazione": ""}
        prima = esiti.get(codice)
        if prima is None or prima["esito"] == RIUSCITO:
            # ⚠️ Vince il rifiuto, in qualunque ordine arrivino le righe.
            esiti[codice] = nuovo if prima is None or nuovo["esito"] == RIFIUTATO else prima
    return PRONTO, esiti


def modo_consegna(elenco, codice):
    """Il modo di consegna dell'account che ha questo codice, o None.

    `elenco` e' `GET /sellers/delivery-modes` gia' spacchettato (MISURATO:
    `{"code", "name", "delivery_delay", "is_express_delivery",
    "more_than30_kg_product"}`).
    """
    cercato = _testo(codice).strip().lower()
    if not cercato or not isinstance(elenco, (list, tuple)):
        return None
    for modo in elenco:
        if isinstance(modo, dict) and _testo(modo.get("code")).strip().lower() == cercato:
            return modo
    return None


def regge_oltre_30kg(modo):
    """Se un modo di consegna dichiara di reggere i colli oltre i 30 kg."""
    return bool(isinstance(modo, dict) and modo.get("more_than30_kg_product") is True)
