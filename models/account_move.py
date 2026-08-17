# -*- coding: utf-8 -*-
"""Creación automática del control documental al publicar facturas de cliente.

No invasivo: se ejecuta después del super() y cualquier falla se registra en
el log sin afectar la publicación contable.
"""
import logging

from odoo import models

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        try:
            invoices = posted.filtered(lambda m: m.move_type == 'out_invoice')
            if invoices:
                self.env['delivery.evidence.control'].sudo()._sync_from_moves(invoices)
        except Exception:
            _logger.exception(
                'Control de Entregas y Evidencias: no se pudo crear el control '
                'automático al publicar; la factura se publicó normalmente.')
        return posted
