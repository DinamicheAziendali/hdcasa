# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
# Connettore Cdiscount/Octopia (Famiglia A — sotto integrations_core).
# CONSEGNA 1: fondamenta e SCHEDE PRODOTTO. Le offerte sono la Consegna 2,
# gli ordini la Consegna 3.
{
    "name": "Marketplace - Cdiscount",
    "version": "18.0.1.1.2",
    "license": "OPL-1",
    "category": "Connector",
    "summary": "Connettore Cdiscount (Octopia): invio delle schede prodotto "
               "a pacchetti asincroni e raccolta degli esiti prima che "
               "scadano.",
    "author": "Angelo Margarella",
    "website": "https://www.hdcasa.it",
    # ⚠️ `mail` e' NOMINATO anche se `integrations_core` lo porta gia': il
    # pacchetto (`cdiscount.pacchetto`) eredita `mail.thread` e
    # `mail.activity.mixin` per l'avviso di scadenza, e una dipendenza
    # transitiva sparisce il giorno in cui il modulo di mezzo smette di averne
    # bisogno. A sparire, li', sarebbe l'unico avviso che questa consegna
    # produce — e l'installazione fallirebbe con un errore che non nomina
    # nessuna di queste due righe.
    "depends": ["integrations_core", "account", "mail"],
    # ⚠️ L'ordine, e QUALE riga e' obbligata e quale no. La distinzione
    # conta: chi crede obbligatorio cio' che non lo e' non si fida piu' di
    # niente il giorno in cui scopre che una delle due regole si puo'
    # violare impunemente.
    #
    # 1. i PERMESSI per primi. ⚠️ NON e' un vincolo tecnico: una vista che
    #    nomina un modello senza `ir.model.access` si carica lo stesso, e
    #    Odoo si limita a scrivere un warning nel registro
    #    («The model … has no access rules…»). E' la convenzione di questa
    #    casa e della revisione dell'App Store, e ha una ragione di lettura:
    #    «chi puo' fare cosa» e' la prima domanda su un modulo che scrive su
    #    un catalogo pubblico, e la risposta sta nella prima riga.
    #    Nello stesso gruppo, subito dopo il CSV, le REGOLE MULTI-AZIENDA:
    #    stanno dopo perche' nominano gli stessi modelli e si leggono come
    #    la seconda meta' della stessa risposta («chi puo' fare cosa» e «su
    #    quali righe»).
    # 2. i DATI (il cron) subito dopo: non dipendono da nessuna vista.
    # 3. le VISTE. ⚠️ Fra loro l'ordine e' INDIFFERENTE, e va detto: nessuna
    #    delle tre nomina l'altra, e quella del canale innesta su
    #    `integrations_core`, che e' una DIPENDENZA — le sue viste sono gia'
    #    caricate da prima che questo modulo cominci. Sta per ultima solo
    #    per lettura: e' la sola che MODIFICA una schermata di un altro
    #    modulo invece di crearne una propria. Se un giorno una di queste
    #    viste nominasse un'altra con un `ref=`, allora l'ordine diventerebbe
    #    obbligato — e allora andra' riscritto anche questo commento.
    # 4. i MENU in fondo a tutto, e QUESTO SI' e' obbligato: un `menuitem`
    #    che nomina un'azione ancora da caricare fa fallire l'installazione
    #    con un «External ID not found», e le tre azioni stanno nei tre file
    #    di vista qui sopra.
    "data": [
        "security/ir.model.access.csv",
        "security/cdiscount_security.xml",
        "data/ir_cron.xml",
        "views/cdiscount_pacchetto_views.xml",
        "views/cdiscount_scheda_views.xml",
        "views/cdiscount_contenuti_wizard_views.xml",
        "views/cdiscount_channel_views.xml",
        "views/cdiscount_menus.xml",
    ],
    "installable": True,
    "application": False,
}
