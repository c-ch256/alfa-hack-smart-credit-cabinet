import copy
import json
from pathlib import Path
import unittest

import numpy as np
import pandas as pd
from jsonschema import Draft202012Validator, ValidationError

from ml.features import (CONTRACT_DIR, INPUT_FIELDS, build_features,
                         request_to_row, row_to_request)
from ml.inference import ModelScorer, risk_band
from ml.train import read_data, split_data


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scorer = ModelScorer()
        cls.examples = json.loads((CONTRACT_DIR / 'scoring_requests.json').read_text(encoding='utf-8'))
        cls.validator = Draft202012Validator(json.loads(
            (CONTRACT_DIR / 'scoring_result.schema.json').read_text(encoding='utf-8')))

    def test_backend_examples(self):
        for example in self.examples:
            req = example['request']
            result = self.scorer.score(req)
            self.validator.validate(result)
            self.assertEqual(result['risk_band'], risk_band(result['pd']))
            self.assertEqual(result, self.scorer.score(req))
            self.assertTrue(result['reasons'])
            self.assertEqual(row_to_request(request_to_row(req)), req)

    def test_all_generated_rows_match_contract(self):
        df = read_data(Path('data/synthetic_data.csv'))
        self.assertEqual(set(df.columns), set(INPUT_FIELDS) | {'default_12m', 'application_date'})
        for row in df.to_dict(orient='records'):
            request_to_row(row_to_request(row))

    def test_no_target_or_date_leakage(self):
        df = read_data(Path('data/synthetic_data.csv'))
        expected = build_features(df)
        df['default_12m'] = 1 - df.default_12m
        df['application_date'] = pd.Timestamp('2099-01-01')
        pd.testing.assert_frame_equal(expected, build_features(df))

    def test_disjoint_splits(self):
        df = read_data(Path('data/synthetic_data.csv'))
        splits = split_data(df, 42)
        self.assertEqual(sum(map(len, splits)), len(df))
        for i, a in enumerate(splits):
            for b in splits[i + 1:]:
                self.assertFalse(set(a) & set(b))
        self.assertLess(df.loc[np.concatenate(splits[:3]), 'application_date'].max(),
                        df.loc[splits[3], 'application_date'].min())

    def test_invalid_and_unknown_inputs(self):
        req = copy.deepcopy(self.examples[0]['request'])
        req['company']['okved'] = '99.99'
        req['company']['region'] = 'Новый регион'
        req['company']['avg_monthly_revenue'] = 0
        self.validator.validate(self.scorer.score(req))
        req['amount'] = -1
        with self.assertRaises(ValidationError):
            self.scorer.score(req)
        req = copy.deepcopy(self.examples[0]['request'])
        req['company']['revenue_trend_6m'] = float('nan')
        with self.assertRaises(ValueError):
            self.scorer.score(req)

    def test_monotonic_overdue_and_batch(self):
        requests = []
        for overdue in (0, 5, 30, 45, 90):
            req = copy.deepcopy(self.examples[0]['request'])
            req['bki']['max_overdue_days_12m'] = overdue
            requests.append(req)
        results = self.scorer.score_many(requests)
        self.assertTrue(np.all(np.diff([r['pd'] for r in results]) >= -1e-12))
        for req, result in zip(requests, results):
            self.assertAlmostEqual(result['pd'], self.scorer.score(req)['pd'], places=12)
        self.assertEqual(self.scorer.score_many([]), [])

    def test_band_boundaries(self):
        self.assertEqual([risk_band(p) for p in [0, .0499, .05, .10, .20, 1]],
                         ['A', 'A', 'B', 'C', 'D', 'D'])


if __name__ == '__main__':
    unittest.main()
