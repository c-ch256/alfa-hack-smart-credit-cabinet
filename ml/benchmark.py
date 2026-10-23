"""python -m ml.benchmark: примеры backend и p95 полного score с SHAP."""
import json
from pathlib import Path
import platform
import time

import numpy as np

from ml.features import CONTRACT_DIR
from ml.inference import ModelScorer


def main():
    scorer = ModelScorer()
    examples = json.loads((CONTRACT_DIR / 'scoring_requests.json').read_text(encoding='utf-8'))
    requests = [example['request'] for example in examples]
    outputs = [{'scenario': example['scenario'], 'result': scorer.score(example['request'])}
               for example in examples]
    for _ in range(10):
        scorer.score(requests[0])
    timings = []
    for i in range(100):
        start = time.perf_counter()
        scorer.score(requests[i % len(requests)])
        timings.append((time.perf_counter() - start) * 1000)
    start = time.perf_counter()
    scorer.score_many((requests * 10))
    report = {'single_call_p50_ms': float(np.percentile(timings, 50)),
              'single_call_p95_ms': float(np.percentile(timings, 95)),
              'batch_30_ms': (time.perf_counter() - start) * 1000,
              'includes': 'input validation, feature calculation, prediction, calibration, SHAP',
              'platform': platform.platform(), 'processor': platform.processor()}
    output = Path('ml/reports')
    output.mkdir(parents=True, exist_ok=True)
    (output / 'backend_examples.json').write_text(json.dumps(outputs, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    (output / 'benchmark.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
