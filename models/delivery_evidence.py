# -*- coding: utf-8 -*-
"""Control de Entregas y Evidencias (control alterno a la facturación).

En esta instancia Odoo NO factura: registra las ventas y las remisiones;
la facturación real vive en CONTPAQi (Compact). Por eso el expediente se
ancla en la ORDEN DE VENTA (el folio de seguimiento de la casa):

- Cantidades pedidas/entregadas: de las líneas de venta y sus remisiones
  reales de Odoo (qty_delivered ya neto de devoluciones).
- Folios de producción: los folios por línea (multi-folio S10978-1, -2…)
  de este mismo módulo.
- Factura: datos de Compact capturados a mano en el expediente (folio,
  fecha, importe), sin ningún vínculo contable con Odoo.
- Evidencias: remisiones firmadas/selladas, acuses y fotos, con
  validación y envío a Administración.
"""
import base64
import io
import logging
import re
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

DELIVERY_STATES = [
    ('pending', 'Pendiente de entregar'),
    ('partial', 'Entrega parcial'),
    ('delivered', 'Entregado'),
    ('review', 'Requiere revisión'),
    ('cancelled', 'Cancelado'),
]

DOC_STATES = [
    ('no_evidence', 'Sin evidencia'),
    ('partial_evidence', 'Evidencia incompleta'),
    ('evidence_received', 'Evidencia recibida'),
    ('ready', 'Completo para Administración'),
    ('sent', 'Enviado a Administración'),
]


class DeliveryEvidenceControl(models.Model):
    _name = 'delivery.evidence.control'
    _description = 'Control de Entregas y Evidencias'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'order_date desc, id desc'
    _rec_name = 'name'

    # ------------------------------------------------------------------
    # Venta (ancla del expediente; todo relacionado, sin copiar datos)
    # ------------------------------------------------------------------
    sale_order_id = fields.Many2one(
        'sale.order', 'Orden de venta', required=True, index=True,
        ondelete='restrict',
    )
    name = fields.Char('Folio', related='sale_order_id.name', store=True)
    company_id = fields.Many2one(related='sale_order_id.company_id', store=True, index=True)
    partner_id = fields.Many2one(related='sale_order_id.partner_id', store=True, string='Cliente')
    partner_code = fields.Char(
        'Código del cliente', related='sale_order_id.partner_id.company_registry', store=True,
    )
    order_date = fields.Date('Fecha', compute='_compute_order_date', store=True)
    currency_id = fields.Many2one(related='sale_order_id.currency_id')
    amount_total = fields.Monetary(related='sale_order_id.amount_total', string='Total venta')
    order_state = fields.Selection(related='sale_order_id.state', string='Estado venta', store=True)
    client_order_ref = fields.Char(
        'OC del cliente', related='sale_order_id.client_order_ref', store=True,
    )
    production_folios = fields.Char(
        'Folios de producción', compute='_compute_sale_links', store=True,
        help='Folios por línea de venta (multi-folio): cada consecutivo '
             'S10978-1, S10978-2… es un folio de producción independiente.',
    )
    picking_ids = fields.Many2many(
        'stock.picking', string='Remisiones', compute='_compute_sale_links', store=True,
    )
    picking_count = fields.Integer(compute='_compute_counts')
    evidence_count = fields.Integer(compute='_compute_counts')

    # Legado de la primera versión (anclada a facturas de Odoo). Se conserva
    # opcional para no romper la base ya desplegada; no se usa.
    move_id = fields.Many2one('account.move', 'Factura Odoo (legado)', readonly=True)

    # ------------------------------------------------------------------
    # Factura Compact (control alterno: captura manual, sin vínculo contable)
    # ------------------------------------------------------------------
    compact_invoice_folio = fields.Char(
        'Factura Compact', tracking=True,
        help='Serie y folio de la factura emitida en CONTPAQi. Captura manual: '
             'esta instancia de Odoo no factura.',
    )
    compact_invoice_date = fields.Date('Fecha factura Compact', tracking=True)
    compact_invoice_amount = fields.Monetary('Importe factura Compact', tracking=True)

    # ------------------------------------------------------------------
    # Cantidades (de las remisiones reales; ver _update_from_source)
    # ------------------------------------------------------------------
    line_ids = fields.One2many('delivery.evidence.control.line', 'control_id', 'Detalle')
    qty_ordered = fields.Float('Cant. pedida', digits='Product Unit of Measure', readonly=True)
    qty_delivered = fields.Float('Cant. entregada (neta)', digits='Product Unit of Measure', readonly=True)
    qty_pending = fields.Float('Cant. pendiente', digits='Product Unit of Measure', readonly=True)
    delivered_pct = fields.Float('% entregado', readonly=True, aggregator='avg')
    delivery_state = fields.Selection(
        DELIVERY_STATES, 'Estado de entrega', default='pending',
        readonly=True, index=True, tracking=True,
    )
    review_reason = fields.Text('Motivo de revisión', readonly=True)

    # ------------------------------------------------------------------
    # Evidencias y seguimiento administrativo
    # ------------------------------------------------------------------
    evidence_ids = fields.One2many('delivery.evidence.document', 'control_id', 'Evidencias')
    doc_state = fields.Selection(
        DOC_STATES, 'Estado documental', default='no_evidence',
        readonly=True, index=True, tracking=True,
    )
    evidence_received_date = fields.Date('Fecha de evidencia', readonly=True)
    evidence_user_id = fields.Many2one('res.users', 'Recibió evidencia', readonly=True)
    responsible_id = fields.Many2one('res.users', 'Responsable', tracking=True)
    ready_exception = fields.Boolean('Completado con excepción', readonly=True)
    sent_date = fields.Datetime('Enviado a Administración', readonly=True)
    sent_user_id = fields.Many2one('res.users', 'Envió', readonly=True)
    notes = fields.Text('Observaciones')
    days_without_evidence = fields.Integer(
        'Días sin evidencia', compute='_compute_days_without_evidence',
        help='Días desde la fecha del pedido sin evidencia validada.',
    )
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ('sale_order_uniq', 'unique(sale_order_id)',
         'Ya existe un control de entregas y evidencias para esa orden de venta.'),
    ]

    # ==================================================================
    # Cómputos
    # ==================================================================
    @api.depends('sale_order_id.date_order')
    def _compute_order_date(self):
        for control in self:
            control.order_date = (
                control.sale_order_id.date_order
                and control.sale_order_id.date_order.date() or False
            )

    @api.depends('sale_order_id.order_line.delivery_folio', 'sale_order_id.picking_ids')
    def _compute_sale_links(self):
        for control in self:
            lines = control.sale_order_id.order_line
            folios = [f for f in lines.mapped('delivery_folio') if f]
            control.production_folios = ', '.join(dict.fromkeys(folios)) or False
            control.picking_ids = [(6, 0, control.sale_order_id.picking_ids.ids)]

    def _compute_counts(self):
        for control in self:
            control.picking_count = len(control.picking_ids)
            control.evidence_count = len(control.evidence_ids)

    @api.depends('doc_state', 'order_date')
    def _compute_days_without_evidence(self):
        today = fields.Date.context_today(self)
        for control in self:
            if control.doc_state in ('no_evidence', 'partial_evidence') and control.order_date:
                control.days_without_evidence = (today - control.order_date).days
            else:
                control.days_without_evidence = 0

    # ==================================================================
    # Cantidades desde las remisiones reales de Odoo
    # ==================================================================
    def _update_from_source(self):
        """Recalcula el detalle y el estado de entrega desde la venta.

        qty_delivered de la línea de venta ya es el neto real de las
        remisiones validadas menos devoluciones (lo mantiene Odoo desde los
        movimientos de inventario). Solo lectura: no toca ventas ni stock.
        """
        Line = self.env['delivery.evidence.control.line']
        for control in self:
            order = control.sale_order_id
            control.line_ids.unlink()

            if order.state == 'cancel':
                control.write({
                    'delivery_state': 'cancelled',
                    'qty_ordered': 0.0, 'qty_delivered': 0.0, 'qty_pending': 0.0,
                    'delivered_pct': 0.0, 'review_reason': False,
                })
                continue

            totals = {'ord': 0.0, 'dlv': 0.0, 'pen': 0.0}
            product_lines = order.order_line.filtered(
                lambda l: not l.display_type and l.product_id)
            for sale_line in product_lines:
                ordered = sale_line.product_uom_qty
                delivered = sale_line.qty_delivered
                pending = max(ordered - delivered, 0.0)
                Line.create({
                    'control_id': control.id,
                    'sale_line_id': sale_line.id,
                    'product_id': sale_line.product_id.id,
                    'uom_id': sale_line.product_uom.id,
                    'qty_ordered': ordered,
                    'qty_delivered': delivered,
                    'qty_pending': pending,
                })
                totals['ord'] += ordered
                totals['dlv'] += delivered
                totals['pen'] += pending

            rounding = 0.001
            if not product_lines:
                state, reason = 'review', _('La orden no tiene líneas de producto.')
            elif totals['pen'] <= rounding:
                state, reason = 'delivered', False
            elif totals['dlv'] > rounding:
                state, reason = 'partial', False
            else:
                state, reason = 'pending', False

            control.write({
                'qty_ordered': totals['ord'],
                'qty_delivered': totals['dlv'],
                'qty_pending': totals['pen'],
                'delivered_pct': (100.0 * totals['dlv'] / totals['ord']) if totals['ord'] else 0.0,
                'delivery_state': state,
                'review_reason': reason,
            })

    def _update_evidence_stage(self):
        """Etapas automáticas de evidencia (no toca ready/sent, que son acciones)."""
        for control in self:
            if control.doc_state in ('ready', 'sent'):
                continue
            validated = control.evidence_ids.filtered(lambda e: e.state == 'validated')
            if validated:
                control.doc_state = 'evidence_received'
                if not control.evidence_received_date:
                    control.evidence_received_date = fields.Date.context_today(control)
                    control.evidence_user_id = self.env.user
            elif control.evidence_ids:
                control.doc_state = 'partial_evidence'
            else:
                control.doc_state = 'no_evidence'

    # ==================================================================
    # Sincronización desde órdenes de venta confirmadas
    # ==================================================================
    @api.model
    def _sync_from_orders(self, orders):
        """Crea/actualiza controles para las ventas dadas. Idempotente."""
        stats = {'created': 0, 'updated': 0, 'skipped': 0, 'review': 0}
        existing = {
            c.sale_order_id.id: c
            for c in self.with_context(active_test=False).search(
                [('sale_order_id', 'in', orders.ids)])
        }
        for order in orders:
            if order.state not in ('sale', 'done', 'cancel'):
                stats['skipped'] += 1
                continue
            control = existing.get(order.id)
            if control:
                control._update_from_source()
                stats['updated'] += 1
            else:
                control = self.create({'sale_order_id': order.id})
                control._update_from_source()
                stats['created'] += 1
            if control.delivery_state == 'review':
                stats['review'] += 1
        return stats

    # ==================================================================
    # Acciones
    # ==================================================================
    def _is_manager(self):
        return self.env.user.has_group('restricciones_entregas.group_delivery_evidence_manager')

    def action_refresh(self):
        self._update_from_source()
        self._update_evidence_stage()
        return True

    def action_validate_evidence(self):
        self.ensure_one()
        if not self._is_manager():
            raise UserError(_('Solo el responsable de Entregas y Evidencias puede validar evidencias.'))
        drafts = self.evidence_ids.filtered(lambda e: e.state == 'draft')
        if not drafts:
            raise UserError(_('No hay evidencias pendientes de validar.'))
        drafts.action_validate()
        return True

    def action_mark_ready(self):
        """Marca el expediente como completo, sin trabas.

        No exige evidencias, observaciones ni permisos especiales: el flujo
        mínimo de la casa es capturar folio/fecha/importe de la factura
        Compact y marcar. Lo que falte se anota informativamente en el
        chatter para trazabilidad, pero no bloquea.
        """
        self.ensure_one()
        if self.doc_state == 'sent':
            raise UserError(_('El control ya fue enviado a Administración.'))
        pending = self.qty_pending > 0.001 or self.delivery_state != 'delivered'
        self.write({'doc_state': 'ready', 'ready_exception': pending})
        missing = []
        if pending:
            missing.append(_('%(qty)s pendiente de entregar') % {'qty': self.qty_pending})
        if not self.evidence_ids:
            missing.append(_('sin evidencias cargadas'))
        if not self.compact_invoice_folio:
            missing.append(_('sin factura Compact capturada'))
        body = _('Expediente marcado como completo para Administración.')
        if missing:
            body += _(' Nota: %s.') % ', '.join(missing)
        self.message_post(body=body)
        return True

    def action_mark_sent(self):
        if not self._is_manager():
            raise UserError(_('Solo el responsable puede marcar el envío a Administración.'))
        for control in self:
            if control.doc_state != 'ready':
                raise UserError(_(
                    'El control %s no está listo para Administración.') % control.name)
            control.write({
                'doc_state': 'sent',
                'sent_date': fields.Datetime.now(),
                'sent_user_id': self.env.user.id,
            })
            control.message_post(body=_('Enviado a Administración.'))
        return True

    def action_reopen(self):
        self.ensure_one()
        if not self._is_manager():
            raise UserError(_('Solo el responsable puede reabrir un control.'))
        self.write({'ready_exception': False, 'sent_date': False, 'sent_user_id': False})
        self._update_evidence_stage()
        self.message_post(body=_('Control reabierto por el responsable.'))
        return True

    def action_generate_report(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Generar relación'),
            'res_model': 'delivery.evidence.report.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_control_ids': [(6, 0, self.ids)]},
        }

    def action_open_sale(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'sale.order',
            'res_id': self.sale_order_id.id,
            'view_mode': 'form',
        }

    def action_open_pickings(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Remisiones'),
            'res_model': 'stock.picking',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.picking_ids.ids)],
        }

    def unlink(self):
        if any(c.doc_state == 'sent' for c in self) and not self._is_manager():
            raise UserError(_('Un control enviado a Administración no se puede eliminar.'))
        return super().unlink()

    # ==================================================================
    # API para la aplicación OWL (Centro de operación)
    # ==================================================================
    TAB_DOMAINS = {
        'all': [],
        'pending': [('delivery_state', 'in', ['pending', 'partial'])],
        'no_evidence': [('delivery_state', '=', 'delivered'),
                        ('doc_state', 'in', ['no_evidence', 'partial_evidence'])],
        'ready': [('doc_state', '=', 'ready')],
        'sent': [('doc_state', '=', 'sent')],
        'review': [('delivery_state', '=', 'review')],
    }

    @api.model
    def js_bootstrap(self):
        counts = {
            tab: self.search_count(domain)
            for tab, domain in self.TAB_DOMAINS.items()
        }
        return {
            'user_name': self.env.user.name,
            'is_manager': self.env.user.has_group(
                'restricciones_entregas.group_delivery_evidence_manager'),
            'counts': counts,
            'evidence_types': [
                {'value': v, 'label': l}
                for v, l in self.env['delivery.evidence.document']._fields['evidence_type'].selection
            ],
        }

    @api.model
    def js_list(self, tab='all', search='', limit=120):
        domain = list(self.TAB_DOMAINS.get(tab, []))
        if search:
            term = search.strip()
            domain += ['|', '|', '|', '|',
                       ('name', 'ilike', term),
                       ('partner_id', 'ilike', term),
                       ('client_order_ref', 'ilike', term),
                       ('production_folios', 'ilike', term),
                       ('compact_invoice_folio', 'ilike', term)]
        controls = self.search(domain, limit=limit, order='order_date desc, id desc')
        return [c._js_row() for c in controls]

    def _js_row(self):
        self.ensure_one()
        return {
            'id': self.id,
            'name': self.name or '',
            'partner': self.partner_id.name or '',
            'partner_code': self.partner_code or '',
            'date': self.order_date and self.order_date.strftime('%d/%m/%Y') or '',
            'amount_total': self.amount_total,
            'currency': self.currency_id.name or 'MXN',
            'qty_ordered': self.qty_ordered,
            'qty_delivered': self.qty_delivered,
            'qty_pending': self.qty_pending,
            'pct': round(self.delivered_pct, 1),
            'delivery_state': self.delivery_state,
            'doc_state': self.doc_state,
            'days': self.days_without_evidence,
            'folios': self.production_folios or '',
            'oc': self.client_order_ref or '',
            'compact_folio': self.compact_invoice_folio or '',
            'evidence_count': len(self.evidence_ids),
            'sent_date': self.sent_date and fields.Datetime.context_timestamp(
                self, self.sent_date).strftime('%d/%m/%Y') or '',
            'exception': self.ready_exception,
        }

    def js_detail(self):
        self.ensure_one()
        data = self._js_row()
        data.update({
            'order_state': self.order_state,
            'review_reason': self.review_reason or '',
            'notes': self.notes or '',
            'responsible': self.responsible_id.name or '',
            'compact_date': self.compact_invoice_date and
                self.compact_invoice_date.strftime('%Y-%m-%d') or '',
            'compact_amount': self.compact_invoice_amount,
            'evidence_received_date': self.evidence_received_date and
                self.evidence_received_date.strftime('%d/%m/%Y') or '',
            'lines': [{
                'id': l.id,
                'product': l.product_id.display_name or '',
                'folio': l.production_folio or '',
                'uom': l.uom_id.name or '',
                'qty_ordered': l.qty_ordered,
                'qty_delivered': l.qty_delivered,
                'qty_pending': l.qty_pending,
            } for l in self.line_ids],
            'evidences': [{
                'id': e.id,
                'type': e.evidence_type,
                'type_label': dict(e._fields['evidence_type'].selection)[e.evidence_type],
                'name': e.name,
                'file_name': e.file_name or '',
                'url': '/web/content?model=delivery.evidence.document&field=file'
                       f'&id={e.id}&download=true&filename={e.file_name or e.name}',
                'doc_date': e.doc_date and e.doc_date.strftime('%d/%m/%Y') or '',
                'uploaded_by': e.create_uid.name,
                'uploaded_at': fields.Datetime.context_timestamp(
                    e, e.create_date).strftime('%d/%m/%Y %H:%M'),
                'state': e.state,
                'validated_by': e.validated_by_id.name or '',
                'notes': e.notes or '',
            } for e in self.evidence_ids],
        })
        return data

    def js_add_evidence(self, vals):
        self.ensure_one()
        self.env['delivery.evidence.document'].create({
            'control_id': self.id,
            'evidence_type': vals.get('evidence_type') or 'remision_firmada',
            'name': vals.get('name') or vals.get('file_name') or _('Evidencia'),
            'file': vals['file'],
            'file_name': vals.get('file_name'),
            'doc_date': vals.get('doc_date') or False,
            'notes': vals.get('notes') or False,
        })
        return self.js_detail()

    def js_action(self, action):
        """Ejecuta una acción del flujo y regresa el detalle actualizado."""
        self.ensure_one()
        actions = {
            'refresh': self.action_refresh,
            'validate': self.action_validate_evidence,
            'ready': self.action_mark_ready,
            'sent': self.action_mark_sent,
            'reopen': self.action_reopen,
        }
        if action not in actions:
            raise UserError(_('Acción no reconocida.'))
        actions[action]()
        return self.js_detail()

    def js_set_notes(self, notes):
        self.ensure_one()
        self.notes = notes or False
        return True

    def js_set_compact(self, vals):
        """Captura manual de la factura Compact (folio, fecha, importe)."""
        self.ensure_one()
        allowed = {}
        if 'folio' in vals:
            allowed['compact_invoice_folio'] = (vals['folio'] or '').strip() or False
        if 'date' in vals:
            allowed['compact_invoice_date'] = vals['date'] or False
        if 'amount' in vals:
            try:
                allowed['compact_invoice_amount'] = float(vals['amount'] or 0) or False
            except (TypeError, ValueError):
                raise UserError(_('El importe de la factura Compact no es un número válido.'))
        self.write(allowed)
        return self.js_detail()

    def js_validate_document(self, document_id):
        self.ensure_one()
        doc = self.evidence_ids.filtered(lambda d: d.id == document_id)
        doc.action_validate()
        return self.js_detail()

    @api.model
    def js_bulk_action(self, control_ids, action):
        """Aplica una acción a varios controles; reporta éxito/fallo por folio."""
        results = {'ok': [], 'failed': []}
        for control in self.browse(control_ids):
            try:
                with self.env.cr.savepoint():
                    control.js_action(action)
                results['ok'].append(control.name)
            except UserError as error:
                results['failed'].append({'name': control.name, 'reason': str(error)})
        return results

    @api.model
    def js_sync_recent(self, days=60):
        if not self.env.user.has_group('restricciones_entregas.group_delivery_evidence_manager'):
            raise UserError(_('Solo el responsable puede sincronizar ventas.'))
        date_from = fields.Datetime.now() - timedelta(days=days)
        orders = self.env['sale.order'].search([
            ('state', 'in', ['sale', 'done']),
            ('date_order', '>=', date_from),
        ])
        stats = self._sync_from_orders(orders)
        stats['total'] = len(orders)
        return stats

    @api.model
    def js_match_excel(self, file_b64, filename):
        """Empata un Excel contra los controles por folio de venta, folio de
        producción, OC del cliente o factura Compact: sirve para palomear en
        lote lo que venga listado en cualquier layout de Excel."""
        content = base64.b64decode(file_b64)
        fname = (filename or '').lower()
        tokens = set()
        if fname.endswith('.xlsx'):
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            for sheet in wb.worksheets:
                for row in sheet.iter_rows(values_only=True):
                    for value in row:
                        if isinstance(value, str) and 2 < len(value.strip()) <= 64:
                            tokens.add(value.strip().lower())
        elif fname.endswith('.xls'):
            try:
                import xlrd
            except ImportError:
                raise UserError(_('El servidor no puede leer .xls; guarda el archivo como .xlsx.'))
            wb = xlrd.open_workbook(file_contents=content)
            for sheet in wb.sheets():
                for r in range(sheet.nrows):
                    for c in range(sheet.ncols):
                        value = sheet.cell_value(r, c)
                        if isinstance(value, str) and 2 < len(value.strip()) <= 64:
                            tokens.add(value.strip().lower())
        else:
            raise UserError(_('Sube un archivo .xlsx o .xls.'))

        index = {}
        for control in self.search([]):
            identifiers = [control.name or '', control.client_order_ref or '',
                           control.compact_invoice_folio or '']
            identifiers += (control.production_folios or '').split(', ')
            for identifier in identifiers:
                if identifier:
                    index.setdefault(identifier.strip().lower(), control)

        matched = self.browse()
        unmatched = []
        for token in sorted(tokens):
            control = index.get(token)
            if control:
                matched |= control
            else:
                unmatched.append(token)

        # Diagnóstico de tokens con forma de folio: si la orden de venta
        # confirmada existe sin control, se crea al vuelo; si no está
        # confirmada, se explica por qué no puede palomearse.
        folio_pattern = re.compile(r'^[a-z]{0,6}[\-/]?\d{3,}([\-/]\d+)?$')
        diagnostics = []
        SaleOrder = self.env['sale.order']
        for token in unmatched[:300]:
            if not folio_pattern.match(token):
                continue
            order = SaleOrder.search([('name', '=ilike', token)], limit=1)
            if not order:
                diagnostics.append({
                    'token': token.upper(), 'status': 'unknown',
                    'detail': _('No corresponde a ninguna orden de venta, folio de '
                                'producción, OC de cliente ni factura Compact.'),
                })
                continue
            if order.state in ('sale', 'done'):
                self._sync_from_orders(order)
                matched |= self.search([('sale_order_id', '=', order.id)])
                diagnostics.append({
                    'token': order.name, 'status': 'created',
                    'detail': _('Orden confirmada sin control previo: se creó y ya '
                                'aparece en la lista.'),
                })
            elif order.state == 'cancel':
                diagnostics.append({
                    'token': order.name, 'status': 'no_invoice',
                    'detail': _('La orden de venta está cancelada.'),
                })
            else:
                diagnostics.append({
                    'token': order.name, 'status': 'no_invoice',
                    'detail': _('La orden de venta existe pero aún no está confirmada; '
                                'no hay expediente que palomear.'),
                })

        return {
            'matched': [c._js_row() for c in matched.sorted(
                key=lambda c: (c.order_date or fields.Date.today(), c.id))],
            'diagnostics': diagnostics[:40],
            'cells_scanned': len(tokens),
        }


class DeliveryEvidenceControlLine(models.Model):
    _name = 'delivery.evidence.control.line'
    _description = 'Detalle por producto del control de entregas'
    _order = 'id'

    control_id = fields.Many2one(
        'delivery.evidence.control', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one(related='control_id.company_id', store=True)
    sale_line_id = fields.Many2one('sale.order.line', 'Línea de venta', readonly=True)
    production_folio = fields.Char(
        'Folio de producción', related='sale_line_id.delivery_folio', store=True)
    product_id = fields.Many2one('product.product', 'Producto', readonly=True)
    uom_id = fields.Many2one('uom.uom', 'UdM', readonly=True)
    qty_ordered = fields.Float('Pedido', digits='Product Unit of Measure', readonly=True)
    qty_delivered = fields.Float('Entregado neto', digits='Product Unit of Measure', readonly=True)
    qty_pending = fields.Float('Pendiente', digits='Product Unit of Measure', readonly=True)


class DeliveryEvidenceDocument(models.Model):
    _name = 'delivery.evidence.document'
    _description = 'Evidencia de entrega'
    _order = 'create_date desc'

    control_id = fields.Many2one(
        'delivery.evidence.control', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one(related='control_id.company_id', store=True)
    evidence_type = fields.Selection([
        ('remision_firmada', 'Remisión firmada'),
        ('remision_sellada', 'Remisión sellada'),
        ('acuse', 'Acuse de entrega'),
        ('foto', 'Fotografía'),
        ('documento', 'Documento adicional'),
        ('otro', 'Otro'),
    ], 'Tipo', required=True, default='remision_firmada')
    name = fields.Char('Nombre', required=True)
    file = fields.Binary('Archivo', attachment=True, required=True)
    file_name = fields.Char('Nombre del archivo')
    doc_date = fields.Date('Fecha del documento')
    notes = fields.Char('Observaciones')
    state = fields.Selection([
        ('draft', 'Cargada'),
        ('validated', 'Validada'),
    ], 'Validación', default='draft', required=True)
    validated_by_id = fields.Many2one('res.users', 'Validó', readonly=True)
    validated_date = fields.Datetime('Fecha de validación', readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        docs = super().create(vals_list)
        for doc in docs:
            doc.control_id.message_post(body=_(
                'Evidencia cargada: %(type)s — %(name)s'
            ) % {'type': dict(doc._fields['evidence_type'].selection)[doc.evidence_type],
                 'name': doc.name})
        docs.control_id._update_evidence_stage()
        return docs

    def action_validate(self):
        manager = self.env.user.has_group(
            'restricciones_entregas.group_delivery_evidence_manager')
        if not manager:
            raise UserError(_('Solo el responsable puede validar evidencias.'))
        for doc in self.filtered(lambda d: d.state == 'draft'):
            doc.write({
                'state': 'validated',
                'validated_by_id': self.env.user.id,
                'validated_date': fields.Datetime.now(),
            })
            doc.control_id.message_post(body=_('Evidencia validada: %s') % doc.name)
        self.control_id._update_evidence_stage()
        return True

    def unlink(self):
        manager = self.env.user.has_group(
            'restricciones_entregas.group_delivery_evidence_manager')
        controls = self.control_id
        for doc in self:
            if doc.state == 'validated' and not manager:
                raise UserError(_('Una evidencia validada solo la puede eliminar el responsable.'))
            doc.control_id.message_post(body=_('Evidencia eliminada: %s') % doc.name)
        res = super().unlink()
        controls._update_evidence_stage()
        return res


class SaleOrderEvidenceHook(models.Model):
    _inherit = 'sale.order'

    def action_confirm(self):
        """Al confirmar una venta nace su expediente de entregas y evidencias.

        No invasivo: corre después del super() y cualquier falla se registra
        en el log sin afectar la confirmación.
        """
        res = super().action_confirm()
        try:
            self.env['delivery.evidence.control'].sudo()._sync_from_orders(self)
        except Exception:
            _logger.exception(
                'Control de Entregas y Evidencias: no se pudo crear el control '
                'automático al confirmar; la venta se confirmó normalmente.')
        return res
