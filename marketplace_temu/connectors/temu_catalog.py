# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Ricognizione del catalogo Temu: SOLA LETTURA.

Scorre l'elenco delle SKU del negozio, le aggancia ai prodotti Odoo per
riferimento interno e riempie il registro schede. Non scrive NULLA su Temu.
"""
import json
import logging

from .temu_catalog_parser import STATUS_LABELS, match_rows, parse_sku_page

_logger = logging.getLogger(__name__)

# Operazione di elenco SKU (Developer Guide, Product Listing and Delisting).
API_SKU_LIST = "bg.local.goods.sku.list.query"

# Filtri di stato dell'elenco SKU. **Non esiste un valore "tutto" funzionante.**
#
# La documentazione ufficiale si contraddice da sola (verificato il 2026-08-20,
# dettaglio in docs/temu-api-reference.md):
#   - la "Product Listing and Delisting Guide" dichiara sette valori, fra cui 1=ALL;
#   - l'"API Reference" della stessa chiamata ne dichiara due: 2=Active, 3=InActive.
# Sul negozio vero vince la seconda: il valore 1 (ALL) risponde `success` con
# elenco VUOTO, e cosi' 4, 5, 6. Solo 2 e 3 restituiscono righe (393 e 347).
#
# INCOMPLETE, DRAFT e DELETED non mancano: sono stati di ARTICOLO, non di SKU
# (una bozza non ha ancora SKU) e vivono in `bg.local.goods.list.query`, che ha
# infatti un'enumerazione sua: 1=ALL, 4=INCOMPLETE, 5=DRAFT, 6=DELETED.
#
# Quindi: per avere tutte le SKU si chiama DUE volte e si unisce. Leggerne una
# sola significa vedere meta' negozio, ed e' l'errore in cui eravamo caduti —
# invisibile finche' il negozio era in ferie, perche' li' stanno tutte nello
# stesso stato e i conti sembravano tornare.
SKU_SEARCH_TYPES = (2, 3)

# Nome del parametro di pagina. Misurato sul negozio vero (2026-08-20):
# `page`, `pageNumber`, `pageIndex` e `curPage` vengono IGNORATI in silenzio e
# restituiscono sempre la prima pagina; solo `pageNo` fa avanzare l'elenco.
# Un parametro di paginazione ignorato non da' errore: fa rileggere la stessa
# pagina all'infinito, ed e' esattamente quello che era successo.
PAGE_PARAM = "pageNo"

PAGE_SIZE = 100
# Tetto di sicurezza: 500 pagine da 100 = 50.000 SKU. Se lo si tocca, il
# risultato NON è completo e va detto (mai spacciare un elenco troncato per
# l'elenco intero).
MAX_PAGES = 500

# Sottostati che la specifica (§6.1) chiede di evidenziare esplicitamente
# nell'esito: prezzo rifiutato o scheda delistata per prezzo alto.
SUB_STATUS_PREZZO_RIFIUTATO = ("4003", "3004", "6002")


def _totali_per_stato_leggibili(per_stato):
    """Riga leggibile 'Etichetta: N, ...' ordinata dal sottostato più frequente.

    Usa le etichette italiane di STATUS_LABELS; un sottostato non censito o
    assente viene mostrato con il proprio codice grezzo (mai perso in
    silenzio).
    """
    if not per_stato:
        return ""
    ordinati = sorted(per_stato.items(), key=lambda voce: voce[1], reverse=True)
    pezzi = []
    for codice, conteggio in ordinati:
        etichetta = STATUS_LABELS.get(codice) or (
            "senza sottostato" if not codice else "sottostato %s" % codice)
        pezzi.append("%s: %s" % (etichetta, conteggio))
    return ", ".join(pezzi)


class TemuCatalogMixin(object):
    """Metodi di catalogo del connettore Temu (sola lettura)."""

    def recon_catalog(self):
        """Ricognizione completa del catalogo Temu. SOLA LETTURA.

        Scorre TUTTI i contenitori di `SKU_SEARCH_TYPES` e ne unisce il
        risultato: su Temu non esiste un filtro "tutto", e leggerne uno solo
        mostra una porzione del negozio (lezione del 2026-08-20).

        Ritorna il riepilogo, non solleva eccezioni. Chiavi: `letti` (SKU
        DISTINTE, non somma delle pagine), `agganciati`, `orfani`, `creati`,
        `aggiornati`, `pagine` (totale su tutti i contenitori), `errore`
        (stringa o None), `prima_pagina_vuota` (vero solo se NESSUN contenitore
        ha restituito righe: o il negozio è vuoto o i nomi dei campi sono
        cambiati), `per_stato`, `per_contenitore` (quante SKU da ciascun
        filtro) e `prezzo_rifiutato`.
        """
        channel = self.channel
        riepilogo = {"letti": 0, "agganciati": 0, "orfani": 0, "creati": 0,
                     "aggiornati": 0, "pagine": 0, "errore": None,
                     "prima_pagina_vuota": False, "per_stato": {},
                     "per_contenitore": {}, "prezzo_rifiutato": 0}

        if not (channel.temu_app_key and channel.temu_app_secret
                and channel.temu_access_token):
            riepilogo["errore"] = ("Credenziali Temu incomplete sul canale: "
                                   "servono app key, app secret e access token.")
            self._log("temu_recon", "error", riepilogo["errore"])
            return riepilogo

        by_code = self._products_by_default_code()

        # Identificativi già incontrati: valgono per TUTTI i contenitori, così
        # una SKU che comparisse in due filtri viene contata una volta sola, e
        # una pagina che si ripete viene riconosciuta invece di gonfiare i
        # numeri con conteggi che nessuno può smentire.
        sku_viste = set()

        for tipo in SKU_SEARCH_TYPES:
            prima = len(sku_viste)
            self._recon_bucket(tipo, riepilogo, sku_viste, by_code)
            riepilogo["per_contenitore"][tipo] = len(sku_viste) - prima
            if riepilogo["errore"]:
                break

        # Prima pagina vuota è un giudizio sull'INSIEME: se almeno un
        # contenitore ha portato righe, il negozio non è vuoto.
        riepilogo["prima_pagina_vuota"] = (
            not sku_viste and not riepilogo["errore"])

        riepilogo["prezzo_rifiutato"] = sum(
            riepilogo["per_stato"].get(codice, 0)
            for codice in SUB_STATUS_PREZZO_RIFIUTATO)

        if riepilogo["prima_pagina_vuota"]:
            esito = "skip"
        elif riepilogo["errore"]:
            esito = "error"
        else:
            esito = "success"

        messaggio = ("Ricognizione: %(letti)s SKU lette in %(pagine)s pagine, "
                     "%(agganciati)s agganciate, %(orfani)s orfane "
                     "(%(creati)s nuove righe, %(aggiornati)s aggiornate)."
                     % riepilogo)
        contenitori = ", ".join(
            "filtro %s: %s" % (t, n)
            for t, n in sorted(riepilogo["per_contenitore"].items()))
        if contenitori:
            messaggio += " Provenienza: %s." % contenitori
        dettaglio_stati = _totali_per_stato_leggibili(riepilogo["per_stato"])
        if dettaglio_stati:
            messaggio += " Totali per stato: %s." % dettaglio_stati
        self._log("temu_recon", esito, messaggio)
        return riepilogo

    def _recon_bucket(self, tipo, riepilogo, sku_viste, by_code):
        """Legge tutte le pagine di UN contenitore, aggiornando il riepilogo.

        Non ritorna nulla: scrive in `riepilogo` e in `sku_viste`. Un errore
        di rete o di API interrompe l'intera ricognizione (valorizza
        `riepilogo['errore']`), perché un catalogo letto a metà è peggio di un
        catalogo non letto: sembra completo.
        """
        Listing = self.env["centrivo.temu.listing"]
        pagina = 1
        while pagina <= MAX_PAGES:
            result = self.client.call(API_SKU_LIST, {
                "skuSearchType": tipo,
                PAGE_PARAM: pagina,
                "pageSize": PAGE_SIZE,
            })
            if not result.ok:
                riepilogo["errore"] = "%s %s" % (result.error_code or "",
                                                 result.error_msg or "")
                self._log("temu_recon", "error",
                          "Ricognizione interrotta al filtro %s, pagina %s: %s"
                          % (tipo, pagina, riepilogo["errore"]),
                          payload=json.dumps(result.raw, ensure_ascii=False))
                return

            rows, _total, grezzi = parse_sku_page(result.data)
            if grezzi == 0:
                # Contenitore vuoto (o esaurito): non è un errore, è un fatto.
                if pagina == 1:
                    self._log("temu_recon", "skip",
                              "Filtro %s: nessuna SKU. Se il negozio non è "
                              "vuoto, il corpo grezzo nel payload dice se i "
                              "nomi dei campi sono cambiati." % tipo,
                              payload=json.dumps(result.raw,
                                                 ensure_ascii=False)[:20000])
                return

            scartate = grezzi - len(rows)
            if scartate > 0:
                self._log("temu_recon", "skip",
                          "Filtro %s, pagina %s: %s elementi ricevuti, %s "
                          "scartati perché non riconoscibili."
                          % (tipo, pagina, grezzi, scartate))

            nuove = {r["sku_id"] for r in rows if r.get("sku_id")} - sku_viste
            if not nuove:
                # Nessuna SKU nuova: o la paginazione non avanza, o questo
                # contenitore ripete righe già viste altrove. In entrambi i
                # casi insistere non porta nulla.
                if pagina == 1:
                    self._log("temu_recon", "skip",
                              "Filtro %s: tutte le SKU erano già state lette "
                              "da un altro filtro." % tipo)
                else:
                    self._log("temu_recon", "error",
                              "La paginazione non avanza: la pagina %s del "
                              "filtro %s contiene solo SKU già lette."
                              % (pagina, tipo))
                    riepilogo["errore"] = "la paginazione non avanza"
                return

            # Si scrivono SOLO le righe nuove: una SKU presente in due
            # contenitori non va contata due volta nei totali.
            righe_nuove = [r for r in rows if r.get("sku_id") in nuove]
            sku_viste |= nuove

            agganciate, orfane = match_rows(righe_nuove, by_code)
            creati, aggiornati = Listing.upsert_rows(self.channel,
                                                     agganciate + orfane)
            riepilogo["letti"] += len(righe_nuove)
            riepilogo["agganciati"] += len(agganciate)
            riepilogo["orfani"] += len(orfane)
            riepilogo["creati"] += creati
            riepilogo["aggiornati"] += aggiornati
            riepilogo["pagine"] += 1
            for row in righe_nuove:
                stato = row.get("sku_sub_status") or ""
                riepilogo["per_stato"][stato] = (
                    riepilogo["per_stato"].get(stato, 0) + 1)

            # La fine dell'elenco si decide sul conteggio GREZZO ricevuto, non
            # sulle righe sopravvissute al parser: una pagina piena con uno
            # scarto non è l'ultima pagina.
            if grezzi < PAGE_SIZE:
                return
            pagina += 1
        else:
            self._log("temu_recon", "error",
                      "Filtro %s: raggiunto il tetto di %s pagine, l'elenco "
                      "NON è completo." % (tipo, MAX_PAGES))
            riepilogo["errore"] = "elenco troncato al tetto di pagine"


    def _products_by_default_code(self):
        """{riferimento interno MAIUSCOLO: id prodotto} letti come contesto
        della company del canale.

        `with_company()` cambia SOLO la company di contesto (valori
        company-dipendenti come il prezzo): non filtra i record per company.
        La ricerca resta quindi su tutti i prodotti visibili all'utente. I
        prodotti ARCHIVIATI non compaiono in `search()`: una SKU Temu il cui
        prodotto Odoo collegato è stato archiviato risulterà quindi orfana,
        anche se il codice esiste ancora nel database.
        """
        Product = self.env["product.product"].with_company(
            self.channel.company_id)
        prodotti = Product.search([("default_code", "!=", False)])
        mappa = {}
        for prodotto in prodotti:
            codice = (prodotto.default_code or "").strip().upper()
            if codice:
                mappa.setdefault(codice, prodotto.id)
        return mappa
