# -*- coding: utf-8 -*-
"""Relación en Excel para Administración.

Usa xlsxwriter, que forma parte de las dependencias estándar de Odoo (es la
librería con la que el cliente web exporta a .xlsx); no se agrega ninguna
dependencia externa nueva.
"""
import base64
import io

from odoo import fields, models, _
from odoo.exceptions import UserError

from ..models.delivery_evidence import DELIVERY_STATES, DOC_STATES


class DeliveryEvidenceReportWizard(models.TransientModel):
    _name = 'delivery.evidence.report.wizard'
    _description = 'Relación de entregas y evidencias para Administración'

    date_from = fields.Date('Fecha inicial')
    date_to = fields.Date('Fecha final')
    company_id = fields.Many2one(
        'res.company', 'Compañía', default=lambda self: self.env.company)
    partner_ids = fields.Many2many('res.partner', string='Clientes')
    delivery_state = fields.Selection(DELIVERY_STATES, 'Estado de entrega')
    doc_state = fields.Selection(DOC_STATES, 'Estado documental')
    only_ready = fields.Boolean('Solo expedientes completos')
    only_not_sent = fields.Boolean('Solo no enviados', default=True)
    control_ids = fields.Many2many(
        'delivery.evidence.control', string='Controles seleccionados')
    state = fields.Selection(
        [('choose', 'choose'), ('done', 'done')], default='choose')
    file = fields.Binary('Relación', readonly=True)
    file_name = fields.Char('Archivo')
    included_ids = fields.Many2many(
        'delivery.evidence.control', 'dec_report_included_rel',
        string='Incluidos en la relación', readonly=True)

    def _find_controls(self):
        self.ensure_one()
        if self.control_ids:
            return self.control_ids
        domain = []
        if self.company_id:
            domain.append(('company_id', '=', self.company_id.id))
        if self.date_from:
            domain.append(('invoice_date', '>=', self.date_from))
        if self.date_to:
            domain.append(('invoice_date', '<=', self.date_to))
        if self.partner_ids:
            domain.append(('partner_id', 'in', self.partner_ids.ids))
        if self.delivery_state:
            domain.append(('delivery_state', '=', self.delivery_state))
        if self.doc_state:
            domain.append(('doc_state', '=', self.doc_state))
        if self.only_ready:
            domain.append(('doc_state', 'in', ['ready', 'sent']))
        if self.only_not_sent:
            domain.append(('doc_state', '!=', 'sent'))
        return self.env['delivery.evidence.control'].search(
            domain, order='invoice_date, name')

    def action_generate(self):
        self.ensure_one()
        try:
            import xlsxwriter
        except ImportError:
            raise UserError(_('El servidor no tiene disponible la librería xlsxwriter.'))

        controls = self._find_controls()
        if not controls:
            raise UserError(_('No hay controles que coincidan con los filtros.'))

        delivery_labels = dict(DELIVERY_STATES)
        doc_labels = dict(DOC_STATES)

        buffer = io.BytesIO()
        book = xlsxwriter.Workbook(buffer, {'in_memory': True})
        sheet = book.add_worksheet('Relación')

        title_fmt = book.add_format({'bold': True, 'font_size': 14, 'font_color': '#16394C'})
        sub_fmt = book.add_format({'font_color': '#6A7D8A'})
        head_fmt = book.add_format({
            'bold': True, 'bg_color': '#16394C', 'font_color': '#FFFFFF',
            'border': 1, 'text_wrap': True, 'valign': 'vcenter'})
        date_fmt = book.add_format({'num_format': 'dd/mm/yyyy', 'border': 1})
        money_fmt = book.add_format({'num_format': '#,##0.00', 'border': 1})
        qty_fmt = book.add_format({'num_format': '#,##0.00', 'border': 1})
        pct_fmt = book.add_format({'num_format': '0.0"%"', 'border': 1})
        cell_fmt = book.add_format({'border': 1})
        total_fmt = book.add_format({
            'bold': True, 'num_format': '#,##0.00', 'top': 2, 'bg_color': '#F2F6F9'})
        total_lbl_fmt = book.add_format({'bold': True, 'top': 2, 'bg_color': '#F2F6F9'})

        company = self.company_id or self.env.company
        period = _('Periodo: %(f)s a %(t)s') % {
            'f': self.date_from and self.date_from.strftime('%d/%m/%Y') or '—',
            't': self.date_to and self.date_to.strftime('%d/%m/%Y') or '—',
        }
        sheet.write(0, 0, _('Relación de Entregas y Evidencias — %s') % company.name, title_fmt)
        sheet.write(1, 0, period, sub_fmt)
        sheet.write(2, 0, _('Generado el %(d)s por %(u)s') % {
            'd': fields.Datetime.context_timestamp(
                self, fields.Datetime.now()).strftime('%d/%m/%Y %H:%M'),
            'u': self.env.user.name,
        }, sub_fmt)

        headers = [
            (_('Fecha'), 11), (_('Serie y Folio'), 18), (_('Código cliente'), 13),
            (_('Razón social'), 32), (_('OC del cliente'), 16), (_('Órdenes de venta'), 18),
            (_('Folios de producción'), 24), (_('Moneda'), 8),
            (_('Venta (moneda original)'), 16), (_('Venta (moneda compañía)'), 16),
            (_('Cant. facturada'), 12), (_('Cant. entregada'), 12), (_('Cant. pendiente'), 12),
            (_('% entregado'), 10), (_('Estado entrega'), 14), (_('Estado evidencia'), 18),
            (_('Fecha evidencia'), 12), (_('Envío a Administración'), 14), (_('Observaciones'), 30),
        ]
        header_row = 4
        for col, (label, width) in enumerate(headers):
            sheet.write(header_row, col, label, head_fmt)
            sheet.set_column(col, col, width)
        sheet.autofilter(header_row, 0, header_row + len(controls), len(headers) - 1)
        sheet.freeze_panes(header_row + 1, 0)

        row = header_row
        totals = {'orig': 0.0, 'comp': 0.0, 'inv': 0.0, 'dlv': 0.0, 'pen': 0.0}
        for control in controls:
            row += 1
            sheet.write(row, 0, control.invoice_date or '', date_fmt)
            sheet.write(row, 1, control.name or '', cell_fmt)
            sheet.write(row, 2, control.partner_code or '', cell_fmt)
            sheet.write(row, 3, control.partner_id.name or '', cell_fmt)
            sheet.write(row, 4, control.client_order_ref or '', cell_fmt)
            sheet.write(row, 5, ', '.join(control.sale_order_ids.mapped('name')), cell_fmt)
            sheet.write(row, 6, control.production_folios or '', cell_fmt)
            sheet.write(row, 7, control.currency_id.name or '', cell_fmt)
            sheet.write(row, 8, control.amount_total, money_fmt)
            sheet.write(row, 9, control.amount_total_signed, money_fmt)
            sheet.write(row, 10, control.qty_invoiced, qty_fmt)
            sheet.write(row, 11, control.qty_delivered, qty_fmt)
            sheet.write(row, 12, control.qty_pending, qty_fmt)
            sheet.write(row, 13, control.delivered_pct, pct_fmt)
            sheet.write(row, 14, delivery_labels.get(control.delivery_state, ''), cell_fmt)
            sheet.write(row, 15, doc_labels.get(control.doc_state, ''), cell_fmt)
            sheet.write(row, 16, control.evidence_received_date or '', date_fmt)
            sheet.write(row, 17, control.sent_date and fields.Datetime.context_timestamp(
                self, control.sent_date).strftime('%d/%m/%Y') or '', cell_fmt)
            sheet.write(row, 18, control.notes or '', cell_fmt)
            totals['orig'] += control.amount_total
            totals['comp'] += control.amount_total_signed
            totals['inv'] += control.qty_invoiced
            totals['dlv'] += control.qty_delivered
            totals['pen'] += control.qty_pending

        row += 1
        sheet.write(row, 3, _('TOTALES (%s registros)') % len(controls), total_lbl_fmt)
        for col in (0, 1, 2, 4, 5, 6, 7, 13, 14, 15, 16, 17, 18):
            sheet.write(row, col, '', total_lbl_fmt)
        sheet.write(row, 8, totals['orig'], total_fmt)
        sheet.write(row, 9, totals['comp'], total_fmt)
        sheet.write(row, 10, totals['inv'], total_fmt)
        sheet.write(row, 11, totals['dlv'], total_fmt)
        sheet.write(row, 12, totals['pen'], total_fmt)

        book.close()
        file_name = 'relacion_entregas_evidencias_%s.xlsx' % fields.Date.context_today(
            self).strftime('%Y%m%d')
        self.write({
            'state': 'done',
            'file': base64.b64encode(buffer.getvalue()),
            'file_name': file_name,
            'included_ids': [(6, 0, controls.ids)],
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_mark_included_sent(self):
        """Marca como enviados los controles incluidos, solo bajo confirmación
        explícita del usuario responsable (el botón lleva confirm en la vista)."""
        self.ensure_one()
        ready = self.included_ids.filtered(lambda c: c.doc_state == 'ready')
        if not ready:
            raise UserError(_('Ninguno de los controles incluidos está en estado '
                              '"Completo para Administración".'))
        ready.action_mark_sent()
        return {'type': 'ir.actions.act_window_close'}
