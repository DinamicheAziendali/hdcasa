# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
# ⚠️ Solo il connettore: gli altri file di questa cartella sono logica PURA e
# si importano da soli quando servono (e i test di tools/ li caricano senza
# Odoo). Importare qui `cdiscount` e' cio' che lo registra nel registro dei
# connettori — senza questa riga il codice «cdiscount» non comparirebbe nella
# tendina del canale e nessun canale potrebbe essere Cdiscount.
from . import cdiscount
