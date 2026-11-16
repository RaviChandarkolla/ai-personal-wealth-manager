from wealth_advisor.domain.stock360.models import Report360, Section  # noqa: F401
from wealth_advisor.domain.stock360.template import SECTIONS, template_headings


def test_registry_covers_every_template_heading():
    heads = template_headings()
    assert [n for n, _ in heads] == list(range(18))
    for (num, title), spec in zip(heads, sorted(SECTIONS, key=lambda s: s.number), strict=True):
        assert spec.number == num
        assert title.upper().startswith(spec.title.split(" (")[0].upper()[:20]), (title, spec.title)


def test_registry_parts_are_1_to_4_and_all_present():
    assert len(SECTIONS) == 18
    assert {s.part for s in SECTIONS} == {1, 2, 3, 4}
    assert SECTIONS[0].id == "step0"
