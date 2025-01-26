from odoo import models, fields, api
from datetime import timedelta
from odoo.exceptions import UserError

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    commitment_date = fields.Datetime(
        string='Fecha de Entrega Prometida',
        default=lambda self: self._default_commitment_date(),
        help="Fecha de entrega prometida por defecto a 15 días desde la fecha del pedido."
    )

    can_edit_commitment_date = fields.Boolean(
        string="Puede Editar Fecha de Entrega",
        compute='_compute_can_edit_commitment_date'
    )

    @api.model
    def _default_commitment_date(self):
        """Devuelve la fecha de entrega prometida por defecto a 15 días desde la fecha del pedido."""
        date_order = self.env.context.get('default_date_order', fields.Datetime.now())
        return fields.Datetime.to_string(fields.Datetime.from_string(date_order) + timedelta(days=15))

    def _compute_can_edit_commitment_date(self):
        """Calcula si el usuario actual tiene permisos para editar la fecha de entrega."""
        for order in self:
            order.can_edit_commitment_date = self.env.user.has_group('restricciones_entregas.group_edit_commitment_date') or self.env.user.has_group('base.group_system')

    @api.model
    def create(self, vals):
        """Valida la fecha de entrega al crear un pedido."""
        if 'commitment_date' in vals and 'date_order' in vals:
            commitment_date = fields.Datetime.from_string(vals['commitment_date'])
            date_order = fields.Datetime.from_string(vals['date_order'])
            if commitment_date < date_order:
                raise UserError("La fecha de entrega prometida no puede ser anterior a la fecha del pedido.")
        return super(SaleOrder, self).create(vals)

    def write(self, vals):
        """Valida permisos y la fecha de entrega al modificar un pedido."""
        if 'commitment_date' in vals:
            allowed_groups = [
                'base.group_system',  # Administradores del sistema
                'restricciones_entregas.group_edit_commitment_date',  # Nuevo grupo
            ]
            if not any(self.env.user.has_group(group) for group in allowed_groups):
                raise UserError("No tienes permisos para modificar la fecha de entrega prometida.")

            for order in self:
                date_order = order.date_order
                commitment_date = fields.Datetime.from_string(vals['commitment_date'])
                if commitment_date < date_order:
                    raise UserError(f"La fecha de entrega prometida para el pedido {order.name} no puede ser anterior a la fecha del pedido.")

        return super(SaleOrder, self).write(vals)