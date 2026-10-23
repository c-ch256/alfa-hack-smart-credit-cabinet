import contextlib
import io
import unittest

import numpy as np
import pandas as pd

from ml.generate_data import AS_OF_DATE, compute_default_label, generate_data


class GeneratorTests(unittest.TestCase):
    @staticmethod
    def generate(n=15000, seed=42):
        with contextlib.redirect_stdout(io.StringIO()):
            return generate_data(n, seed)

    def test_reproducible(self):
        pd.testing.assert_frame_equal(self.generate(1000), self.generate(1000))
        self.assertFalse(self.generate(1000).equals(self.generate(1000, 43)))

    def test_limits_and_mature_labels(self):
        df = self.generate()
        self.assertFalse(df.isna().any().any())
        self.assertTrue(np.isfinite(df.select_dtypes(include='number')).all().all())
        self.assertEqual(set(df.product_code), {'working_capital', 'investment'})
        for product, limits in [('working_capital', (300000, 30000000, 3, 36)),
                                ('investment', (1000000, 100000000, 12, 84))]:
            rows = df[df.product_code == product]
            self.assertTrue(rows.amount.between(*limits[:2]).all())
            self.assertTrue(rows.term_months.between(*limits[2:]).all())
        self.assertTrue((df.bank_client_months <= df.business_age_months).all())
        no_loans = df.active_loans == 0
        self.assertTrue((df.loc[no_loans, ['total_debt', 'existing_monthly_payments']] == 0).all().all())
        self.assertTrue((df.defaults == 0).all())
        self.assertTrue(df.max_overdue_days_12m.between(0, 90).all())
        end = pd.Timestamp(AS_OF_DATE) - pd.DateOffset(months=12)
        start = end - pd.DateOffset(months=24) + pd.Timedelta(days=1)
        self.assertTrue(df.application_date.between(start, end).all())
        self.assertEqual(set(df.default_12m), {0, 1})
        self.assertNotIn('default_prob', df.columns)

    def test_rates_across_seeds(self):
        for seed in (42, 43, 44):
            rate = self.generate(seed=seed).default_12m.mean()
            self.assertTrue(0.05 <= rate <= 0.08, (seed, rate))

    def test_identical_features_have_stochastic_outcomes(self):
        # При одинаковом риске исходы всё равно могут отличаться.
        df = pd.concat([self.generate(1)] * 10000, ignore_index=True)
        labels = compute_default_label(df, df, df, df, np.random.default_rng(42))
        self.assertEqual(set(labels), {0, 1})
        self.assertTrue(0.05 <= labels.mean() <= 0.08)

    def test_invalid_size(self):
        with self.assertRaises(ValueError):
            self.generate(0)


if __name__ == '__main__':
    unittest.main()
