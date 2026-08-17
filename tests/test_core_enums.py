from lxcell.enums.core_enums import AccountType, PaymentMethod


def test_core_enums_behave_as_strings():
    assert AccountType.CHECKING == "checking"
    assert PaymentMethod.PEER_TO_PEER == "peer_to_peer"
