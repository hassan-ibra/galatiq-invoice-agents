def test_package_imports():
    import invoice_agents

    assert invoice_agents.__name__ == "invoice_agents"
