# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Comunicazione delle spedizioni a Temu (evasione con corrieri propri).

COME FUNZIONA DAVVERO, letto sulla documentazione il 2026-08-20 e diverso da
come lo avevamo immaginato:

  `sendType` = 0  tutto l'ordine in UN pacco con UN tracking
  `sendType` = 1  ordine diviso in PIU' pacchi con PIU' tracking
  `sendType` = 2  piu' ordini in un pacco solo

Quindi le spedizioni multiple SONO supportate. Il vincolo vero e' un altro, piu'
stretto e piu' insidioso: **una singola riga d'ordine va comunicata tutta in una
volta**. Se una riga viene spedita a rate, i tracking vanno raccolti e mandati
insieme (per questo esiste `subSendRequests`).

Da qui il disegno: si comunicano SOLO le righe la cui quantita' e' interamente
uscita dal magazzino, e ogni riga gia' comunicata viene registrata perche' **un
numero di tracking su Temu si usa una volta sola**. Cosi' un ordine spedito in
tre picking diventa tre comunicazioni pulite, invece di una bugia iniziale
("e' partito tutto") o di un errore alla seconda.
"""
import json
import logging

from .temu_orders_parser import parse_order, righe_da_spedire

_logger = logging.getLogger(__name__)

API_SHIPMENT_CONFIRM = "bg.logistics.shipment.v2.confirm"
API_SHIPMENT_GET = "bg.logistics.shipment.v2.get"
API_WAREHOUSES = "bg.logistics.warehouse.list.get"
API_ORDER_DETAIL = "bg.order.detail.v2.get"

SEND_TYPE_UNICO = 0
SEND_TYPE_MULTIPLO = 1


class TemuShippingMixin(object):
    """Metodi di spedizione del connettore Temu."""

    def push_shipment(self, order_map):
        """Comunica a Temu le righe uscite dal magazzino. Idempotente.

        Ritorna True se ha comunicato qualcosa (o se non c'era piu' niente da
        comunicare), False in caso di errore. Non solleva eccezioni sui guasti
        di Temu: il cron del nucleo isola gia' gli errori per ordine, ma un
        connettore che esplode rende illeggibile il log.

        ⚠️ UNICA ECCEZIONE che esce di qui: la `UserError` del turno occupato
        qui sotto. E' voluta — dice a chi ha cliccato perche' non si e' fatto
        niente — e il cron del nucleo la isola per ordine come tutte le altre.

        ⚠️ E PERCHE' QUELLA PROMESSA VA MANTENUTA ALLA LETTERA. L'unico
        chiamante automatico e' `cron_push_shipments` nel nucleo, e il suo
        `except` fa una `create` NUDA leggendo per giunta un campo del
        record. Se da qui uscisse un errore di database, la transazione
        sarebbe gia' ABORTITA: quell'`except` esploderebbe a sua volta e la
        passata di cron morirebbe per TUTTI i canali di TUTTI i marketplace,
        ManoMano e BricoBravo compresi, che sono in produzione. Per questo
        ogni scrittura di questo file — registro anti-doppione, marchio
        sull'ordine, righe di log, magazzino — passa da un savepoint.
        """
        # ⚠️ Un giro per volta su questo canale. Due clic — o un cron che si
        # sovrappone a un clic — comunicherebbero la stessa spedizione due
        # volte, e su Temu un numero di tracking si usa UNA volta sola: il
        # secondo invio verrebbe rifiutato e la riga resterebbe ferma. Qui,
        # a differenza delle giacenze, non c'e' nessuna chiave di unicita'
        # che protegga lato Temu.
        self._prendi_il_turno("comunicazione delle spedizioni")
        channel = self.channel
        parent_sn = order_map.external_id

        if order_map.shipment_pushed:
            return True
        sale_order = order_map.sale_order_id
        if not sale_order:
            return self._temu_errore_spedizione(
                parent_sn, "Nessun ordine Odoo collegato.")

        # 1) Le righe dell'ordine come le conosce Temu.
        risposta = self.client.call(API_ORDER_DETAIL,
                                    {"parentOrderSn": parent_sn})
        if not risposta.ok:
            return self._temu_errore_spedizione(
                parent_sn, "Dettaglio ordine non leggibile: %s %s"
                % (risposta.error_code or "", risposta.error_msg or ""))
        ordine = parse_order(risposta.data)
        if not ordine:
            return self._temu_errore_spedizione(
                parent_sn, "Dettaglio ordine in una forma non riconosciuta.")
        righe_temu = righe_da_spedire(ordine)
        if not righe_temu:
            return self._temu_errore_spedizione(
                parent_sn, "Nessuna riga da spedire su questo ordine.")

        # 2) Quanto e' uscito davvero, per prodotto, e con quali tracking.
        uscite, spedizioni = self._temu_quantita_uscite(sale_order)
        if not spedizioni:
            # Non e' un errore: semplicemente non e' ancora partito niente.
            return True

        gia_fatte = set(self.env["centrivo.temu.shipment"].search([
            ("channel_id", "=", channel.id),
            ("parent_order_sn", "=", parent_sn),
        ]).mapped("order_sn"))

        # 3) Quali righe sono COMPLETE e non ancora comunicate.
        da_comunicare = []
        incomplete = []
        for riga in righe_temu:
            if riga["order_sn"] in gia_fatte:
                continue
            prodotto = self._temu_prodotto_di_riga(riga)
            if not prodotto:
                incomplete.append("%s (prodotto non risolto)" % riga["order_sn"])
                continue
            uscita = uscite.get(prodotto.id, 0)
            if uscita < riga["qty"]:
                incomplete.append(
                    "%s (usciti %s di %s)" % (riga["order_sn"], uscita, riga["qty"]))
                continue
            da_comunicare.append((riga, prodotto))

        if not da_comunicare:
            if incomplete:
                self._temu_traccia(
                    "skip",
                    "Niente da comunicare: righe non ancora complete — %s. "
                    "Temu vuole ogni riga spedita tutta in una volta."
                    % ", ".join(incomplete), external_id=parent_sn)
            return True

        # 4) Corriere e tracking. Il primo trasferimento evaso porta il tracking
        #    principale, gli altri finiscono come tracking aggiuntivi.
        principale = spedizioni[0]
        codice_corriere, errore = self._temu_codice_corriere(principale["picking"])
        if errore:
            return self._temu_errore_spedizione(parent_sn, errore)

        aggiuntivi = []
        for altra in spedizioni[1:]:
            codice, _err = self._temu_codice_corriere(altra["picking"])
            if codice:
                aggiuntivi.append({
                    "carrierId": int(codice),
                    "trackingNumber": altra["tracking"],
                    "selfShippingWarehouseId": self._temu_magazzino(),
                })

        magazzino = self._temu_magazzino()
        pacco = {
            "orderSendInfoList": [
                {"orderSn": riga["order_sn"],
                 "parentOrderSn": parent_sn,
                 "goodsId": int(riga["goods_id"]) if riga["goods_id"].isdigit() else riga["goods_id"],
                 "skuId": int(riga["sku_id"]) if riga["sku_id"].isdigit() else riga["sku_id"],
                 "quantity": riga["qty"]}
                for riga, _p in da_comunicare
            ],
            "carrierId": int(codice_corriere),
            "trackingNumber": principale["tracking"],
        }
        if magazzino:
            pacco["selfShippingWarehouseId"] = magazzino
        if aggiuntivi:
            pacco["subSendRequests"] = aggiuntivi

        tutte = len(da_comunicare) == len(righe_temu)
        corpo = {
            "sendType": SEND_TYPE_UNICO if tutte else SEND_TYPE_MULTIPLO,
            "sendRequestList": [pacco],
        }

        # 5) Modalita' simulazione: si costruisce il messaggio e lo si scrive nel
        #    log SENZA mandarlo. Serve a leggere cosa partirebbe prima di
        #    accendere davvero il push.
        if channel.temu_simulate:
            self._temu_traccia(
                "skip",
                "SIMULAZIONE: messaggio pronto ma NON inviato (%s righe, "
                "corriere %s, tracking %s)."
                % (len(da_comunicare), codice_corriere,
                   principale["tracking"]),
                payload=json.dumps(corpo, ensure_ascii=False)[:20000],
                external_id=parent_sn)
            return True

        esito = self.client.call(API_SHIPMENT_CONFIRM, corpo)

        # ⚠️ PRIMA di dire «rifiutata», si guarda se il verdetto e' CERTO.
        # Un 502 o una rete caduta vogliono dire che la richiesta PUO' essere
        # arrivata lo stesso — e su Temu un numero di tracking si usa UNA VOLTA
        # SOLA. Se la si segnasse fallita e qualcuno la rimandasse, Temu la
        # rifiuterebbe perche' quel tracking risulta gia' usato, e la riga
        # resterebbe bloccata senza che si capisca perche'.
        incerto = self._causa_incerta(esito)
        if incerto:
            # ⚠️ IL MESSAGGIO DICE COSA SUCCEDE DAVVERO, non cosa vorremmo.
            # Qui c'era scritto «NON la si rimanda»: era falso. Niente
            # trattiene questa riga — `shipment_pushed` resta False e il
            # registro anti-doppione e' vuoto, quindi al prossimo giro il
            # cron ricomincia da capo con LO STESSO tracking, da solo. La
            # quarantena vera (risolvere l'ignoto rileggendo da Temu) e' un
            # lavoro a se': finche' non c'e', questo testo non deve
            # promettere una protezione che non esiste.
            return self._temu_errore_spedizione(
                parent_sn,
                "Esito IGNOTO sulla comunicazione della spedizione: %s.\n\n"
                "⚠️ Non si sa se Temu l'abbia presa, e su Temu un numero di "
                "tracking si usa una volta sola. ⚠️ IL PROSSIMO GIRO "
                "RIPROVERA' DA SOLO con lo stesso tracking: se la "
                "comunicazione era gia' passata, quel secondo invio verra' "
                "rifiutato e la riga restera' ferma. Va guardato PRIMA sul "
                "Seller Center — o col bottone «Stato spedizione su Temu» su "
                "questo ordine — se l'ordine %s risulta gia' spedito, e solo "
                "allora si decide."
                % (incerto, parent_sn),
                payload=json.dumps(esito.raw, ensure_ascii=False))

        if not esito.ok:
            return self._temu_errore_spedizione(
                parent_sn, "Temu ha rifiutato la spedizione: %s %s"
                % (esito.error_code or "", esito.error_msg or ""),
                payload=json.dumps(esito.raw, ensure_ascii=False))

        # 6) Si registra cosa e' partito, PRIMA di dichiarare l'ordine spedito.
        #
        # ⚠️ DA QUI IN POI TEMU HA GIA' ACCETTATO: e' il punto di non ritorno
        # del modulo, e ogni scrittura passa da un savepoint. Non e' zelo:
        # `centrivo.temu.shipment` ha un `unique(channel_id, order_sn)`, e una
        # `create` che lo violasse — due `orderSn` uguali nel dettaglio, una
        # colonna corta — metterebbe la transazione in stato ABORTITO. Nuda,
        # l'eccezione uscirebbe da `push_shipment` (che promette di non
        # sollevare) e finirebbe nell'`except` del cron del nucleo, che fa a
        # sua volta una `create` nuda su una transazione gia' morta: la
        # passata di cron morirebbe per tutti i canali di tutti i marketplace.
        Registro = self.env["centrivo.temu.shipment"]
        registrate = []
        non_registrate = []
        for riga, _p in da_comunicare:
            # ⚠️ Il valore reso SI LEGGE, come prescrive la classe base: un
            # `False` e' l'unica traccia che questa riga NON e' finita nel
            # registro anti-doppione.
            if self._al_riparo(Registro.create, {
                    "channel_id": channel.id,
                    "parent_order_sn": parent_sn,
                    "order_sn": riga["order_sn"],
                    "quantity": riga["qty"],
                    "carrier_code": codice_corriere,
                    "tracking_number": principale["tracking"],
                    "picking_id": principale["picking"].id,
            }):
                registrate.append(riga["order_sn"])
            else:
                non_registrate.append(riga["order_sn"])

        # ⚠️ `gia_fatte` cresce SOLO di cio' che e' davvero in banca dati, non
        # di cio' che si voleva scrivere: al giro dopo e' il registro a dire
        # cosa e' gia' partito, e una riga contata qui ma mancante li' farebbe
        # dichiarare completo un ordine che non lo e'.
        gia_fatte |= set(registrate)
        complete = gia_fatte >= {r["order_sn"] for r in righe_temu}
        segnato = True
        if complete:
            segnato = self._al_riparo(order_map.write,
                                      {"shipment_pushed": True})

        avvisi = ((esito.data or {}).get("warningMessage") or [])
        messaggio = ("Spedizione comunicata: %s righe su %s, corriere %s, "
                     "tracking %s.%s"
                     % (len(da_comunicare), len(righe_temu), codice_corriere,
                        principale["tracking"],
                        "" if complete else
                        " L'ordine NON e' ancora completo: le righe restanti "
                        "partiranno alla prossima spedizione."))
        if avvisi:
            messaggio += " Avvisi di Temu: %s." % "; ".join(str(a) for a in avvisi)
        # ⚠️ IL CASO PEGGIORE DEL MODULO, e il messaggio deve dirlo per intero:
        # la spedizione E' PARTITA su Temu ma in Odoo non ne resta traccia.
        # Chi legge non deve capire «non e' partita»: deve capire che e'
        # partita e che Odoo non lo sa, perche' la conseguenza — al giro dopo
        # si rimanda lo stesso tracking, e su Temu si usa una volta sola — e'
        # esattamente l'opposto di quella di un invio fallito.
        if non_registrate:
            messaggio += (
                "\n\n⚠️ ATTENZIONE: la spedizione E' PARTITA su Temu, ma in "
                "Odoo NON e' stata registrata per %s righe (%s): la scrittura "
                "e' stata annullata dal database. Odoo quindi non sa che quelle "
                "righe sono gia' state comunicate e al prossimo giro provera' a "
                "rimandare LO STESSO tracking, che su Temu si usa una volta "
                "sola. Va guardato sul Seller Center — o col bottone «Stato "
                "spedizione su Temu» su questo ordine — com'e' messa la "
                "spedizione dell'ordine %s prima di toccare altro."
                % (len(non_registrate), ", ".join(non_registrate), parent_sn))
        if not segnato:
            messaggio += (
                "\n\n⚠️ E l'ordine NON e' stato segnato come spedito: anche "
                "quella scrittura e' stata annullata. Le righe sono nel "
                "registro, quindi al giro dopo non si rimanda niente, ma "
                "l'ordine continuera' a comparire fra quelli da comunicare.")
        guasto = bool(non_registrate) or not segnato
        self._temu_traccia("error" if guasto else "success", messaggio,
                           external_id=parent_sn)
        return not guasto

    # ------------------------------------------------------------------
    def _temu_quantita_uscite(self, sale_order):
        """Quantita' uscite per prodotto e trasferimenti pronti da comunicare.

        Un trasferimento conta solo se e' `done` E ha un numero di spedizione:
        senza tracking non c'e' niente da dire a Temu. L'ordine dei
        trasferimenti e' cronologico, cosi' il tracking principale e' quello del
        PRIMO pacco uscito.
        """
        uscite = {}
        spedizioni = []
        pickings = sale_order.picking_ids.filtered(
            lambda p: p.state == "done" and p.carrier_tracking_ref)
        for picking in pickings.sorted(key=lambda p: (p.date_done or p.write_date)):
            spedizioni.append({"picking": picking,
                               "tracking": picking.carrier_tracking_ref})
            for move in picking.move_ids:
                if move.state != "done":
                    continue
                uscite[move.product_id.id] = (
                    uscite.get(move.product_id.id, 0) + move.quantity)
        return uscite, spedizioni

    def _temu_prodotto_di_riga(self, riga):
        """Il prodotto Odoo di una riga Temu, usando lo stesso aggancio
        dell'import: prima il registro schede per `skuId`, poi i codici."""
        prodotto, _diagnosi = self._temu_trova_prodotto(riga)
        return prodotto or False

    def _temu_codice_corriere(self, picking):
        """Identificativo Temu del corriere del trasferimento.

        Riusa per intero il motore del nucleo (vettore → corriere → codice del
        marketplace, con le eccezioni configurabili): Temu non ha una strada
        sua. L'unica differenza e' che qui il "codice" e' un NUMERO.
        """
        from odoo.addons.integrations_core.connectors.carrier_resolver import (
            code_for_brand)
        channel = self.channel
        modello, res_id, nome = channel._picking_carrier_source(picking)
        if not modello:
            return None, ("Il trasferimento %s non ha un vettore: senza vettore "
                          "non si puo' dire a Temu chi ha in mano il pacco."
                          % picking.name)
        sorgente = self.env["centrivo.carrier.source"].search([
            ("source_model", "=", modello), ("source_res_id", "=", res_id),
        ], limit=1)
        if not sorgente or not sorgente.brand_id:
            return None, ("Il vettore '%s' non e' collegato a nessun corriere. "
                          "Si collega in Integrations → Vettori." % (nome or modello))
        eccezione = self.env["centrivo.carrier.override"].search([
            ("channel_id", "=", channel.id),
            ("brand_id", "=", sorgente.brand_id.id),
        ], limit=1)
        codice = code_for_brand(
            sorgente.brand_id.code,
            type(self).carrier_brand_codes,
            eccezione.external_code if eccezione else None)
        if not codice:
            return None, ("Il corriere '%s' non ha un identificativo Temu. "
                          "Si vede in Copertura corrieri, e si risolve con una "
                          "riga in Eccezioni corrieri."
                          % sorgente.brand_id.name)
        if not str(codice).isdigit():
            return None, ("L'identificativo Temu del corriere '%s' non e' un "
                          "numero ('%s'): Temu vuole l'identificativo numerico "
                          "del fornitore logistico, non una sigla."
                          % (sorgente.brand_id.name, codice))
        return str(codice), None

    def _temu_magazzino(self):
        """Identificativo del magazzino di partenza, letto una volta e tenuto.

        Se il canale non ce l'ha, si chiede a Temu e si scrive: e' un dato che
        non cambia, e chiederlo a ogni spedizione sarebbe una chiamata sprecata.
        """
        channel = self.channel
        if channel.temu_warehouse_id:
            return channel.temu_warehouse_id
        risposta = self.client.call(API_WAREHOUSES, {})
        if not risposta.ok:
            return ""
        elenco = (risposta.data or {}).get("warehouseList") or []
        scelto = ""
        for w in elenco:
            if not isinstance(w, dict):
                continue
            if w.get("defaultWarehouse") or not scelto:
                scelto = str(w.get("warehouseId") or "")
                if w.get("defaultWarehouse"):
                    break
        if scelto:
            # ⚠️ Savepoint anche qui, e il valore si legge. Questo metodo gira
            # DENTRO `push_shipment`, prima della conferma: una write nuda che
            # rompesse abortirebbe la transazione e il giro morirebbe — con
            # dietro la passata di cron di tutti i marketplace. E c'e' la
            # seconda ragione, meno ovvia: in Odoo `cr.savepoint()` fa un
            # flush ENTRANDO, quindi una write nuda lasciata in canna
            # partirebbe all'ingresso del savepoint SUCCESSIVO, fuori da ogni
            # protezione. Se non si scrive non si perde niente: al giro dopo
            # si richiede il magazzino, che e' una chiamata sprecata e basta.
            if not self._al_riparo(channel.sudo().write,
                                   {"temu_warehouse_id": scelto}):
                _logger.error(
                    "Temu: il magazzino di partenza %s non si e' potuto "
                    "scrivere sul canale %s; si richiedera' al giro dopo.",
                    scelto, channel.id)
        return scelto

    def check_shipment(self, order_map):
        """Rilegge da Temu lo stato della spedizione. Sola lettura."""
        risposta = self.client.call(
            API_SHIPMENT_GET, {"parentOrderSn": order_map.external_id})
        esito = "success" if risposta.ok else "error"
        self._temu_traccia(
            esito,
            "Stato spedizione riletto da Temu."
            if risposta.ok else
            "Stato spedizione non leggibile: %s %s"
            % (risposta.error_code or "", risposta.error_msg or ""),
            payload=json.dumps(risposta.raw, ensure_ascii=False)[:20000],
            external_id=order_map.external_id, operazione="check_shipment")
        return risposta.ok

    def _temu_traccia(self, esito, messaggio, payload=None, external_id=None,
                      operazione="push_shipment"):
        """Una riga di registro delle spedizioni, che non fa esplodere il giro.

        ⚠️ `_log` fa un `create`, cioe' una SCRITTURA: su una transazione
        abortita esplode verso l'alto, e da `push_shipment` un'eccezione non
        deve uscire (vedi la sua docstring: l'unica ammessa e' la `UserError`
        del turno). Passa quindi da un savepoint, e il valore reso si legge —
        un `False` e' l'unica traccia che la riga non e' finita nel registro.

        ⚠️ Esiste come metodo, e non come cinque blocchi copiati, per la
        stessa ragione di `_temu_registra` nel giro ordini: e' cosi' che le
        `_log` nude sopravvivono a un giro di cura. Il gemello e' li'.
        """
        if not self._al_riparo(self._log, operazione, esito, messaggio,
                               payload=payload, external_id=external_id):
            _logger.error("Temu: riga di registro della spedizione non "
                          "scritta [%s/%s]. %s", operazione, esito, messaggio)

    def _temu_errore_spedizione(self, parent_sn, messaggio, payload=None):
        """Scrive l'errore nel log e ritorna False."""
        self._temu_traccia("error", messaggio, payload=payload,
                           external_id=parent_sn)
        return False
