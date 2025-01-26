{
    'name': 'Restricciones Entregas - Fecha Entrega Hexagonos',
    'version': '1.0',
    'category': 'Sales',
    'summary': 'Configurar fecha de entrega por defecto a 15 días',
    'description': """
        Configura la fecha de entrega por defecto a 15 días desde la creación de la orden de venta.
        Restringe la modificación de esta fecha solo al administrador.
    """,
    'author': 'Alphaqueb Consulting SAS',
    'website': 'http://www.alphaqueb.com',
    'depends': ['sale_management'],
    'data': [
        'security/security.xml',
        'views/sale_order_views.xml',
    ],

    'installable': True,
    'application': False,
}