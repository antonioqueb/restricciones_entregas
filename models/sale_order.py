from odoo import models, fields, api
from datetime import timedelta
from odoo.exceptions import UserError

DELIVERY_LINE_CUTOFF = '2026-04-16 00:00:00'


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    commitment_date = fields.Datetime(
        string='Fecha de Entrega Prometida',
        default=lambda self: self._default_commitment_date(),
        help="Representa la próxima fecha efectiva de entrega pendiente de la orden."
    )

    can_edit_commitment_date = fields.Boolean(
        string="Puede Editar Fecha de Entrega",
        compute='_compute_can_edit_commitment_date'
    )

    use_line_delivery_schedule = fields.Boolean(
        string='Usa Programación por Línea',
        compute='_compute_use_line_delivery_schedule',
        store=True,
        readonly=True,
        help='Indica si la orden pertenece al nuevo esquema de programación por línea.'
    )

    @api.model
    def _delivery_line_cutoff_dt(self):
        return fields.Datetime.from_string(DELIVERY_LINE_CUTOFF)

    @api.depends('date_order')
    def _compute_use_line_delivery_schedule(self):
        cutoff = self._delivery_line_cutoff_dt()
        for order in self:
            order_date = order.date_order or fields.Datetime.now()
            order.use_line_delivery_schedule = order_date >= cutoff

    @api.model
    def _default_commitment_date(self):
        date_order = self.env.context.get('default_date_order', fields.Datetime.now())
        return fields.Datetime.to_string(
            fields.Datetime.from_string(date_order) + timedelta(days=15)
        )

    def _compute_can_edit_commitment_date(self):
        for order in self:
            if order.state in ['draft', 'sent']:
                order.can_edit_commitment_date = (
                    self.env.user.has_group('restricciones_entregas.group_edit_commitment_date')
                    or self.env.user.has_group('base.group_system')
                )
            else:
                order.can_edit_commitment_date = (
                    self.env.user.has_group('restricciones_entregas.group_edit_commitment_date_confirmed')
                    or self.env.user.has_group('base.group_system')
                )

    def _check_commitment_date_permissions(self):
        for order in self:
            if order.state in ['draft', 'sent']:
                allowed = (
                    self.env.user.has_group('restricciones_entregas.group_edit_commitment_date')
                    or self.env.user.has_group('base.group_system')
                )
                if not allowed:
                    raise UserError("No tienes permisos para modificar la fecha de entrega en cotizaciones.")
            else:
                allowed = (
                    self.env.user.has_group('restricciones_entregas.group_edit_commitment_date_confirmed')
                    or self.env.user.has_group('base.group_system')
                )
                if not allowed:
                    raise UserError("No tienes permisos para modificar la fecha de entrega en órdenes confirmadas.")

    def _minimum_allowed_commitment_date(self):
        self.ensure_one()
        base_dt = self.date_order or fields.Datetime.now()
        return base_dt + timedelta(days=15)

    def _validate_commitment_date_minimum(self, commitment_date_value, order_name=None):
        self.ensure_one()
        if not commitment_date_value:
            return
        minimum_date = self._minimum_allowed_commitment_date()
        if commitment_date_value < minimum_date:
            label = order_name or self.name or "el pedido"
            raise UserError(
                f"La fecha de entrega para {label} no puede ser menor a 15 días posteriores "
                f"a la fecha del pedido. Mínimo permitido: {fields.Datetime.to_string(minimum_date)}"
            )

    def _get_pending_delivery_lines(self):
        self.ensure_one()
        return self.order_line.filtered(
            lambda l: not l.display_type
            and l.report_commitment_date
            and l.product_uom_qty > l.qty_delivered
            and l.show_in_delivery_report
        )

    def _get_next_pending_line_commitment_date(self):
        self.ensure_one()
        pending_lines = self._get_pending_delivery_lines()
        if not pending_lines:
            return False
        return min(pending_lines.mapped('report_commitment_date'))

    def _sync_commitment_date_from_lines(self):
        for order in self:
            next_date = order._get_next_pending_line_commitment_date()
            if next_date and order.commitment_date != next_date:
                super(SaleOrder, order.with_context(skip_commitment_line_sync=True)).write({
                    'commitment_date': next_date
                })

    def _has_multiple_pending_line_dates(self):
        self.ensure_one()
        dates = set(self._get_pending_delivery_lines().mapped('report_commitment_date'))
        return len(dates) > 1

    @api.model_create_multi
    def create(self, vals_list):
        orders = super().create(vals_list)

        for order, vals in zip(orders, vals_list):
            commitment_date = fields.Datetime.from_string(vals['commitment_date']) if vals.get('commitment_date') else order.commitment_date
            order._validate_commitment_date_minimum(commitment_date, order.name)

            if order.use_line_delivery_schedule:
                for line in order.order_line.filtered(lambda l: not l.display_type and not l.line_commitment_date):
                    line.with_context(skip_order_commitment_sync=True).write({
                        'line_commitment_date': order.commitment_date
                    })

            order._sync_commitment_date_from_lines()

        return orders

    def write(self, vals):
        old_values = {}
        track_fields = ['commitment_date', 'client_order_ref', 'warehouse_id', 'pricelist_id']
        track_line_fields = ['product_id', 'name', 'product_uom', 'product_uom_qty', 'price_unit']

        for field_name in track_fields:
            if field_name in vals:
                for order in self:
                    old_values.setdefault(order.id, {})[field_name] = getattr(order, field_name)

        if 'commitment_date' in vals and not self.env.context.get('skip_commitment_line_sync'):
            self._check_commitment_date_permissions()

            new_commitment_date = fields.Datetime.from_string(vals['commitment_date'])
            for order in self:
                order._validate_commitment_date_minimum(new_commitment_date, order.name)

                if order.use_line_delivery_schedule and order._has_multiple_pending_line_dates():
                    raise UserError(
                        "No puedes modificar la fecha global porque la orden ya tiene múltiples fechas de entrega por línea. "
                        "Debes editar las fechas directamente en las líneas."
                    )

        res = super().write(vals)

        if 'commitment_date' in vals and not self.env.context.get('skip_commitment_line_sync'):
            for order in self.filtered(lambda o: o.use_line_delivery_schedule):
                for line in order.order_line.filtered(lambda l: not l.display_type):
                    if not line.line_commitment_date:
                        line.with_context(skip_order_commitment_sync=True).write({
                            'line_commitment_date': order.commitment_date
                        })

        user_name = self.env.user.display_name
        for order in self:
            for field_name in track_fields:
                if field_name in vals:
                    old_value = old_values[order.id].get(field_name)
                    new_value = getattr(order, field_name)
                    if old_value != new_value:
                        old_str = old_value and str(old_value) or 'N/A'
                        new_str = new_value and str(new_value) or 'N/A'
                        message = (
                            f"Cambio en {field_name.replace('_', ' ').capitalize()} - Pedido: {order.name} - Usuario: {user_name} "
                            f"- Antes: {old_str} - Ahora: {new_str}"
                        )
                        order.message_post(body=message, message_type='comment', subtype_xmlid='mail.mt_note')

            for line in order.order_line:
                for field_name in track_line_fields:
                    old_value = line._origin and line._origin[field_name] or 'N/A'
                    new_value = getattr(line, field_name)
                    if old_value != new_value:
                        old_str = old_value and str(old_value) or 'N/A'
                        new_str = new_value and str(new_value) or 'N/A'
                        message = (
                            f"Cambio en {field_name.replace('_', ' ').capitalize()} en línea de pedido ({line.product_id.display_name}) - Pedido: {order.name} - Usuario: {user_name} "
                            f"- Antes: {old_str} - Ahora: {new_str}"
                        )
                        order.message_post(body=message, message_type='comment', subtype_xmlid='mail.mt_note')

        return res