from receipts.locate import locate

FILLER = "The report reviews capacity additions across regions and discusses policy, grid, and permitting issues. "


def para(fact):
    return fact + " " + FILLER * 4


def test_matching_number_ranks_first():
    text = "\n\n".join([
        para("Offshore wind capacity in Europe grew from 12 GW in 2014 to 24 GW in 2019."),
        para("Offshore wind capacity in Europe grew from 11 GW in 2015 to 25 GW in 2020."),
        para("Hydropower capacity in Asia was stable."),
    ])
    top = locate("Offshore wind capacity in Europe grew to 25 GW in 2020.", text)
    assert "25 GW in 2020" in top[0].text
    assert top[0].score > top[1].score


def test_offsets_index_into_text_and_k_limits():
    text = "\n\n".join(para(f"Fact number {i} about wind.") for i in range(6))
    out = locate("wind fact", text, k=2)
    assert len(out) == 2
    for p in out:
        assert text[p.start : p.end] == p.text


def test_word_overlap_without_numbers():
    text = "\n\n".join([para("Solar module prices fell sharply."), para("Cats sleep a lot.")])
    assert "Solar module prices" in locate("Solar module prices fell", text)[0].text


def test_empty_and_unrelated():
    assert locate("anything", "") == []
    assert locate("quantum entanglement", "Solar module prices fell.") == []
