from odoo import models, fields, api
from datetime import timedelta, date
from odoo.exceptions import UserError

DELIVERY_LINE_CUTOFF = '2026-04-16 00:00:00'


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    line_commitment_date = fields.Datetime(
        string='Fecha de Entrega Línea',
        default=lambda self: self._default_line_commitment_date(),
        help="Fecha comprometida específica para esta línea."
    )

    client_order_ref = fields.Char(
        string='OC Cliente',
        related='order_id.client_order_ref',
        store=True,
        readonly=True,
    )

    report_commitment_date = fields.Datetime(
        string='Fecha Entrega Reporte',
        compute='_compute_delivery_report_fields',
        store=True,
        readonly=True,
        help='Fecha efectiva para el reporte de entregas. Usa la fecha de línea para pedidos nuevos y la global para históricos.'
    )

    show_in_delivery_report = fields.Boolean(
        string='Mostrar en Reporte',
        compute='_compute_delivery_report_fields',
        store=True,
        readonly=True,
    )

    qty_to_deliver_report = fields.Float(
        string='Pendiente',
        compute='_compute_delivery_report_fields',
        store=True,
        readonly=True,
    )

    delivery_days_remaining = fields.Integer(
        string='Entrega en',
        compute='_compute_delivery_report_fields',
        store=True,
        readonly=True,
    )

    delivery_line_status = fields.Selection(
        [
            ('Vencida', 'Vencida'),
            ('Próxima', 'Próxima'),
            ('Pendiente', 'Pendiente'),
            ('Entregada', 'Entregada'),
        ],
        string='Estatus Entrega',
        compute='_compute_delivery_report_fields',
        store=True,
        readonly=True,
    )

    @api.model
    def _delivery_line_cutoff_dt(self):
        return fields.Datetime.from_string(DELIVERY_LINE_CUTOFF)

    def _is_new_delivery_logic_order(self):
        self.ensure_one()
        order_date = self.order_id.date_order or fields.Datetime.now()
        return order_date >= self._delivery_line_cutoff_dt()

    @api.model
    def _default_line_commitment_date(self):
        order_id = self.env.context.get('default_order_id')
        if order_id:
            order = self.env['sale.order'].browse(order_id)
            if order and order.date_order and order.date_order >= fields.Datetime.from_string(DELIVERY_LINE_CUTOFF):
                return order.commitment_date or fields.Datetime.to_string(order.date_order + timedelta(days=15))
            return order.commitment_date or False

        date_order = self.env.context.get('default_date_order', fields.Datetime.now())
        return fields.Datetime.to_string(
            fields.Datetime.from_string(date_order) + timedelta(days=15)
        )

    @api.depends(
        'product_uom_qty',
        'qty_delivered',
        'line_commitment_date',
        'order_id.commitment_date',
        'order_id.date_order',
        'display_type',
    )
    def _compute_delivery_report_fields(self):
        today = date.today()
        cutoff = self._delivery_line_cutoff_dt()

        for line in self:
            pending = max((line.product_uom_qty or 0.0) - (line.qty_delivered or 0.0), 0.0)
            line.qty_to_deliver_report = pending

            order_date = line.order_id.date_order or fields.Datetime.now()
            is_new_logic = order_date >= cutoff

            if line.display_type:
                line.report_commitment_date = False
                line.show_in_delivery_report = False
                line.delivery_days_remaining = 0
                line.delivery_line_status = False
                continue

            if is_new_logic:
                effective_date = line.line_commitment_date or False
            else:
                effective_date = line.order_id.commitment_date or False

            line.report_commitment_date = effective_date
            line.show_in_delivery_report = bool(effective_date)

            if effective_date:
                days_remaining = (effective_date.date() - today).days
                line.delivery_days_remaining = max(days_remaining, 0)
            else:
                line.delivery_days_remaining = 0

            if not effective_date:
                line.delivery_line_status = False
            elif pending <= 0:
                line.delivery_line_status = 'Entregada'
            elif effective_date.date() < today:
                line.delivery_line_status = 'Vencida'
            elif 0 <= (effective_date.date() - today).days <= 2:
                line.delivery_line_status = 'Próxima'
            else:
                line.delivery_line_status = 'Pendiente'

    def _minimum_allowed_line_commitment_date(self):
        self.ensure_one()
        base_dt = self.order_id.date_order or fields.Datetime.now()
        return base_dt + timedelta(days=15)

    @api.constrains('line_commitment_date', 'order_id')
    def _check_line_commitment_date(self):
        for line in self:
            if not line.line_commitment_date or not line.order_id or not line.order_id.date_order:
                continue

            if not line._is_new_delivery_logic_order():
                continue

            minimum_date = line._minimum_allowed_line_commitment_date()
            if line.line_commitment_date < minimum_date:
                raise UserError(
                    f"La fecha de entrega de la línea ({line.product_id.display_name or line.name}) "
                    f"no puede ser menor a 15 días posteriores a la fecha del pedido. "
                    f"Mínimo permitido: {fields.Datetime.to_string(minimum_date)}"
                )

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)

        for line, vals in zip(lines, vals_list):
            if line.display_type or not line.order_id:
                continue

            if not line._is_new_delivery_logic_order():
                continue

            if not vals.get('line_commitment_date') and line.order_id.commitment_date:
                line.with_context(skip_order_commitment_sync=True).write({
                    'line_commitment_date': line.order_id.commitment_date
                })

        if not self.env.context.get('skip_order_commitment_sync'):
            lines.mapped('order_id')._sync_commitment_date_from_lines()

        for line in lines.filtered(lambda l: not l.display_type and l.order_id and l.line_commitment_date):
            line.order_id.message_post(
                body=(
                    f"Se programó fecha de entrega para la línea "
                    f"({line.product_id.display_name or line.name}): {line.line_commitment_date}"
                ),
                message_type='comment',
                subtype_xmlid='mail.mt_note'
            )

        return lines

    def write(self, vals):
        old_dates = {}
        if 'line_commitment_date' in vals:
            for line in self:
                old_dates[line.id] = line.line_commitment_date

        res = super().write(vals)

        if 'line_commitment_date' in vals:
            for line in self.filtered(lambda l: not l.display_type and l.order_id and l.line_commitment_date):
                old_value = old_dates.get(line.id)
                new_value = line.line_commitment_date
                if old_value != new_value:
                    line.order_id.message_post(
                        body=(
                            f"Cambio en fecha de entrega de línea "
                            f"({line.product_id.display_name or line.name}) "
                            f"- Antes: {old_value or 'N/A'} - Ahora: {new_value or 'N/A'}"
                        ),
                        message_type='comment',
                        subtype_xmlid='mail.mt_note'
                    )

        if not self.env.context.get('skip_order_commitment_sync'):
            self.mapped('order_id')._sync_commitment_date_from_lines()

        return res