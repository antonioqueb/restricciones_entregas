from odoo import models, fields, api
from datetime import timedelta
from odoo.exceptions import UserError

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    commitment_date = fields.Datetime(
        string='Fecha de Entrega Prometida',
        readonly=True,
        default=lambda self: self._default_commitment_date(),
        help="Fecha de entrega prometida por defecto a 15 días desde la fecha del pedido."
    )

    @api.model
    def _default_commitment_date(self):
        """Devuelve la fecha de entrega prometida por defecto a 15 días desde la fecha del pedido."""
        date_order = self.env.context.get('default_date_order', fields.Datetime.now())
        return fields.Datetime.to_string(fields.Datetime.from_string(date_order) + timedelta(days=15))

    @api.model
    def create(self, vals):
        if 'commitment_date' in vals and 'date_order' in vals:
            commitment_date = fields.Datetime.from_string(vals['commitment_date'])
            date_order = fields.Datetime.from_string(vals['date_order'])
            if commitment_date < date_order:
                raise UserError("La fecha de entrega prometida no puede ser anterior a la fecha del pedido.")
        return super(SaleOrder, self).create(vals)

    def write(self, vals):
        if 'commitment_date' in vals and not self.env.user.has_group('base.group_system'):
            raise UserError("Solo los administradores pueden modificar la fecha de entrega prometida.")
        
        if 'commitment_date' in vals:
            for order in self:
                date_order = order.date_order
                commitment_date = fields.Datetime.from_string(vals['commitment_date'])
                if commitment_date < date_order:
                    raise UserError(f"La fecha de entrega prometida para el pedido {order.name} no puede ser anterior a la fecha del pedido.")
        
        return super(SaleOrder, self).write(vals)