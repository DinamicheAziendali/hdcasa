# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Com'e' fatto un ORDINE Cdiscount (Octopia), e come si dichiara spedito.

Solo forma: qui non si parla con nessuno e non si importa Odoo. E' la
Consegna 3; il contratto e' `docs/cdiscount-ordini-contratto.md`.

Cosa e' MISURATO (2026-09-02, sull'account vero, senza ordini) e cosa e'
LETTO (documentazione ufficiale):

- MISURATO: il percorso di scarico e la sua paginazione a indice da 100,
  il filtro `status` validato, il conteggio (`/orders/count`, un intero
  nudo), i 66 corrieri di `GET /carriers`;
- LETTO: la forma di un ordine (`orderId`, `customer.reference`,
  `billingAddress`, `lines[]` con `offer.sellerProductId`, `sellingPrice`,
  `offerPrice.commission`, `delivery`, `shippingAddress`) e il corpo della
  spedizione (`parcelNumber`, `carrierName`, `trackingUrl`).

⚠️ **Un campo che non torna produce un ordine IN ERRORE che nomina il
campo**, mai un ordine nato a meta': il tipo del rifiuto e' un contratto,
solo `ValueError`, e chi importa lo cattura ordine per ordine.

Questo file NON importa Odoo: si prova con tools/test_cdiscount_ordini.py.
"""
from urllib.parse import urlencode

try:
    from .cdiscount_schede import _testo
except ImportError:  # eseguito fuori da Odoo, dai test di tools/
    from cdiscount_schede import _testo

# MISURATO: `pageSize` massimo 100, `pageIndex` da 1; `limit` viene ignorato.
ORDINI_PER_PAGINA = 100
MAX_PAGINE_ORDINI = 200
# ⚠️ Si scaricano SOLO gli InPreparation: l'indirizzo di consegna esiste solo
# da li', ed e' l'unico stato in cui si puo' spedire (LETTO).
STATO_DA_SCARICARE = "InPreparation"
PERCORSO_ORDINI = "/orders"
PERCORSO_CONTEGGIO_IN_ATTESA = "/orders/count?status=WaitingAcceptance"
# Le righe che NON si importano (LETTO): annullate, in annullamento,
# rifiutate. E quelle di un altro `supplyMode`: non le spediamo noi.
STATI_RIGA_ESCLUSI = ("cancelled", "cancelrequest", "refused", "rejected")
SUPPLY_MODE_NOSTRO = "seller"
SEGNAPOSTO_TRACCIAMENTO = "{tracking}"

# MISURATO il 2026-09-02: `GET /carriers`, 66 voci, elenco nudo di
# `{code, label}`. ⚠️ Il `code` NON e' unico: `pcc` porta quattro etichette
# (La Poste, Colissimo, Chronopost, Chronofresh) e `dhldp` due. Per non
# perderle, ai doppioni si aggiunge un suffisso; cio' che si manda a Octopia
# e' comunque l'ETICHETTA (`carrierName`, vedi `corpo_spedizione`), che e'
# quella vera. Un'etichetta arrivava con uno spazio in coda: tolto.
CORRIERI = [
    ("dpd", "DPD"), ("pnl", "PostNL"), ("fex", "FedEx"), ("med", "Med Africa"),
    ("tnt", "TNT"), ("mre", "Mondial Relay"), ("brt", "BRT"),
    ("mal", "Malaysia Post"), ("fwl", "FastWL"), ("cub", "CUBYN"),
    ("dac", "Dachser"), ("cne", "CNE Express"), ("ems", "China EMS"),
    ("yun", "Yun Express"), ("ara", "Aramex"), ("hep", "Heppner"),
    ("default", "autre"), ("roy", "Royal Mail"), ("hmr", "Hermes"),
    ("gls", "GLS"), ("asd", "Asendia"), ("sfe", "SF Express"),
    ("sfc", "SFC Service"), ("ups", "UPS"), ("cpr", "Colis Privé"),
    ("cnp", "China Post"), ("4px", "4PX"), ("maz", "Mazet"),
    ("rlc", "Relais Colis"), ("dbs", "DB Schenker"), ("bps", "Bpost"),
    ("wip", "WishPost"), ("wan", "WanbExpress"), ("gef", "Gefco"),
    ("ser", "Seur"), ("CLogistics", "CLOG"), ("khn", "Kuehne + Nagel"),
    ("pcc", "La Poste"), ("pcc-2", "Colissimo"), ("pcc-3", "Chronopost"),
    ("pcc-4", "Chronofresh"), ("geo", "GEODIS"), ("dhldp", "DHL"),
    ("dhldp-2", "Deutsche Post"), ("yan", "Yanwen"), ("vir", "VIR"),
    ("tgt", "TONGTUEXPRESS"), ("j&t", "AFS"), ("cdr", "Coordinadora"),
    ("sue", "SwissUniversalExpress"), ("dsv", "DSV dpd"),
    ("clogp30", "CChezVous"), ("trk", "Trusk"), ("agd", "Agediss"),
    ("gofo", "GofoAfs"), ("gel", "Gel"), ("erd", "Eurodis"),
    ("jsp", "Jersey Post"), ("syp", "Sunyou Post"), ("ams", "Amazon Shipping"),
    ("vcv", "VertChezVous"), ("can", "Cainiao"), ("jcex", "JCEX"),
    ("ydh", "YDH"), ("war", "Warning"), ("landmark-global", "Landmark"),
]
_ETICHETTE = {codice.lower(): etichetta for codice, etichetta in CORRIERI}

# ⚠️ I marchi corriere del tronco (`centrivo.carrier.brand.code`) che si
# traducono in un corriere di Octopia. **In Francia si spedisce solo con BRT e
# GLS** (deciso da Angelo il 2026-09-02): Poste Italiane e SDA NON sono qui,
# e non e' una dimenticanza — Poste Italiane non esiste nell'elenco di
# Octopia, e la voce generica «autre» non e' stata voluta. Un vettore senza
# traduzione ferma la spedizione dicendolo, che e' il verso giusto.
MARCHI_CORRIERI = {
    "brt": "brt",
    "gls": "gls",
    "dhl": "dhldp",
    "ups": "ups",
    "tnt": "tnt",
    "dpd": "dpd",
    "fedex": "fex",
}


def percorso_ordini(pagina, canale_vendita):
    """La pagina `pagina` degli ordini da scaricare (MISURATO)."""
    return "%s?%s" % (PERCORSO_ORDINI, urlencode(
        {"status": STATO_DA_SCARICARE, "salesChannelId": canale_vendita,
         "pageSize": ORDINI_PER_PAGINA, "pageIndex": int(pagina)}))


def etichetta_corriere(codice):
    """L'etichetta di Octopia per un codice di `CORRIERI`, o ""."""
    return _ETICHETTE.get(_testo(codice).strip().lower(), "")


def url_tracciamento(modello, tracking):
    """L'indirizzo di tracciamento dal modello del tronco, o "".

    Senza il segnaposto `{tracking}` non si inventa niente: un indirizzo che
    non porta il numero manderebbe il cliente su una pagina vuota.
    """
    modello = _testo(modello)
    tracking = _testo(tracking).strip()
    if not modello or not tracking or SEGNAPOSTO_TRACCIAMENTO not in modello:
        return ""
    return modello.replace(SEGNAPOSTO_TRACCIAMENTO, tracking)


def corpo_spedizione(tracking, etichetta, url=""):
    """Il corpo di `POST /orders/{orderId}/shipments`: UN collo per l'ordine.

    LETTO: «Cdiscount only enables one parcel per complete order». Senza
    `orderLineIds` il collo copre tutte le righe. `trackingUrl` e' facoltativo
    e si manda solo se c'e'.
    """
    numero = _testo(tracking).strip()
    nome = _testo(etichetta).strip()
    if not numero:
        raise ValueError("manca il numero di tracciamento: senza, Cdiscount "
                         "non accetta la spedizione.")
    if not nome:
        raise ValueError("manca il nome del corriere per Cdiscount.")
    collo = {"parcelNumber": numero, "carrierName": nome}
    indirizzo = _testo(url).strip()
    if indirizzo:
        collo["trackingUrl"] = indirizzo
    return [collo]


def _numero(valore, predefinito=None):
    """Un numero dal JSON, o il predefinito. `"2"` e' un due; `True` no."""
    if valore is None or valore is False or isinstance(valore, bool):
        return predefinito
    try:
        return float(valore)
    except (TypeError, ValueError):
        return predefinito


def indirizzo(grezzo):
    """Un indirizzo Octopia nella forma nostra (LETTO).

    `firstName lastName` fanno il nome (la civilta' — Mme, M. — resta fuori:
    un contatto Odoo che si chiama «Mme Camille Durand» non si cerca e non
    si stampa bene); `addressLine1` e' la via,
    le righe 2 e 3 finiscono insieme nella seconda via. Tutto e' testo
    pulito; cio' che manca e' "".
    """
    grezzo = grezzo if isinstance(grezzo, dict) else {}
    nome = " ".join(p for p in (_testo(grezzo.get("firstName")),
                                _testo(grezzo.get("lastName"))) if p)
    via2 = " ".join(p for p in (_testo(grezzo.get("addressLine2")),
                                _testo(grezzo.get("addressLine3"))) if p)
    return {
        "nome": nome,
        "azienda": _testo(grezzo.get("companyName")),
        "partita_iva": _testo(grezzo.get("companyVatNumber")),
        "via": _testo(grezzo.get("addressLine1")),
        "via2": via2,
        "cap": _testo(grezzo.get("postalCode")),
        "citta": _testo(grezzo.get("city")),
        "provincia": _testo(grezzo.get("stateOrRegion")),
        "paese": _testo(grezzo.get("countryCode")).upper(),
    }


def _riga(grezza, numero_ordine):
    """Una riga d'ordine nella forma nostra, o l'esclusione, o un ValueError.

    Rende `(riga, None)` se e' importabile, `(None, esclusione)` se non lo
    e'. Un campo obbligatorio che manca (codice, quantita', prezzo) solleva
    nominando la riga e l'ordine.
    """
    id_riga = _testo(grezza.get("orderLineId"))
    chi = "Ordine %s, riga %s" % (numero_ordine, id_riga or "<senza id>")
    stato = _testo(grezza.get("status"))
    offerta = grezza.get("offer") if isinstance(grezza.get("offer"), dict) else {}
    supply = _testo(offerta.get("supplyMode")).lower()
    if stato.lower() in STATI_RIGA_ESCLUSI:
        return None, {"id": id_riga, "perche": "riga in stato %s" % stato}
    if supply and supply != SUPPLY_MODE_NOSTRO:
        return None, {"id": id_riga,
                      "perche": "riga con supplyMode %s: non la spediamo noi"
                      % _testo(offerta.get("supplyMode"))}
    codice = _testo(offerta.get("sellerProductId"))
    if not codice:
        raise ValueError("%s: manca `offer.sellerProductId`, il nostro codice "
                         "articolo. Senza, la riga non si aggancia a nessun "
                         "prodotto." % chi)
    quantita = _numero(grezza.get("quantity"))
    if quantita is None or quantita <= 0:
        raise ValueError("%s: la quantita' e' %r, e non e' un numero positivo."
                         % (chi, grezza.get("quantity")))
    vendita = (grezza.get("sellingPrice")
               if isinstance(grezza.get("sellingPrice"), dict) else {})
    prezzo = _numero(vendita.get("unitSalesPrice"))
    if prezzo is None:
        raise ValueError("%s: manca `sellingPrice.unitSalesPrice`, il prezzo "
                         "pagato dal cliente." % chi)
    offerta_prezzo = (grezza.get("offerPrice")
                      if isinstance(grezza.get("offerPrice"), dict) else {})
    commissione = (offerta_prezzo.get("commission")
                   if isinstance(offerta_prezzo.get("commission"), dict)
                   else {})
    consegna = (grezza.get("delivery")
                if isinstance(grezza.get("delivery"), dict) else {})
    return {
        "id": id_riga,
        "codice": codice,
        "gtin": _testo(offerta.get("productGtin")),
        "titolo": _testo(offerta.get("productTitle")),
        "stato": stato,
        "quantita": int(quantita),
        "prezzo": prezzo,
        "spedizione": _numero(vendita.get("shippingCost"), 0.0),
        "commissione_con_iva": _numero(commissione.get("amountWithVat"), 0.0),
        "commissione_senza_iva": _numero(commissione.get("amountWithoutVat"),
                                         0.0),
        "tasso_commissione": _numero(commissione.get("rate"), 0.0),
        "promesso_entro": _testo(consegna.get("promisedAtMax")),
        "spedire_entro": _testo(consegna.get("shippedAtMax")),
        "consegna": (grezza.get("shippingAddress")
                     if isinstance(grezza.get("shippingAddress"), dict)
                     else None),
    }, None


def leggi_ordine(corpo):
    """Un ordine Octopia nella forma nostra, o un ValueError che dice cosa.

    Rende un dizionario con: `numero`, `stato`, `cliente` (il riferimento
    anonimo), `fatturazione` e `consegna` (indirizzi nostri),
    `consegna_uguale`, `righe` (importabili), `escluse`, `totale` (quello
    pagato dal cliente, o None), `valuta`, `pagamento`, `acquistato_il`,
    `spedire_entro`, `business`.

    ⚠️ La consegna e' quella della PRIMA riga che ne ha una (LETTO: sta
    sulla riga, non sull'ordine); se nessuna riga ne porta, e' la
    fatturazione.
    """
    if not isinstance(corpo, dict):
        raise ValueError("l'ordine non e' un dizionario: %r" % (corpo,))
    numero = _testo(corpo.get("orderId"))
    if not numero:
        raise ValueError("l'ordine non ha `orderId`: senza numero non si "
                         "importa e non si spedisce.")
    grezze = corpo.get("lines")
    if not isinstance(grezze, (list, tuple)):
        raise ValueError("Ordine %s: `lines` non e' un elenco (%r)."
                         % (numero, grezze))
    righe, escluse = [], []
    for grezza in grezze:
        if not isinstance(grezza, dict):
            raise ValueError("Ordine %s: una riga non e' un dizionario (%r)."
                             % (numero, grezza))
        riga, esclusa = _riga(grezza, numero)
        if riga is not None:
            righe.append(riga)
        else:
            escluse.append(esclusa)
    fatturazione = indirizzo(corpo.get("billingAddress"))
    consegna_grezza = next((r["consegna"] for r in righe if r["consegna"]),
                           None)
    consegna = indirizzo(consegna_grezza) if consegna_grezza else dict(fatturazione)
    for riga in righe:
        riga.pop("consegna", None)
    totale = corpo.get("totalPrice")
    totale = (_numero(totale.get("sellingPrice"))
              if isinstance(totale, dict) else None)
    cliente = corpo.get("customer") if isinstance(corpo.get("customer"), dict) else {}
    pagamento = corpo.get("payment") if isinstance(corpo.get("payment"), dict) else {}
    return {
        "numero": numero,
        "stato": _testo(corpo.get("status")),
        "cliente": _testo(cliente.get("reference")),
        "fatturazione": fatturazione,
        "consegna": consegna,
        "consegna_uguale": consegna == fatturazione,
        "righe": righe,
        "escluse": escluse,
        "totale": totale,
        "valuta": _testo(corpo.get("currencyCode")),
        "pagamento": _testo(pagamento.get("method")),
        "acquistato_il": _testo(corpo.get("purchasedAt")),
        "spedire_entro": _testo(corpo.get("shippedAtMax")),
        "business": corpo.get("businessOrder") is True,
    }


def totale_righe(righe):
    """Quanto valgono le righe importabili: prezzo x quantita' + spedizione."""
    return round(sum(r["prezzo"] * r["quantita"] + r["spedizione"]
                     for r in righe), 2)


def spedizione_righe(righe):
    """La spedizione totale delle righe importabili."""
    return round(sum(r["spedizione"] for r in righe), 2)
