{
    'name': 'Restricciones Entregas - Fecha Entrega Hexagonos',
    'version': '18.0.2.1',
    'category': 'Sales',
    'summary': 'Configurar fecha de entrega por defecto a 15 días',
    'description': """
        Configura la fecha de entrega por defecto a 15 días desde la creación de la orden de venta.
        Restringe la modificación de esta fecha solo al administrador.
        Agrega fecha de entrega por línea y reporte segregado de entregas.
        Asigna un folio consecutivo por línea (ej. S00300-1, S00300-2) para dar
        seguimiento y planificar producción por línea sin dividir la orden de venta.
    """,
    'author': 'Alphaqueb Consulting SAS',
    'website': 'http://www.alphaqueb.com',
    'depends': ['sale_management', 'sale_stock'],
    'data': [
        'security/security.xml',
        'security/delivery_evidence_security.xml',
        'security/ir.model.access.csv',
        'views/sale_order_views.xml',
        'views/sale_order_line_delivery_report_views.xml',
        'views/delivery_evidence_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'restricciones_entregas/static/src/scss/delivery_evidence_app.scss',
            'restricciones_entregas/static/src/js/delivery_evidence_app.js',
            'restricciones_entregas/static/src/xml/delivery_evidence_app.xml',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}