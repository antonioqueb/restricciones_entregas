# -*- coding: utf-8 -*-
from odoo import fields, models, _


class DeliveryEvidenceSyncWizard(models.TransientModel):
    _name = 'delivery.evidence.sync.wizard'
    _description = 'Sincronizar facturas con el control de entregas'

    date_from = fields.Date('Fecha inicial')
    date_to = fields.Date('Fecha final')
    company_id = fields.Many2one(
        'res.company', 'Compañía', default=lambda self: self.env.company)
    partner_ids = fields.Many2many('res.partner', string='Clientes')
    state = fields.Selection(
        [('choose', 'choose'), ('done', 'done')], default='choose')
    result = fields.Text('Resultado', readonly=True)

    def action_sync(self):
        self.ensure_one()
        domain = [('move_type', '=', 'out_invoice'), ('state', '=', 'posted')]
        if self.company_id:
            domain.append(('company_id', '=', self.company_id.id))
        if self.date_from:
            domain.append(('invoice_date', '>=', self.date_from))
        if self.date_to:
            domain.append(('invoice_date', '<=', self.date_to))
        if self.partner_ids:
            domain.append(('partner_id', 'in', self.partner_ids.ids))

        moves = self.env['account.move'].search(domain)
        stats = self.env['delivery.evidence.control']._sync_from_moves(moves)
        self.write({
            'state': 'done',
            'result': _(
                'Facturas revisadas: %(total)s\n'
                'Controles creados: %(created)s\n'
                'Controles actualizados: %(updated)s\n'
                'Omitidos: %(skipped)s\n'
                'Con inconsistencias (requieren revisión): %(review)s'
            ) % dict(stats, total=len(moves)),
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
