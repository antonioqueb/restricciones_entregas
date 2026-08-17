import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Rellena la fecha de entrega por línea a partir de la fecha global.

    Reglas de seguridad:
    - Solo escribe en sale_order_line.line_commitment_date; NUNCA toca
      sale_order.commitment_date.
    - Solo rellena líneas vacías (line_commitment_date IS NULL); nunca
      sobrescribe fechas de línea existentes.
    - Corre por SQL para no disparar defaults, constraints ni la
      sincronización línea→orden durante la actualización.
    - Odoo la ejecuta una sola vez (transición a la versión 18.0.4.0).
    """
    cr.execute(
        """
        UPDATE sale_order_line AS sol
        SET line_commitment_date = so.commitment_date
        FROM sale_order AS so
        WHERE so.id = sol.order_id
          AND sol.display_type IS NULL
          AND sol.line_commitment_date IS NULL
          AND so.commitment_date IS NOT NULL
        RETURNING sol.id
        """
    )
    updated_ids = [row[0] for row in cr.fetchall()]
    _logger.info(
        "restricciones_entregas 18.0.4.0: line_commitment_date rellenada en "
        "%s líneas desde sale_order.commitment_date (solo líneas vacías).",
        len(updated_ids),
    )

    if updated_ids:
        env = api.Environment(cr, SUPERUSER_ID, {})
        lines = env['sale.order.line'].browse(updated_ids)
        lines.invalidate_recordset(['line_commitment_date'])
        lines.modified(['line_commitment_date'])
        env.flush_all()
