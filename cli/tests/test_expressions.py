"""QGIS expression → MapLibre expression.

Two things are being pinned here, and the second matters as much as the first:

* what the subset TRANSLATES to, exactly — a filter that is nearly right draws the wrong features,
  and nobody notices until a reader does;
* what it REFUSES. An unsupported construct must raise `Unsupported` naming itself, because the
  fidelity report is built from those names. A translator that silently approximates is worse than
  one that declines.
"""
from __future__ import annotations

import pytest

from geodeploy.expressions import (Unsupported, from_maplibre, negate, supports,
                                   to_maplibre, try_maplibre)


class TestLiteralsAndFields:
    def test_a_quoted_name_is_a_field(self):
        assert to_maplibre('"type"') == ["get", "type"]

    def test_a_bare_word_is_a_field_too(self):
        # QGIS resolves an unquoted identifier against the layer's attributes, and most hand-written
        # rules are spelled that way.
        assert to_maplibre("type") == ["get", "type"]

    def test_strings_numbers_and_booleans(self):
        assert to_maplibre("'a'") == "a"
        assert to_maplibre("42") == 42
        assert to_maplibre("1.5") == 1.5
        assert to_maplibre("1e3") == 1000.0
        assert to_maplibre("TRUE") is True
        assert to_maplibre("false") is False
        assert to_maplibre("NULL") is None

    def test_a_doubled_quote_escapes_inside_a_string(self):
        assert to_maplibre("'it''s'") == "it's"

    def test_a_field_name_may_contain_spaces_and_punctuation(self):
        assert to_maplibre('"Land use (2024)"') == ["get", "Land use (2024)"]

    def test_feature_id(self):
        assert to_maplibre("$id") == ["id"]

    def test_empty_is_no_filter_not_a_true_filter(self):
        # "no filter" and "a filter matching everything" are the same on a map and not in a style:
        # MapLibre wants the key absent.
        assert to_maplibre("") is None
        assert to_maplibre("   ") is None
        assert to_maplibre(None) is None


class TestComparison:
    def test_equality_uses_the_maplibre_spelling(self):
        assert to_maplibre('"a" = 1') == ["==", ["get", "a"], 1]
        assert to_maplibre('"a" == 1') == ["==", ["get", "a"], 1]

    def test_both_spellings_of_not_equal(self):
        assert to_maplibre('"a" <> 1') == ["!=", ["get", "a"], 1]
        assert to_maplibre('"a" != 1') == ["!=", ["get", "a"], 1]

    def test_ordering(self):
        assert to_maplibre('"pop" >= 100') == [">=", ["get", "pop"], 100]
        assert to_maplibre('"pop" < 100') == ["<", ["get", "pop"], 100]

    def test_a_comparison_between_two_fields(self):
        assert to_maplibre('"a" > "b"') == [">", ["get", "a"], ["get", "b"]]


class TestBoolean:
    def test_and_flattens_into_one_all(self):
        # Nested ["all", ["all", …]] is legal and unreadable; a flat list is what a human would write.
        assert to_maplibre('"a" = 1 AND "b" = 2 AND "c" = 3') == [
            "all", ["==", ["get", "a"], 1], ["==", ["get", "b"], 2], ["==", ["get", "c"], 3]]

    def test_or_flattens_into_one_any(self):
        assert to_maplibre('"a" = 1 OR "b" = 2') == [
            "any", ["==", ["get", "a"], 1], ["==", ["get", "b"], 2]]

    def test_and_binds_tighter_than_or(self):
        assert to_maplibre('"a" = 1 OR "b" = 2 AND "c" = 3') == [
            "any",
            ["==", ["get", "a"], 1],
            ["all", ["==", ["get", "b"], 2], ["==", ["get", "c"], 3]]]

    def test_parentheses_override_precedence(self):
        assert to_maplibre('("a" = 1 OR "b" = 2) AND "c" = 3') == [
            "all",
            ["any", ["==", ["get", "a"], 1], ["==", ["get", "b"], 2]],
            ["==", ["get", "c"], 3]]

    def test_not(self):
        assert to_maplibre('NOT "a" = 1') == ["!", ["==", ["get", "a"], 1]]

    def test_case_is_ignored_for_keywords(self):
        assert to_maplibre('"a" = 1 and "b" = 2') == to_maplibre('"a" = 1 AND "b" = 2')


class TestMembershipAndRange:
    def test_in_becomes_a_literal_array(self):
        assert to_maplibre('"k" IN (\'a\', \'b\')') == [
            "in", ["get", "k"], ["literal", ["a", "b"]]]

    def test_not_in(self):
        assert to_maplibre('"k" NOT IN (1, 2)') == [
            "!", ["in", ["get", "k"], ["literal", [1, 2]]]]

    def test_in_refuses_a_non_literal(self):
        # MapLibre's `in` takes a literal array; a field there would silently match nothing.
        with pytest.raises(Unsupported) as exc:
            to_maplibre('"k" IN ("other")')
        assert exc.value.construct == "IN"

    def test_between_expands_to_two_comparisons(self):
        assert to_maplibre('"pop" BETWEEN 10 AND 20') == [
            "all", [">=", ["get", "pop"], 10], ["<=", ["get", "pop"], 20]]

    def test_not_between(self):
        assert to_maplibre('"pop" NOT BETWEEN 10 AND 20') == [
            "!", ["all", [">=", ["get", "pop"], 10], ["<=", ["get", "pop"], 20]]]


class TestNull:
    def test_is_null_tests_absence_and_null(self):
        # In a vector tile an unset attribute is simply not encoded, so `has` is the honest test;
        # in GeoJSON a property can be present AND null, so both are checked.
        assert to_maplibre('"a" IS NULL') == [
            "any", ["!", ["has", "a"]], ["==", ["get", "a"], None]]

    def test_is_not_null_is_its_negation(self):
        assert to_maplibre('"a" IS NOT NULL') == [
            "!", ["any", ["!", ["has", "a"]], ["==", ["get", "a"], None]]]


class TestLike:
    def test_starts_with(self):
        assert to_maplibre("\"n\" LIKE 'ab%'") == ["==", ["slice", ["get", "n"], 0, 2], "ab"]

    def test_ends_with(self):
        assert to_maplibre("\"n\" LIKE '%ab'") == ["==", ["slice", ["get", "n"], -2], "ab"]

    def test_contains(self):
        assert to_maplibre("\"n\" LIKE '%ab%'") == ["!=", ["index-of", "ab", ["get", "n"]], -1]

    def test_no_wildcard_is_equality(self):
        assert to_maplibre("\"n\" LIKE 'ab'") == ["==", ["get", "n"], "ab"]

    def test_ilike_lowercases_both_sides(self):
        assert to_maplibre("\"n\" ILIKE '%AB%'") == [
            "!=", ["index-of", "ab", ["downcase", ["get", "n"]]], -1]

    def test_not_like(self):
        assert to_maplibre("\"n\" NOT LIKE 'ab%'") == [
            "!", ["==", ["slice", ["get", "n"], 0, 2], "ab"]]

    def test_a_wildcard_in_the_middle_is_refused(self):
        with pytest.raises(Unsupported) as exc:
            to_maplibre("\"n\" LIKE 'a%b'")
        assert exc.value.construct == "LIKE"

    def test_the_single_character_wildcard_is_refused(self):
        with pytest.raises(Unsupported):
            to_maplibre("\"n\" LIKE 'a_c'")


class TestArithmetic:
    def test_the_four_operators(self):
        assert to_maplibre('"a" + 1') == ["+", ["get", "a"], 1]
        assert to_maplibre('"a" - 1') == ["-", ["get", "a"], 1]
        assert to_maplibre('"a" * 2') == ["*", ["get", "a"], 2]
        assert to_maplibre('"a" / 2') == ["/", ["get", "a"], 2]
        assert to_maplibre('"a" % 2') == ["%", ["get", "a"], 2]

    def test_multiplication_binds_tighter_than_addition(self):
        assert to_maplibre("1 + 2 * 3") == ["+", 1, ["*", 2, 3]]

    def test_arithmetic_inside_a_comparison(self):
        assert to_maplibre('"a" * 2 > 10') == [">", ["*", ["get", "a"], 2], 10]

    def test_a_negative_literal_stays_a_literal(self):
        assert to_maplibre("-5") == -5

    def test_negating_an_expression_becomes_a_subtraction(self):
        assert to_maplibre('-"a"') == ["-", 0, ["get", "a"]]

    def test_concatenation(self):
        assert to_maplibre("'a' || \"b\" || 'c'") == ["concat", "a", ["get", "b"], "c"]


class TestFunctions:
    def test_the_direct_mappings(self):
        assert to_maplibre('lower("a")') == ["downcase", ["get", "a"]]
        assert to_maplibre('upper("a")') == ["upcase", ["get", "a"]]
        assert to_maplibre('length("a")') == ["length", ["get", "a"]]
        assert to_maplibre('abs("a")') == ["abs", ["get", "a"]]
        assert to_maplibre('floor("a")') == ["floor", ["get", "a"]]

    def test_variadic(self):
        assert to_maplibre('coalesce("a", "b", 0)') == [
            "coalesce", ["get", "a"], ["get", "b"], 0]
        assert to_maplibre("concat('x', \"a\")") == ["concat", "x", ["get", "a"]]

    def test_if_becomes_case(self):
        assert to_maplibre("if(\"a\" = 1, 'y', 'n')") == [
            "case", ["==", ["get", "a"], 1], "y", "n"]

    def test_strpos_is_one_based_like_qgis(self):
        # QGIS returns 1 for the first character and 0 when absent; index-of returns 0 and -1.
        assert to_maplibre("strpos(\"a\", 'x')") == ["+", ["index-of", "x", ["get", "a"]], 1]

    def test_substr_is_one_based_like_qgis(self):
        assert to_maplibre('substr("a", 2, 3)') == ["slice", ["get", "a"], 1, 4]

    def test_left_and_right(self):
        assert to_maplibre('left("a", 3)') == ["slice", ["get", "a"], 0, 3]
        assert to_maplibre('right("a", 3)') == ["slice", ["get", "a"], ["-", 0, 3]]

    def test_an_unknown_function_names_itself(self):
        # The NAME is what a fidelity report prints, so it has to reach the message.
        with pytest.raises(Unsupported) as exc:
            to_maplibre('md5("a")')
        assert exc.value.construct == "md5()"

    def test_replace_is_refused_by_name(self):
        with pytest.raises(Unsupported) as exc:
            to_maplibre("replace(\"a\", 'x', 'y')")
        assert exc.value.construct == "replace()"

    def test_wrong_arity_is_refused(self):
        with pytest.raises(Unsupported):
            to_maplibre('lower("a", "b")')


class TestCase:
    def test_case_with_else(self):
        assert to_maplibre("CASE WHEN \"a\" = 1 THEN 'x' ELSE 'y' END") == [
            "case", ["==", ["get", "a"], 1], "x", "y"]

    def test_several_whens(self):
        assert to_maplibre(
            "CASE WHEN \"a\" = 1 THEN 'x' WHEN \"a\" = 2 THEN 'y' ELSE 'z' END") == [
            "case", ["==", ["get", "a"], 1], "x", ["==", ["get", "a"], 2], "y", "z"]

    def test_a_missing_else_becomes_null_not_an_invented_default(self):
        # MapLibre requires a fallback where QGIS's ELSE is optional; omitting it in QGIS yields
        # NULL, and inventing something else would draw features the author left undrawn.
        assert to_maplibre("CASE WHEN \"a\" = 1 THEN 'x' END") == [
            "case", ["==", ["get", "a"], 1], "x", None]


class TestRefusals:
    @pytest.mark.parametrize("expression, construct", [
        ("@map_scale > 1000", "@map_scale"),
        ("$geometry", "$geometry"),
        ("$area > 5", "$area"),
    ])
    def test_rendering_variables_and_geometry_name_themselves(self, expression, construct):
        with pytest.raises(Unsupported) as exc:
            to_maplibre(expression)
        assert exc.value.construct == construct

    def test_an_unterminated_string(self):
        with pytest.raises(Unsupported):
            to_maplibre("\"a\" = 'x")

    def test_trailing_rubbish_is_not_ignored(self):
        with pytest.raises(Unsupported):
            to_maplibre('"a" = 1 "b"')

    def test_an_unexpected_character(self):
        with pytest.raises(Unsupported):
            to_maplibre('"a" = 1 & "b" = 2')


class TestHelpers:
    def test_try_maplibre_reports_rather_than_raises(self):
        expression, reason = try_maplibre('"a" = 1')
        assert expression == ["==", ["get", "a"], 1] and reason is None
        expression, reason = try_maplibre("@map_scale > 1")
        assert expression is None and "@map_scale" in reason

    def test_supports(self):
        assert supports('"a" IN (1, 2)')
        assert not supports("intersects($geometry, @atlas_geometry)")

    def test_negate_builds_an_else_filter(self):
        a = ["==", ["get", "k"], "a"]
        b = ["==", ["get", "k"], "b"]
        assert negate([a, b]) == ["!", ["any", a, b]]

    def test_negate_of_one_sibling_skips_the_any(self):
        a = ["==", ["get", "k"], "a"]
        assert negate([a]) == ["!", a]

    def test_negate_of_nothing_is_nothing(self):
        # Every sibling matched everything, so there is nothing left for an ELSE to draw.
        assert negate([]) is None
        assert negate([None, None]) is None


class TestRealRules:
    """Filters taken from the kind of project this exists for, translated end to end."""

    def test_a_typical_categorised_rule(self):
        assert to_maplibre("\"Type\" = 'Canal'") == ["==", ["get", "Type"], "Canal"]

    def test_a_typical_range_rule(self):
        assert to_maplibre('"pop_max" >= 100000 AND "pop_max" < 1000000') == [
            "all", [">=", ["get", "pop_max"], 100000], ["<", ["get", "pop_max"], 1000000]]

    def test_a_rule_over_several_fields(self):
        assert to_maplibre(
            "\"class\" IN ('A', 'B') AND \"width\" > 2 AND \"name\" IS NOT NULL") == [
            "all",
            ["in", ["get", "class"], ["literal", ["A", "B"]]],
            [">", ["get", "width"], 2],
            ["!", ["any", ["!", ["has", "name"]], ["==", ["get", "name"], None]]]]

    def test_a_rule_with_a_computed_comparison(self):
        assert to_maplibre('"area" / 10000 > 5') == [">", ["/", ["get", "area"], 10000], 5]


class TestBackToQgis:
    """`from_maplibre` — narrower than its twin on purpose: it only reads what we ourselves emit."""

    def test_comparisons(self):
        assert from_maplibre(["==", ["get", "a"], 1]) == '"a" = 1'
        assert from_maplibre(["!=", ["get", "a"], "x"]) == "\"a\" <> 'x'"
        assert from_maplibre([">=", ["get", "pop"], 100]) == '"pop" >= 100'

    def test_and_or_are_parenthesised_so_precedence_survives(self):
        combined = ["all", ["==", ["get", "a"], 1],
                    ["any", ["==", ["get", "b"], 2], ["==", ["get", "c"], 3]]]
        assert from_maplibre(combined) == '"a" = 1 AND ("b" = 2 OR "c" = 3)'

    def test_not(self):
        assert from_maplibre(["!", ["==", ["get", "a"], 1]]) == 'NOT ("a" = 1)'

    def test_in_and_not_in(self):
        node = ["in", ["get", "k"], ["literal", ["a", "b"]]]
        assert from_maplibre(node) == "\"k\" IN ('a', 'b')"
        assert from_maplibre(["!", node]) == "\"k\" NOT IN ('a', 'b')"

    def test_the_null_shape_comes_back_as_is_null(self):
        # Not as its parts: `IS NULL` is what the author wrote, and it is what they should get back.
        assert from_maplibre(to_maplibre('"a" IS NULL')) == '"a" IS NULL'
        assert from_maplibre(to_maplibre('"a" IS NOT NULL')) == '"a" IS NOT NULL'

    def test_has_alone_is_is_not_null(self):
        assert from_maplibre(["has", "a"]) == '"a" IS NOT NULL'

    def test_a_shape_we_do_not_emit_is_refused_rather_than_guessed(self):
        with pytest.raises(Unsupported):
            from_maplibre(["interpolate", ["linear"], ["zoom"], 0, 1])

    @pytest.mark.parametrize("expression", [
        '"a" = 1',
        '"a" <> 2',
        '"pop" >= 100 AND "pop" < 1000',
        "\"k\" IN ('a', 'b')",
        "\"k\" NOT IN (1, 2)",
        '"a" IS NULL',
        '"a" IS NOT NULL',
        "\"Type\" = 'Canal'",
    ])
    def test_round_trip_through_both_directions(self, expression):
        """QGIS → MapLibre → QGIS produces something that translates to the SAME filter.

        Not string equality — whitespace and redundant parentheses are not worth preserving — but
        the FILTER has to be identical, because that is what decides which features are drawn.
        """
        once = to_maplibre(expression)
        assert to_maplibre(from_maplibre(once)) == once
