def migrate(cr, version):
    cr.execute("""
    delete from ir_ui_view where id in (2618,2619,2622, 2639);
    delete from ir_model where id = 786;
    """)
