from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    """Asigna folio consecutivo a las líneas de las órdenes existentes
    que ya operan bajo el esquema de programación por línea."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    orders = env['sale.order'].search([('use_line_delivery_schedule', '=', True)])
    orders._assign_delivery_folio_numbers()
