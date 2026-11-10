"""Part 2 mathematical checks; these tests do not contact the service."""
from decimal import Decimal, localcontext
from fractions import Fraction
import unittest

from scripts import statistics as model

D = Decimal


class StatisticsTests(unittest.TestCase):
    def test_cdf_boundaries_and_late_duplicate_start(self):
        with localcontext() as ctx:
            ctx.prec = 80
            for t in (0, 49, 50, 100, 200, 249, 250):
                self.assertEqual(model.completion_cdf(D(t), True), model.uniform_cdf(D(t)))
            self.assertEqual(model.completion_cdf(D(50), True), 0)
            self.assertEqual(model.completion_cdf(D(400), True), 1)
            self.assertEqual(model.completion_cdf(D(600), True), 1)
            self.assertLess(abs(model.completion_cdf(D(300), True) - D(37) / 49), D("1e-70"))
            epsilon = D("1e-30")
            for boundary in (50, 250, 400):
                left = model.completion_cdf(D(boundary) - epsilon, True)
                right = model.completion_cdf(D(boundary) + epsilon, True)
                self.assertLess(abs(right - left), D("1e-29"))

    def test_batch_independence_and_fixed_gpu_time(self):
        with localcontext() as ctx:
            ctx.prec = 80
            for n in (100, 10000):
                self.assertLess(abs(model.batch_cdf(D(1300), n, True) - (D(37) / 49) ** n), D("1e-70"))
                self.assertEqual(model.batch_cdf(D(1400), n, True), 1)

    def test_closed_forms_match_independent_cdf_inversion(self):
        model.verify()
        # Exercise the earlier linear-CDF branch as well as the requested tail.
        for probability in (D("0.01"), D("0.3"), D("0.8")):
            self.assertLess(abs(model.quantile(1, probability, True)
                                - model.inverted_quantile(1, probability, True)), D("1e-60"))

    def test_win_probabilities_by_exact_area_integration(self):
        # Independent geometric integration: triangle area for X > 200 + Y.
        # Both axes have width 350; its legs are 400 - 200 - 50 = 150.
        win_all = Fraction(150 * 150, 2 * 350 * 350)
        duplicated = Fraction(400 - 200, 400 - 50)
        self.assertEqual(duplicated, Fraction(4, 7))
        self.assertEqual(win_all, Fraction(9, 98))
        self.assertEqual(win_all / duplicated, Fraction(9, 56))
        reported = model.results()
        for key, fraction in (
            ("duplicate_percent_of_all_original_requests", duplicated),
            ("duplicate_wins_percent_of_all_original_requests", win_all),
            ("duplicate_wins_percent_of_duplicated_requests", win_all / duplicated),
        ):
            percentage = D(100) * D(fraction.numerator) / D(fraction.denominator)
            self.assertEqual(reported[key], f"{percentage:.9f}")

    def test_cdf_dominance_and_larger_batch_smaller_gain(self):
        with localcontext() as ctx:
            ctx.prec = 80
            for t in range(50, 401):
                self.assertGreaterEqual(model.completion_cdf(D(t), True) + D("1e-75"), model.uniform_cdf(D(t)))
            gains = [model.quantile(n) - model.quantile(n, duplicates=True) for n in (100, 10000)]
            self.assertGreater(gains[0], gains[1])
            self.assertGreater(gains[1], 0)
            # First-order asymptotic improvement is 262.5 * (-ln(.95)) / n.
            approximate = D("262.5") * -D("0.95").ln() / 10000
            self.assertLess(abs(gains[1] / approximate - 1), D("0.00005"))

    def test_invalid_quantile_inputs(self):
        for n, p in ((0, D("0.95")), (True, D("0.95")), (100, D(0)), (100, D(1))):
            with self.assertRaises(ValueError):
                model.quantile(n, p)


if __name__ == "__main__":
    unittest.main()
