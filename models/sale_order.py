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
        """Devuelve la fecha de entrega prometida por defecto a 15 días."""
        date_order = self.env.context.get('default_date_order', fields.Datetime.now())
        return fields.Datetime.to_string(
            fields.Datetime.from_string(date_order) + timedelta(days=15)
        )

    def _compute_can_edit_commitment_date(self):
        """Determina si el usuario puede editar la fecha de entrega según el estado y su grupo."""
        for order in self:
            if order.state in ['draft', 'sent']:
                # *** SOLO si tiene grupo de editar en cotización (o es usuario de sistema) ***
                order.can_edit_commitment_date = (
                    self.env.user.has_group('restricciones_entregas.group_edit_commitment_date')
                    or self.env.user.has_group('base.group_system')
                )
            else:
                # *** SOLO si tiene grupo de editar órdenes confirmadas (o es usuario de sistema) ***
                order.can_edit_commitment_date = (
                    self.env.user.has_group('restricciones_entregas.group_edit_commitment_date_confirmed')
                    or self.env.user.has_group('base.group_system')
                )

    @api.model
    def create(self, vals):
        """Valida la fecha de entrega al crear un pedido."""
        if 'commitment_date' in vals and 'date_order' in vals:
            commitment_date = fields.Datetime.from_string(vals['commitment_date'])
            date_order = fields.Datetime.from_string(vals['date_order'])
            if commitment_date < date_order:
                raise UserError(
                    "La fecha de entrega prometida no puede ser anterior a la fecha del pedido."
                )
        return super(SaleOrder, self).create(vals)

    def write(self, vals):
        """Valida permisos para modificar la fecha de entrega y registra cambios en el chatter."""
        # 1) Guardar la fecha de entrega antes de modificarla
        old_dates = {}
        if 'commitment_date' in vals:
            for order in self:
                old_dates[order.id] = order.commitment_date

        # 2) Validar permisos y restricciones, tal cual tu lógica original.
        if 'commitment_date' in vals:
            for order in self:
                if order.state in ['draft', 'sent']:
                    # ** Editar fecha en estado de Cotización **
                    if not (self.env.user.has_group('restricciones_entregas.group_edit_commitment_date')
                            or self.env.user.has_group('base.group_system')):
                        raise UserError(
                            "No tienes permisos para modificar la fecha de entrega en cotizaciones."
                        )
                else:
                    # ** Editar fecha en órdenes confirmadas **
                    if not (self.env.user.has_group('restricciones_entregas.group_edit_commitment_date_confirmed')
                            or self.env.user.has_group('base.group_system')):
                        raise UserError(
                            "No tienes permisos para modificar la fecha de entrega en órdenes confirmadas."
                        )

                # Validar que la fecha de entrega no sea anterior a la fecha del pedido
                date_order = order.date_order
                new_commitment_date = fields.Datetime.from_string(vals['commitment_date'])
                if new_commitment_date < date_order:
                    raise UserError(
                        f"La fecha de entrega para el pedido {order.name} no puede "
                        "ser anterior a la fecha del pedido."
                    )

        # 3) Llamamos al super para que se apliquen los cambios
        res = super(SaleOrder, self).write(vals)

        # 4) Registrar el cambio en el chatter si la fecha se modificó realmente
        if 'commitment_date' in vals:
            for order in self:
                old_date = old_dates.get(order.id)
                new_date = order.commitment_date
                if old_date != new_date:
                    # Pon el formato que gustes para el mensaje
                    old_str = old_date and old_date.strftime('%d/%m/%Y %H:%M:%S') or 'N/A'
                    new_str = new_date and new_date.strftime('%d/%m/%Y %H:%M:%S') or 'N/A'
                    user_name = self.env.user.display_name
                    
                    message = (
                        "<b>Cambio en la Fecha de Entrega</b><br/>"
                        f"<b>Pedido:</b> {order.name}<br/>"
                        f"<b>Usuario:</b> {user_name}<br/>"
                        f"<b>Antes:</b> {old_str}<br/>"
                        f"<b>Ahora:</b> {new_str}"
                    )
                    order.message_post(body=message)

        return res
