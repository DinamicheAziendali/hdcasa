# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Invio delle fatture a Temu.

DUE PASSAGGI, non uno (diverso da ManoMano, dove il PDF si manda diretto):
  1. `temu.pay.tax.get.galerie.signature` da' una firma per il caricamento;
  2. il file va caricato con quella firma, e l'indirizzo che si ottiene
  3. si dichiara con `temu.pay.tax.merchant.upload.invoice`.

⚠️ IL PASSAGGIO 2 NON E' DOCUMENTATO nelle pagine dell'API. L'unico riferimento
e' nella guida su resi e rimborsi, che per le etichette di reso cita il percorso
`/api/galerie/general_file`. Quindi qui c'e' un'ipotesi, ed e' segnata come
tale: il metodo che carica il file logga SEMPRE la risposta grezza, cosi' il
primo tentativo reale ci insegna la forma giusta invece di lasciarci a
indovinare. Tutto il resto e' documentato e verificato.

Cosa e' stato verificato sul negozio vero (2026-08-20): la chiamata della firma
**funziona**, quindi il permesso c'e'. Il caricamento e la dichiarazione non
sono provabili senza un ordine aperto e una fattura vera.

Nota utile: a differenza di ManoMano, Temu accetta anche le NOTE DI CREDITO
(`invoiceDirection` = 2).
"""
import json
import logging

_logger = logging.getLogger(__name__)

API_SIGNATURE = "temu.pay.tax.get.galerie.signature"
API_UPLOAD = "temu.pay.tax.merchant.upload.invoice"

# Percorso citato dalla guida resi per il caricamento dei file. IPOTESI.
GALERIE_PATH = "/api/galerie/general_file"

DIREZIONE_FATTURA = 1
DIREZIONE_NOTA_CREDITO = 2
DESTINATARIO_CONSUMATORE = 1
DESTINATARIO_PIATTAFORMA = 2


class TemuInvoiceMixin(object):
    """Invio fatture e note di credito a Temu."""

    def push_invoice(self, invoice):
        """Manda a Temu la fattura (o nota di credito) di un ordine. Idempotente.

        `invoice` e' un `account.move` gia' confermato. Ritorna True/False e non
        solleva eccezioni: la contabilita' non deve MAI fermarsi per un problema
        di marketplace.
        """
        channel = self.channel
        if invoice.temu_document_sent:
            self._log("push_invoice", "skip",
                      "Fattura %s gia' inviata a Temu." % invoice.name,
                      external_id=invoice.name)
            return True

        order_map = self._temu_order_map_of(invoice)
        if not order_map:
            self._log("push_invoice", "error",
                      "La fattura %s non e' collegata a nessun ordine Temu."
                      % invoice.name, external_id=invoice.name)
            return False

        direzione = (DIREZIONE_NOTA_CREDITO
                     if invoice.move_type == "out_refund"
                     else DIREZIONE_FATTURA)

        pdf, nome_file, errore = self._temu_pdf_fattura(invoice)
        if errore:
            self._log("push_invoice", "error", errore, external_id=invoice.name)
            return False

        if channel.temu_simulate:
            self._log("push_invoice", "skip",
                      "SIMULAZIONE: fattura %s pronta per l'ordine %s "
                      "(%s, %s byte) ma NON inviata."
                      % (invoice.name, order_map.external_id,
                         "nota di credito" if direzione == DIREZIONE_NOTA_CREDITO
                         else "fattura", len(pdf)),
                      external_id=order_map.external_id)
            return True

        # 1) Firma per il caricamento.
        firma = self.client.call(API_SIGNATURE, {})
        if not firma.ok:
            self._log("push_invoice", "error",
                      "Firma per il caricamento non ottenuta: %s %s"
                      % (firma.error_code or "", firma.error_msg or ""),
                      external_id=order_map.external_id)
            return False
        valore_firma = (firma.data or {}).get("signature")

        # 2) Caricamento del file (parte NON documentata).
        indirizzo, errore = self._temu_carica_file(pdf, nome_file, valore_firma)
        if errore:
            self._log("push_invoice", "error", errore,
                      external_id=order_map.external_id)
            return False

        # 3) Dichiarazione della fattura.
        esito = self.client.call(API_UPLOAD, {
            "parentOrderSn": order_map.external_id,
            "invoiceDirection": direzione,
            "invoiceName": nome_file,
            "recipientType": DESTINATARIO_CONSUMATORE,
            "fileUrl": indirizzo,
        })
        # ⚠️ PRIMA di dire «rifiutata», si guarda se il verdetto e' CERTO.
        # Un 502 o una rete caduta vogliono dire che la dichiarazione PUO'
        # essere arrivata lo stesso. Chiamarla «rifiutata» — un rifiuto
        # CERTO, che e' falso — invita a ripremere il bottone, e su Temu
        # finirebbe un SECONDO documento fiscale sullo stesso ordine: l'help
        # del campo lo dice da solo, un doppione su Temu non si cancella da
        # qui. E' la stessa cura gia' applicata a spedizioni, prezzi e
        # giacenze; qui mancava, ed e' il posto dove costa di piu'.
        incerto = self._causa_incerta(esito)
        if incerto:
            self._log("push_invoice", "error",
                      "Esito IGNOTO sull'invio della fattura %s: %s.\n\n"
                      "⚠️ Non si sa se Temu l'abbia presa. Il documento NON "
                      "e' segnato come inviato, quindi il bottone si lascia "
                      "ripremere: ma se la dichiarazione era gia' passata, un "
                      "secondo invio metterebbe su Temu un SECONDO documento "
                      "fiscale sullo stesso ordine, e da qui non si cancella. "
                      "Va guardato sul Seller Center se l'ordine %s ha gia' "
                      "la sua fattura, e solo allora si decide."
                      % (invoice.name, incerto, order_map.external_id),
                      payload=json.dumps(esito.raw, ensure_ascii=False)[:8000],
                      external_id=order_map.external_id)
            return False

        if not esito.ok:
            self._log("push_invoice", "error",
                      "Temu ha rifiutato la fattura %s: %s %s"
                      % (invoice.name, esito.error_code or "",
                         esito.error_msg or ""),
                      payload=json.dumps(esito.raw, ensure_ascii=False)[:8000],
                      external_id=order_map.external_id)
            return False

        # ⚠️ IL PUNTO DI NON RITORNO, ed e' quello che costa di piu' del
        # modulo: Temu HA GIA' preso il documento fiscale. La scrittura del
        # marchio passa quindi da un savepoint, e il valore reso si legge.
        # Nuda, questa `write` faceva due danni insieme. Primo: un errore del
        # database uscirebbe da `push_invoice`, che nella sua docstring
        # promette il contrario con una ragione buona — la contabilita' non si
        # ferma per un problema di marketplace — e in un bottone su piu'
        # fatture ucciderebbe anche tutte quelle dopo. Secondo, ed e' il peggio:
        # `temu_document_sent` resterebbe falso mentre su Temu la fattura c'e',
        # quindi il bottone si lascia ripremere e ci finisce un SECONDO
        # documento fiscale sullo stesso ordine — che da qui non si cancella.
        # E' la stessa cura del marchio `shipment_pushed` sulle spedizioni.
        segnata = self._al_riparo(invoice.sudo().write,
                                  {"temu_document_sent": True,
                                   "temu_document_url": indirizzo})
        messaggio = ("%s %s inviata a Temu per l'ordine %s."
                     % ("Nota di credito"
                        if direzione == DIREZIONE_NOTA_CREDITO else "Fattura",
                        invoice.name, order_map.external_id))
        if not segnata:
            # ⚠️ Chi legge non deve capire «non e' partita»: deve capire che
            # E' partita e che Odoo non lo sa. La conseguenza e' l'opposta di
            # quella di un invio fallito — li' si riprova, qui riprovare
            # duplica un documento fiscale.
            messaggio += (
                "\n\n⚠️ ATTENZIONE: il documento E' su Temu, ma in Odoo NON "
                "e' stato segnato come inviato: la scrittura e' stata "
                "annullata dal database. Il bottone quindi si lascia "
                "ripremere, e ripremerlo metterebbe su Temu un SECONDO "
                "documento fiscale sullo stesso ordine, che da qui non si "
                "cancella. Va guardato sul Seller Center che l'ordine %s ha "
                "gia' la sua fattura, e il marchio va rimesso a mano prima di "
                "toccare altro." % order_map.external_id)
        self._log("push_invoice", "error" if not segnata else "success",
                  messaggio, external_id=order_map.external_id)
        return bool(segnata)

    # ------------------------------------------------------------------
    def _temu_carica_file(self, contenuto, nome_file, firma):
        """Carica il file e ritorna (indirizzo, errore).

        ⚠️ PARTE NON DOCUMENTATA. La forma della richiesta e' dedotta dalla
        guida resi, che cita `/api/galerie/general_file`. Per questo si logga
        SEMPRE la risposta grezza: al primo tentativo reale sapremo la forma
        giusta invece di continuare a indovinare — e' lo stesso metodo che ci ha
        fatto trovare i tre difetti del catalogo.
        """
        import requests  # locale: serve solo qui, e non e' un multipart JSON
        base = (self.channel.base_url or "https://openapi-b-eu.temu.com").rstrip("/")
        url = base + GALERIE_PATH
        try:
            risposta = requests.post(
                url,
                files={"file": (nome_file, contenuto, "application/pdf")},
                data={"signature": firma},
                timeout=60,
            )
            grezzo = risposta.text[:4000]
            self._log("push_invoice", "skip",
                      "Risposta grezza del caricamento file (HTTP %s) — serve a "
                      "capire la forma giusta, non e' un errore."
                      % risposta.status_code,
                      payload=grezzo)
            if risposta.status_code >= 400:
                return None, ("Caricamento del file rifiutato (HTTP %s). Il "
                              "corpo della risposta e' nel Log operazioni."
                              % risposta.status_code)
            dati = risposta.json()
        except Exception as exc:  # noqa: BLE001
            return None, ("Caricamento del file non riuscito: %s. Questa e' la "
                          "parte non documentata dell'invio fatture." % exc)

        for chiave in ("fileUrl", "url", "downloadUrl"):
            valore = (dati.get("result") or dati).get(chiave)
            if valore:
                return valore, None
        return None, ("Il caricamento e' andato a buon fine ma non si trova "
                      "l'indirizzo del file nella risposta: aggiungere il nome "
                      "giusto accanto a 'fileUrl' nel connettore.")

    def _temu_pdf_fattura(self, invoice):
        """PDF della fattura come byte. Ritorna (contenuto, nome, errore).

        ⚠️ IL SAVEPOINT NON E' ZELO. La stampa di una fattura non e' una sola
        lettura: il report `account.account_invoices` e' un report ARCHIVIATO,
        cioe' Odoo ci crea sopra un `ir.attachment`. Se quella scrittura
        rompesse, l'`except` qui sotto se la inghiottirebbe e renderebbe un
        errore garbato — ma la transazione resterebbe ABORTITA, e la riga di
        registro che il chiamante scrive subito dopo esploderebbe verso l'alto
        da un metodo che promette di non sollevare. Il savepoint la rimette in
        piedi; catturare in Python, da solo, non la salva.
        """
        try:
            with self.env.cr.savepoint():
                report = self.env.ref("account.account_invoices")
                contenuto, _tipo = report.sudo()._render_qweb_pdf(
                    report.report_name, res_ids=invoice.ids)
        except Exception as exc:  # noqa: BLE001
            return None, None, ("Non si riesce a generare il PDF della fattura "
                                "%s: %s" % (invoice.name, exc))
        nome = "%s.pdf" % (invoice.name or "fattura").replace("/", "-")
        return contenuto, nome, None

    def _temu_order_map_of(self, invoice):
        """L'ordine Temu da cui nasce la fattura, o False."""
        OrderMap = self.env["centrivo.order.map"]
        ordini = invoice.line_ids.sale_line_ids.order_id
        if not ordini:
            return False
        return OrderMap.search([
            ("channel_id", "=", self.channel.id),
            ("sale_order_id", "in", ordini.ids),
        ], limit=1)
