"""Keep the first-party authority cost explicit in existing domain SQL budgets."""
def domain_statements(statements):
    authority = [s for s in statements if "from health_consent_state" in s.lower()]
    assert len(authority) == 1, authority
    return [s for s in statements if s not in authority]
