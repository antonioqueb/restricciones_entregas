from odoo import models, fields, api
from datetime import timedelta
from odoo.exceptions import UserError


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    line_commitment_date = fields.Datetime(
        string='Fecha Entrega Línea',
        default=lambda self: self._default_line_commitment_date(),
        help="Fecha comprometida para esta línea específica."
    )

    @api.model
    def _default_line_commitment_date(self):
        order_id = self.env.context.get('default_order_id')
        if order_id:
            order = self.env['sale.order'].browse(order_id)
            if order and order.commitment_date:
                return order.commitment_date

        date_order = self.env.context.get('default_date_order', fields.Datetime.now())
        return fields.Datetime.to_string(
            fields.Datetime.from_string(date_order) + timedelta(days=15)
        )

    @api.constrains('line_commitment_date', 'order_id')
    def _check_line_commitment_date(self):
        for line in self:
            if not line.line_commitment_date or not line.order_id or not line.order_id.date_order:
                continue

            if line.line_commitment_date < line.order_id.date_order:
                raise UserError(
                    f"La fecha de entrega de la línea ({line.product_id.display_name or line.name}) "
                    f"no puede ser anterior a la fecha del pedido."
                )

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)

        for line, vals in zip(lines, vals_list):
            if not line.display_type and not vals.get('line_commitment_date') and line.order_id and line.order_id.commitment_date:
                line.with_context(skip_order_commitment_sync=True).write({
                    'line_commitment_date': line.order_id.commitment_date
                })

        if not self.env.context.get('skip_order_commitment_sync'):
            lines.mapped('order_id')._sync_commitment_date_from_lines()

        for line in lines.filtered(lambda l: not l.display_type and l.order_id):
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
            for line in self.filtered(lambda l: not l.display_type and l.order_id):
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